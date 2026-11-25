"""effluent-04 SM-D22 事故夹具播种与「旧监控台」硬改复现库。

本模块只服务于事故复现/测试/开发库播种，不是正式写指标通道：
- seed_incident: 播种「已封批代 G2、正式列已被硬改、影子草稿血缘边混入」的事故现场；
- old_console_hard_writeback: 模拟事故期监控台绕过封批校验/批锁直接硬 UPDATE 正式列。

红期数据库可能还没有 snapshot_metrics/layer 等新列，因此全部按 PRAGMA 实际列兼容写入。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "effluent_04_sm_d22.json"

LOCK_KEY_PREFIX = "sample:"
DEVIATION_PHRASE = "封后禁改偏航"
SHADOW_EDGE_TYPE = "封后影子修订"


def load_incident() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _id_by_code(conn, table: str, code_col: str, code: str) -> int | None:
    row = conn.execute(
        f"SELECT id FROM {table} WHERE {code_col}=?", (code,)
    ).fetchone()
    return int(row[0]) if row else None


def seed_incident(conn: sqlite3.Connection, *, attacked: bool = True) -> dict:
    """播种 SM-D22 事故现场。幂等：已存在 SM-D22 时直接回传关键 id。"""
    fx = load_incident()

    pid = _id_by_code(conn, "plants", "plant_code", fx["plant"]["plant_code"])
    if pid is None:
        p = fx["plant"]
        cur = conn.execute(
            """INSERT INTO plants(plant_code,name,basin,design_cap_m3d,status,created_at)
               VALUES (?,?,?,?,?,?)""",
            (p["plant_code"], p["name"], p["basin"], p["design_cap_m3d"],
             p["status"], "2026-11-21T07:50:00"),
        )
        pid = cur.lastrowid

    oid = _id_by_code(conn, "outfalls", "outfall_code", fx["outfall"]["outfall_code"])
    if oid is None:
        o = fx["outfall"]
        cur = conn.execute(
            """INSERT INTO outfalls(outfall_code,plant_id,river_reach,cod_limit,nh3n_limit,
                   tp_limit,status,created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (o["outfall_code"], pid, o["river_reach"], o["cod_limit"],
             o["nh3n_limit"], o["tp_limit"], o["status"], "2026-11-01T09:30:00"),
        )
        oid = cur.lastrowid

    permit_id = _id_by_code(
        conn, "discharge_permits", "permit_code", fx["permit"]["permit_code"]
    )
    if permit_id is None:
        pm = fx["permit"]
        conn.execute(
            """INSERT INTO discharge_permits(permit_code,plant_id,quota_m3d,cod_cap,nh3n_cap,
                   tp_cap,period_label,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (pm["permit_code"], pid, pm["quota_m3d"], pm["cod_cap"], pm["nh3n_cap"],
             pm["tp_cap"], pm["period_label"], pm["status"], "2026-11-01T10:30:00"),
        )

    sample_id = _id_by_code(conn, "sample_batches", "sample_code", fx["sample"]["sample_code"])
    if sample_id is None:
        s = fx["sample"]
        metrics = (
            fx["official_metrics_hard_written"]
            if attacked
            else fx["official_metrics_before_attack"]
        )
        cur = conn.execute(
            """INSERT INTO sample_batches(sample_code,outfall_id,permit_ref,metrics,flow_m3h,
                   generation,status,adjudicated_at,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
            (s["sample_code"], oid, s["permit_ref"],
             json.dumps(metrics, ensure_ascii=False), fx["sample"]["flow_m3h"],
             s["generation"], s["status"], s["adjudicated_at"], s["created_at"]),
        )
        sample_id = cur.lastrowid

    seal_id = _id_by_code(conn, "discharge_seals", "seal_ref", fx["seal"]["seal_ref"])
    if seal_id is None:
        seal_cols = table_columns(conn, "discharge_seals")
        if "snapshot_metrics" in seal_cols:
            conn.execute(
                """INSERT INTO discharge_seals(sample_id,sample_generation,snapshot,
                       snapshot_metrics,seal_ref,status,sealed_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (sample_id, fx["seal"]["sample_generation"], fx["seal"]["snapshot_text"],
                 json.dumps(fx["seal"]["snapshot_metrics"], ensure_ascii=False),
                 fx["seal"]["seal_ref"], fx["seal"]["status"], fx["seal"]["sealed_at"]),
            )
        else:
            conn.execute(
                """INSERT INTO discharge_seals(sample_id,sample_generation,snapshot,
                       seal_ref,status,sealed_at) VALUES (?,?,?,?,?,?)""",
                (sample_id, fx["seal"]["sample_generation"], fx["seal"]["snapshot_text"],
                 fx["seal"]["seal_ref"], fx["seal"]["status"], fx["seal"]["sealed_at"]),
            )

    draft_cols = table_columns(conn, "metric_shadow_drafts")
    if draft_cols:
        draft_id = _id_by_code(conn, "metric_shadow_drafts", "draft_code",
                               fx["shadow_draft"]["draft_code"])
        if draft_id is None:
            d = fx["shadow_draft"]
            conn.execute(
                """INSERT INTO metric_shadow_drafts(draft_code,sample_id,metrics,reason,
                       status,created_by,created_at) VALUES (?,?,?,?,?,?,?)""",
                (d["draft_code"], sample_id,
                 json.dumps(d["metrics"], ensure_ascii=False), d["reason"],
                 d["status"], "console", "2026-11-21T09:10:00"),
            )

    # 影子血缘边（事故泄漏边）
    exists = conn.execute(
        "SELECT id FROM metric_lineage WHERE detail=?",
        (fx["shadow_lineage_edge"]["detail"],),
    ).fetchone()
    if not exists:
        lin_cols = table_columns(conn, "metric_lineage")
        if {"layer", "draft_code", "promoted"} <= lin_cols:
            conn.execute(
                """INSERT INTO metric_lineage(from_sample_id,to_sample_id,edge_type,detail,
                       layer,draft_code,promoted,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (sample_id, None, fx["shadow_lineage_edge"]["edge_type"],
                 fx["shadow_lineage_edge"]["detail"], "shadow",
                 fx["shadow_draft"]["draft_code"], 0, "2026-11-21T09:11:00"),
            )
        else:
            conn.execute(
                """INSERT INTO metric_lineage(from_sample_id,to_sample_id,edge_type,detail,created_at)
                   VALUES (?,?,?,?,?)""",
                (sample_id, None, fx["shadow_lineage_edge"]["edge_type"],
                 fx["shadow_lineage_edge"]["detail"], "2026-11-21T09:11:00"),
            )

    # 正式对照血缘边（任何修复后都必须仍可见，证明不是靠清空血缘造假绿）
    exists = conn.execute(
        "SELECT id FROM metric_lineage WHERE detail=?",
        (fx["official_lineage_edge"]["detail"],),
    ).fetchone()
    if not exists:
        conn.execute(
            """INSERT INTO metric_lineage(from_sample_id,to_sample_id,edge_type,detail,created_at)
               VALUES (?,?,?,?,?)""",
            (sample_id, None, fx["official_lineage_edge"]["edge_type"],
             fx["official_lineage_edge"]["detail"], "2026-11-21T09:01:00"),
        )

    # 未封批探针批次：供争用测证明「批锁」独立于封批标记
    lp = fx["unsealed_lock_probe"]
    probe_id = _id_by_code(conn, "sample_batches", "sample_code", lp["sample_code"])
    if probe_id is None:
        cur = conn.execute(
            """INSERT INTO sample_batches(sample_code,outfall_id,permit_ref,metrics,flow_m3h,
                   generation,status,adjudicated_at,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
            (lp["sample_code"], oid, fx["permit"]["permit_code"],
             json.dumps(lp["metrics"], ensure_ascii=False), lp["flow_m3h"],
             1, lp["status"], "2026-11-22T10:00:00", "2026-11-22T09:50:00"),
        )
        probe_id = cur.lastrowid

    conn.commit()
    return {
        "plant_id": pid,
        "outfall_id": oid,
        "sample_id": sample_id,
        "probe_id": probe_id,
        "lock_key": LOCK_KEY_PREFIX + str(sample_id),
        "probe_lock_key": LOCK_KEY_PREFIX + str(probe_id),
    }


def official_metrics(conn: sqlite3.Connection, sample_id: int) -> dict:
    row = conn.execute("SELECT metrics FROM sample_batches WHERE id=?", (sample_id,)).fetchone()
    return json.loads(row[0] if not isinstance(row, sqlite3.Row) else row["metrics"])


def old_console_hard_writeback(
    conn: sqlite3.Connection, sample_code: str, patch: dict
) -> dict:
    """事故期监控台回写通道的等价物：无封批校验、无批锁，整列覆盖硬改。"""
    row = conn.execute(
        "SELECT id, metrics FROM sample_batches WHERE sample_code=?", (sample_code,)
    ).fetchone()
    sid = row["id"] if isinstance(row, sqlite3.Row) else row[0]
    raw = row["metrics"] if isinstance(row, sqlite3.Row) else row[1]
    metrics = json.loads(raw or "{}")
    metrics.update({k: float(v) for k, v in patch.items() if v is not None})
    conn.execute(
        "UPDATE sample_batches SET metrics=? WHERE id=?",
        (json.dumps(metrics, ensure_ascii=False), sid),
    )
    conn.commit()
    return metrics
