#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""effluent-04 SM-D22 事故复现 / 封后禁改复验脚本。

用法：
  PYTHONPATH=backend python scripts/repro_effluent_04.py                # 复现三病（修复前应 REPRODUCED）
  PYTHONPATH=backend python scripts/repro_effluent_04.py --verify-fixed # 修复后复验（应 ALL FIXED）

脚本默认使用临时 DB，不碰 data/app.db。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import db  # noqa: E402
from effluent04_seed import (  # noqa: E402
    DEVIATION_PHRASE,
    load_incident,
    official_metrics,
    seed_incident,
)


def _boot_db() -> tuple[Path, dict]:
    tmp = Path(tempfile.mkdtemp(prefix="eff04_repro_")) / "repro.db"
    db.DB_PATH = tmp
    db.init_db()
    with db.connect() as conn:
        ids = seed_incident(conn)
    return tmp, ids


def _admin_headers() -> dict:
    from fastapi.testclient import TestClient

    from app import app

    c = TestClient(app)
    tok = c.post("/api/login", json={"username": "admin", "password": "admin123"}).json()[
        "token"
    ]
    return {"Authorization": "Bearer " + tok}


def repro_jia(tmp: Path, ids: dict) -> dict:
    """甲病：已封批 SM-D22 正式指标列仍被硬改，对账假绿。"""
    out = {"name": "jia_official_rewrite", "symptoms": []}
    with db.connect() as conn:
        before = official_metrics(conn, ids["sample_id"])
        seal = conn.execute(
            "SELECT snapshot FROM discharge_seals WHERE id=(SELECT MAX(id) FROM discharge_seals)"
        ).fetchone()

    fx = load_incident()
    patch = fx["console_writeback_attempt"]
    blocked = False
    block_msg = ""
    try:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT metrics FROM sample_batches WHERE id=?", (ids["sample_id"],)
            ).fetchone()
            metrics = json.loads(row["metrics"])
            metrics.update(patch)
            conn.execute(
                "UPDATE sample_batches SET metrics=? WHERE id=?",
                (json.dumps(metrics, ensure_ascii=False), ids["sample_id"]),
            )
            conn.commit()
        with db.connect() as conn:
            after = official_metrics(conn, ids["sample_id"])
        if abs(after["cod"] - float(patch["cod"])) < 1e-9:
            out["symptoms"].append(
                f"正式指标列仍被改写 cod {before['cod']} -> {after['cod']}（封批快照口径=56.0）"
            )
    except Exception as exc:  # 修复后触发器拒写
        blocked = True
        block_msg = str(exc)
        with db.connect() as conn:
            after = official_metrics(conn, ids["sample_id"])

    # 对账：修复前读被改正式列 -> 假绿；修复后读封批快照 -> 红灯/代次对齐
    from reconcile_svc import _reconcile_permit

    with db.connect() as conn:
        recon = _reconcile_permit(conn, fx["permit"]["permit_code"])
    out.update(
        before=before,
        after=after,
        blocked=blocked,
        block_msg=block_msg,
        recon_balanced=recon["balanced"],
        recon=recon,
    )
    if recon["balanced"]:
        out["symptoms"].append("对账继续亮合格（读被硬改后的正式列，与封批快照不同代）")
    out["reproduced"] = bool(out["symptoms"])
    return out


def repro_yi() -> dict:
    """乙病：未升正影子草稿血缘边混进 /api/lineage。"""
    from fastapi.testclient import TestClient

    from app import app

    c = TestClient(app)
    h = _admin_headers()
    items = c.get("/api/lineage", headers=h).json()["items"]
    leaked = [
        i for i in items
        if i["edge_type"] == "封后影子修订" or "shadow_draft:SD-SM-D22" in (i.get("detail") or "")
    ]
    official_visible = any(i["edge_type"] == "封批快照锚点" for i in items)
    return {
        "name": "yi_shadow_in_lineage",
        "leaked_edges": leaked,
        "official_visible": official_visible,
        "reproduced": bool(leaked),
        "symptoms": [f"影子草稿边混入血缘页：{leaked[0]['detail']}"] if leaked else [],
    }


def repro_zheng(ids: dict) -> dict:
    """争用病：两路并发无批锁读改写 -> 丢失更新，且无锁痕/争用痕。"""
    sid = ids["probe_id"]  # 用未封批探针，排除封批标记单点判断
    b_read = threading.Barrier(2)
    results = {}

    def worker(name: str, cod: float):
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT metrics FROM sample_batches WHERE id=?", (sid,)
            ).fetchone()
            metrics = json.loads(row["metrics"])
            b_read.wait(timeout=10)  # 两路都在同一旧值上读出
            metrics["cod"] = cod
            conn.execute(
                "UPDATE sample_batches SET metrics=? WHERE id=?",
                (json.dumps(metrics, ensure_ascii=False), sid),
            )
            conn.commit()
            results[name] = cod
        finally:
            conn.close()

    t1 = threading.Thread(target=worker, args=("A", 11.1))
    t2 = threading.Thread(target=worker, args=("B", 33.3))
    t1.start(); t2.start(); t1.join(); t2.join()

    with db.connect() as conn:
        final_cod = official_metrics(conn, sid)["cod"]
        locks = conn.execute(
            "SELECT COUNT(*) c FROM effluent_locks WHERE lock_key=?",
            (ids["probe_lock_key"],),
        ).fetchone()["c"]
        traces = conn.execute(
            "SELECT COUNT(*) c FROM contention_log WHERE lock_key=?",
            (ids["probe_lock_key"],),
        ).fetchone()["c"]

    lost_update = abs(final_cod - 11.1) > 1e-9 and abs(final_cod - 33.3) < 1e-9
    no_lock = locks == 0 and traces == 0
    symptoms = []
    if lost_update:
        symptoms.append(f"两路同批改指标无锁：A=11.1 写入被覆盖，最终 cod={final_cod}（丢失更新）")
    if no_lock:
        symptoms.append("无批锁记录、无争用痕")
    return {
        "name": "zheng_no_batch_lock",
        "final_cod": final_cod,
        "lost_update": lost_update,
        "lock_rows": locks,
        "contention_rows": traces,
        "reproduced": lost_update and no_lock,
        "symptoms": symptoms,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-fixed", action="store_true")
    args = ap.parse_args()

    tmp, ids = _boot_db()
    print(f"[eff04] temp db = {tmp}")
    jia = repro_jia(tmp, ids)
    yi = repro_yi()
    zheng = repro_zheng(ids)

    for r in (jia, yi, zheng):
        tag = "REPRODUCED" if r["reproduced"] else ("BLOCKED/FIXED" if not args.verify_fixed else "FIXED")
        print(f"\n=== {r['name']}: {tag} ===")
        for s in r["symptoms"]:
            print("  ! " + s)
        if not r["symptoms"]:
            print("  · 无事故症状")

    if args.verify_fixed:
        jia_yi_ok = (
            jia["blocked"]
            and not jia["recon_balanced"]
            and not yi["reproduced"]
            and yi["official_visible"]
        )
        print(
            "\n[zheng 注] 裸 SQL 读改写绕过服务层批锁，未封批探针仍会丢失更新——这是预期；"
            "批锁互斥由 tests/test_effluent_04_zheng.py 与 scripts/ping_metric_lock.py "
            "经 HTTP 通道判定（已封批行另有触发器兜底零改）。"
        )
        print("\nVERDICT:", "ALL FIXED" if jia_yi_ok else "STILL BROKEN")
        return 0 if jia_yi_ok else 1

    reproduced_all = jia["reproduced"] and yi["reproduced"] and zheng["reproduced"]
    print("\nVERDICT:", "THREE INCIDENTS REPRODUCED (三红有据)" if reproduced_all
          else "未完整复现（检查夹具/代码状态）")
    return 0 if reproduced_all else 2


if __name__ == "__main__":
    raise SystemExit(main())
