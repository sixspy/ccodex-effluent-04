"""排放许可与采样对账证明。

effluent-04：合格灯必须与封批快照同代。已封批批次不再用可被回写的正式列判定，
而用 discharge_seals.snapshot_metrics（缺失时解析快照文本）判定；正式列与快照
漂移时直接判不合格（封后禁改偏航），杜绝 SM-D22 式假绿。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user
from metrics_math import parse_triplet
from validators import validate_permit_match

router = APIRouter(tags=["reconcile"])


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    from db import connect

    with connect() as conn:
        rows = conn.execute(
            "SELECT permit_code FROM discharge_permits ORDER BY permit_code"
        ).fetchall()
        return {"items": [_reconcile_permit(conn, r["permit_code"]) for r in rows]}


@router.get("/api/reconcile/{permit_code}")
def reconcile_one(permit_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    from db import connect

    with connect() as conn:
        if not conn.execute(
            "SELECT id FROM discharge_permits WHERE permit_code=?", (permit_code,)
        ).fetchone():
            raise HTTPException(404, "许可不存在")
        return _reconcile_permit(conn, permit_code)


def _snapshot_metrics(seal) -> dict | None:
    if seal is None:
        return None
    if "snapshot_metrics" in seal.keys() and seal["snapshot_metrics"]:
        try:
            return json.loads(seal["snapshot_metrics"])
        except (TypeError, ValueError):
            pass
    return parse_triplet(seal["snapshot"] or "")


def _reconcile_permit(conn, permit_code: str) -> dict:
    permit = conn.execute(
        "SELECT * FROM discharge_permits WHERE permit_code=?", (permit_code,)
    ).fetchone()
    samples = conn.execute(
        """SELECT sample_code, flow_m3h, metrics, status, generation, id FROM sample_batches
           WHERE permit_ref=? AND status NOT IN ('作废')""",
        (permit_code,),
    ).fetchall()
    if not samples:
        return {
            "permit_code": permit_code,
            "balanced": False,
            "lamp": "不合格",
            "reason": "无采样批次",
            "samples": [],
            "lamp_generation": None,
        }
    caps = {
        "cod": permit["cod_cap"],
        "nh3n": permit["nh3n_cap"],
        "tp": permit["tp_cap"],
    }
    details = []
    all_ok = True
    drift_count = 0
    lamp_generations: list[int] = []
    for s in samples:
        metrics = json.loads(s["metrics"] or "{}")
        seal = conn.execute(
            """SELECT * FROM discharge_seals
               WHERE sample_id=? AND status='已封批'
               ORDER BY sample_generation DESC, id DESC LIMIT 1""",
            (s["id"],),
        ).fetchone()

        drift = False
        if seal is not None:
            judged = _snapshot_metrics(seal) or metrics
            judged_generation = int(seal["sample_generation"])
            judged_source = "seal_snapshot"
            lamp_generations.append(judged_generation)
            drift = any(
                abs(float(metrics.get(k, 0.0)) - float(judged.get(k, 0.0))) > 1e-9
                for k in ("cod", "nh3n", "tp")
            )
        else:
            judged = metrics
            judged_generation = int(s["generation"])
            judged_source = "official_current"

        ok = validate_permit_match(
            caps, judged, float(s["flow_m3h"]), float(permit["quota_m3d"])
        )
        if drift:
            ok = False
            drift_count += 1
        if not ok:
            all_ok = False
        details.append(
            {
                "sample_code": s["sample_code"],
                "status": s["status"],
                "generation": s["generation"],
                "lamp_generation": judged_generation,
                "judged_generation": judged_generation,
                "judged_source": judged_source,
                "metrics": metrics,
                "judged_metrics": judged,
                "snapshot_metrics": judged if seal is not None else None,
                "metrics_drift": drift,
                "flow_m3h": s["flow_m3h"],
                "matched": ok,
            }
        )
    seal_rows = conn.execute(
        """SELECT d.seal_ref, d.sample_generation, s.sample_code
           FROM discharge_seals d JOIN sample_batches s ON s.id=d.sample_id
           WHERE s.permit_ref=?""",
        (permit_code,),
    ).fetchall()
    lamp_gen = max(lamp_generations) if lamp_generations else None
    return {
        "permit_code": permit_code,
        "quota_m3d": permit["quota_m3d"],
        "caps": caps,
        "balanced": all_ok,
        "lamp": "合格" if all_ok else "不合格",
        "lamp_generation": lamp_gen,
        "generation_aligned": all(
            d["judged_source"] == "seal_snapshot"
            for d in details
            if d["status"] == "已封批"
        ),
        "drift_count": drift_count,
        "proof_ready": all_ok and len(seal_rows) >= 1,
        "seal_count": len(seal_rows),
        "samples": details,
        "seals": [dict(x) for x in seal_rows],
    }


@router.get("/api/proof/sample/{sample_code}")
def proof_sample(sample_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    from db import connect

    with connect() as conn:
        sample = conn.execute(
            "SELECT * FROM sample_batches WHERE sample_code=?", (sample_code,)
        ).fetchone()
        if not sample:
            raise HTTPException(404, "采样不存在")
        holds = conn.execute(
            "SELECT * FROM exceed_holds WHERE sample_id=?", (sample["id"],)
        ).fetchall()
        releases = conn.execute(
            "SELECT * FROM discharge_releases WHERE sample_id=?", (sample["id"],)
        ).fetchall()
        seals = conn.execute(
            "SELECT * FROM discharge_seals WHERE sample_id=?", (sample["id"],)
        ).fetchall()
        open_holds = [h for h in holds if h["status"] == "开放"]
        closed = [h for h in holds if h["status"] == "已关闭"]
        effective_seal = next((s for s in seals if s["status"] == "已封批"), None)
        return {
            "sample_code": sample_code,
            "status": sample["status"],
            "generation": sample["generation"],
            "released": any(r["status"] == "已准排" for r in releases),
            "sealed": any(s["status"] == "已封批" for s in seals),
            "metrics_readonly": effective_seal is not None,
            "lamp_generation": (
                effective_seal["sample_generation"] if effective_seal else None
            ),
            "exceed_closed": len(open_holds) == 0 and (len(closed) > 0 or sample["status"] != "旁路挂起"),
            "holds": [dict(h) for h in holds],
            "releases": [dict(r) for r in releases],
            "seals": [dict(s) for s in seals],
        }
