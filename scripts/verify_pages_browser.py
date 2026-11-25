#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""effluent-04 五页点验（真实 Chromium）。

逐页点开：封批页 / 采样页 / 血缘页 / 对账页 / 排放口页，
按 effluent-04 独验收段断言并截图到 docs/evidence/shots。
退出码 0 = 五页同态全部通过。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
SHOTS = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)

results: dict[str, dict] = {}
console_errors: list[str] = []


def record(page_name: str, ok: bool, detail: str):
    results[page_name] = {"ok": ok, "detail": detail}
    print(f"[{'PASS' if ok else 'FAIL'}] {page_name}: {detail}")


def nav_click(page, name: str):
    page.locator("nav.ribbon button", has_text=name).first.click()
    page.wait_for_timeout(350)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        page.goto(BASE + "/", wait_until="networkidle")
        page.click("button:has-text('进入排放批')")
        page.wait_for_selector("nav.ribbon", timeout=10000)
        page.wait_for_timeout(800)

        # 1) 封批页：生效标记可读
        try:
            nav_click(page, "封批")
            page.wait_for_selector("[data-seal-effective-marker]", timeout=8000)
            row = page.locator('[data-seal-row="SM-D22"]')
            txt = row.inner_text()
            assert "封批代已生效" in txt and "G2" in txt, txt
            page.screenshot(path=str(SHOTS / "01_seal.png"), full_page=True)
            record("封批页", True, "SM-D22 行显示「封批代已生效 · G2」标记")
        except Exception as e:
            record("封批页", False, repr(e))
            page.screenshot(path=str(SHOTS / "01_seal_fail.png"), full_page=True)

        # 2) 采样页：SM-D22 三输入只读 + 浏览器内强写被拒
        try:
            nav_click(page, "采样")
            page.wait_for_selector("#metric-cod", timeout=8000)
            page.wait_for_timeout(300)
            states = {}
            for key in ("cod", "nh3n", "tp"):
                el = page.locator(f"#metric-{key}")
                states[key] = {
                    "readOnly": el.evaluate("n => n.readOnly"),
                    "hasAttr": el.get_attribute("readonly") is not None,
                    "editable": el.is_editable(),
                    "value": el.input_value(),
                }
            assert all(v["readOnly"] and v["hasAttr"] and not v["editable"] for v in states.values()), states
            assert states["cod"]["value"].startswith("22"), states
            save_disabled = page.locator("#metric-save-btn").is_disabled()
            assert save_disabled, "封批生效时回写按钮必须禁用"
            assert "封后禁改偏航" in page.locator("[data-readonly-banner]").inner_text()
            # 用页面令牌直接打 API，验证浏览器侧强写同样被拒且 code 正确
            api_check = page.evaluate(
                """async () => {
                  const tok = localStorage.getItem('effluent_token');
                  const r = await fetch('/api/samples/SM-D22/metrics', {
                    method:'POST', headers:{'Content-Type':'application/json','Authorization':'Bearer '+tok},
                    body: JSON.stringify({cod: 99.9})
                  });
                  const j = await r.json();
                  return {status:r.status, code:j.code, detail:j.detail};
                }"""
            )
            assert api_check["status"] == 409 and api_check["code"] == "metric_frozen_after_seal", api_check
            assert "封后禁改偏航" in api_check["detail"]
            page.screenshot(path=str(SHOTS / "02_samples_readonly.png"), full_page=True)
            record("采样页", True, f"三输入只读/按钮禁用；浏览器强写 409 {api_check['code']}")
        except Exception as e:
            record("采样页", False, repr(e))
            page.screenshot(path=str(SHOTS / "02_samples_fail.png"), full_page=True)

        # 3) 血缘页：主表无影子草稿；隔离区展开后可见
        try:
            nav_click(page, "血缘")
            page.wait_for_selector("[data-lineage-main]", timeout=8000)
            main_txt = page.locator("[data-lineage-main]").inner_text()
            assert "SD-SM-D22" not in main_txt and "封后影子修订" not in main_txt, main_txt
            assert "封批快照锚点" in main_txt, "正式对照边必须仍在"
            page.screenshot(path=str(SHOTS / "03_lineage_clean.png"), full_page=True)
            page.click("[data-toggle-drafts]")
            page.wait_for_selector("[data-shadow-isolated] [data-draft-row]", timeout=5000)
            iso_txt = page.locator("[data-shadow-isolated]").inner_text()
            assert "SD-SM-D22-G2-R1" in iso_txt and "未升正" in iso_txt
            page.screenshot(path=str(SHOTS / "03_lineage_drafts_isolated.png"), full_page=True)
            record("血缘页", True, "主表无未升正影子修订；隔离台账可查 SD-SM-D22-G2-R1")
        except Exception as e:
            record("血缘页", False, repr(e))
            page.screenshot(path=str(SHOTS / "03_lineage_fail.png"), full_page=True)

        # 4) 对账页：D22 不合格灯与 G2 同代，明细判漂移偏航
        try:
            nav_click(page, "对账")
            page.wait_for_selector("[data-reconcile-table]", timeout=8000)
            d22row = page.locator('[data-permit="DP-2026-D22"]')
            txt = d22row.inner_text()
            assert "不合格" in txt and "G2" in txt, txt
            panel = page.locator(".panel", has_text="单许可 / 采样证明")
            panel.locator("input").first.fill("DP-2026-D22")
            panel.locator("button", has_text="对账").first.click()
            page.wait_for_selector("[data-recon-samples]", timeout=8000)
            srow = page.locator('[data-recon-sample="SM-D22"]')
            stxt = srow.inner_text()
            assert "G2" in stxt and "封批快照" in stxt and "封后禁改偏航" in stxt, stxt
            page.screenshot(path=str(SHOTS / "04_reconcile.png"), full_page=True)
            record("对账页", True, "DP-2026-D22 不合格灯 G2；SM-D22 按封批快照判定并标偏航漂移")
        except Exception as e:
            record("对账页", False, repr(e))
            page.screenshot(path=str(SHOTS / "04_reconcile_fail.png"), full_page=True)

        # 5) 排放口页：曲线点 data-ts 与正式指标同戳
        try:
            nav_click(page, "排放口")
            page.wait_for_selector("[data-curve-point]", timeout=8000)
            page.wait_for_selector("[data-curve-table]", timeout=5000)
            point_ts = page.locator('circle[data-metric="cod"]').evaluate_all(
                "els => els.map(e => e.getAttribute('data-ts'))"
            )
            cell_ts = page.locator("[data-curve-ts]").evaluate_all(
                "els => els.map(e => e.getAttribute('data-ts'))"
            )
            assert point_ts and cell_ts, (point_ts, cell_ts)
            assert set(point_ts) == set(cell_ts), (point_ts, cell_ts)
            assert "2026-11-21T08:40:00" in point_ts, point_ts  # SM-D22 adjudicated_at
            # 每个指标 2 个点（SM-D22 + LOCKPROBE）
            for m in ("cod", "nh3n", "tp"):
                n = page.locator(f'circle[data-curve-point][data-metric="{m}"]').count()
                assert n == 2, (m, n)
            page.screenshot(path=str(SHOTS / "05_outfalls_curve.png"), full_page=True)
            record("排放口页", True, f"曲线点戳与表戳一致且=正式指标行时间戳：{point_ts}")
        except Exception as e:
            record("排放口页", False, repr(e))
            page.screenshot(path=str(SHOTS / "05_outfalls_fail.png"), full_page=True)

        page.wait_for_timeout(200)
        browser.close()

    if console_errors:
        print("\n-- browser console/page errors --")
        for e in dict.fromkeys(console_errors):
            print("  ", e[:300])

    out = {"base": BASE, "results": results, "console_errors": console_errors[:10]}
    (SHOTS.parent / "06_browser_pages.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok_all = all(v["ok"] for v in results.values()) and len(results) == 5
    print("\nBROWSER_PAGES VERDICT", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
