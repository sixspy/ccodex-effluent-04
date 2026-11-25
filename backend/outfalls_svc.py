"""排放口限值与开放态。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from models import OutfallIn

router = APIRouter(tags=["outfalls"])


def _curve_points(conn, outfall_id: int) -> list[dict]:
    """曲线点严格取正式指标行；ts 用指标行自带时间戳（裁定时间优先，否则入库时间）。

    这保证「排放口页曲线采样点与正式指标同戳」，前端不得另造时间轴。
    """
    rows = conn.execute(
        """SELECT s.id, s.sample_code, s.status, s.generation, s.flow_m3h,
                  s.metrics, s.created_at, s.adjudicated_at,
                  (SELECT d.sample_generation FROM discharge_seals d
                    WHERE d.sample_id=s.id AND d.status='已封批'
                    ORDER BY d.sample_generation DESC LIMIT 1) AS seal_generation
           FROM sample_batches s
           WHERE s.outfall_id=? AND s.status NOT IN ('作废')
           ORDER BY s.id ASC""",
        (outfall_id,),
    ).fetchall()
    points = []
    for r in rows:
        metrics = json.loads(r["metrics"] or "{}")
        ts = r["adjudicated_at"] or r["created_at"]
        points.append(
            {
                "sample_id": r["id"],
                "sample_code": r["sample_code"],
                "ts": ts,
                "timestamp": ts,
                "created_at": r["created_at"],
                "adjudicated_at": r["adjudicated_at"],
                "status": r["status"],
                "generation": r["generation"],
                "seal_generation": r["seal_generation"],
                "sealed": r["seal_generation"] is not None,
                "flow_m3h": r["flow_m3h"],
                "cod": metrics.get("cod"),
                "nh3n": metrics.get("nh3n"),
                "tp": metrics.get("tp"),
                "metrics": metrics,
            }
        )
    return points


@router.get("/api/outfalls")
def list_outfalls(authorization: AuthHeader = None, plant_id: int | None = None):
    require_user(authorization)
    with connect() as conn:
        if plant_id:
            rows = conn.execute(
                """SELECT o.*, p.plant_code, p.name plant_name FROM outfalls o
                   JOIN plants p ON p.id=o.plant_id WHERE o.plant_id=? ORDER BY o.id""",
                (plant_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT o.*, p.plant_code, p.name plant_name FROM outfalls o
                   JOIN plants p ON p.id=o.plant_id ORDER BY o.id"""
            ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/outfalls/{outfall_id}")
def get_outfall(outfall_id: int, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            """SELECT o.*, p.plant_code, p.design_cap_m3d FROM outfalls o
               JOIN plants p ON p.id=o.plant_id WHERE o.id=?""",
            (outfall_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "排放口不存在")
        recent = conn.execute(
            """SELECT id, sample_code, status, flow_m3h, generation, metrics,
                      created_at, adjudicated_at FROM sample_batches
               WHERE outfall_id=? ORDER BY id DESC LIMIT 10""",
            (outfall_id,),
        ).fetchall()
        recent_samples = []
        for s in recent:
            d = dict(s)
            d["metrics"] = json.loads(d["metrics"] or "{}")
            d["ts"] = d["adjudicated_at"] or d["created_at"]
            recent_samples.append(d)
        curve = _curve_points(conn, outfall_id)
        return {
            "outfall": dict(row),
            "recent_samples": recent_samples,
            "curve": curve,
            "points": curve,
        }


@router.get("/api/outfalls/{outfall_id}/curve")
def get_outfall_curve(outfall_id: int, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            "SELECT id, outfall_code FROM outfalls WHERE id=?", (outfall_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "排放口不存在")
        points = _curve_points(conn, outfall_id)
        return {
            "outfall_id": outfall_id,
            "outfall_code": row["outfall_code"],
            "points": points,
        }


@router.post("/api/outfalls")
def create_outfall(body: OutfallIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        plant = conn.execute(
            "SELECT * FROM plants WHERE id=?", (body.plant_id,)
        ).fetchone()
        if not plant:
            raise HTTPException(404, "处理厂不存在")
        if conn.execute(
            "SELECT id FROM outfalls WHERE outfall_code=?", (body.outfall_code,)
        ).fetchone():
            raise HTTPException(400, "排放口编码已存在")
        cur = conn.execute(
            """INSERT INTO outfalls(outfall_code,plant_id,river_reach,cod_limit,nh3n_limit,
               tp_limit,status,created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (
                body.outfall_code,
                body.plant_id,
                body.river_reach,
                body.cod_limit,
                body.nh3n_limit,
                body.tp_limit,
                body.status,
                now_iso(),
            ),
        )
        oid = cur.lastrowid
        write_audit(conn, user, "outfall_create", body.outfall_code, body.model_dump())
        conn.commit()
        return {"id": oid, **body.model_dump()}


@router.post("/api/outfalls/{outfall_id}/status")
def set_outfall_status(outfall_id: int, status: str, authorization: AuthHeader = None):
    user = require_writer(authorization)
    if status not in ("开放", "停用", "检修"):
        raise HTTPException(400, "非法状态")
    with connect() as conn:
        row = conn.execute("SELECT * FROM outfalls WHERE id=?", (outfall_id,)).fetchone()
        if not row:
            raise HTTPException(404, "排放口不存在")
        conn.execute("UPDATE outfalls SET status=? WHERE id=?", (status, outfall_id))
        write_audit(
            conn, user, "outfall_status", row["outfall_code"], {"status": status}
        )
        conn.commit()
        return {"id": outfall_id, "status": status}
