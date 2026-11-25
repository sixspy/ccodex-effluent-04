"""采样批次入库、超标裁定与 effluent-04 指标回写封死。"""
from __future__ import annotations

import json
import threading

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from effluent04_seed import DEVIATION_PHRASE
from effluent_errors import MetricFrozenAfterSeal
from locks import release, require_lock
from metrics_math import merge_metrics, parse_triplet
from models import MetricWriteIn, SampleIn
from validators import (
    adjudicate_exceed,
    metric_summary,
    validate_flow_cap,
    validate_metrics_nonneg,
    validate_outfall_open,
    validate_plant_running,
)

router = APIRouter(tags=["samples"])

# 同进程同批互斥：兜住「同账号两路会话」在同一瞬间回写
_RUNTIME_META = threading.Lock()
_RUNTIME_LOCKS: dict[int, threading.Lock] = {}


def _runtime_batch_lock(sample_id: int) -> threading.Lock:
    with _RUNTIME_META:
        lk = _RUNTIME_LOCKS.get(sample_id)
        if lk is None:
            lk = threading.Lock()
            _RUNTIME_LOCKS[sample_id] = lk
        return lk


def _next_sample_code(conn, outfall_id: int) -> str:
    outfall = conn.execute(
        "SELECT outfall_code FROM outfalls WHERE id=?", (outfall_id,)
    ).fetchone()
    prefix = outfall["outfall_code"] if outfall else f"OF-{outfall_id}"
    n = conn.execute(
        "SELECT COUNT(*) c FROM sample_batches WHERE outfall_id=?", (outfall_id,)
    ).fetchone()["c"]
    return f"SM-{prefix}-{n + 1:03d}"


def _resolve_sample(conn, sample_ref: str | int):
    """路径参数既接受数字 id，也接受 SM-D22 这样的 sample_code。"""
    ref = str(sample_ref)
    if ref.isdigit():
        row = conn.execute("SELECT * FROM sample_batches WHERE id=?", (int(ref),)).fetchone()
        if row:
            return row
    return conn.execute(
        "SELECT * FROM sample_batches WHERE sample_code=?", (ref,)
    ).fetchone()


def _effective_seal(conn, sample_id: int):
    return conn.execute(
        """SELECT * FROM discharge_seals
           WHERE sample_id=? AND status='已封批'
           ORDER BY sample_generation DESC, id DESC LIMIT 1""",
        (sample_id,),
    ).fetchone()


def seal_snapshot_metrics(seal_row) -> dict | None:
    if seal_row is None:
        return None
    keys = seal_row.keys()
    if "snapshot_metrics" in keys and seal_row["snapshot_metrics"]:
        try:
            return json.loads(seal_row["snapshot_metrics"])
        except (TypeError, ValueError):
            pass
    return parse_triplet(seal_row["snapshot"] or "")


def _seal_flags(conn, sample_id: int, current_metrics: dict | None = None) -> dict:
    seal = _effective_seal(conn, sample_id)
    if seal is None:
        return {
            "seal_effective": False,
            "metrics_readonly": False,
            "effective_generation": None,
            "lock_key": f"sample:{sample_id}",
            "metrics_drift": False,
        }
    snap = seal_snapshot_metrics(seal)
    drift = False
    if snap and current_metrics:
        drift = any(
            abs(float(current_metrics.get(k, 0.0)) - float(snap.get(k, 0.0))) > 1e-9
            for k in ("cod", "nh3n", "tp")
        )
    return {
        "seal_effective": True,
        "metrics_readonly": True,
        "effective_generation": seal["sample_generation"],
        "effective_marker": f"封批代已生效 · G{seal['sample_generation']}",
        "seal_ref": seal["seal_ref"],
        "seal_snapshot_metrics": snap,
        "metrics_drift": drift,
        "drift_note": (
            f"{DEVIATION_PHRASE}：正式指标列与封批快照 G{seal['sample_generation']} 不同代，"
            "合格判定以封批快照为准"
            if drift
            else None
        ),
        "lock_key": f"sample:{sample_id}",
    }


@router.get("/api/samples")
def list_samples(authorization: AuthHeader = None, status: str | None = None):
    require_user(authorization)
    with connect() as conn:
        q = """SELECT s.*, o.outfall_code, p.plant_code FROM sample_batches s
               JOIN outfalls o ON o.id=s.outfall_id
               JOIN plants p ON p.id=o.plant_id"""
        if status:
            rows = conn.execute(
                q + " WHERE s.status=? ORDER BY s.id DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(q + " ORDER BY s.id DESC").fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["metrics"] = json.loads(d["metrics"] or "{}")
            d.update(_seal_flags(conn, d["id"], d["metrics"]))
            items.append(d)
        return {"items": items}


@router.get("/api/samples/{sample_ref}")
def get_sample(sample_ref: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            """SELECT s.*, o.outfall_code, o.cod_limit, o.nh3n_limit, o.tp_limit,
                      p.name plant_name
               FROM sample_batches s
               JOIN outfalls o ON o.id=s.outfall_id
               JOIN plants p ON p.id=o.plant_id
               WHERE s.id=? OR s.sample_code=?""",
            (int(sample_ref) if str(sample_ref).isdigit() else -1, sample_ref),
        ).fetchone()
        if not row:
            raise HTTPException(404, "采样批次不存在")
        data = dict(row)
        data["metrics"] = json.loads(data["metrics"] or "{}")
        data.update(_seal_flags(conn, data["id"], data["metrics"]))
        holds = conn.execute(
            "SELECT * FROM exceed_holds WHERE sample_id=? ORDER BY id DESC",
            (data["id"],),
        ).fetchall()
        seals = conn.execute(
            "SELECT * FROM discharge_seals WHERE sample_id=? ORDER BY id DESC",
            (data["id"],),
        ).fetchall()
        releases = conn.execute(
            "SELECT * FROM discharge_releases WHERE sample_id=? ORDER BY id DESC",
            (data["id"],),
        ).fetchall()
        return {
            "sample": data,
            "exceed_holds": [dict(h) for h in holds],
            "discharge_seals": [dict(s) for s in seals],
            "discharge_releases": [dict(r) for r in releases],
        }


@router.post("/api/samples")
def create_sample(body: SampleIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"outfall:{body.outfall_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            outfall = conn.execute(
                """SELECT o.*, p.status plant_status, p.design_cap_m3d
                   FROM outfalls o JOIN plants p ON p.id=o.plant_id WHERE o.id=?""",
                (body.outfall_id,),
            ).fetchone()
            if not outfall:
                raise HTTPException(404, "排放口不存在")
            validate_outfall_open(outfall["status"])
            validate_plant_running(outfall["plant_status"])
            metrics = body.metrics.model_dump()
            validate_metrics_nonneg(metrics)
            validate_flow_cap(body.flow_m3h, float(outfall["design_cap_m3d"]))
            permit = conn.execute(
                "SELECT * FROM discharge_permits WHERE permit_code=?",
                (body.permit_ref,),
            ).fetchone()
            if not permit:
                raise HTTPException(400, "排放许可不存在")
            if permit["status"] != "有效":
                raise HTTPException(400, "排放许可未生效")
            exceeded, metric, measured, limit = adjudicate_exceed(
                metrics,
                {
                    "cod": outfall["cod_limit"],
                    "nh3n": outfall["nh3n_limit"],
                    "tp": outfall["tp_limit"],
                },
            )
            code = body.sample_code or _next_sample_code(conn, body.outfall_id)
            if conn.execute(
                "SELECT id FROM sample_batches WHERE sample_code=?", (code,)
            ).fetchone():
                raise HTTPException(400, "采样编号已存在")
            status = "旁路挂起" if exceeded else "已裁定"
            adjudicated = None if exceeded else now_iso()
            cur = conn.execute(
                """INSERT INTO sample_batches(sample_code,outfall_id,permit_ref,metrics,flow_m3h,
                   generation,status,adjudicated_at,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    code,
                    body.outfall_id,
                    body.permit_ref,
                    json.dumps(metrics, ensure_ascii=False),
                    body.flow_m3h,
                    1,
                    status,
                    adjudicated,
                    now_iso(),
                ),
            )
            sid = cur.lastrowid
            hold = None
            if exceeded:
                hold_key = f"EX-{code}-{metric}"
                conn.execute(
                    """INSERT INTO exceed_holds(sample_id,hold_key,exceed_metric,measured,
                       limit_value,status,opened_at,note)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        sid,
                        hold_key,
                        metric,
                        measured,
                        limit,
                        "开放",
                        now_iso(),
                        "入库自动挂起",
                    ),
                )
                hold = hold_key
            write_audit(
                conn,
                user,
                "sample_create",
                code,
                {
                    "status": status,
                    "metrics": metrics,
                    "summary": metric_summary(metrics),
                    "exceed": exceeded,
                },
            )
            conn.commit()
            return {
                "id": sid,
                "sample_code": code,
                "status": status,
                "exceed": exceeded,
                "hold_key": hold,
            }
        finally:
            release(conn, lock_key, user)
            conn.commit()


def _coerce_patch(body: MetricWriteIn) -> dict:
    if body.metrics:
        src = body.metrics
        patch = {
            k: float(src[k])
            for k in ("cod", "nh3n", "tp")
            if k in src and src[k] is not None
        }
    else:
        patch = {
            k: float(getattr(body, k))
            for k in ("cod", "nh3n", "tp")
            if getattr(body, k) is not None
        }
    return patch


def _record_freeze_deviation(
    conn, user: str, sample, patch: dict, body: MetricWriteIn
) -> None:
    gen = None
    seal = _effective_seal(conn, sample["id"])
    if seal is not None:
        gen = seal["sample_generation"]
    detail = (
        f"{DEVIATION_PHRASE}：{sample['sample_code']} 封批代 G{gen} 已生效，"
        f"监控台回写 COD/氨氮/总磷被拒，正式指标表零改；"
        f"尝试值={patch}；原因={body.reason or '监控台回写'}"
    )
    conn.execute(
        """INSERT INTO deviation_log(kind,code,sample_id,sample_code,lock_key,actor,detail,created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            "metric_write_rejected",
            DEVIATION_PHRASE,
            sample["id"],
            sample["sample_code"],
            f"sample:{sample['id']}",
            user,
            detail,
            now_iso(),
        ),
    )
    write_audit(
        conn,
        user,
        DEVIATION_PHRASE,
        sample["sample_code"],
        {
            "code": "metric_frozen_after_seal",
            "generation": gen,
            "patch": patch,
            "reason": body.reason,
        },
    )


def _write_metric(sample_ref: str, body: MetricWriteIn, authorization: str | None):
    user = require_writer(authorization)
    with connect() as conn:
        sample = _resolve_sample(conn, sample_ref)
        if not sample:
            raise HTTPException(404, "采样批次不存在")
        sid = int(sample["id"])
        lock_key = f"sample:{sid}"
        holder = f"{user}#{body.session_id}" if body.session_id else user

        # 1) 批锁仲裁（先于封批判定：争用痕必须独立成立，禁止单点封标记交差）
        acquired_db = require_lock(conn, lock_key, holder)

        # 2) 同进程会话互斥（同账号两路会话的瞬间并发）
        rlk = _runtime_batch_lock(sid)
        acquired_rt = rlk.acquire(blocking=False)
        if not acquired_rt:
            conn.execute(
                """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
                   VALUES (?,?,?,?,?)""",
                (lock_key, holder, "争用失败", "同进程并发回写被运行时批锁仲裁", now_iso()),
            )
            conn.commit()
            if acquired_db:
                release(conn, lock_key, holder)
                conn.commit()
            from effluent_errors import LockBusy

            raise LockBusy("同批指标正被另一路会话回写，后到仅留争用痕，指标零写入")

        try:
            seal = _effective_seal(conn, sid)
            if seal is not None:
                patch = _coerce_patch(body)
                _record_freeze_deviation(conn, user, sample, patch, body)
                conn.commit()
                raise MetricFrozenAfterSeal(
                    f"{DEVIATION_PHRASE}：{sample['sample_code']} 封批代 "
                    f"G{seal['sample_generation']} 已生效，COD/氨氮/总磷三列只读，"
                    "正式指标表一律零改并拒写"
                )

            patch = _coerce_patch(body)
            if not patch:
                raise HTTPException(400, "未提供任何指标值（cod/nh3n/tp 或 metrics）")
            current = json.loads(sample["metrics"] or "{}")
            metrics = merge_metrics(current, patch)
            validate_metrics_nonneg(metrics)
            conn.execute(
                "UPDATE sample_batches SET metrics=? WHERE id=?",
                (json.dumps(metrics, ensure_ascii=False), sid),
            )
            write_audit(
                conn,
                user,
                "metric_writeback",
                sample["sample_code"],
                {"patch": patch, "metrics": metrics},
            )
            conn.commit()
            return {
                "ok": True,
                "id": sid,
                "sample_code": sample["sample_code"],
                "metrics": metrics,
                "metrics_readonly": False,
                "lock_key": lock_key,
            }
        finally:
            if acquired_rt:
                try:
                    rlk.release()
                except RuntimeError:
                    pass
            if acquired_db:
                release(conn, lock_key, holder)
                conn.commit()


@router.post("/api/samples/metrics")
def write_metrics_collection(body: MetricWriteIn, authorization: AuthHeader = None):
    if body.sample_id is not None:
        ref = str(body.sample_id)
    elif body.sample_code:
        ref = body.sample_code
    else:
        raise HTTPException(422, "集合式回写必须提供 sample_id 或 sample_code")
    return _write_metric(ref, body, authorization)


@router.post("/api/metrics/writeback")
def writeback_metrics(body: MetricWriteIn, authorization: AuthHeader = None):
    """别名：监控台指标回写常见路径 /api/metrics/writeback。"""
    return write_metrics_collection(body, authorization)


@router.post("/api/metric/writeback")
def writeback_metric_singular(body: MetricWriteIn, authorization: AuthHeader = None):
    return write_metrics_collection(body, authorization)


@router.post("/api/metrics/update")
def update_metrics_alias(body: MetricWriteIn, authorization: AuthHeader = None):
    """别名：/api/metrics/update。"""
    return write_metrics_collection(body, authorization)


@router.put("/api/samples/metrics")
@router.put("/api/metrics")
def put_metrics_collection(body: MetricWriteIn, authorization: AuthHeader = None):
    return write_metrics_collection(body, authorization)


@router.put("/api/samples/{sample_ref}")
def put_sample_root_metrics(sample_ref: str, body: MetricWriteIn, authorization: AuthHeader = None):
    """别名：REST 风 PUT /api/samples/{id|code}，仅承接指标回写并同口径冻结。"""
    return _write_metric(sample_ref, body, authorization)


@router.patch("/api/samples/{sample_ref}")
def patch_sample_root_metrics(sample_ref: str, body: MetricWriteIn, authorization: AuthHeader = None):
    return _write_metric(sample_ref, body, authorization)


@router.post("/api/samples/metric")
def write_sample_metric_flat(body: MetricWriteIn, authorization: AuthHeader = None):
    """别名：/api/samples/metric 集合式单数路径。"""
    return write_metrics_collection(body, authorization)


@router.post("/api/samples/{sample_ref}/metric")
def write_metric_singular(sample_ref: str, body: MetricWriteIn, authorization: AuthHeader = None):
    return _write_metric(sample_ref, body, authorization)


@router.post("/api/samples/{sample_ref}/metrics")
def write_metrics(sample_ref: str, body: MetricWriteIn, authorization: AuthHeader = None):
    if body.sample_id is not None and str(body.sample_id) != str(sample_ref):
        raise HTTPException(400, "路径批次与 body.sample_id 不一致")
    return _write_metric(sample_ref, body, authorization)


@router.put("/api/samples/{sample_ref}/metrics")
def put_metrics(sample_ref: str, body: MetricWriteIn, authorization: AuthHeader = None):
    return _write_metric(sample_ref, body, authorization)


@router.patch("/api/samples/{sample_ref}/metrics")
def patch_metrics(sample_ref: str, body: MetricWriteIn, authorization: AuthHeader = None):
    return _write_metric(sample_ref, body, authorization)
