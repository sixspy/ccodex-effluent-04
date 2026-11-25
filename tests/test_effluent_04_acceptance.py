# -*- coding: utf-8 -*-
"""effluent-04 独验收段的补充回归（三红转绿后新增，不再见红）。

覆盖：
- 对账合格灯与封批快照同代、SM-D22 假绿已灭；
- 排放口曲线点与正式指标同戳；
- 回写路径别名全部同口径拒写；
- 五个前端页面源文件带只读/同代/同戳/隔离钩子。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app
from db import init_db
from effluent04_seed import seed_incident

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "eff04_acc.db")
    init_db()
    import db

    with db.connect() as conn:
        ids = seed_incident(conn)
    c = TestClient(app)
    tok = c.post("/api/login", json={"username": "admin", "password": "admin123"}).json()[
        "token"
    ]
    return c, {"Authorization": "Bearer " + tok}, ids


def test_reconcile_d22_lamp_generation_aligned(client):
    c, h, _ = client
    d = c.get("/api/reconcile/DP-2026-D22", headers=h).json()
    assert d["balanced"] is False, "快照 COD=56 超许可帽 50，必须灭假绿"
    assert d["lamp_generation"] == 2
    sm = next(s for s in d["samples"] if s["sample_code"] == "SM-D22")
    assert sm["judged_source"] == "seal_snapshot"
    assert sm["judged_generation"] == 2
    assert sm["metrics_drift"] is True
    assert sm["matched"] is False


def test_reconcile_e01_sealed_sample_green_at_g2(client):
    c, h, _ = client
    d = c.get("/api/reconcile/DP-2026-E01", headers=h).json()
    sm = next(s for s in d["samples"] if s["sample_code"] == "SM-OF-E1-A-001")
    assert sm["matched"] is True
    assert sm["lamp_generation"] == 2
    assert sm["judged_source"] == "seal_snapshot"


def test_outfall_curve_points_share_official_timestamp(client):
    c, h, ids = client
    d = c.get(f"/api/outfalls/{ids['outfall_id']}/curve", headers=h).json()
    d22 = next(p for p in d["points"] if p["sample_code"] == "SM-D22")
    assert d22["ts"] == "2026-11-21T08:40:00"
    assert d22["timestamp"] == d22["adjudicated_at"] == d22["ts"]
    assert d22["cod"] == 22.0  # 正式列（事故硬改值），曲线不另造数
    assert d22["seal_generation"] == 2


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/samples/{sid}/metrics", {"cod": 1}),
        ("/api/samples/{sid}/metric", {"cod": 1}),
        ("/api/samples/metrics", {"sample_id": "SID", "cod": 1}),
        ("/api/metrics/writeback", {"sample_code": "SM-D22", "cod": 1}),
        ("/api/metric/writeback", {"sample_code": "SM-D22", "cod": 1}),
        ("/api/samples/metric", {"sample_id": "SID", "cod": 1}),
    ],
)
def test_writeback_aliases_all_reject(client, path, payload):
    c, h, ids = client
    sid = ids["sample_id"]
    p = path.format(sid=sid)
    body = {k: (sid if v == "SID" else v) for k, v in payload.items()}
    r = c.post(p, headers=h, json=body)
    assert r.status_code == 409, (p, r.status_code, r.text[:160])
    assert r.json().get("code") == "metric_frozen_after_seal"


def test_frontend_pages_carry_required_hooks():
    sv = (ROOT / "frontend/js/views/samples_view.js").read_text(encoding="utf-8")
    seal = (ROOT / "frontend/js/views/seal_view.js").read_text(encoding="utf-8")
    lin = (ROOT / "frontend/js/views/lineage_view.js").read_text(encoding="utf-8")
    rec = (ROOT / "frontend/js/views/reconcile_view.js").read_text(encoding="utf-8")
    out = (ROOT / "frontend/js/views/outfalls_view.js").read_text(encoding="utf-8")

    assert 'id="metric-cod"' in sv and ":readonly=\"metricTarget.readonly\"" in sv
    assert "metric-nh3n" in sv and "metric-tp" in sv
    assert "data-seal-effective-marker" in seal and "封批代已生效" in seal
    assert "data-lineage-main" in lin and "data-shadow-isolated" in lin
    assert "data-lamp-generation" in rec and "封批快照" in rec
    assert "data-curve-point" in out and "data-ts" in out and "同戳" in out
