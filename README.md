# 污水排放批（effluent）

数据工程母仓：处理厂采样入库 → 超标旁路挂起 → 准排放裁定 → 封批升代 → 排放许可对账。

## 骨架一句话

采样批次入库后裁定超标才准排放封批，驳回进旁路挂起，指标血缘与排放许可对账。

## 一键起环境

```bash
docker compose up -d --build
```

浏览器访问 http://localhost:11600

- 管理账号：`admin` / `admin123`
- 读写账号：`writer` / `writer123`
- 只读账号：`viewer` / `viewer123`

## 本地开发

```powershell
cd projects/14-effluent
$env:PYTHONPATH="backend"
python -m uvicorn app:app --app-dir backend --reload --port 8000
```

## 自测

```powershell
$env:PYTHONPATH="backend"
python -m pytest
python scripts/count_loc.py
```

## effluent-04 封后禁改（SM-D22 · 排污丁第 4 槽）

事故：封批代 G2 已生效的 SM-D22 仍被监控台回写 COD，快照曲线与正式指标错位，对账读被硬改后的列继续假绿。

**取舍：取甲路径并当班封死，不与乙路径折中并存**——封批代生效后指标正式表零改并拒写；影子草稿表仅作事故取证，不提供升正/回写通道。完整口径见 [`docs/effluent-04-封后禁改说明.md`](docs/effluent-04-封后禁改说明.md)。

```bash
# 三道失败测（先红：11 failed；修复后全绿）
PYTHONPATH=backend python3 -m pytest \
  tests/test_effluent_04_jia.py \
  tests/test_effluent_04_yi.py \
  tests/test_effluent_04_zheng.py

# 事故夹具复现（修复前 THREE INCIDENTS REPRODUCED / 修复后 ALL FIXED）
PYTHONPATH=backend python3 scripts/repro_effluent_04.py
PYTHONPATH=backend python3 scripts/repro_effluent_04.py --verify-fixed

# 两路并发打同批 SM-D22 抢批锁（对已起 HTTP 服务，纯标准库）
python3 scripts/ping_metric_lock.py --base-url http://127.0.0.1:8000
# 或 bash scripts/ping_metric_lock ...

# 五页真实浏览器点验（需 playwright + chromium）
python3 scripts/verify_pages_browser.py
```

封死后的五道独立闸（禁止单点封批标记交差）：

1. **甲 · 正式列零改**：`sample_batches.metrics` 冻结触发器（`封后禁改偏航` RAISE ABORT）+ 采样服务 `POST /api/samples/{id|code}/metrics` 等别名一律 `409 metric_frozen_after_seal`，并写 `deviation_log`/审计。
2. **乙 · 血缘不混草稿**：`/api/lineage` 只出 `layer='official' AND promoted=1`，事故边类型 `封后影子修订` 兜底排除；未升正草稿只在 `/api/metric-drafts` 隔离台账。
3. **争 · 批锁**：锁键 `sample:{id}`，原子抢锁；后到 `409 lock_busy` 且只留 `contention_log` 争用痕、败方零写入。锁判定先于封批判定，SM-D22 上两错误码可分。
4. **对账同代**：已封批批次按 `discharge_seals.snapshot_metrics`（G2）判定并回 `lamp_generation`；正式列漂移即灭假绿。
5. **五页同态**：封批页「封批代已生效 · G2」标记 → 采样页 COD/氨氮/总磷只读；血缘主表无影子修订；对账灯与快照同代；排放口曲线点 `data-ts` 与正式指标行时间戳同戳。

证据留档：`docs/evidence/`（修复前红测、修复后绿测、复现前后、ping 输出、浏览器五页截图与 JSON）。

