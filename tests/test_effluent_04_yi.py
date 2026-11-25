# -*- coding: utf-8 -*-
"""effluent-04 失败测乙：未升正影子草稿不得混进血缘页/血缘 API。

见红口径（修复前）：事故夹具的「封后影子修订」边（shadow_draft:SD-SM-D22-G2-R1，
未升正）被 GET /api/lineage 原样返回 —— 影子草稿未升正却混进血缘页。

转绿口径：血缘服务默认只读正式层（layer=official/promoted=1），影子边被过滤；
正式对照边仍在（防清空式假绿）；影子草稿可在隔离只读口查到，但绝不混入主血缘。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app
from db import init_db
from effluent04_seed import SHADOW_EDGE_TYPE, seed_incident


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "eff04_yi.db")
    init_db()
    import db

    with db.connect() as conn:
        seed_incident(conn)
    c = TestClient(app)
    tok = c.post("/api/login", json={"username": "admin", "password": "admin123"}).json()[
        "token"
    ]
    return TestClient(app), {"Authorization": "Bearer " + tok}


def _is_shadow(item: dict) -> bool:
    if item.get("layer") == "shadow" or item.get("promoted") in (0, False):
        return True
    if item.get("edge_type") == SHADOW_EDGE_TYPE:
        return True
    detail = item.get("detail") or ""
    return "shadow_draft:SD-SM-D22" in detail


def test_yi_1_lineage_must_not_mix_unpromoted_shadow_draft(client):
    """乙红：未升正影子草稿边混在血缘主表。"""
    c, h = client
    r = c.get("/api/lineage", headers=h)
    assert r.status_code == 200
    items = r.json()["items"]
    leaked = [i for i in items if _is_shadow(i)]
    if leaked:
        pytest.fail(
            "乙红：未升正影子草稿混入血缘页/API："
            + " | ".join(f"{i.get('edge_type')}:{i.get('detail')}" for i in leaked)
        )


def test_yi_2_official_control_edge_still_visible(client):
    """转绿不得靠清空血缘：正式封批快照锚点边必须仍可读。"""
    c, h = client
    items = c.get("/api/lineage", headers=h).json()["items"]
    assert any(i.get("edge_type") == "封批快照锚点" for i in items), (
        "正式血缘对照边丢失，疑似用清空列表造假绿"
    )


def test_yi_3_shadow_drafts_isolated_readonly_endpoint(client):
    """影子草稿本身要有隔离台账（未升正），但与血缘主表分离。"""
    c, h = client
    r = c.get("/api/metric-drafts", headers=h)
    assert r.status_code == 200, "乙红：缺少影子草稿隔离只读台账 /api/metric-drafts"
    items = r.json()["items"]
    draft = next((d for d in items if d.get("draft_code") == "SD-SM-D22-G2-R1"), None)
    assert draft is not None, "事故影子草稿应在隔离台账中可查（取证）"
    assert draft.get("status") == "未升正"
    # 隔离台账中的草稿不得被标记为已升正
    assert draft.get("promoted") in (0, False, None) or draft.get("status") != "已升正"


def test_yi_4_lineage_create_cannot_smuggle_shadow_layer(client):
    """正式血缘写入通道不得私盖 shadow 层把封后修订洗白进主表。"""
    c, h = client
    r = c.post(
        "/api/lineage",
        headers=h,
        json={
            "from_sample_id": 1,
            "edge_type": SHADOW_EDGE_TYPE,
            "detail": "shadow_draft:SD-FAKE 试图混入",
            "layer": "shadow",
        },
    )
    # 要么拒写，要么写成 official 但仍被影子规则识别；最稳妥是 4xx 拒写
    if r.status_code < 400:
        items = c.get("/api/lineage", headers=h).json()["items"]
        assert not any("SD-FAKE" in (i.get("detail") or "") and _is_shadow(i) for i in items)
