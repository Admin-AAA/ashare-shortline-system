# 出场策略 v4 落地 — 2026-09-15（第二轮）

## 一句话

在当天刚发布的 v3 之上，再收紧**两个真规则**旋钮——**入场跳空上限 10% → 6%**
（跳得太多就不追）与 **trailing 最小锁利 0.3% → 2.5%**——三窗全部指标继续改善，
且相对 v3 的**逐月同号 0.82（21 个月）达到 L-058 的 0.80 门槛**。

## 改动内容（v3 → v4）

| 项 | v3 | v4 |
| --- | --- | --- |
| `gap_risk.max_entry_gap_up_pct` | 10% | **6%**（T+1 开盘高于推荐价 6% 以上则放弃入场） |
| `trailing.minimum_lock_gain_pct` | 0.3% | **2.5%**（地板 = max(min_lock, 峰值×0.5)） |

其余与 v3 相同（止盈关闭、止损 5.5%、trailing 激活 1.5%）。
产物 `backend/app/policies/execution_policy.v4.json`，fingerprint
`d0b4fc18c1cc8c683176f4b6db34935a336d2fab06e6c8f61bbcc32664b28470`；registry current → v4。

## 证据

### 1. 二轴扫描（只用真规则旋钮）

`scripts/research_exit_sweep_three_window.py --stage gap`。**刻意不扫
`gap_risk.stop_gap_loss_cap_pct`**：那是成交假设（`price = max(bar_open, entry×(1−cap/100))`，
把 −20% 的跳空按 −8% 记账），降低它等于用更乐观的成交价刷收益，没有任何真实机制。
第一版扫描曾把它降到 8% 并得到更高数字（ΣΔsum +345），**该结果已被显式否决、不采信**。

固定 cap=15%、只扫 mge × min_lock（× 同 bar 优先级）共 50 个配置，全部三窗过闸门。
入选配置 `mge6_ml2.5`：

| 窗口 | n | WR | avg_net | PF | sum_net | 闸门 |
| --- | --- | --- | --- | --- | --- | --- |
| W1 | 981 | 57.6% | +0.6448 | 1.374 | +632.6 | PRODUCTION |
| W2 | 693 | 57.4% | +0.0468 | 1.023 | +32.4 | PRODUCTION |
| W3 | 94 | 55.3% | +0.2737 | 1.161 | +25.7 | PRODUCTION |

### 2. 稳健性（为什么不是挑 Σ 最大的那个）

`scripts/research_exit_robustness_check.py --mge 6 --min-lock 2.5 --neighbors`，
基线 = 当天已发布的 v3：

| 候选 | W1 Δavg/Δsum/ΔWR | W2 | W3 | 逐月同号(21月) |
| --- | --- | --- | --- | --- |
| **mge6_ml2.5（采用）** | +0.075/+73/+0.3pp | +0.014/+9/+0.6pp | +0.231/+22/+0.6pp | **0.82** |
| mge6_ml3.5 | +0.105/+103/+0.5pp | +0.057/+39/+0.9pp | +0.044/+4/+0.6pp | 0.73 |
| mge5_ml2.5 | +0.058/+54/+0.2pp | +0.028/+19/+0.7pp | +0.231/+22/+0.6pp | 0.77 |
| mge7_ml2.5 | +0.056/+55/+0.2pp | −0.003/−2/+0.4pp | +0.250/+24/+1.1pp | 0.77 |
| ml1.5 各档 | 微弱/负 | — | — | 0.50~0.59 |

结论：ΣΔsum 最大的是 `mge6_ml3.5`（+146），但它 W3 只 +4 且逐月同号 0.73 **不达 0.80**；
`mge6_ml2.5` 是唯一"三窗全改善 + 同号率≥0.80"的配置，故采用它。`ml1.5` 一档整体无效
（同号率 0.50~0.59）说明这一档改善确实来自 min_lock 提高，而非噪声。

### 3. 闭环验证 + 测试

`scripts/research_verify_execution_policy_v3.py`（用 registry current 的
`DEFAULT_EXECUTION_POLICY` 重算三窗）→ 三项全部 ✓ 与扫描一致。

受影响测试面 174 passed。期间有意更新了 6 处锁旧口径的断言（最小锁利 0.3→2.5 导致
trailing 地板抬到 2.5%，出场由"盘内回落触发"变成"次日开盘跳空触发"）：
`test_causal_profit_floor_locks_fraction_of_prior_peak_profit`（显式 min_lock=0 以隔离
锁利比例机制）、`test_production_exit_uses_next_day_profit_floor_50_policy`、
`test_simulate_outcome_defaults_to_production_profit_floor_policy`、
`test_resolve_exit_causal_min_lock_pct_parameter`（改为 max(生产常量, 比例值)，不再写死 0.3）。

## 已知限制

1. 仍未达原目标：三窗 WR 57.6/57.4/55.3 < 60%，W2 的 PF 1.023 < 1.2。
2. 同日两次发布（v3→v4）都在同一组三窗上挑选；v4 的选择依据是 L-058 的逐月同号门槛
   （0.82），但仍属**同窗内优化**，需要 live_forward 样本独立确认。
3. 与 v3 相同的口径限制：path A canonical、W3 只覆盖 19/30 个交易日、历史 outcome 不重算。

## 复现

```bash
cd backend
.venv/bin/python scripts/research_exit_sweep_three_window.py --stage gap --out output/research/exit_sweep_gap_honest.jsonl
.venv/bin/python scripts/research_exit_robustness_check.py --mge 6 --min-lock 2.5 --neighbors
.venv/bin/python scripts/research_verify_execution_policy_v3.py
```
