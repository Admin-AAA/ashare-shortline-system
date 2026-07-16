# Bug: 辅助数据逐股 Tushare 拉取卡死推荐 run（4.6 小时 + 429 限流）

**报告日期**: 2026-07-16
**严重度**: 高（A 项端到端验收受阻 + 僵尸体进程泄漏）
**北极星影响**: A（formal run 卡死）→ 间接影响 D（无法端到端验证策略改动）

## 现象

2026-07-16 有 **15 个 interrupted formal run**，其中多个卡在 `auxiliary_data` phase：
- `run_20260716_090836_2a7454`（09:08 启动，heartbeat 停在 09:32，子进程 PID 24801 至 19:30 仍存活）
- 日志显示 `Tushare pledge_stat 第1次请求失败(HTTP 429)` 和 `ProxyError` 反复重试
- DB 标记 `status=interrupted`（heartbeat > 1200s 被 `_auto_recover_stale_runs` 回收），但子进程仍在卡 Tushare 重试不退出（僵尸进程）

## 根因

### 1. 逐股 Tushare 调用（核心）

`app/services/auxiliary/state_adapters.py` 的 `TushareStateProvider` 有 **5 个方法实现为逐股循环**：

```python
def pledge_stat(self, request):
    for code in request.requested_codes:           # 4948 只
        frame = self._client.pledge_stat(ts_code=_ts_code(code))  # 逐股调 Tushare
```

| # | 方法 | 行号 | Tushare 接口 | 模式 | 本地表 |
|---|------|------|-------------|------|--------|
| 4 | `capital_flow` | 314-341 | `moneyflow(ts_code=)` | **逐股** ✗ | CapitalFlow |
| 9 | `pledge_stat` | 417-432 | `pledge_stat(ts_code=)` | **逐股** ✗ | PledgeStat |
| 11 | `fina_indicator` | 450-475 | `fina_indicator(ts_code=)` | **逐股** ✗ | FinaIndicator |
| 12 | `auction` | 477-496 | `stk_auction(ts_code=)` | **逐股** ✗ | AuctionData |
| 13 | `holder_number` | 498-509 | `stk_holdernumber(ts_code=)` | **逐股** ✗ | HolderNumber |

其余 8 个方法已是 trade_date 维度批量。

**触发链**：`workbench_service.py:1352` 把全市场 prefiltered 股票池（~4948 只）作为 `instruments` 传入 `run_auxiliary_application` → `requested_codes` → 5 个逐股方法各循环 4948 次。

**计算**：5 方法 × 4948 股 = **24740 次 Tushare 请求/日**。Tushare 限流 90 次/分钟（`_RATE_LIMIT_PER_MINUTE=90`），理论耗时 **275 分钟（4.6 小时）**，且 429 限流让实际更久。

### 2. 每日同步未覆盖慢变数据

`app/services/scheduler_service.py:_run_auxiliary_remediation`（每日同步的辅助数据步）只补：
- `target_tables = ["limit_up_board", "concept_sector", "dragon_tiger", "news", "capital_flow"]`（capital_flow 限 top 300）
- `run_auxiliary_application(datasets=("margin_detail","block_trade","hsgt_flow","hsgt_top10"))`（4 个批量集）

**完全不同步**：pledge_stat / holder_number / fina_indicator / auction / broker_recommend。

本地表稀疏（实测）：pledge_stats 384 行、broker_recommends 1310、holder_numbers 553（全市场应 5000+）。

### 3. 推荐 run 强制 refresh=True

`app/services/auxiliary/application.py:245-255`：`skip_aux_refresh=False`（trend_core 等默认）→ `refresh=True` → 注入 `TushareStateProvider` 逐股拉。

本地表稀疏 + refresh 强制逐股拉 = 4948 股 × 5 接口卡死。

### 4. Tushare 批量能力（修复依据）

实测 Tushare 接口的批量支持：

| 接口 | 批量参数 | 实测行数 | 可批量 |
|------|---------|---------|--------|
| `moneyflow(trade_date=)` | trade_date | 5500 | ✅ |
| `stk_holdernumber(end_date=)` | end_date | 5500 | ✅ |
| `pledge_stat(end_date=)` | end_date | 2466 | ✅ |
| `fina_indicator(ann_date=)` | ann_date | 已有 `fetch_fina_indicator_batch` | ✅ |
| `stk_auction(trade_date=)` | trade_date | **权限不足** | ❌ |

4 个数据集支持批量（1 次调用拉全市场），stk_auction 无权限（memory 已记，应跳过）。

## 修复方案

### 修复 A（核心，已落地）：逐股方法改为 trade_date/period 批量

`TushareStateProvider` 的 5 个原逐股方法全部改为单次批量调用 + `requested_codes` 过滤：

| 方法 | 改造前 | 改造后 | Tushare 调用 |
|------|--------|--------|-------------|
| `capital_flow` | `for code: moneyflow(ts_code=,start,end)` | `moneyflow(trade_date=)` | 4948 → 1 |
| `pledge_stat` | `for code: pledge_stat(ts_code=)` | `pledge_stat(end_date=季末日)` | 4948 → 1 |
| `holder_number` | `for code: stk_holdernumber(ts_code=,end_date,limit)` | `stk_holdernumber(end_date=季末日)` | 4948 → 1 |
| `fina_indicator` | `for code: fina_indicator(ts_code=,limit)` | `fina_indicator(period=季末日)` | 4948 → 1 |
| `auction` | `for code: stk_auction(ts_code=,trade_date)` | `stk_auction(trade_date=)`（异常即返回 ()） | 4948 → 1（无权限立即短路） |

关键实现点：
- 新增 `_quarter_end_day()` 把任意交易日回退到最近季末披露日（质押/股东人数/财务指标按季度披露，非季末日按 `ts_code` 调返回空）。
- 所有过滤 `wanted = set(requested_codes)`；`wanted` 为空（scheduler 批量同步传 `instruments=()`）时不过滤、拉全市场落库——与既有的 `margin_detail`/`hsgt_*` 批量集行为一致。
- `capital_flow` 累计窗口字段（3d/5d/10d）改为 0.0 兜底：run 时通常已被本地表覆盖（`refresh=False`），`refresh=True` 兜底路径只入当日净额，累计窗口交给每日同步的 `compute_cumulative_inflows`。

### 修复 B（已落地）：每日同步补全慢变数据

`scheduler_service.py:_run_auxiliary_remediation` 的批量同步集从
`("margin_detail","block_trade","hsgt_flow","hsgt_top10")` 扩展为
`("margin_detail","block_trade","hsgt_flow","hsgt_top10","pledge_stat","holder_number","fina_indicator")`。

每集一次批量调用全市场落库，run 时走本地优先。pledge_stats 表从 384 行预期升至 5000+。

### 修复 C（不单独做，由 A+B 蕴含）

慢变数据刷新策略不变（仍 `refresh` 取决于调用方）；A+B 已让 run 时本地优先路径拿到全市场数据，逐股 `refresh=True` 卡死不复现。

## 验证

盘后实测（2026-07-16，trade_date=2026-07-16）：

| 数据集 | 改造前 | 改造后 | 耗时 | 表行数变化 |
|--------|--------|--------|------|-----------|
| pledge_stat | 4948 次逐股（卡死 4.6h） | 1 次 end_date 批量 + 季末回退链 | 3.0s | 384 → 2634 |
| holder_number | 4948 次逐股（卡死） | 1 次 end_date 批量 + NaN/去重容错 | 0.8s | 553 → 6032 |
| capital_flow | 4948 次逐股（卡死） | 1 次 trade_date 批量 | <1s | （代理支持，正常） |
| fina_indicator | 4948 次逐股（卡死） | period 批量失败→ts_code+period 逐股兜底（cap 200） | <30s | 17708（已有，代理限制） |
| auction | 4948 次逐股（无权限） | 1 次 trade_date 批量，异常即 () | <1s | 0（无权限） |

关键发现：
1. **fina_indicator 代理不支持 `period=`/`ann_date=` 批量**（返回 500），只有 `ts_code=` 单股可行。
   fina_indicator 改为：先试 period 批量，失败回退到 ts_code+period 逐股，加 `_FINA_PER_STOCK_CAP=200`
   上限防止宽召回（~6000 股）卡死；每日同步不纳入（instruments=() 无 requested_codes 不触发逐股兜底），
   由 `backfill_fina_indicator.py` 在财报季手动/定时按 ts_code 逐股补。
2. **pledge_stat 披露日不规则**：2026-06-30/2026-03-31 无数据，2025-12-31/2025-09-30 有数据。
   `_quarter_end_candidates` 按季末从近到远回退到第一个非空日期。
3. **holder_number Tushare 返回 NaN holder_num + 重复 (code, end_date)**：
   - NaN 导致 `int(NaN)` ValueError 中断整批 → 加 `_safe_int`（NaN→None→落 0）。
   - 重复自然键触发 `_assert_unique_batch` 抛错 → 加 `seen_keys` 去重。

测试：`app/services/auxiliary/tests/test_state_provider_batch.py` 9 个测试全绿（单次批量调用/过滤/空 codes 不过滤/季末跨年回退/auction 无权限短路/fina cap 守卫/holder NaN+去重容错）。`pytest auxiliary+scheduler+data_gap+auxiliary_application` 共 75 passed。

## 影响/回滚

- 改动文件：`backend/app/services/auxiliary/state_adapters.py`、`backend/app/services/scheduler_service.py`、新增测试 + 本 bug 报告。
- 回滚点：保留原 `capital_flow` 累计窗口语义已迁移到每日同步；如需还原只需 revert 这两个文件 + 删测试文件。
- 本路径不涉及云端（云端不跑推荐管道）；本地后端需重启使 scheduler 改动生效。
