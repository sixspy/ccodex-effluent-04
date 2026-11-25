"""排放封批升代快照。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import release, require_lock
from models import SealIn
from validators import metric_summary, validate_sample_sealable

router = APIRouter(tags=["seal"])


def _decorate(row) -> dict:
    d = dict(row)
    snap_metrics = None
    raw = d.get("snapshot_metrics")
    if raw:
        try:
            snap_metrics = json.loads(raw)
        except (TypeError, ValueError):
            snap_metrics = None
    d["snapshot_metrics"] = snap_metrics
    effective = d.get("status") == "已封批"
    d["effective"] = effective
    d["seal_effective"] = effective
    d["effective_marker"] = (
        f"封批代已生效 · G{d.get('sample_generation')}" if effective else "封批代未生效"
    )
    d["metrics_readonly"] = effective
    return d


@router.get("/api/seals")
def list_seals(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.*, s.sample_code FROM discharge_seals d
               JOIN sample_batches s ON s.id=d.sample_id
               ORDER BY d.id DESC"""
        ).fetchall()
        return {"items": [_decorate(r) for r in rows]}


@router.get("/api/seals/effective/{sample_code}")
def effective_seal(sample_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        sample = conn.execute(
            "SELECT id, sample_code, generation FROM sample_batches WHERE sample_code=?",
            (sample_code,),
        ).fetchone()
        if not sample:
            raise HTTPException(404, "采样批次不存在")
        row = conn.execute(
            """SELECT * FROM discharge_seals
               WHERE sample_id=? AND status='已封批'
               ORDER BY sample_generation DESC, id DESC LIMIT 1""",
            (sample["id"],),
        ).fetchone()
        if not row:
            return {
                "sample_code": sample_code,
                "seal_effective": False,
                "metrics_readonly": False,
                "marker": "封批代未生效",
            }
        d = _decorate(row)
        return {
            "sample_code": sample_code,
            "seal_effective": True,
            "metrics_readonly": True,
            "generation": d["sample_generation"],
            "seal_ref": d["seal_ref"],
            "marker": d["effective_marker"],
            "effective_marker": d["effective_marker"],
            "snapshot": d["snapshot"],
            "snapshot_metrics": d["snapshot_metrics"],
            "sealed_at": d["sealed_at"],
        }


@router.post("/api/seals")
def create_seal(body: SealIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"sample:{body.sample_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            sample = conn.execute(
                "SELECT * FROM sample_batches WHERE id=?", (body.sample_id,)
            ).fetchone()
            if not sample:
                raise HTTPException(404, "采样批次不存在")
            validate_sample_sealable(sample["status"])
            open_hold = conn.execute(
                "SELECT id FROM exceed_holds WHERE sample_id=? AND status='开放'",
                (body.sample_id,),
            ).fetchone()
            if open_hold:
                raise HTTPException(400, "开放挂起未闭，禁止封批")
            metrics = json.loads(sample["metrics"] or "{}")
            gen = int(sample["generation"]) + 1
            snap = body.snapshot or metric_summary(metrics)
            seal_ref = f"SL-{sample['sample_code']}-G{gen}"
            conn.execute(
                """INSERT INTO discharge_seals(sample_id,sample_generation,snapshot,
                       snapshot_metrics,seal_ref,status,sealed_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    body.sample_id,
                    gen,
                    snap,
                    json.dumps(metrics, ensure_ascii=False),
                    seal_ref,
                    "已封批",
                    now_iso(),
                ),
            )
            conn.execute(
                """UPDATE sample_batches SET generation=?, status=? WHERE id=?""",
                (gen, "已封批", body.sample_id),
            )
            write_audit(
                conn,
                user,
                "seal",
                sample["sample_code"],
                {"seal_ref": seal_ref, "generation": gen, "snapshot": snap},
            )
            conn.commit()
            return {
                "seal_ref": seal_ref,
                "generation": gen,
                "snapshot": snap,
                "effective_marker": f"封批代已生效 · G{gen}",
                "metrics_readonly": True,
            }
        finally:
            release(conn, lock_key, user)
            conn.commit()
