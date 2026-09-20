# 策略深度复核（2026-09-20）

## 根因清单

1. W1 与 W2 的主要缺口是胜率。当前生产成交口径的 WR 分别为 63.28% 与 61.19%，PF 和累计净收益已经满足目标。
2. W3 随最新数据更新后为 n=60、WR66.67%、PF1.913、Σ+84.90。2026-09-18 的文档结果只覆盖至 2026-09-11；新增交易日使 W3 从 70.5% 回到 66.67%。
3. 全卡 `final_score >= 70` 是选择侧最接近候选，W1/W2/W3 的 WR 为 66.05%/61.64%/69.77%。该候选三个窗口均获 `PRODUCTION`，绝对 WR70% 仍未达到。
4. `take_profit=1%` 是出场侧最接近候选，W1/W2 WR 达到 74.25%/72.73%，W3 只有 68.75%，PF 为 1.187；三个窗口的 `evaluate_challenger()` 均为 `FALSIFIED`。
5. 两条成交模拟存在逐笔差异。同一批 366/209/66 张卡中，生产 outcome 路径与 canonical pure execution 的已结算笔数为 354/201/60 对 365/209/62，逐笔差异数量为 76/47/18。W1/W2 有 11/8 笔在 outcome 路径未触发，pure execution 路径却成交；两边均结算但净收益不同的记录为 65/39/16 笔。
6. W3 最后若干信号尚未完成五个交易日持有期。生产 outcome 路径标记 4 笔 pending；任何最终胜率都必须继续排除这些记录。

## 受阻约束

- 选择侧：提高 `final_score` 下限能改善 W1/W3，W2 胜率基本不变。
- 成交方式：陈小群与板块相对强度改用次日开盘后，W3 的 PF 与累计净收益下降。
- 市场日级状态：34 组信号日市场广度与涨跌停保护没有形成三窗同号候选。
- 出场侧：低止盈把 W1/W2 推过 WR70%，同时把 W3 PF 压到 1.2 以下附近。
- 测量侧：两条成交模拟尚未逐笔一致，出场参数结果不能进入发布。

## 需要补充的数据

- 覆盖 W1、W2、W3 的分钟 K 线，用于统一止损、trailing、止盈在同一交易日的先后次序。
- 覆盖三个窗口的集合竞价与次日开盘成交状态，用于确认 `next_open` 的可成交价格与涨停封单状态。
- 每笔推荐的统一执行输入快照，至少包含策略声明的 `entry_mode`、`final_score`、`_resolve_position_size` 结果、止损价、止盈价和 execution policy 指纹。

## 复现

```bash
cd backend
.venv/bin/python scripts/research_strategy_ablation_fast.py --window-range 2026-08-01:2026-09-18 --targets none --out output/wr70/w3_baseline.json --panel-dir output/wr70/panel_v27_current --universe historical --workers 6 --policy-version 27 --skill-weights preset --output-count 6
.venv/bin/python scripts/research_wr70_entry_guard.py --guard-family entry --panel-dir output/wr70/panel_v27_current --out output/wr70/round3_multifactor_guard_current_w3.json
.venv/bin/python scripts/research_wr70_entry_guard.py --guard-family day --panel-dir output/wr70/panel_v27_current --out output/wr70/round4_market_day_guard_current_w3.json
.venv/bin/python scripts/research_wr70_exit_screen.py --panel-dir output/wr70/panel_v27_current --out output/wr70/round5_exit_screen_current_w3.json
.venv/bin/python scripts/research_wr70_baseline_compare.py --panel-dir output/wr70/panel_v27_current --out output/wr70/baseline_engine_compare.json
```
