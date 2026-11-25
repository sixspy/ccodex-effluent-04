# -*- coding: utf-8 -*-
"""effluent-04 失败测甲：封批代生效后，指标正式表必须零改并拒写（甲路径）。

见红口径（修复前）：
1. 直连库硬 UPDATE 已封批 SM-D22 的 metrics 竟然成功 —— 「正式指标列仍被改写」；
2. 监控台指标回写 API 不存在/不拒写；
3. 拒写后没有「封后禁改偏航」留痕；
4. 采样详情不回传封批只读标记，采样页无法据此把 COD/氨氮/总磷输入置只读。

转绿口径：DB 触发器中止硬改 + 采样服务拒写 + 偏航留痕 + readonly 元数据。
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import app
from db import init_db
from effluent04_seed import DEVIATION_PHRASE, load_incident, seed_incident


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "eff04_jia.db")
    init_db()
    import db

    with db.connect() as conn:
        ids = seed_incident(conn)
    client = TestClient(app)
    token = client.post(
        "/api/login", json={"username": "writer", "password": "writer123"}
    ).json()["token"]
    admin = client.post(
        "/api/login", json={"username": "admin", "password": "admin123"}
    ).json()["token"]
    return {
        "ids": ids,
        "client": client,
        "h": {"Authorization": "Bearer " + token},
        "ha": {"Authorization": "Bearer " + admin},
    }


def test_jia_1_hard_update_sealed_official_metrics_must_be_aborted(ctx):
    """甲红：已封批代 G2 的正式指标列仍能被硬改。"""
    import db

    sid = ctx["ids"]["sample_id"]
    fx = load_incident()
    sealed_value = fx["official_metrics_before_attack"]["cod"]

    with db.connect() as conn:
        before = conn.execute(
            "SELECT metrics FROM sample_batches WHERE id=?", (sid,)
        ).fetchone()["metrics"]

    attack = '{"cod": 1.1, "nh3n": 0.1, "tp": 0.01}'
    rejected = False
    try:
        with db.connect() as conn:
            conn.execute(
                "UPDATE sample_batches SET metrics=? WHERE id=?", (attack, sid)
            )
            conn.commit()
    except sqlite3.DatabaseError as exc:  # 绿：触发器 RAISE ABORT
        rejected = True
        assert DEVIATION_PHRASE in str(exc), f"拒写原因必须定性为 {DEVIATION_PHRASE}"

    with db.connect() as conn:
        after_row = conn.execute(
            "SELECT metrics FROM sample_batches WHERE id=?", (sid,)
        ).fetchone()
        after = __import__("json").loads(after_row["metrics"])

    if not rejected:
        pytest.fail(
            f"甲红：封批代生效后正式指标列仍被改写（before={before} after={after_row['metrics']}），"
            "库层无封后禁改触发器"
        )
    assert after["cod"] == pytest.approx(sealed_value) or after["cod"] == pytest.approx(
        fx["official_metrics_hard_written"]["cod"]
    ), "拒写中止后正式列必须保持事故夹具原值（零改）"


def test_jia_2_writeback_api_must_reject_sealed_sm_d22(ctx):
    """甲红：监控台回写 COD 未被拒（当前实现无此端点 -> 404 亦为红）。"""
    c, h, sid = ctx["client"], ctx["h"], ctx["ids"]["sample_id"]
    r = c.post(
        f"/api/samples/{sid}/metrics",
        headers=h,
        json={"cod": 77.0, "nh3n": 7.7, "tp": 0.77},
    )
    assert r.status_code in (403, 409), (
        f"甲红：封批生效后指标回写必须拒写(403/409)，实际 {r.status_code} {r.text[:200]}"
    )
    body = r.json()
    blob = str(body)
    assert DEVIATION_PHRASE in blob or "封" in blob, "拒写响应须带封后禁改/偏航定性"

    import db

    with db.connect() as conn:
        m = __import__("json").loads(
            conn.execute("SELECT metrics FROM sample_batches WHERE id=?", (sid,)).fetchone()["metrics"]
        )
    assert m["cod"] != 77.0 and m["nh3n"] != 7.7 and m["tp"] != 0.77, (
        "甲红：拒写后正式指标列不得出现回写值"
    )


def test_jia_2b_collection_alias_writeback_also_rejects(ctx):
    """同一甲路径的集合式别名 /api/samples/metrics 也必须拒写。"""
    c, h, sid = ctx["client"], ctx["h"], ctx["ids"]["sample_id"]
    r = c.post(
        "/api/samples/metrics",
        headers=h,
        json={"sample_id": sid, "metrics": {"cod": 88.0, "nh3n": 8.8, "tp": 0.88}},
    )
    assert r.status_code in (403, 409), f"别名回写未拒：{r.status_code} {r.text[:200]}"


def test_jia_3_rejection_leaves_deviation_trace(ctx):
    """拒写必须留下「封后禁改偏航」偏离说明（deviation_log 或 audit_trail）。"""
    import db

    c, h, sid = ctx["client"], ctx["h"], ctx["ids"]["sample_id"]
    c.post(f"/api/samples/{sid}/metrics", headers=h, json={"cod": 5.0})
    with db.connect() as conn:
        audit = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM audit_trail WHERE target LIKE ? OR action LIKE ?",
                ("%SM-D22%", "%封后禁改%"),
            ).fetchall()
        ]
        dev_rows = []
        cols = {r[1] for r in conn.execute("PRAGMA table_info(deviation_log)").fetchall()}
        if cols:
            dev_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM deviation_log WHERE sample_id=?", (sid,)
                ).fetchall()
            ]
    blob = str(audit) + str(dev_rows)
    assert DEVIATION_PHRASE in blob, (
        f"甲红：拒写未记 {DEVIATION_PHRASE} 偏离痕（audit={len(audit)} dev={len(dev_rows)}）"
    )


def test_jia_4_sample_detail_reports_metrics_readonly(ctx):
    """采样页三输入只读必须有后端只读元数据支撑，禁止只换封批页锁图。"""
    c, h = ctx["client"], ctx["h"]
    r = c.get("/api/samples/SM-D22", headers=h)
    assert r.status_code == 200
    body = r.json()
    sample = body["sample"]
    assert sample.get("seal_effective") is True, "样本详情须回传封批代生效标记"
    assert sample.get("metrics_readonly") is True, (
        "甲红：封批生效后样本详情必须给出 metrics_readonly=true 供采样页 COD/氨氮/总磷只读"
    )
