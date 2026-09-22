# 涨停策略研究记录

## 第 3 轮，2026-09-22：同一具名营业部再次净买入

- 候选分支：`feat/dragon-seat-repeat-20260922`。
- 假设：近期涨停股票的同一营业部在不同日期再次净买入，可能反映持续需求；预期改善次日涨停率与交易净收益。
- 回归测试：新增 2 项，与已有涨停形态回归合计 4 项通过。
- 三窗指标及 bootstrap：`output/strategies-deep-opt-report-2026-09-22.md`；逐笔记录及复现命令：`output/dragon-seat-repeat-metrics-2026-09-22.json`。
- 闸门结果：FALSIFIED。原始相对判定 W1/W2/W3 为 FALSIFIED/FALSIFIED/PRODUCTION；三个窗口均未满足绝对目标。
- 归档位置：JSON 的 `archived_falsified_directions`；归档键 `dragon-seat-repeat-20260922:named-repeat-net-buy`。
- 删除 / 新建技能：0 / 0。本轮只有研究声明，不加入预设。
- 发布：未修改生产策略，不进行策略版本递增或后端重启。
- 下一轮方向：补充历史发布时间和盘中主动买卖、委托证据；不再扫描本轮月份、次数或价格阈值。
- 累计统计：3 轮 / 0 PASS / 3 FALSIFIED 轮次（5 个方向）/ 0 删除 / 1 新建研究技能。前两轮记录见 `output/strategies-deep-opt-self-driving-2026-09-21.md`。

## 第 4 轮，2026-09-22：机构记录一致净买入

- 候选分支：`feat/dragon-seat-repeat-20260922`。
- 假设：前日机构记录均为净买入，可能代表多类资金共同需求；预期改善次日涨停率与交易净收益。
- 回归测试：真实机构记录的完整金额与信号日期检查通过，加入整体回归共 6 项通过。
- 三窗指标及 bootstrap：`output/strategies-deep-opt-report-2026-09-22.md` 第 4 轮；逐笔记录与复现命令：`output/institution-agreement-metrics-2026-09-22.json`。
- 闸门结果：FALSIFIED，三个窗口 WR、PF、Σ 均未达标。
- 归档位置：JSON 的 `archived_falsified_directions`，以及项目研究状态注册表；归档键 `dragon-seat-repeat-20260922:institution_agreement`。
- 删除 / 新建技能：0 / 0，研究声明不进入预设。
- 下一轮方向：固定检验同一具名营业部由净卖出转为净买入。
- 累计：4 轮 / 0 PASS / 4 FALSIFIED 轮次（6 个方向）/ 0 删除 / 1 新建研究技能。

## 第 5 轮，2026-09-22：同一营业部由净卖出转为净买入

- 候选分支：`feat/dragon-seat-repeat-20260922`。
- 假设：最近一次可确认的净卖出之后出现净买入，可能反映该营业部重新参与；预期改善次日涨停率与交易净收益。
- 回归测试：真实席位日期顺序、净额符号及指标复算检查通过，整体回归共 6 项通过。
- 三窗指标及 bootstrap：`output/strategies-deep-opt-report-2026-09-22.md` 第 5 轮；逐笔记录与复现命令：`output/seat-reversal-metrics-2026-09-22.json`。
- 闸门结果：FALSIFIED，三个窗口 WR、PF、Σ 均未达标。
- 归档位置：JSON 的 `archived_falsified_directions`，以及项目研究状态注册表；归档键 `dragon-seat-repeat-20260922:seat_reversal`。
- 删除 / 新建技能：0 / 0，研究声明不进入预设。
- 下一轮方向：无。本次达到连续 5 轮无 PASS 的停止条件，交付瓶颈报告；不再新增候选，不自行降低标准。
- 累计：5 轮 / 0 PASS / 5 FALSIFIED 轮次（7 个方向）/ 0 删除 / 1 新建研究技能。

三项席位方向均未发布；归档代码、测试和证据按同一席位研究主题提交。提交与合并状态在最终交付中报告。
