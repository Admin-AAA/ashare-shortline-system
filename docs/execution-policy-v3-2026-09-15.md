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
# 闭环验证：用已发布 artifact（registry current）重算三窗并断言与扫描一致
.venv/bin/python scripts/research_verify_execution_policy_v3.py
# L-016 串行 champion 重放
.venv/bin/python scripts/si_sequential_champion_replay.py --output output/research/seq_champion_replay_v3_20260915.json
# 受影响测试面
.venv/bin/pytest tests/test_execution_policy.py tests/test_evidence_identity.py -q
```

## 发布后补记：两个必须留下的坑（2026-09-15）

### 坑 1（严重，会让生产结算全线失败）：关止盈不能写超大数

第一版把"止盈关闭"实现成 `_resolve_take_profit_price` 返回 `1e308`（照搬金标准核
内部的哨兵值）。全量套件立刻暴露 `pymysql.err.DataError: (1264, "Out of range value
for column 'take_profit_price'")` —— 因为 `recommendation_outcomes.take_profit_price`
是 MySQL `FLOAT`（上限约 3.4e38），`1e308` 存不进去，**每一笔结算都会失败**。

修法：关止盈时返回 **`None`**（列可空，语义就是"无目标价"），并在因果退出核的
边界处把 `None` 转成"永不触发"（与 `execution_core` 对 `take_profit_pct is None`
的处理一致）；结果字典也保持 `NULL`。
教训：**语义哨兵值必须检查落库列的真实取值范围**，否则"逻辑正确、持久化爆掉"。

### 坑 2：守卫里硬编码的生产常量会漂移

`scripts/self_check.py` 的 D 项"重放身份"守卫把止损写成 `-4.5`；v3 改成 −5.5 后
`tests/test_self_check_canonical_baseline.py` 立刻失败——这条守卫正是为抓漂移而存在。
已改为从出场策略派生（`_default_stop_loss_pct()`）。

### 发布后闭环验证（已通过）

`scripts/research_verify_execution_policy_v3.py` 用 **registry current 的
`DEFAULT_EXECUTION_POLICY`**（而不是扫描脚本里的临时对象）重算三窗：

| 窗口 | n | WR | avg_net | PF | sum_net | 与扫描 |
| --- | --- | --- | --- | --- | --- | --- |
| W1 | 982 | 57.3% | +0.5695 | 1.325 | +559.3 | ✓ |
| W2 | 695 | 56.8% | +0.0331 | 1.016 | +23.0 | ✓ |
| W3 | 95 | 54.7% | +0.0427 | 1.025 | +4.1 | ✓ |

### 全量测试

`pytest tests/ -q` → **2914 passed / 15 failed / 5 skipped**。
15 项与本次改动前的既有基线失败**逐项相同**（alembic 迁移版本断言 ×4、
realtime_quote 环境 ×3、self_check A 项旧行为 ×2、shortline golden ×2、
data_source_management、production_selection_policy、self_improve_state_dirty_tree_guard、
strategy_plugin_architecture）：**本次引入的新失败为 0**。

期间有意更新了 6 处"锁旧口径"的测试（版本号/止损值/文档投影 golden、
outcome 模拟的开关固定、e2e 与并发结算的 K 线长度与断言），并新增 2 个守卫用例
（结算必须尊重 enabled 开关；投影必须带三个 `*_ENABLED`）。
