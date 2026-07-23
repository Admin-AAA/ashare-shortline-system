# BUG 报告：复盘页 T+1 离场口径失真

## 背景

复盘页（RecordsPage + RecordsStockDetail）展示的胜率与收益有两套口径并存：

1. **列表页** `return_1d/3d/5d`（`records_service._compute_returns`）：基准价 = 推荐价（≈推荐日收盘），`pct_chg` 链式累乘，纯持有到收盘，不止盈不止损。
2. **详情页** `executed_return` + `exit_reason`（`outcome_simulation._simulate_outcome`）：基准价 = 次日实际开盘价（`_resolve_entry`），按止损/止盈/trailing 模拟出场，含双边滑点 + 佣金印花税。

用户在详情页看到"按第二天开盘价算，但在第二天就止盈了"，而 A 股 T+1 交收规定**买入当日不能卖出**——实盘第二天（T+1）买入的股票当天无法止盈。模拟引擎 `_resolve_exit` 扫描 `holding_bars` 时**包含了入场日当天的 bar**，允许 T+1 日内触发止盈/止损（含开盘跳空止损），违反 T+1，导致复盘收益不可在实盘复现。

## 根因

1. **入场日参与离场扫描**：`_resolve_exit` → `_resolve_exit_base` / `_resolve_exit_with_trailing` 从 `holding_bars[0]`（入场日）起扫，入场日当天 high/low 触及止盈/止损即离场，但买入当日不可卖。
2. **未区分推荐时点**：系统既可盘后自动推荐（17:35 cron，推荐价≈收盘，T+1 才能买），也可手动盘中触发（推荐价为盘中实时价，T 当天可成交）。旧逻辑对两种时点一视同仁，入场日固定为 T+1，且当天可离场——对盘中推荐多禁了一天可执行性，对盘后推荐允许了不可执行的当日离场。

## 改动方法

核心规则：**入场日当天不触发任何离场（含跳空止损、盘中 high/low 触及止盈止损），从入场日次日起扫止损/止盈/trailing**。推荐时点决定入场日与最早可离场日：

- **盘后推荐**（`recommended_at` 落在非交易时段）：T+1 入场、T+2 起可离场。
- **盘中推荐**（`recommended_at` 落在交易时段 09:30–15:00，UTC 01:30–07:00）：T 当天入场、T+1 起可离场。

推荐时点判定基于 `recommended_at` 时间戳（`datetime.utcnow` 存储，北京时间 09:30–15:00 = UTC 01:30–07:00）。`recommended_at` 缺失时按盘后处理（保守延后离场）。

## 代码位置

### `backend/app/services/outcome_simulation.py`

1. **新增盘后/盘中判定**（模块顶部）：
   - 常量 `_INTRADAY_UTC_START` / `_INTRADAY_UTC_END`。
   - 函数 `_is_intraday_recommendation(rec)`：读 `rec.recommended_at` 时间戳判定。

2. **`_simulate_outcome`**：
   - 调 `_resolve_entry` 时传入 `signal_bar=signal_bar`，供盘中推荐当日入场。
   - `holding_bars` 构造改为基于 `entry_date` 在 `bars` 中定位起点（`holding_start`），替代旧的 `future_bars[entry_index:]`——盘中推荐入场日可能是 `signal_bar`（T 当天），`entry_index` 相对 `entry_window` 的语义与 `future_bars` 不同步，按 `entry_date` 定位最稳。删除不再使用的 `entry_index` 变量。

3. **`_resolve_entry`**：
   - 新增 `signal_bar` 关键字参数。盘中推荐时 `entry_window = [signal_bar] + future_bars[:lookahead]`（signal_bar 作首候选，T 当天按推荐价成交）；盘后推荐维持 `future_bars[:lookahead]`。

4. **`_resolve_exit`**：
   - 入口处跳过 `holding_bars[0]`（入场日）：若 `len(holding_bars) <= 1` 返回 `horizon_close_t1_locked`（持仓但未到可卖日，按入场日收盘标记）；否则 `scan_bars = holding_bars[1:]` 传入 `_resolve_exit_base` 与 `_resolve_exit_with_trailing`。新增退出原因 `horizon_close_t1_locked`。

### `backend/app/services/outcome_service.py`

5. **`_compute_horizon_metric_records`**：预解析 entry 时传入 `signal_bar`（从 `row_bars[sig_idx]` 取），与 `_simulate_outcome` 同口径。

6. **`_scoped_recommendation_rows`**：select 列新增 `Recommendation.recommended_at`（供 `_is_intraday_recommendation` 读取），并同步更新去重 `seen` 的位置下标（`trade_date` 从 `row[9]` 移到 `row[10]`）。

### `backend/tests/test_outcome_simulation.py`

7. `_resolve_exit` 相关单测：原先第一根 bar 即"持仓首日且可离场"，现统一前置一根入场日 bar（不触发离场），原断言落在第二根及之后。涉及 `test_resolve_exit_take_profit_path_preserved` / `test_resolve_exit_trailing_uses_min_open_on_gap_down` / `test_resolve_exit_trailing_same_bar_uses_trigger_price` / `test_resolve_exit_stop_loss_priority_over_trailing` / `test_resolve_exit_same_bar_high_surge_then_stop_loss_uses_stop_loss` / `test_resolve_exit_trailing_not_activated_below_threshold`。

8. `_simulate_outcome` 截断测试（`test_simulate_outcome_min_return_truncated_to_exit_not_horizon` / `test_simulate_outcome_take_profit_min_return_truncated`）：给 rec 设 `recommended_at` 为盘中时间，让走盘中路径（T 当天入场、T+1 离场），保留"止盈/止损后 min_return 截断到 entry→exit"的测试本意。

### `backend/scripts/verify_t_plus_one_exit.py`

9. 反事实验证脚本：对比改后真实口径 vs 库里旧 outcome，拆分盘后/盘中输出胜率/均收益/最大亏损/盈亏比/标签翻转数。

## 反事实验证结果

样本：最近 200 笔 LIVE formal 已结算推荐（104 笔盘中、96 笔盘后）。

| 口径 | 胜率 | 均收益 | 最大亏损 | 盈亏比 |
|---|---|---|---|---|
| 旧口径（库里） | 65.0% | +0.80% | −5.97% | 1.42 |
| 新口径·整体 | 71.0% | +2.37% | −12.29% | 2.26 |
| 新口径·盘中推荐 | 78.8% | +3.70% | −7.17% | 4.05 |
| 新口径·盘后推荐 | 62.5% | +0.93% | −12.29% | 1.36 |

- 盘中推荐大幅改善（盈亏比 1.09→4.05）：旧口径让盘中推荐"T+1 入场、T+1 当天离场"违反 T+1，新口径"T 当天入场、T+1 离场"更早锁利。
- 盘后推荐盈亏比 1.91→1.36：延后一天离场的真实代价，符合 T+1 实盘约束。
- 最大亏损 −5.97%→−12.29%：延后离场遇到连续跳空跌停的票亏得更深，是 T+1 交收的固有代价（实盘 T+1 买入后跌停确实卖不掉），属诚实化而非 bug。
- 标签翻转 44/200 笔。

## 后续（未在本批改动完成）

- **重算历史 outcome**：需运行 `recompute_stale_outcomes` 让库中已标注 outcome 反映新口径（会改变 success/failed 标签与 executed_return，进而影响驾驶舱胜率/盈亏比）。
- **前端口径标注**：列表页与详情页显式标出基准价（推荐价 / 次日开盘）与最早可离场日，避免混淆。

## 测试

```
cd backend
.venv/bin/python -m pytest tests/test_outcome_simulation.py tests/test_exit_rules.py -q
# 34 passed
```

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
