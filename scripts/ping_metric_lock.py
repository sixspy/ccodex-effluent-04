#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ping_metric_lock — effluent-04 争用验收脚本。

两路会话（默认 admin / writer）经 HTTP 并发打同一批 SM-D22 的指标回写：
  1) 屏障后同时抢持久批锁 POST /api/locks/acquire?lock_key=sample:{id}，恰好一成一败；
  2) 第二道屏障后同时 POST /api/samples/{id}/metrics 各写不同 COD；
  3) 校验：锁败方收到 code=lock_busy、contention_log 留「争用失败」痕、其值零写入；
  4) SM-D22 已封批：锁胜方收到 code=metric_frozen_after_seal（封后禁改偏航），
     从而证明批锁与封批标记是两道独立服务，不是单点封标记交差。

用法：
  python scripts/ping_metric_lock.py --base-url http://127.0.0.1:8000
  python scripts/ping_metric_lock.py --sample SM-D22 --rounds 3
退出码 0 = 争用口径全部成立；1 = 不成立。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

PHRASE = "封后禁改偏航"


def request(base: str, method: str, path: str, token: str | None = None, body: dict | None = None):
    url = base.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return e.code, parsed


def login(base: str, username: str, password: str) -> str:
    status, data = request(base, "POST", "/api/login", body={
        "username": username, "password": password
    })
    if status != 200 or "token" not in data:
        raise SystemExit(f"login failed for {username}: {status} {data}")
    return data["token"]


def find_sample(base: str, token: str, sample_code: str) -> dict:
    status, data = request(base, "GET", f"/api/samples/{urllib.parse.quote(sample_code)}", token)
    if status == 200:
        return data["sample"]
    # 退路：列表里按编号找
    status, data = request(base, "GET", "/api/samples", token)
    for s in data.get("items", []):
        if s["sample_code"] == sample_code:
            return s
    raise SystemExit(f"sample {sample_code} not found: {status}")


def run_round(base: str, sample_code: str, cod_a: float, cod_b: float,
              user_a: tuple[str, str], user_b: tuple[str, str]) -> tuple[bool, dict]:
    tok_a = login(base, *user_a)
    tok_b = login(base, *user_b)
    sample = find_sample(base, tok_a, sample_code)
    sid = sample["id"]
    key = f"sample:{sid}"
    sealed = bool(sample.get("seal_effective") or sample.get("metrics_readonly"))
    cod_before = (sample.get("metrics") or {}).get("cod")

    acq_barrier = threading.Barrier(2)
    write_barrier = threading.Barrier(2)
    out: dict[str, dict] = {}

    def worker(tag: str, token: str, cod: float):
        sid_session = f"ping-{tag}"
        q = urllib.parse.urlencode({"lock_key": key, "session_id": sid_session})
        acq_barrier.wait(timeout=15)
        sa, da = request(base, "POST", f"/api/locks/acquire?{q}", token)
        write_barrier.wait(timeout=15)
        sw, dw = request(
            base, "POST", f"/api/samples/{sid}/metrics", token,
            {"cod": cod, "nh3n": 1.11, "tp": 0.111, "session_id": sid_session},
        )
        out[tag] = {"acquire": sa, "acquire_body": da, "write": sw, "write_body": dw, "cod": cod,
                    "session_id": sid_session}

    ta = threading.Thread(target=worker, args=("A", tok_a, cod_a))
    tb = threading.Thread(target=worker, args=("B", tok_b, cod_b))
    ta.start(); tb.start(); ta.join(20); tb.join(20)

    _, locks_payload = request(base, "GET", "/api/locks", tok_a)
    traces = [c for c in locks_payload.get("contention", []) if c.get("lock_key") == key]
    _, after_payload = request(base, "GET", f"/api/samples/{sid}", tok_a)
    cod_after = after_payload["sample"]["metrics"].get("cod")

    acq_codes = sorted(v["acquire"] for v in out.values())
    winner_tag = next((t for t, v in out.items() if v["acquire"] == 200), None)
    loser_tag = next((t for t, v in out.items() if v["acquire"] == 409), None)

    checks = {
        "acquire_one_win_one_busy": acq_codes == [200, 409],
        "loser_is_lock_busy": (
            loser_tag is not None
            and out[loser_tag]["write"] == 409
            and (out[loser_tag]["write_body"].get("code") == "lock_busy"
                 or "锁" in json.dumps(out[loser_tag]["write_body"], ensure_ascii=False))
        ),
        "contention_trace_persisted": any("争用" in (c.get("action") or "") for c in traces),
        "loser_value_zero_write": cod_after != out[loser_tag]["cod"] if loser_tag else False,
    }
    if sealed:
        checks["winner_hits_freeze_not_lock"] = (
            winner_tag is not None
            and out[winner_tag]["write"] == 409
            and out[winner_tag]["write_body"].get("code") == "metric_frozen_after_seal"
        )
        checks["sealed_official_unchanged"] = cod_after == cod_before
    else:
        checks["winner_write_ok"] = (
            winner_tag is not None and out[winner_tag]["write"] == 200
            and cod_after == out[winner_tag]["cod"]
        )

    # 清理：胜方释放持久批锁（holder 必须与会话一致）
    if winner_tag:
        sess = out[winner_tag].get("session_id")
        q = urllib.parse.urlencode({"lock_key": key, "session_id": sess}) if sess else \
            f"lock_key={urllib.parse.quote(key)}"
        tok = tok_a if winner_tag == "A" else tok_b
        request(base, "POST", f"/api/locks/release?{q}", tok)

    summary = {
        "sample": sample_code, "sample_id": sid, "lock_key": key, "sealed": sealed,
        "cod_before": cod_before, "cod_after": cod_after,
        "outcomes": out, "contention_for_key": traces[-5:], "checks": checks,
    }
    return all(checks.values()), summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--sample", default="SM-D22")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--cod-a", type=float, default=41.1)
    ap.add_argument("--cod-b", type=float, default=43.3)
    args = ap.parse_args()

    all_ok = True
    for i in range(max(1, args.rounds)):
        ok, summary = run_round(
            args.base_url, args.sample,
            args.cod_a + i, args.cod_b + i,
            ("admin", "admin123"), ("writer", "writer123"),
        )
        all_ok &= ok
        print(f"PING_METRIC_LOCK round={i + 1} ok={ok}")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("CHECKS " + json.dumps(summary["checks"], ensure_ascii=False))

    print("PING_METRIC_LOCK VERDICT " + ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
