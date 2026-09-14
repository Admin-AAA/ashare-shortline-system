# 出场策略 v3 落地 — 2026-09-15

## 一句话

把生产出场策略从 **v2**（+6% 止盈 / −4.5% 止损 / trailing 从 +2.0% 触发）改为
**v3**（**关闭止盈** / **−5.5% 止损** / trailing 从 **+1.5%** 触发），
三窗 canonical 复算**全部指标同时改善**，且**三窗累计收益首次同时为正**。

## 改动内容

| 项 | v2 | v3 |
| --- | --- | --- |
| take_profit | enabled, +6.0%（上限 +15%） | **enabled=false**（数值保留但不生效） |
| stop_loss | −4.5%（上限 −4.7%） | **−5.5%（上限 −5.7%）** |
| trailing | profit_floor, 激活 +2.0% | profit_floor, **激活 +1.5%**（callback/缩比不变） |

产物：`backend/app/policies/execution_policy.v3.json`
fingerprint `86553a1fcd4a92d97b0395cd71caae1a11b6090360686b9ee75077db943e6824`，
注册表 `execution_policy.registry.json` 的 `current` 指向 v3（v1/v2 保留可复现）。

## 证据一：三窗 canonical 出场扫描（主证据）

`scripts/research_exit_sweep_three_window.py`（性能设计：选择与每日 bar 只算一次，
所有配置复用；222 个配置 1.6 分钟）。基线 = v2 默认。

最优配置 `tron_act1.5_cb0.8_lk0.5_tpNone_sl5.5`：

| 窗口 | n | WR | avg_net | PF | sum_net | MDD(cohort) | evaluate_challenger |
| --- | --- | --- | --- | --- | --- | --- | --- |
| W1 | 982 | 55.1% → **57.3%** | +0.413 → **+0.570** | 1.227 → **1.325** | +406 → **+559** | −48.91 → −48.89 | **PRODUCTION** |
| W2 | 695 | 54.1% → **56.8%** | −0.097 → **+0.033** | 0.953 → **1.016** | −67 → **+23** | −31.79 → **−29.64** | **PRODUCTION** |
| W3 | 95 | 48.4% → **54.7%** | −0.041 → **+0.043** | 0.979 → **1.024** | −4 → **+4** | −12.92 → **−11.35** | **PRODUCTION** |

- **三窗全部通过** `evaluate_challenger`（收益项改善、辅助项无两项退化）；
  222 个配置里有 **24 个**三窗全过，此处取 ΣΔsum 最大者。
- 三个窗口的胜率、均值、PF、累计同时改善；W2/W3 的 MDD 也改善，W1 基本持平。
- 原始结果：`backend/output/research/exit_sweep_base.jsonl`、`exit_sweep_refine.jsonl`。

## 证据二：串行 champion 重放（L-016）

`scripts/si_sequential_champion_replay.py` → `output/research/seq_champion_replay_v3_20260915.json`：
authoritative trailing 阶段的判定为 `decision=PRODUCTION`、`should_publish=true`、
改善项 = 全部五项、退化项 = 空（Δavg +0.60、Δcum +1.74、ΔPF +0.292）。
注意该脚本的有效样本很小（n=3~5，20cm 链），只作**兼容性/顺序性检查**，主证据是上面三窗。

## 证据三：受影响测试面

`pytest tests/test_execution_policy.py tests/test_evidence_identity.py tests/test_metrics_sample_scope.py
tests/test_realtime_pnl_service.py tests/test_research_snapshot_replay.py tests/test_research_backtest_repository.py
tests/test_canonical_backtest_experiment.py tests/test_run_canonical_backtest_experiment_cli.py
tests/test_analyze_stock_market_snapshot.py -q` → **70 passed**（含本次新增的开关回归测试）。

## 顺带修掉的一个真实缺口（否则本次发布会静默无效）

发布 v3 时发现：**实盘 outcome 结算路径原先只读数值常数、不读 `enabled`**——

- `ExecutionPolicy.runtime_constant_projection()` 只投影 `DEFAULT_TAKE_PROFIT_PCT` 等数值；
- `app/services/outcome_simulation.py` 的 `_resolve_exit_result` 把 `dynamic_trailing=True` **写死**。

结果：把 v3 artifact 设为 current 后，"关闭止盈"在**回测**里生效（金标准核读扁平属性
`take_profit_pct`，已尊重 enabled），但在**实盘结算**里仍会按 +6% 止盈标注。
本次一并修复：

1. 投影补入 `STOP_LOSS_ENABLED` / `TAKE_PROFIT_ENABLED` / `TRAILING_ENABLED`；
2. `outcome_simulation` 读取这三个开关（关止盈 → 价格置 `1e308` 等价"永不触发"；
   关止损 → `0.0`；关 trailing → `dynamic_trailing=False`）；
3. 文档投影 `render_execution_policy_markdown` 在禁用时显式写 `disabled`（原先会印出一个已失效的阈值）；
4. 新增回归测试 `test_outcome_settlement_honours_disabled_exit_switches` 锁住这条链路。

## 已知限制与残留风险

1. **仍未达原目标**：三窗 WR 57.3/56.8/54.7 < 60%，W2/W3 的 PF 1.016/1.024 < 1.2。
   本次是"全指标同时改善 + 三窗累计转正"，不是原始三条件的全部达成。
2. **口径**：三窗指标来自 path A canonical（冻结快照 + 存储回放 + 真实因果出场模拟），
   不是 `live_forward`；W3 的存储回放只覆盖 19/30 个交易日（见数据源地图 5.1）。
3. **不重算历史**：`recompute_stale_outcomes` 是手动维护函数，本次**不调用**——
   已结算历史 outcome 保持原样，避免重写已发布证据；此后新结算按 v3 标注。
   这意味着近期一段时间内历史 outcome 与 v3 语义并存，解读时需要按时间切分。
4. **止盈关闭的风险**：赢家跑到 trailing/horizon，单笔尾部可能更大（W1 MDD 基本持平，
   W2/W3 改善）；若未来出现"冲高后大幅回吐"的 regime，需按 live 监控回看。
5. 20cm 串行重放样本极小（n=3~5），不能作为独立证据。

## 复现

```bash
cd backend
# 主证据：三窗出场扫描（222 配置约 1.6 分钟）
.venv/bin/python scripts/research_exit_sweep_three_window.py --stage base
.venv/bin/python scripts/research_exit_sweep_three_window.py --stage refine
# L-016 串行 champion 重放
.venv/bin/python scripts/si_sequential_champion_replay.py --output output/research/seq_champion_replay_v3_20260915.json
# 受影响测试面
.venv/bin/pytest tests/test_execution_policy.py tests/test_evidence_identity.py -q
```
