# -*- coding: utf-8 -*-
"""effluent-04 争用失败测：两路会话同时回写同批 SM-D22 指标必须抢批锁。

见红口径（修复前）：指标回写端点不存在（404），同批改指标无 `sample:{id}` 批锁、
effluent_locks/contention_log 无争用痕 —— 即「两路同批改指标无锁」。

转绿口径（必须由 locks 批锁服务救绿，禁止用单个封批标记判断交差）：
- 批锁被他会话持有时，后到者 409 + lock_busy，且只在 contention_log 留「争用失败」痕；
- 败方回写值零入库；
- 锁判定先于封批判定：封批 SM-D22 上，持锁方吃到的是「封后禁改偏航」冻结拒写，
  抢锁失败方吃到的是「锁被占用」，两个错误码必须不同。
"""
from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import app
from db import init_db
from effluent04_seed import seed_incident


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "eff04_zheng.db")
    init_db()
    import db

    with db.connect() as conn:
        ids = seed_incident(conn)
    c = TestClient(app)

    def login(u: str, p: str) -> dict:
        tok = c.post("/api/login", json={"username": u, "password": p}).json()["token"]
        return {"Authorization": "Bearer " + tok}

    return {"ids": ids, "client": c, "login": login}


def _contention(lock_key: str) -> list[dict]:
    import db

    with db.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM contention_log WHERE lock_key=? ORDER BY id", (lock_key,)
            ).fetchall()
        ]


def _metric_cod(sample_id: int):
    import db

    with db.connect() as conn:
        row = conn.execute(
            "SELECT metrics FROM sample_batches WHERE id=?", (sample_id,)
        ).fetchone()
        return json.loads(row["metrics"])["cod"]


def test_zheng_1_late_session_blocked_by_batch_lock(ctx):
    """确定性争用：admin 持批锁，writer 后到回写必须 409 + 争用痕 + 零写入。"""
    c, login = ctx["client"], ctx["login"]
    sid = ctx["ids"]["probe_id"]  # 未封批探针：排除封批标记单点判断
    key = f"sample:{sid}"
    ha, hw = login("admin", "admin123"), login("writer", "writer123")

    acq = c.post(f"/api/locks/acquire?lock_key={key}", headers=ha)
    assert acq.status_code == 200

    r = c.post(
        f"/api/samples/{sid}/metrics",
        headers=hw,
        json={"cod": 61.6, "nh3n": 6.1, "tp": 0.61},
    )
    if r.status_code == 404:
        pytest.fail("争用红：指标回写端点不存在，两路同批改指标无锁可抢")
    assert r.status_code == 409, f"后到会话必须被批锁挡住(409)，实际 {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body.get("code") == "lock_busy" or "锁" in str(body), "必须是批锁争用拒绝"

    traces = _contention(key)
    assert any(t.get("actor") == "writer" and "争用" in (t.get("action") or "") for t in traces), (
        "后到仅留争用痕：contention_log 缺 writer 的「争用失败」记录"
    )
    assert _metric_cod(sid) != pytest.approx(61.6), "争用败方零写入"


def test_zheng_2_two_concurrent_sessions_race_acquire_and_write(ctx):
    """两路线程屏障并发：先抢批锁再同批改指标，恰好一成一败，败方零写入。"""
    ids, login = ctx["ids"], ctx["login"]
    sid = ids["probe_id"]
    key = f"sample:{sid}"
    acq_barrier = threading.Barrier(2)
    write_barrier = threading.Barrier(2)
    outcomes: dict[str, dict] = {}

    def worker(username: str, password: str, cod: float):
        client = TestClient(app)
        tok = client.post(
            "/api/login", json={"username": username, "password": password}
        ).json()["token"]
        h = {"Authorization": "Bearer " + tok}
        acq_barrier.wait(timeout=10)
        ra = client.post(f"/api/locks/acquire?lock_key={key}", headers=h)
        write_barrier.wait(timeout=10)
        rw = client.post(
            f"/api/samples/{sid}/metrics",
            headers=h,
            json={"cod": cod, "nh3n": 1.1, "tp": 0.11},
        )
        outcomes[username] = {
            "acquire": ra.status_code,
            "write": rw.status_code,
            "body": rw.json() if rw.headers.get("content-type", "").startswith("application/json") else {},
            "cod": cod,
        }

    t1 = threading.Thread(target=worker, args=("admin", "admin123", 11.1))
    t2 = threading.Thread(target=worker, args=("writer", "writer123", 33.3))
    t1.start(); t2.start(); t1.join(15); t2.join(15)

    assert len(outcomes) == 2, "两个并发工作线程都必须返回"
    acq_codes = sorted(o["acquire"] for o in outcomes.values())
    assert acq_codes == [200, 409], f"批锁抢占必须恰好一成一败，实际 {acq_codes}"

    writes = [(u, o["write"]) for u, o in outcomes.items()]
    winner = next((u for u, s in writes if s == 200), None)
    loser = next((u for u, s in writes if s == 409), None)
    if winner is None or loser is None:
        pytest.fail(f"争用红：同批改指标未见批锁互斥结果 {writes}（疑似两路无锁均可写）")

    loser_body = outcomes[loser]["body"]
    assert loser_body.get("code") == "lock_busy" or "锁" in str(loser_body)
    final_cod = _metric_cod(sid)
    assert final_cod == pytest.approx(outcomes[winner]["cod"]), "胜方值必须在库"
    assert final_cod != pytest.approx(outcomes[loser]["cod"]), "争用败方零写入"
    assert len(_contention(key)) >= 1, "必须留下争用痕"


def test_zheng_3_sealed_d22_lock_error_distinct_from_freeze(ctx):
    """SM-D22 已封批：锁忙 409(lock_busy) 与封后冻结 409(metric_frozen_after_seal) 必须可分。"""
    c, login = ctx["client"], ctx["login"]
    sid = ctx["ids"]["sample_id"]
    key = f"sample:{sid}"
    ha, hw = login("admin", "admin123"), login("writer", "writer123")
    cod_before = _metric_cod(sid)

    assert c.post(f"/api/locks/acquire?lock_key={key}", headers=ha).status_code == 200

    r_holder = c.post(f"/api/samples/{sid}/metrics", headers=ha, json={"cod": 1.0})
    r_loser = c.post(f"/api/samples/{sid}/metrics", headers=hw, json={"cod": 2.0})

    assert r_holder.status_code == 409
    assert r_loser.status_code == 409
    b_holder, b_loser = r_holder.json(), r_loser.json()
    assert b_holder.get("code") == "metric_frozen_after_seal", (
        f"持锁方应撞封后冻结，实际 {b_holder}"
    )
    assert b_loser.get("code") == "lock_busy", f"后到方应撞批锁，实际 {b_loser}"
    assert b_holder.get("code") != b_loser.get("code"), "禁止单点封批标记判断救齐争用红"
    assert _metric_cod(sid) == pytest.approx(cod_before), "SM-D22 两路回写都不得改正式列"
    traces = _contention(key)
    assert any(t.get("actor") == "writer" for t in traces), "锁败方 writer 须留争用痕"
