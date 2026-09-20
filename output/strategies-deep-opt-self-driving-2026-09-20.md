# 策略深度自驱优化记录（2026-09-20）

## 第 1 轮

- 候选分支：`feat/wr70-three-window-20260920`
- 假设：最高分策略为炒股养家、基础短线或陈小群时，提高信号日质量下限可减少 T+1 失败，预期改善 WR 与 PF。
- 回归测试：研究入口编译通过；152 组参数完成。
- 三窗指标：见 `output/strategies-wr70-bottleneck-metrics-2026-09-20.json` 第 1 轮。
- 闸门结果：FALSIFIED。W3 闸门为 FALSIFIED，三个窗口 WR 均低于 70%。
- 归档位置：`archived_falsified_directions: wr70-20260920:entry-guard-single`
- 删除或新建技能：无。
- 下一轮方向：按卡片最高分策略研究入场方式。
- 累计统计：1 轮 / 0 PASS / 1 FALSIFIED / 0 删除 / 0 新建。

## 第 2 轮

- 候选分支：`feat/wr70-three-window-20260920`
- 假设：陈小群与板块相对强度信号使用次日开盘，可补充动量延续成交，预期改善 WR 与累计净收益。
- 回归测试：生产 `_simulate_outcome` 三窗完成。
- 三窗指标：见指标 JSON 第 2 轮。
- 闸门结果：FALSIFIED。W3 PF 和累计净收益下降，WR 仍为 66.67%。
- 归档位置：`archived_falsified_directions: wr70-20260920:best-strategy-entry-mode`
- 删除或新建技能：无。
- 下一轮方向：全卡质量条件组合。
- 累计统计：2 轮 / 0 PASS / 2 FALSIFIED / 0 删除 / 0 新建。

## 第 3 轮

- 候选分支：`feat/wr70-three-window-20260920`
- 假设：均线支撑、共识、涨幅、波动、位置与 `final_score` 的双条件可减少边界失败，预期改善 WR 与 PF。
- 回归测试：513 组 path A 参数完成；空成交窗口按不合格候选处理。
- 三窗指标：见指标 JSON 第 3 轮。
- 闸门结果：FALSIFIED。最接近候选 W1/W2/W3 WR 为 66.05%/61.64%/69.77%。
- 归档位置：`archived_falsified_directions: wr70-20260920:entry-guard-pairs`
- 删除或新建技能：无。
- 下一轮方向：市场日级保护。
- 累计统计：3 轮 / 0 PASS / 3 FALSIFIED / 0 删除 / 0 新建。

## 第 4 轮

- 候选分支：`feat/wr70-three-window-20260920`
- 假设：上涨家数占比与涨跌停状态可识别 T+1 承接不足的交易日，预期改善 WR 与最大回撤。
- 回归测试：34 组信号日参数完成。
- 三窗指标：没有候选形成三窗同号改善；基线指标见指标 JSON。
- 闸门结果：FALSIFIED。
- 归档位置：`archived_falsified_directions: wr70-20260920:market-day-gate`
- 删除或新建技能：无。
- 下一轮方向：盈利保留参数。
- 累计统计：4 轮 / 0 PASS / 4 FALSIFIED / 0 删除 / 0 新建。

## 第 5 轮

- 候选分支：`feat/wr70-three-window-20260920`
- 假设：更早锁定盘中盈利可把回落单转为净盈利，预期改善 WR 与 PF。
- 回归测试：canonical pure execution 11 个变体完成，仓位指标读取 `_resolve_position_size`。
- 三窗指标：见指标 JSON 第 5 轮。
- 闸门结果：FALSIFIED。`take_profit=1%` 的 W3 WR68.75%、PF1.187；三个窗口发布判定均为 FALSIFIED。
- 归档位置：`archived_falsified_directions: wr70-20260920:exit-profit-lock`
- 删除或新建技能：无。
- 下一轮方向：停止；交回用户决定是否补充分钟数据与统一成交模拟。
- 累计统计：5 轮 / 0 PASS / 5 FALSIFIED / 0 删除 / 0 新建。
