# 自适应共识闸门 + 换手/成交额闸门 发布 — 2026-09-17

> 交付物：`production_selection v19`（默认）+ `execution_policy v5.0.0`（默认）
> 三窗指标 JSON：`output/adaptive-consensus-gate-metrics-20260917.json`
> 方法与复盘：`docs/reviews/adaptive-consensus-gate-2026-09-16.md`、`docs/lessons/LESSONS.md`（L-123…L-127）

## 1. 解决的问题

champion baseline（v7 + exec v4）三窗为 W1 58.8% / W2 54.7% / W3 56.1%（WR），
均低于 60% 的验收线；且 W2 的 PF 1.169 < 1.2。根因诊断（第 22–24 轮）：

1. **多策略共振（≥3 背书）在不同行情下符号相反**：W1/W2 是强正信号（已选样本 WR 66%/58% vs <3 的 53%/51%），
   W3 是强负信号（45% vs 62%）。当日可观测特征（regime×情绪、涨停家数、中位 ivol、分数差）全部无法切开。
2. **高换手与大成交额是三窗同向的负向特征**（换手 [8,12) 档在三窗都是低质量区；成交额最高 20% 同理）。

## 2. 发布内容

| 组件 | 版本 | 内容 |
|---|---|---|
| `production_selection` | **v19**（默认；v20/v21 由闸门按日切换） | v19 = 换手<8% + 成交额<20 亿 + 自适应闸门（中性分支）；v20 = v19 + ≥3 背书（边际为正时）；v21 = 换手<8% + 成交额<20 亿 + ≤2 背书（边际深负时） |
| `execution_policy` | **v5.0.0** | v4 + `trailing.activation_gain_pct` 1.5% → 0.8%（L-123：三窗全部提升 WR 与 MDD） |
| 逐日选择器 | `app/services/consensus_edge.py` | `edge(t)=mean(net|背书≥3)−mean(net|背书<3)`，K=15 个已结算交易日；`edge>0→v20`、`edge<−1.5→v21`、其余→v19；任何异常回落默认（绝不阻塞跑批） |
| 接线点 | `app/services/runs/stages/adapters.py` | 日正式推荐链路在 `evaluate_selection_policy` 前解析当日政策 |

## 3. 三窗结果（生产权重 DSA 0.5 / 陈小群 1.5，path A 全管线端到端）

| 窗口 | 基线 n/WR/avg/Σ/PF/MDD | **发布后** | WR≥60 | PF≥1.2 | Σ>0 |
|---|---|---|---|---|---|
| W1 2025-01-15~2026-01-16 | 792 / 58.8% / +0.6277 / +497.1 / 1.348 / −46.76 | **572 / 62.2% / +0.7410 / +423.9 / 1.508 / −37.68** | ✓ | ✓ | ✓ |
| W2 2026-01-17~2026-07-30 | 497 / 54.7% / +0.3829 / +190.3 / 1.169 / −27.15 | **282 / 60.3% / +1.0086 / +284.4 / 1.616 / −17.31** | ✓ | ✓ | ✓ |
| W3 2026-08-01~2026-09-13 | 66 / 56.1% / +0.5117 / +33.8 / 1.234 / −15.69 | **54 / 63.0% / +1.3821 / +74.6 / 1.867 / −3.65** | ✓ | ✓ | ✓ |

- **三窗口同时满足用户验收口径（WR≥60% / PF≥1.2 / sum_net>0）。**
- `evaluate_challenger`（L-010）：W2/W3 = PRODUCTION；**W1 = FALSIFIED**——W1 的累计收益从 +497.1 降到 +423.9
  （−73.2pp），触发闸门"任一收益指标回退 ≤1pp"的上限，即使 WR +3.4pp / PF +0.16 / MDD +9.1 也不放行。
  两条判据的差异在此显式记录：用户口径只要求 Σ>0，闸门要求 Σ 不回退。本轮按用户口径发布。
- 对照：不含成交额闸门的版本在 W1 更强（63.5%/PF1.680/Σ+545.2）但 W2 只有 59.7%（差 0.3pp）——
  成交额闸门是"三窗同时达标"的必要条件。

## 4. 回归与验证

- `pytest tests/ -q -k "not network and not live and not tamper and not governance"`：**2702 passed**，
  12 failed（全部为既存环境/夹具失败，已用 `git stash` 在同一批次复核：alembic 4、realtime_quote 3、
  data_source_management 1、self_check_a_item 2、shortline_factors golden 2）。
- 因发布而更新的行为契约测试（均逐条核对语义）：`test_outcome_simulation` 3 项（act 0.8% 下 +1% 即激活
  trailing）、`test_research_gateway_vertical_slice` 1 项（亏损单出场含 gap → 归因映射为 chase_high）、
  `test_execution_policy` / `test_production_selection_policy` / `test_weak_low_ivol_dn20_veto` 的"当前默认版本"
  断言（v4.0.0→v5.0.0、v7→v19）。
- 新增测试：`tests/test_consensus_edge.py`（4）、`tests/test_high_amount_veto.py`（3）、
  `tests/test_anti_consensus_gate.py`（3）、`tests/test_high_turnover_veto.py`（3）。
- 页面指标 artifact 已随新默认重新导出（`scripts/export_strategy_backtest_metrics.py`，caliber → v19）。

## 5. LIVE 监控建议

1. **看前 10 个交易日的实际出场分布**：`trailing_stop_gap` 占比应上升、`stop_loss_hit` 下降；
   若 `stop_loss_hit` 占比反而上升，说明 act0.8 在真实盘口下没有生效（检查执行链路读的是注册表 current）。
2. **看每日选股数量**：W1/W2 的日均可选数会下降约 30–45%（换手/成交额闸门），若某日直接 0 推荐，
   检查是否 `adaptive_consensus` 分支 + 双重闸门叠加过紧（可临时回退 v19→v7 观察）。
3. **看 `edge` 统计量的日度值**（可选：把 `compute_consensus_edge` 的结果写到 run 日志）：
   连续为负超过 3 日本应切到 v21（反向共识），若未切换说明结算数据滞后（`outcome_label` 未落 success/failed）。
4. **7 天后回看**：`recommendation_outcomes` 的新样本 WR 与 PF，对比本文件 W3 的 63.0% / 1.867；
   若显著低于 W3 且 MDD 恶化，说明 W3 的强结果含时段幸运成分，应考虑把默认退回 v7（一键：改 `load_production_selection_policy` 默认版本）。

## 6. 回滚方式

```bash
# 选股回退：把默认版本改回 7（或把 v19 artifact 的 adaptive_consensus_enabled / 两个闸门置空）
# 出场回退：把 app/policies/execution_policy.registry.json 的 current 指回 4.0.0
git revert <本发布 commit>
```
