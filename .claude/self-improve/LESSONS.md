# Self-improve 教训注册表

这里只记录能够跨轮复用、会改变未来决策的知识。单次运行状态、临时样本数字和普通实验结果写入结构化运行记录，不写入本文件。

状态含义：

- `active`：仍需提示词显式约束。
- `encoded`：已有代码、测试或自动护栏承载；提示词只保留上位原则。
- `superseded`：已被更准确的教训替代。

## L-001 回测与 LIVE 口径污染

- 状态：active
- 适用：所有业绩报告和晋级判定
- 证据：历史上曾将 backtest `n=39` 的结果写成 LIVE formal `n=328`。
- 永久规则：每组指标必须携带 `source`、`as_of`、代码版本、样本范围和成本模型；不同 source 禁止计算差值。
- 吸收位置：主提示词“选择证据路径”“验证与决策”。

## L-002 因果口径变化会使旧 champion 失效

- 状态：active
- 适用：成交时序、复权、费用、去重、统计范围和未来函数修复
- 证据：历史成交与 outcome 真实性修复后，旧固定胜率和盈亏比仍被用于比较，形成污染基线。
- 永久规则：因果或统计口径变化后，先同代码重放未修改 production，再比较 challenger；重放前不得报告差值。
- 吸收位置：主提示词“真实性硬门禁”。

## L-003 指数强势不等于短线赚钱效应强

- 状态：active
- 适用：regime 构造及分 regime 回测
- 证据：存在沪深 300 趋势强、但题材 breadth 弱且短线胜率低于 35% 的窗口；单日 breadth 脉冲也会误导。
- 永久规则：regime 必须由指数趋势与个股 breadth 的版本化公共实现共同决定；中性和未知状态单列。
- 禁止做法：复用已知有单维度缺陷的历史快照标签，或在单轮提示词中临时发明阈值。
- 吸收位置：主提示词“选择证据路径”。

## L-004 单日簇和稀疏特征会制造虚假因子

- 状态：encoded
- 适用：因子相关性、亏损簇和参数诊断
- 证据：小样本 point-biserial、单日 regime 冲击及分母接近零曾被误判为 alpha 或 alpha 衰减。
- 永久规则：先检查跨交易日稳定性、特征覆盖率、基础相关绝对值和 regime 聚集；单日或稀疏噪声不得生成生产规则。
- 自动护栏：`scripts/feature_signal_guard.py`、alpha decay 诊断及相关测试。
- 吸收位置：主提示词“选择唯一动作”。

## L-005 回测数据深度和幸存者偏差必须显式披露

- 状态：active
- 适用：历史回测晋级
- 证据：早期 `daily_kbars` 仅覆盖少量股票，且当前 universe 缺少部分历史退市或无数据标的。
- 永久规则：报告历史应有 universe 的缺口比例和偏差方向；缺口超过 5% 时只作诊断。
- 吸收位置：主提示词“选择证据路径”。

## L-006 无输入变化必须切换证据路径

- 状态：active
- 适用：定时自我优化
- 证据：OOS 固定在 `n=27/30` 时，凌晨连续产生十余次完全相同的等待或“否证”记录。
- 永久规则：代码和 LIVE 水位未变化时，不重复查询同一批 LIVE outcome；立即切换到历史回测、未测试 OOS 窗口、测量修复或新策略研究。LIVE 的 `blocked_until` 只约束 LIVE 路径，不得阻断整轮收益优化。
- 纠错记录：2026-07-30 曾把本规则错误实现为全局 `NO_NEW_EVIDENCE` 短路，导致历史回测也被跳过；该实现已撤销。
- 吸收位置：主提示词“PRECHECK”。

## L-007 调度诊断必须先校验时间窗和时区

- 状态：active
- 适用：scheduler、数据新鲜度和 outcome 结算诊断
- 证据：北京时间凌晨把尚未到触发点的 cron 误判为失活；数据库 UTC 时间也曾被当成本地时间读取。
- 永久规则：诊断前同时记录当前本地时间、调度时区、计划触发时间、数据库存储时区和下一有效数据事件。
- 吸收位置：主提示词“PRECHECK”的 `blocked_until/next_event`。

## L-008 worktree 不能隔离共享实验状态

- 状态：active
- 适用：并发 self-improve
- 证据：多个实例虽使用独立 worktree，仍共享 production MySQL、champion 和 Markdown 日志，产生重复轮号、乱序记录及基线竞争。
- 永久规则：默认只允许一个 self-improve 实例；运行记录按 `run_id` 结构化写入。并行只允许明确证明互不共享数据、基线和发布路径的只读任务。
- 自动护栏：`scripts/run-self-improve.sh` 原子目录锁。

## L-009 验证窗口不能反复调参

- 状态：active
- 适用：所有 challenger 研究
- 证据：同一批 outcome 上连续更换阈值会把 holdout 变成训练集，并产生选择性汇报。
- 永久规则：时间序列 walk-forward 必须包含 purge/embargo 和 untouched holdout；用过的 holdout 作废；主要变体全部登记。
- 吸收位置：主提示词“统计协议”“验证与决策”。

## L-010 候选晋级不等于直接发布

- 状态：active
- 适用：回测或 LIVE 验证通过后的发布
- 证据：强回测可独立提供晋级证据，但仍不能证明 LIVE 执行链没有系统性偏差。
- 永久规则：回测或 LIVE 配对验证只能晋级到 shadow；shadow 满足 LIVE 样本门槛后才可小权重 production。
- 吸收位置：主提示词“晋级条件”。

## L-011 板块拆分 divergence ≠ 板块级 exit 缺陷

- 状态：active
- 适用：看到 20cm vs 10cm（或任二元切分）D 项收益大幅分化时
- 证据：2026-07-30 official forward clean≥72（n=69）显示 10cm avg -2.85%/netPL 0.21 vs 20cm +1.98%/netPL 2.13。但反事实证伪两条 exit 侧解释：①只放宽 10cm 固定止损（-5.5%→-7~-12%）胜率升（31.6%→47.4%）但 avg 仍深负（最好 -2.05%、netPL≤0.36），因为止损簇亏损大头是隔夜跳空穿透（open<任意止损价，gap 成交），止损距离几乎无助；唯一 CI 显著的 -12% 档 max_loss -12.19% 突破 champion 风险边界且贴 -15% GAP_CAP。②regime 分解显示该 divergence 是 regime 成分效应：弱势 10cm -2.55% vs 20cm +3.67%（同一 regime 两板反向），结构性强势两板同亏（-4.51%/-5.75%，毒区）。
- 永久规则：看到板块/二元收益分化，先做 regime 分解 + 出场结构分解，确认是「同 regime 下的执行差异」再谈板块级 exit 改动；跳空主导的损失不能用止损距离修复。10cm 弱势的亏损是 regime-entry 问题（追高/regime 闸门已 archived），不是 exit 问题。
- 禁止做法：对 10cm 单独加风险闸门/降权/改止损宽度（反事实已证伪）；对 20cm 加闸门（self_check 护栏）。
- 复现脚本：`backend/scripts/si_board_stop_counterfactual.py`（official forward scope + 因果引擎重放 + 按 trade_date 的 paired cluster bootstrap）。
- 吸收位置：主提示词“选择唯一动作”。
