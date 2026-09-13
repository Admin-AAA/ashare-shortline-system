# 项目教训注册表

本文件是项目中可跨轮复用、会改变后续决策的教训唯一事实源。单次运行状态、临时样本数字和普通实验结果写入结构化运行记录，不写入本文件。

可靠性不是由 `active`/`encoded` 状态自动保证；当前代码、测试、数据库与历史证据的批量核验见 [`VALIDATION.md`](./VALIDATION.md)。

## 状态含义

- `active`：仍需在研究或实现时显式约束。
- `encoded`：已有代码、测试、护栏或操作手册承载；本条保留上位原则。
- `superseded`：已被更准确的教训替代，保留用于历史追溯。

## 迁移说明

本文件由项目外的历史 self-improve 教训快照迁入。历史快照有重复的 `L-045`～`L-050` 编号；迁移时按出现顺序重新分配为唯一的 `L-001`～`L-057`，每条保留“原始编号”字段。后续只能使用本文件的 canonical 编号，新条目追加到文件末尾。

2026-08-15 可靠性核验后，L-020、L-026、L-027、L-029、L-031、L-034、L-035、L-036、L-055、L-056 因证据不合格、无法同口径复现或被当前数据反驳而从注册表删除。上述编号永久退役，不得复用；删除原因见 [`VALIDATION.md`](./VALIDATION.md)，原文仅通过 Git 历史追溯。

## L-001 回测与 LIVE 口径污染

- 原始编号：L-001

- 状态：active
- 适用：所有业绩报告和晋级判定
- 证据：历史上曾将 backtest `n=39` 的结果写成 LIVE formal `n=328`。
- 永久规则：每组指标必须携带 `source`、`as_of`、代码版本、样本范围和成本模型；不同 source 禁止计算差值。
- 吸收位置：主提示词“选择证据路径”“验证与决策”。

## L-002 因果口径变化会使旧 champion 失效

- 原始编号：L-002

- 状态：active
- 适用：成交时序、复权、费用、去重、统计范围和未来函数修复
- 证据：历史成交与 outcome 真实性修复后，旧固定胜率和盈亏比仍被用于比较，形成污染基线。
- 永久规则：因果或统计口径变化后，先同代码重放未修改 production，再比较 challenger；重放前不得报告差值。
- 吸收位置：主提示词“真实性硬门禁”。

## L-003 指数强势不等于短线赚钱效应强

- 原始编号：L-003

- 状态：active
- 适用：regime 构造及分 regime 回测
- 证据：存在沪深 300 趋势强、但题材 breadth 弱且短线胜率低于 35% 的窗口；单日 breadth 脉冲也会误导。
- 永久规则：regime 必须由指数趋势与个股 breadth 的版本化公共实现共同决定；中性和未知状态单列。
- 禁止做法：复用已知有单维度缺陷的历史快照标签，或在单轮提示词中临时发明阈值。
- 吸收位置：主提示词“选择证据路径”。

## L-004 单日簇和稀疏特征会制造虚假因子

- 原始编号：L-004

- 状态：encoded
- 适用：因子相关性、亏损簇和参数诊断
- 证据：小样本 point-biserial、单日 regime 冲击及分母接近零曾被误判为 alpha 或 alpha 衰减。
- 永久规则：照常报告跨交易日稳定性、特征覆盖率、基础相关绝对值和 regime 聚集；这些数字不得形成额外发布门槛。发布统一服从 L-010 的整体改善与串行 champion 规则。
- 自动护栏：`scripts/feature_signal_guard.py`、alpha decay 诊断及相关测试。
- 吸收位置：主提示词“选择唯一动作”。

## L-005 回测数据深度和幸存者偏差必须显式披露

- 原始编号：L-005

- 状态：active
- 适用：历史回测晋级
- 证据：早期 `daily_kbars` 仅覆盖少量股票，且当前 universe 缺少部分历史退市或无数据标的。
- 永久规则：报告历史应有 universe 的缺口比例；该数字不得形成风险提示或生产发布门槛。
- 吸收位置：主提示词“选择证据路径”。

## L-006 无输入变化必须切换证据路径

- 原始编号：L-006

- 状态：active
- 适用：定时自我优化
- 证据：OOS 固定在 `n=27/30` 时，凌晨连续产生十余次完全相同的等待或“否证”记录。
- 永久规则：代码和 LIVE 水位未变化时，不重复查询同一批 LIVE outcome；立即切换到历史回测、未测试 OOS 窗口、测量修复或新策略研究。LIVE 的 `blocked_until` 只约束 LIVE 路径，不得阻断整轮收益优化。
- 纠错记录：2026-07-30 曾把本规则错误实现为全局 `NO_NEW_EVIDENCE` 短路，导致历史回测也被跳过；该实现已撤销。
- 吸收位置：主提示词“PRECHECK”。

## L-007 调度诊断必须先校验时间窗和时区

- 原始编号：L-007

- 状态：active
- 适用：scheduler、数据新鲜度和 outcome 结算诊断
- 证据：北京时间凌晨把尚未到触发点的 cron 误判为失活；数据库 UTC 时间也曾被当成本地时间读取。
- 永久规则：诊断前同时记录当前本地时间、调度时区、计划触发时间、数据库存储时区和下一有效数据事件。
- 吸收位置：主提示词“PRECHECK”的 `blocked_until/next_event`。

## L-008 worktree 不能隔离共享实验状态

- 原始编号：L-008

- 状态：active
- 适用：并发 self-improve
- 证据：多个实例虽使用独立 worktree，仍共享 production MySQL、champion 和 Markdown 日志，产生重复轮号、乱序记录及基线竞争。
- 永久规则：默认只允许一个 self-improve 实例；运行记录按 `run_id` 结构化写入。并行只允许明确证明互不共享数据、基线和发布路径的只读任务。
- 自动护栏：`backend/scripts/self_improve_state.py` fcntl 锁串行化 claim/publish + `setup-worktree`/`release-worktree` 每窗口隔离 checkout + `with-publish-lock` 串行化并发 merge。

## L-009 验证窗口记录不构成发布门槛

- 原始编号：L-009

- 状态：active
- 适用：所有 challenger 研究
- 证据：同一批 outcome 上连续更换阈值会把 holdout 变成训练集，并产生选择性汇报。
- 永久规则：主要变体和用过的窗口全部登记，避免重复做完全相同的实验；holdout 数量、窗口数量、单日聚集、样本量和显著性不构成额外门槛，发布统一服从 L-010 的整体改善规则。
- 吸收位置：主提示词“统计协议”“验证与决策”。

## L-010 整体优于 production 的候选允许直接发布

- 原始编号：L-010

- 状态：active
- 适用：回测或 LIVE 验证通过后的发布
- 旧规则：回测或 LIVE 配对验证只能晋级 shadow，满足额外样本门槛后才能 production。
- 用户决策（2026-07-30）：取消 shadow、OBSERVE、`n>=100`、多窗口、CI 显著性和覆盖率等发布门槛；样本量、CI、覆盖率和窗口数只报告数字，不得附加劝退或风险提示。
- 用户决策（2026-07-31）：生产发布条件从"两个收益指标都必须改善 + 三项辅助全不退化"放宽为**允许收益↔胜率/PF 权衡**——主收益至少一项改善；某收益指标小幅下降（≤ 1pp）仅在净胜率提升 ≥ 3pp 或 PF 提升 ≥ 0.1 时允许；辅助三项至少一项改善且至多一项退化。逻辑见 `challenger_decision.evaluate_challenger()`（唯一判定器）。
- 整体改善定义：见 `challenger_decision.py`——至少一个收益指标改善；收益下降被显著胜率/PF 提升补偿且 ≤ 1pp；辅助至多一项退化。仅允许 `0.01` 的数值精度容差。
- 串行规则：每发布一个候选立即生成新 champion；其他相对旧 champion 的候选必须重新验证，禁止直接叠加。
- 永久保留：真实性硬门禁、可复现差分、回归测试、回滚点和发布后 LIVE 监控。
- 吸收位置：主提示词“生产发布条件” + `challenger_decision.py`（唯一判定器）。

## L-011 板块拆分 divergence ≠ 板块级 exit 缺陷

- 原始编号：L-011

- 状态：active
- 适用：看到 20cm vs 10cm（或任二元切分）D 项收益大幅分化时
- 证据：2026-07-30 official forward clean≥72（n=69）显示 10cm avg -2.85%/netPL 0.21 vs 20cm +1.98%/netPL 2.13。但反事实证伪两条 exit 侧解释：①只放宽 10cm 固定止损（-5.5%→-7~-12%）胜率升（31.6%→47.4%）但 avg 仍深负（最好 -2.05%、netPL≤0.36），因为止损簇亏损大头是隔夜跳空穿透（open<任意止损价，gap 成交），止损距离几乎无助；唯一 CI 显著的 -12% 档 max_loss -12.19% 突破 champion 风险边界且贴 -15% GAP_CAP。②regime 分解显示该 divergence 是 regime 成分效应：弱势 10cm -2.55% vs 20cm +3.67%（同一 regime 两板反向），结构性强势两板同亏（-4.51%/-5.75%，毒区）。
- 永久规则：看到板块/二元收益分化，先做 regime 分解 + 出场结构分解，确认是「同 regime 下的执行差异」再谈板块级 exit 改动；跳空主导的损失不能用止损距离修复。10cm 弱势的亏损是 regime-entry 问题（追高/regime 闸门已 archived），不是 exit 问题。
- 禁止做法：对 10cm 单独加风险闸门/降权/改止损宽度（反事实已证伪）；对 20cm 加闸门（self_check 护栏）。
- 复现脚本：`backend/scripts/si_board_stop_counterfactual.py`（official forward scope + 因果引擎重放 + 按 trade_date 的 paired cluster bootstrap）。
- 吸收位置：主提示词“选择唯一动作”。

## L-012 D 项 champion 查询必须套用 official sample scope

- 原始编号：L-012

- 状态：active
- 适用：任何生成/引用 D 项 champion 基线（clean≥72 等）的查询或报告
- 证据：2026-07-30 发现 `scripts/self_check.py` 的 `_print_low_score_line_pollution` 查询只过滤 `run_mode='formal'` + `outcome_label`，漏掉 `OFFICIAL_SAMPLE_SQL` 全套口径（live_forward/completed/data_as_of<=td/source_snapshot_id/生成日==交易日），把 `historical_replay` 等非前瞻 formal outcome 计入 clean≥72 总体，使 champion 净胜率由真值 ~48.6% 虚高到 58.6%（193 笔非前瞻被排除后回到 48.6%）。整轮 Δ 比较此前建立在错误基线上。
- 永久规则：D 项 champion / clean 口径查询必须与 `_fetch_dedup_live_formal` 同口径——内联 `OFFICIAL_SAMPLE_SQL`，不得只按 `run_mode='formal'` 过滤；`historical_replay` scope 的 formal outcome 一律不计入 live-forward 基线。任何"champion 待重放"提示解除前，先确认查询已套 scope 且存储 outcome 与当前代码重放零翻转。
- 修复/护栏：`LIVE_FORMAL_CLEAN_POLLUTION_SQL`（f-string 内联 OFFICIAL_SAMPLE_SQL）+ `backend/tests/test_self_check_scope_guard_champion.py`（断言查询含全套口径子句，防回归）。
- 真实基线（修正后，北极星目标 WR62%/PF2.0）：clean≥72 scoped 净WR48.6%/PF1.04；rec_id 口径 WR49.2%/PF0.90；均低于目标。
- 吸收位置：主提示词“真实性硬门禁”“验证与决策”。

## L-013 regime 归因只能读取决策时持久化标签

- 原始编号：L-013

- 状态：active
- 适用：任何依赖 regime 的复盘、硬否决重验、regime χ² 分桶、最差桶归因
- 证据：2026-07-30 发现 aggregator 硬门槛惩罚（结构性强势 +4 / 强势 +8）基于决策时 `_snapshot_regime`，但 `score_line_meta` 此前从不持久化该标签，也不持久化 `regime_strong_penalty`。下游若读 `feature_snapshot_json.market_regime`，拿到的是 `feature_builder` 按 trade_date **事后重算**的回溯标签——与决策时实际施加的门槛不一致。14:25 实例据此误判 3 笔为"realness bug"，实则为标签回溯；13:18 的 climax/osc 硬否决子集切分、self_check regime χ²/最差桶均被污染。
- 永久规则：优先读取 `score_line_meta.decision_regime`（=决策时实际使用的 `_snapshot_regime`）。旧记录可回退到每笔推荐的 `market_context_json`：持久化代码明确保存 `card["market_snapshot"]`，而 `card` 与 `apply_pool_rules` 收到同一个 snapshot。禁止读取 `feature_snapshot_json.market_regime`，因为 feature 重建可能事后改变它；也禁止按 trade_date 重新计算标签冒充决策时值。`regime_strong_penalty` 仍从 `score_line_meta` 读取。
- 修复/护栏：`aggregator.apply_pool_rules` 现持久化 `decision_regime` + `regime_strong_penalty` 进 `score_line_meta`；`backend/tests/test_aggregator_decision_regime.py`（4 例锁定持久化/自洽/不回溯）。
- 状态补充（2026-07-30）：新记录由 `score_line_meta.decision_regime` 直接锁定；历史记录虽缺该字段，但逐笔 `market_context_json` 可作为同一决策时 snapshot 的兼容证据，无需危险回填。
- 吸收位置：主提示词“选择证据路径”“验证与决策”。

## L-014 资金流因果回测必须用 strictly-prior-day 快照

- 原始编号：L-014

- 状态：active
- 适用：任何用 `capital_flow` 表做 champion/challenger 配对回测、因子研究或 entry 过滤
- 证据：2026-07-30 诚实配对测试发现，系统自带 `capital_flow_factor_evidence.DEDUP_LIVE_FORMAL_CAPITAL_FLOW_SQL` 用 `cf.trade_date = r.trade_date`（T-day 快照）做 join。但 production formal run 有盘前 cron（08:30 `formal_run_safety_net`）也会生成 trade_date=T 的推荐，盘前决策时 T-day 资金流尚未发布 → 该 join 对盘前 run **含未来函数**。同一数据集上，T-day 快照口径给出反向（mean_diff −2.48%, CI 跨 0）结果，而 strictly-prior-day（trade_date 严格早于信号日 T 的最大可得日）快照口径方向与探索窗口一致且零未来函数。
- 永久规则：资金流因子因果回测一律取 **strictly-prior-day** 资金流快照（推荐信号日 T 之前最近已发布交易日），不得用 T-day 快照做主判定；T-day 快照仅作敏感性对照并明确标注未来函数风险。所有资金流 entry 过滤只用决策时可得的前置快照。
- 复现状态：一次性实验脚本已随基础设施切换归档；结构化运行记录保留 prior-day 主判定与 T-day 敏感性对照。
- 实证（2026-07-30 si20260730-1957，clean>=72 官方重放 n=78）：honest strictly-prior-day 口径下 capital_flow 逆向硬否决**不复制**——被否决子集 n=45 WR46.7%/PF0.66≈champion(WR46.2%/PF0.78)，非毒桶；challenger(剔该子集) n=33 WR45.5%(**反降**)/avg_net-0.103%/PF0.96/max_loss-7.41%，胜率不升、覆盖-57.7%(78→33)；paired CI[-2.769,1.468] lo 远<0。self_check 探索窗口「3日累计净流入 流入>0 WR44.3% vs ≤0 WR59.4% (Δ−15.1pp)」系 **T-day 快照(含未来函数)伪影**——T-day 敏感性对照 mean_diff=−1.788(负)、crosses 0。结论：clean>=72 总体中 prior-day 资金流逆向信号无干净 edge，该方向归档，勿据此加 entry 规则。
- 吸收位置：主提示词“真实性硬门禁”“选择证据路径”。

## L-015 主动弃权型组合级测量（仅旧三级门槛已废止）

- 原始编号：L-015

- 状态：active
- 适用：主动减少覆盖率的 challenger 组合级测量
- 来源：用户 2026-07-30 19:xx 反馈——原提示词把 shadow 与 production 门槛混得太近，导致大量"证据不足无法晋级"实为有价值但未确认的候选被与普通 OBSERVE 一起搁置。
- 旧三级门槛已由用户废止：不再使用 research/shadow/production 三层样本门槛，也不再以 n、窗口或 CI 阻止发布。
- **主动弃权型策略必须组合级测量**：challenger 不交易某子池时，按交易日构造组合收益，把 abstain 当现金（当日收益=0），同时报告资金利用率、月均交易数、组合最大回撤、机会成本。不得只比较保留下来的交易。
- **覆盖损失不自动判坏**：关键看释放资金如何处理——闲置现金则比组合年化收益与资金利用率；可投入低相关策略则可能提高组合效率；若要求每日最低候选数则设最低推荐数。本例 20cm-only 覆盖−57.7% 实为机制（避开 10cm-only 弃权日中 champion 净亏 −12.75pp），是收益来源而非成本。
- **机会成本符号**：champion 在 challenger 弃权日的净收益为负=空仓避损（净收益）；为正=真正放弃的收益。勿把 abstain 日的"未交易"一律当机会成本。
- 与 L-010 关系：只保留本条的组合级测量方法；最终发布完全服从 L-010 的整体改善与串行 champion 规则。
- 复现脚本：`backend/scripts/si_sequential_champion_replay.py`（exit-date 对齐组合曲线 + abstain=现金 + 统一整体改善判定）。
- 吸收位置：主提示词“生产发布条件”“串行 champion 规则”。

## L-016 trailing 候选必须相对当前入场 champion 串行重放

- 原始编号：L-016

- 状态：active
- 适用：任何退出、止损、止盈或 trailing 候选
- 证据：trailing 0.90 曾在全板旧 champion 上通过并被另一分支记为 production；但本地 `main` 已先发布 20cm-only，而退出候选仍把 10cm 历史交易算入。2026-07-30 按真实发布顺序重放 20cm population 后，0.50→0.90 的平均净收益 1.5130%→1.4458%、PF 1.7584→1.7246%，均退化；累计净收益 -2.4014%→-1.4112% 改善，胜率和 MDD 持平，故不满足 L-010，判定 FALSIFIED。
- 永久规则：退出候选先套用当前 production 的全部入场、过滤和仓位规则，再在同一最终 population 上比较；旧 champion 的实验结果不得跨生产版本复用。
- 当前生产：20cm-only + 结构性强势 hard veto + trailing profit_floor 0.50；0.90 不得视为已发布或待自动合并。
- 复现脚本：`backend/scripts/si_sequential_champion_replay.py`。

## L-017 发布状态必须由 Git 与生产配置共同证明

- 原始编号：L-017

- 状态：active
- 适用：champion 发现、候选串行排序、状态恢复与发布
- 证据：20cm-only、结构性强势 veto、trailing 0.90 分散在互不包含的分支，但共享状态文件曾把三者同时登记为 published，形成不存在的组合 champion。
- 永久规则：`published_candidates` 只是缓存，不是事实来源。每轮必须校验 production commit 的祖先关系与实际配置值；不在同一 `main` 历史且未出现在生产配置中的候选必须标为 invalidated 或 falsified。
- 发布规则：一个候选合入后立即重放剩余候选；严禁根据多个候选分别优于旧 champion 推断组合后仍优。
- 自动护栏：`challenger_decision.evaluate_challenger`、`si_sequential_champion_replay.py` 和原子状态写入器。

## L-018 formal 重放不能代替策略信号回测

- 原始编号：L-018

- 状态：active
- 适用：所有参数优化、新策略研究、champion/challenger 对比
- 证据：self-improve 第 31–52 轮所有网格搜索都在 live_forward formal 重放（路径 B）上进行，窗口仅 15 个交易日/78 笔交易/2026-06-03~2026-07-29。参数网格（止损/止盈/trailing/horizon/策略过滤）在这么小的单窗口上被扫到局部最优，然后所有后续轮次宣告 `ALL-GRIDS-EXHAUSTED`。本地 daily_kbars 实际覆盖 2021-05-26~2026-07-30 共 1257 个交易日 5603 只股票，但从未被用于策略信号回测。
- 永久规则：参数优化和新策略研究的缺省证据路径必须是**策略信号回测**（路径 A）——在全历史上重建每个交易日候选集，用生产级逻辑模拟完整 pipeline。路径 B（formal 重放）仅用于 outcome 一致性检查和 LIVE 同场配对验证。禁止在路径 B 的单窗口小样本上扫参数网格然后宣告穷举。
- 证据口径：local daily_kbars 1257 个交易日覆盖 2021-2026 五种不同 regime 阶段，足以提供独立窗口验证。
- 吸收位置：主提示词“选择证据路径”。

## L-019 小样本 champion 不能宣告局部最优

- 原始编号：L-019

- 状态：encoded
- 适用：所有 champion 发布后状态维护和后续轮次选题
- 证据：bf509b8 champion n=14（20cm-only + no-DS 过滤后）来自同一样本的两步修剪，WR71.43%/avg_net5.84% 是幸存者偏差，不是策略 alpha。但提示词没有 champion 有效性门槛，网格穷举后系统只能输出 `ALL-GRIDS-EXHAUSTED → AWAIT LIVE OOS`，形成死锁。
- 永久规则：
  1. Champion n < 30：禁止宣告 `ALL-GRIDS-EXHAUSTED` 或等价结论；必须继续在新数据窗口验证或转向独立研究。
  2. Champion n < 50：状态文件必须标注 `provisional: true`，下一轮优先寻找异窗口证据。
  3. 仅当 champion 在至少两个独立不重叠的时间窗口上都表现为正期望（各窗口独立计算 avg_net>0/PF>1.0），才允许宣告“champion 已稳定”。
  4. 同一（champion 参数，回测窗口）组合上，每类参数的网格扫描只能做一次；做完后记录到 `scanned_grids`。连续两轮在同一组合上扫不同参数 = 自动切换方向。
- 自动护栏：`self_improve_state.publish_champion` 拒绝在小样本（n<50）上清除 `provisional` 标志（`SMALL_SAMPLE_PROVISIONAL_GUARD`）。
- 吸收位置：主提示词“Champion 有效性门槛”和“选择证据路径”。

## L-021 单指标大幅改善≠可发布：辅助双退化门禁实例

- 原始编号：L-021

- 状态：active
- 适用：任何候选发布判定，尤其网格扫描中「累计收益变化显著」的候选
- 证据：2026-08-15 当前 Path-A 重放中，2025-H1 单笔均值 +0.1247%、PF 1.0609，但累计收益 -40.88%、MDD -51.60%；2026-H1 单笔均值 +0.0047%、PF 1.0021，但累计收益 -61.58%、MDD -63.11%。单笔质量、链式累计收益和组合回撤可以方向相反，不能用一个指标代替整体判定。
- 永久规则：判定发布时把累计净收益看作「分布形状的副产品」而非独立信号。avg_net（单笔均值）才反映每笔质量；累计收益改善而 avg_net 持平/微变 + 辅助退化时，优先怀疑收益时间分布改变而非质量提升。真正可发布的候选应在 avg_net 和累计收益上同时体现改善，且辅助退化 ≤ 1 项。
- 吸收位置：主提示词「生产发布条件」。

## L-022 publish 状态补丁必须深合并累加型字段

- 原始编号：L-022

- 状态：encoded（已修 + 回归测试）
- 适用：所有 publish-champion / publish-finding / commit-update 调用方
- 证据：`state.update(state_patch)` 顶层浅替换。publish-finding 传 `{"scanned_grids":{"profit_floor_ratio...":{...}}}` 时，整个 `scanned_grids` 被替换成只含新键的对象，把已完成的 `entry_gap_filter_on_current_champion` 扫描记录一起丢掉。方向池 `_list_unscanned_grid_params` 于是重新分配已 FALSIFIED 的 entry_gap 方向，浪费一轮。类似风险覆盖 `archived_falsified_directions`、`published_findings` 等累加型 list。
- 永久规则：任何通过 state_patch 写入的累加型字段必须用深合并：`scanned_grids` dict merge、`archived_falsified_directions`/`published_findings` list union；`canonical_production`/`researcher_registry` 等幂等顶层键仍可整体替换。已固化为 `_apply_state_patch`（三处调用点统一替换）+ 2 个回归测试（test_state_patch_deep_merges_scanned_grids_and_unions_lists / test_publish_champion_patch_deep_merges_scanned_grids）。
- 执行陷阱：**改完 self_improve_state.py 后，恢复/写入状态文件必须用 worktree 里修复后的脚本**（`cd <worktree>/backend && <venv>/python scripts/self_improve_state.py ...`），否则主仓库旧脚本会再次浅替换把刚恢复的记录又盖掉。合并回 main 后主仓库脚本才更新。
- 吸收位置：主提示词「网格参数扫描的去重与防过拟合护栏」+ OPERATIONS.md 状态文件字段说明。

## L-023 path A 无法用 sr.score 复现生产仓位语义（三重脱节）

- 原始编号：L-023

- 状态：active
- 适用：任何在 path A 上建模 position_sizing / score_bucket 的尝试
- 证据：给 path A 加 `score_bucket_85_halve`（`sr.score>=85 → 0.5`）后，96% 交易被减半（1385/1441），而生产 `_resolve_position_size` 用 `final_score`（聚合加权分）>=85 仅占 17.5%（46/263）。三重脱节：
  1. **score 量纲**：path A `sr.score` 是策略原始分（mean 90），生产 final_score 是多组件聚合分（mean 79.4）。生产 `chao-gu-yang-jia` skill score=72 → final_score=72；`trend-pullback-continuation` skill 85 → final 76.24。聚合后系统性打折。
  2. **cap 缺失**：生产 800 笔已触发交易 61%（488）仓位由 `market_discipline.max_position_pct` 决定（15/20/30/35/50/55%），path A 引擎无 cap 概念。cap 逐日变化（同一天 15%+20% 混合）。
  3. **champion 声明脱节**：3c129ba 标榜 `score_bucket_85_halve`，但 LIVE 中 final_score>=85 仅 44/801，score-bucket 分支几乎不触发，真实仓位策略=等权×cap。
- 永久规则：**在 path A 上建模生产仓位前必须先校准 score 量纲或加 cap 建模**。`_resolve_position_size` 的唯一真相源是 `Recommendation.final_score`（聚合加权分），不是策略原始分。要用 path A 验证仓位，需：给 path A 加 `max_position_pct` cap（逐日 market_discipline），或先建立 sr.score→final_score 的映射（可参考生产 skill_breakdown_json 的聚合权重）。
- 回滚记录：错误的 score_bucket 建模（用 sr.score 触发）已从 path A 回退，path A 恢复等权口径（与生产「等权×cap」最接近的可复现近似）。
- 吸收位置：主提示词「真实性硬门禁」+ OPERATIONS.md 路径 A 说明。

## L-024 闸门放行但发布会触发大面积数据重算时，判定 FINDING 而非机械 PRODUCTION

- 原始编号：L-024

- 状态：active
- 适用：候选通过 evaluate_challenger 但生产改动面波及历史数据一致性
- 证据：horizon=7 在 path A 全历史（n=3611）通过 evaluate_challenger（avg +0.025pp、cum +9.05pp、WR +0.78pp、PF +0.012，仅 MDD -3.77pp 退化），但：(1) 所有 paired bootstrap CI 下界为负（全窗口 [-0.006,+0.040]），avg 改善微小且不排除 0；(2) 三窗口 avg 改善仅 +0.030/+0.050/+0.002pp，cum +9pp 由 2025_H2 单窗口 +24.88pp 极端值驱动（L-021 模式）；(3) MDD 在弱势窗口（2026_H1，当前环境）退化 -2.78pp；(4) **生产 horizon_days 默认 5 散落 7+ 处**（outcome_service/scheduler_service/workbench_service/runs/outcomes/stock_matrix/tables），改 5→7 会触发 `recompute_stale_outcomes`（用 horizon 参数）重算全部历史 outcome，污染已结算数据。
- 永久规则：`evaluate_challenger` 是唯一发布闸门，但它回答「候选是否优于当前生产」，不回答「发布成本是否可接受」。当候选改善微弱（avg 改善 < MDD 退化幅度）、CI 下界为负、且发布需改 7+ 处生产默认值触发历史数据重算时，判定 FINDING 记录结果而非机械发布。第一性原理：发布 h7 的真实风险（历史 outcome 污染 + 弱势窗口 MDD 恶化）大于其微弱收益。
- 与 L-021 交叉：cum 大幅改善 + avg 微改善 + 单窗口极端值 = 分布形状改变，不是可执行质量提升。
- 吸收位置：主提示词「生产发布条件」+「串行 champion 规则」。

## L-025 网格脚本 champion 口径必须用生产 _resolve_position_size，非 DB position_size

- 原始编号：L-025

- 状态：encoded（已修 + 合并 main）
- 适用：任何比较仓位策略的脚本/网格
- 证据：si_position_sizing_grid.py 原用 DB `position_size`（历史 cap-based 结算值）当 champion，误判 `score_bucket_3tier` 为 PRODUCTION（五项全改善）。实际上生产 champion 3c129ba 是 `score_bucket_85_halve`（`_resolve_position_size` 用 final_score>=85→0.5），DB 里 score>=85 的 18 笔中仅 1 笔是 0.5（其余 15 笔 0.2 是 cap 旧值）。用生产语义重放 70 笔 avg=1.406%/PF=1.895/MDD=-10.03%，与 champion 状态 metrics 完全一致。修正 champion 后全部 5 个 challenger（uniform/score_linear x2/score_bucket_3tier/score_bucket_90_quarter）相对正确 champion 全 FALSIFIED。
- 永久规则：**比较仓位策略时，champion 必须用生产 `_resolve_position_size` 的当前语义（final_score>=85→0.5）重放，不能读 DB 的 position_size**——DB 存的是历史结算时的旧逻辑值，不代表当前生产。配套 MDD 用 exit_date 聚合的真实组合资金曲线，不用 min(weighted_rets)（单笔最小，会误判回撤方向）。
- 与 L-023 交叉：L-023 说 path A 无法复现生产仓位语义（sr.score≠final_score）；本课是说**路径 B（formal 重放）里必须用生产语义重放 position_size**，DB 旧值不可信。
- 吸收位置：主提示词「真实性硬门禁」+ OPERATIONS.md 路径 B 说明。

## L-028 方向池去重必须用主题词匹配扫描键，非参数名子串

- 原始编号：L-028

- 状态：encoded（已修 + 回归测试 + 合并 main）
- 适用：方向池分配、网格去重、已 FALSIFIED 方向归档
- 证据：`_list_unscanned_grid_params` 用参数列表名（`score_line_threshold`、`stop_loss`）做子串匹配 scanned_keys，但历史扫描键用主题词命名（`score_line_on_2025_2026`、`stop_loss_on_2025_2026`）。`"score_line_threshold" in "score_line_on_2025_2026"` → False，导致旧 score-line 扫描记录未被识别，下轮又 claim 到 grid_scan:score_line_threshold。同理 take_profit/trailing_activate/trailing_callback/trailing_min_lock/strategy_filter 全部无匹配，会被重复扫描。
- 永久规则：scanned_grids 记录键必须用**主题词**命名（score_line_on_...、stop_loss_on_...、horizon_on_...），`_GRID_PARAM_TOPICS` 映射参数名→主题词列表，任一主题词命中扫描键即视为已扫。发布 FINDING 时检查记录键是否会被方向池误判为未扫描。
- 修复：`_GRID_PARAM_TOPICS` + 回归测试 `test_claim_does_not_reallocate_scanned_grid_direction`。修复后剩余未扫描参数：take_profit / trailing_activate / trailing_callback / trailing_min_lock / strategy_filter。
- 吸收位置：主提示词「网格参数扫描的去重与防过拟合护栏」+ OPERATIONS.md。

## L-030 peak_callback trailing 相对生产 profit_floor 不发布（策略切换非调参）

- 原始编号：L-030

- 状态：active
- 适用：trailing 家族后续选题；path A 现在支持 trailing_callback 参数化（合并 main 6e14812）
- 证据：path A 全历史 2025_2026（n=3611）扫 peak_callback：cb1.0 通过 evaluate_challenger（avg +0.023pp、cum +1.36pp、WR +0.28pp，仅 MDD -2.89pp 退化）但判定 FINDING 不发布——(1) 策略切换（生产 profit_floor → legacy peak_callback）非参数调优；(2) avg 改善 +0.023pp 远小于 MDD 退化 -2.89pp（125 倍）；(3) trailing_stop_gap 从 champion 17.3%→26.3% 暴增（更宽回调让更多亏损以跳空放大）；(4) callback 家族已在 [[trailing-callback-falsified-2026-07]] 反事实否证。cb2.0 FALSIFIED（WR -1.58pp）。regime：cb1.0 弱势+0.043/强势+0.034/震荡-0.021。
- 永久规则：**候选跨 trailing 策略家族（profit_floor↔peak_callback）时，即使通过 evaluate_challenger 也判 FINDING 不发布**——策略切换改变退出语义，avg 微改善 + MDD 显著退化 + 出场分布剧变（gap 暴增）= 亏损尾部放大，非质量提升。生产 profit_floor 0.50 保持最优。
- 测量工具：`_trailing_stop_return`/`_resolve_exit_causal` 支持 `trailing_callback` 参数化（向后兼容默认全局常量），path A 可扫 callback 网格。与并行研究员 `activate_pct` 参数共存（纯机械合并）。
- 吸收位置：主提示词「串行 champion 规则」+「生产发布条件」。

## L-032 因子方向去重必须匹配 new_strategy:<factor> 前缀，非 "factor" 子串

- 原始编号：L-032

- 状态：encoded（已修 + 回归测试 + 合并 main）
- 适用：方向池分配、已否证因子归档
- 证据：`_list_unscanned_factor_directions` 的 falsified 判断用 `"factor" in d or "因子" in d` 子串匹配 archived，但 archived 条目是 `new_strategy:capital_flow_momentum:做多资金流入被否证(787笔已结算)...`（含 momentum 不含 factor/因子），不被匹配 → 已否证的资金流因子被方向池反复分配。与 L-028 同类（参数名/记录键命名不一致导致去重失效）。
- 永久规则：**归档已否证方向时，字符串必须以 `new_strategy:<factor>:` 前缀命名**；方向池用前缀解析已否证集合，不用松散子串。发布 FINDING 归档时检查前缀是否会被方向池误判。
- 修复：`new_strategy:` 前缀解析 + 回归测试 `test_claim_does_not_reallocate_falsified_factor_direction`。
- 吸收位置：主提示词「网格参数扫描的去重与防过拟合护栏」+ OPERATIONS.md。

## L-033 path A 因子公式必须与生产 feature_builder 逐一对齐

- 原始编号：L-033

- 状态：encoded（已修 + 合并 main b67bc81）
- 适用：任何 path A 重建的因子/特征，尤其与生产评分/过滤耦合的
- 证据：path A 用 `100 - (chip_85-chip_15)/close*100` 算 chip_concentration（值域 0-100，大=集中），生产 feature_builder 用 `(chip_85-chip_15)/chip_15*100`（concentration_90，值域 0~∞，小=集中）。两公式方向相反，path A 策略层 `chip_concentration>=20` 几乎恒真失真（60-100 都满足），chip 评分条件实际不筛选。修复后 20cm 池 chip 分布从 60-100 高集中 → mean 18.4（67%<20），与生产推荐 chip=18 完全一致；champion 基线交易数 3611→3553。
- 永久规则：**path A 重建因子时，公式必须与生产 `feature_builder.py` 逐一对齐**——先 grep 生产对应字段的计算，再写 path A 版本；方向相反/量纲不同的公式会让策略层条件静默失真（不报错但结果全错）。si20260731-009 的「20cm 池 chip 无区分力」结论虽巧合一致，但基于错误公式，不可作为依据。
- 吸收位置：主提示词「回测合规要求」+「真实性硬门禁」。

## L-037 2024 及之前不可作 20cm OOS 窗口（数据深度不足）

- 原始编号：L-037

- 状态：active
- 适用：OOS 窗口选择、方向池 oos_validation 方向
- 证据：daily_kbars 覆盖：2021-2023 仅 5 只、2024 仅 300 只且 **0 只 20cm**、2025 起才有 5500+ 全市场。生产 champion 是 20cm-only，2024-H1 完全无 20cm 样本，无法作 OOS 窗口。有效 OOS 窗口：2025_H1/H2/2026_H1（已在 path A 三分窗口验证，覆盖强势→震荡→弱势三种 regime）。
- 永久规则：**20cm champion 的 OOS 验证只能用 2025 起的窗口**（2025_H1/H2/2026_H1 已覆盖）。方向池 `_list_unchecked_oos_windows` 列出的 2024-H1/H2/2023 等窗口依赖不足数据，claim 到时应确认数据深度后归档，不硬跑失真回测。**数据回填 2024 及之前全市场是回测深度的硬前置**（需 Tushare daily 批量回填 + backfill_indicators）。
- 吸收位置：主提示词「选择证据路径」+ OPERATIONS.md。

## L-038 OOS 窗口方向池去重必须前缀匹配带后缀原因的归档

- 原始编号：L-038

- 状态：encoded（已修 + 回归测试 + 合并 main）
- 适用：方向池分配、OOS 窗口归档
- 证据：`_allocate_direction` 对 OOS 窗口用 `d not in archived` 精确匹配，但 archived 条目带后缀原因（`oos_validation:2024-H1:2024-H1仅300只且0只20cm`），精确匹配漏判 → 方向池反复分配已归档窗口（L-037 归档后下轮又 claim 到 2024-H1）。与 L-028/L-032 同类（命名不一致导致去重失效）。
- 永久规则：**归档方向时字符串可带后缀原因，但方向池匹配必须用前缀**（`d` 或 `d + ":"` 开头）。发布 FINDING 归档时检查前缀是否会被方向池误判。
- 修复：OOS 窗口分配改前缀匹配 + 回归测试 `test_claim_does_not_reallocate_archived_oos_window`。
- 吸收位置：主提示词「网格参数扫描的去重与防过拟合护栏」+ OPERATIONS.md。

## L-039 weak-cap-relax 新 champion 有单日依赖风险（07-08 占 67%）

- 原始编号：L-039

- 状态：active
- 适用：ddd5ffd weak-cap-relax champion 的后续监控与回滚决策
- 证据：新 champion（ddd5ffd，weak 弱势放宽 cap）LIVE 弱势 regime 43 笔中 07-08 单日占 29 笔（67%）。剔除 07-08 后弱势 n=14 avg 2.84%（仍正）但 **WR 从 62.8% 骤降至 42.9%**（-20pp）——avg 靠少数大赢单而非稳定胜率。07-08 当天 WR 72.4%。发布记录（si-20260801-073828）只报 no_0708_avg=2.725 未报 WR 骤降。
- 永久规则：**发布 PRODUCTION 时若样本高度单日集中（单日占比>50%），必须报告剔除该日后的 WR**，不能只报 avg（avg 靠极端值可掩盖 WR 崩溃）。验证 champion 跨窗口稳定性时，先查按日期分布的单日占比，再算剔除后的 WR/avg 双指标。
- 与 L-004/L-021 交叉：单日极端值放大 avg 是常见陷阱；本课是 champion 层面（非候选）的同类风险。
- 吸收位置：主提示词「Champion 有效性门槛」+「串行 champion 规则」。

## L-040 weak relax 弱势 alpha 近期（07-22 后）转负，衰减信号

- 原始编号：L-040

- 状态：active
- 适用：bb5782e weak-relax champion 的后续监控与收紧决策
- 证据：弱势样本按时间分段——07-08 前(06-03~07-07) 11 笔 avg +3.86% WR 54.5%（强正期望）；07-08 当天 29 笔 avg +2.21% WR 72.4%；**07-08 后(07-22/24) 最新 3 笔 avg -1.22% WR 0.0% 全部亏损**。修正 L-039：弱势 alpha 非纯单日依赖（07-08 前也强），但近期转负是新衰减信号。
- 永久规则：**验证 champion 跨窗口稳定性时，把样本按时间分段看近期趋势**——不能只看整体 avg（可能被早期样本拉高）或只看单日依赖（可能忽略近期衰减）。本课是「整体正期望但近期转负」的衰减模式。
- 与 L-039 交叉：L-039 是单日依赖（07-08 占 67%）；本课是近期转负（07-22 后 WR 0%）——两个独立风险叠加。
- 吸收位置：主提示词「Champion 有效性门槛」+「串行 champion 规则」。

## L-041 亏损簇归因：stop_loss 家族 78% 主导，弱势·活跃是最大重灾区

- 原始编号：L-041

- 状态：active
- 适用：亏损归因、止损/入场侧选题
- 证据：clean>=72 20cm failed n=30 归因——stop_loss 家族(hit+gap+both) 12 笔 -61.5% 占总亏损 78%；最大簇=弱势·活跃 stop_loss_hit 4 笔 -22.8%；次之=震荡·高潮 stop_loss -26.5%；跳空(stop_loss_gap avg -6.59%)主导深亏。时间上 07-07~09 三天集中亏损 -90.7%（占 78%）。与 L-011 的“跳空主导损失”方向一致，但历史止损网格结论已因证据不合格退役。
- 永久规则：不能依据已退役网格宣告“止损侧已穷举”。亏损归因仍应优先定位入场、regime 与跳空结构；改止损/出场前先确认归因，并在当前 scope 下重新做可复现重放。
- 与 L-040 交叉：弱势·活跃止损重灾区与弱势近期衰减（L-040）互为印证——弱势 regime 是当前亏损核心来源。
- 吸收位置：主提示词「亏损归因与策略修复」。

## L-042 07-16 单日系统性崩溃 + 近期全市场转弱（非仅弱势）

- 原始编号：L-042

- 状态：active
- 适用：bb5782e champion 的近期监控；全市场短线环境判断
- 证据：07-16 全量 20 笔全部亏损(avg -5.4%)——偏多·活跃 6 笔 avg -5.68% WR 0%（全 stop_loss_hit 追高后普跌）、震荡·活跃 14 笔 avg -5.42% WR 14%（8 笔 stop_loss_gap 跳空主导）。与 07-08（29 笔）构成第二个单日主导。**07-16 后整体转负是系统性**：非弱势 n=22 avg -5.39% WR 9.1%（比弱势 n=4 avg -2.35% 更差）——非仅弱势 regime 衰减（L-040），是**全市场短线在 07-16 后转弱**。
- 永久规则：**champion 样本高度依赖少数极端单日（07-08/07-16）**；评估近期环境时不能只看弱势 regime（L-040），要看全市场（07-16 后非弱势 WR 9.1% 更差）。当前短线环境整体转弱，champion 的近期表现不佳是环境驱动非策略退化。
- 与 L-040/L-041 交叉：弱势衰减（L-040）+ 弱势止损重灾（L-041）+ 全市场转弱（本课）三层叠加。
- 吸收位置：主提示词「Champion 有效性门槛」+「亏损归因」。

## L-043 20cm 排除负期望策略 chao-gu-yang-jia（PRODUCTION，545a8b9）

- 原始编号：L-043

- 状态：encoded（已发布 champion）
- 适用：20cm 策略过滤、负期望策略识别
- 证据：亏损归因（L-041）发现 chao-gu-yang-jia（炒股养家）在 20cm 是跨日稳定负期望（clean>=72 n=15 WR 33% avg -1.43%，10/15 亏损，跨 9 交易日分散非单日聚集）。剔除后 champion 五项全改善零退化：avg +1.413%→+2.039%（+0.63pp）、cum +98.9%→+112.2%、WR 57.1%→63.6%、PF 2.19→2.93、MDD -8.3%→-6.2%，evaluate_challenger PRODUCTION。改动：`EXCLUDED_20CM_BUCKETS` 加 chao-gu-yang-jia-skill（只滤 20cm 保留 10cm，与 DSA 同模式）。
- 永久规则：**负期望策略的识别信号 = 跨日稳定 WR<40% + avg 显著为负 + 非单日聚集**。识别后按 DSA 模式加入 `EXCLUDED_20CM_BUCKETS`（20cm 过滤保留 10cm）。新 champion n=55（原 n=70 剔 15 笔），回滚点 bb5782e。
- 与 L-041 交叉：归因（L-041）→ 策略修复（本课）闭环。
- 吸收位置：主提示词「亏损归因与策略修复」。

## L-044 盘后流水线卡死在逐股票 anns_d 补数（阻塞前瞻样本）

- 原始编号：L-044

- 状态：active
- 适用：盘后流水线、news 辅助数据、前瞻样本积累
- 证据：self_check 诊断 post_sync_formal_recommendation 卡死。根因链：`_refresh_auxiliary_dataset(news_announcement)` 抛 RuntimeError（事件契约拒绝 unknown schema）→ fallback `_backfill_news_announcements` **逐股票调用 pro.anns_d**（每只 120s 读超时 × 全市场 5000+ = 卡死数小时）。直接后果：07-29 后 2 笔推荐未结算，**前瞻样本积累被阻塞**（D 项 + weak relax 衰减验证受阻）。
- 永久规则：**逐股票 Tushare 调用必须限速**（CLAUDE.md 已要求，但 anns_d fallback 遗漏）；news 辅助数据低优先级，可用 `skip_news` 跳过或用批量接口。盘后流水线任何一步卡死都会阻塞 outcome 结算 → 前瞻样本 → D 项闭环，必须优先修复。
- 修复方向：给 `_backfill_news_announcements` 加限速/批量上限（类似 ths_member `--rate-limit 3`），或 fallback 前确认 news_announcement 是否必需。
- 吸收位置：主提示词「工程稳定性」+「选择证据路径」。

## L-045 news 补数失败路径无节流残留 + 回归测试缺陷未提交

- 原始编号：L-045

- 状态：encoded（已修 + 回归测试 + 合并 main 391b2b3）
- 适用：逐股票 Tushare 补数函数、盘后流水线卡死防护
- 证据：f1df6f5 修复 news 补数只在成功路径 sleep（`_time.sleep` 在 215 行成功分支），失败路径 `continue` 直接跳过——provider 快速失败时逐只调用无节流。且回归测试有 3 缺陷未提交到 main：① `monkeypatch.setattr(dgr, "_time", ...)` 因 `_time` 是函数局部 import 直接 AttributeError；② 断言"每笔 sleep"与源码（失败路径不 sleep）不符；③ 测试留在工作区丢失回归保护。修复：import time 提升模块级 + sleep 移循环顶部（成功/失败/空返回每笔 0.35s 限速）+ 测试改 monkeypatch `dgr.time.sleep`。
- 永久规则：**函数内 `import time as _time` 无法被测试 monkeypatch**——限速类函数必须用模块级 `import time`，否则回归测试无法验证限速行为。逐股票 Tushare 补数的 sleep 必须放在循环顶部（含失败路径），不能只放成功分支。提交修复时回归测试必须同 commit 进 main，不能留在工作区。
- 吸收位置：主提示词「工程稳定性」+ OPERATIONS.md「数据同步策略」。

## L-046 驾驶舱 current_run 选择必须优先 live_forward，防 REPLAY run 抢占

- 原始编号：L-045

- 状态：active（FINDING，已合并 main f9e731c）
- 适用：前端驾驶舱运行状态面板、run 选择逻辑、北极星 A 项一致性
- 证据：`_select_dashboard_run` 旧逻辑只按 started_at DESC 取最新 run。研究员 REPLAY/历史补跑 run（evaluation_scope=historical_replay，trade_date=历史回放日如 06-18）started_at 更新时会顶掉当日正式推荐，驾驶舱「运行状态」显示 REPLAY 验证 run、用户误以为系统在跑历史验证。生产库实测：REPLAY 验证 run 抢占后，面板显示 failed/运行异常，而当日「数据同步后正式推荐」（live_forward）其实已 completed。
- 永久规则：**驾驶舱运行状态面板的 run 选择必须优先 evaluation_scope=live_forward（正式推荐）**——排序 evaluation_scope=live_forward 优先 → trade_date 新优先 → completed 优先 → started_at；无 live_forward 时回退旧语义。evaluation_scope 是区分正式推荐 vs REPLAY/补跑的最可靠字段（run_mode 都是 formal，无法区分；trade_date 语义依赖当日假设）。识别 REPLAY/验证 run 用 evaluation_scope=historical_replay，不要靠 run_name 前缀（REPLAY- 前缀是手动起的，非代码契约）。
- 与 `_select_dashboard_recommendation_run`（北极星 A 项已修 formal 优先）是同类缺口：落地页选正式 run 修了，运行状态面板漏修。
- 注意：`list_runs`（历史列表接口）不改排序，只有驾驶舱 current_run 面板需要此语义。
- 回归测试：`test_dashboard_run_selector_ignores_replay_run_over_today_live`（44 dashboard 测试全过）。
- 吸收位置：主提示词「前端体验」+ OPERATIONS.md。

## L-047 调度任务卡死残留：recover 只标 {None,running} 漏标 idle

- 原始编号：L-046

- 状态：encoded（已修 + 回归测试 + 合并 main 1beed31）
- 适用：调度任务状态恢复、卡死诊断
- 证据：post_sync 卡死在 news 补数时 `_execute_task` 阻塞，`finish_task_run` 永不调用 → scheduled_tasks.last_status 停在旧值 idle。重启时 `recover_scheduled_task_runs` 只在 `last_status in {None, "running"}` 时标记 interrupted，idle 卡死任务漏标，last_error 无法区分新旧故障。task_run 侧虽被人工标 failed，但 scheduled_tasks.last_status 不同步。
- 永久规则：**is_running=True 本身就是「上次运行未正常结束」的强证据**（正常路径 finish_task_run 后 finally 必清 is_running），恢复时无条件标记 interrupted，不能用 last_status 是否 running 判定残留。标记时保留原 last_error 追溯信息（仅补充中断标记，不覆盖诊断痕迹）。
- 吸收位置：主提示词「工程稳定性」+ OPERATIONS.md。

## L-048 驾驶舱「最近产出」latest 也必须 live_forward 优先（L-047 补全）

- 原始编号：L-046

- 状态：active（FINDING，已合并 main eae7668）
- 适用：前端驾驶舱最近产出卡片、bootstrap 状态、北极星 A 项一致性
- 证据：`bootstrap_workbench_state` 的 `recent_runs.latest` 此前用 `list_runs(db)[0]`（按 started_at DESC），REPLAY-E2E-0708（historical_replay 补跑）顶掉当日正式推荐，驾驶舱「最近产出」显示回放验证 run。修复后新增 `_select_latest_bootstrap_run`（live_forward 优先→trade_date 新优先→completed 优先→started_at），bootstrap 用它替换 list_runs[0]。生产库实测 bootstrap 返回「数据同步后正式推荐」run_20260731_142003_2e24fc。
- 永久规则：**驾驶舱所有展示「最新 run」的位置都必须 live_forward 优先**——不只有 current_run 面板（L-047），还有「最近产出」卡片（bootstrap.recent_runs.latest）。两处选择逻辑共享同一排序语义。`list_runs`（历史列表接口）不改排序，只有驾驶舱展示层需要。
- 与 L-047 交叉：L-047 修 current_run 面板，L-048 修最近产出卡片——同一类「REPLAY 抢占正式 run 展示」缺口的两个实例。识别 REPLAY/补跑 run 一律用 evaluation_scope=historical_replay。
- 回归测试：`test_bootstrap_latest_run_prioritizes_live_forward_over_replay`（39 dashboard + 2 e2e bootstrap 测试全过）。
- 吸收位置：主提示词「前端体验」+ OPERATIONS.md。

## L-049 path A 回测 --output 不建目录，worktree 跑回测结果无法落盘

- 原始编号：L-047

- 状态：encoded（已修 + 验证 + 合并 main）
- 适用：回测脚本、worktree 环境
- 证据：`si_path_a_signal_backtest.py` 写 `--output` 时 `open()` 直接打开，worktree/CI 环境没有 `output/` 目录 → FileNotFoundError，回测结果无法落盘（主证据路径丢失）。实测复现两次（2025_H1 champion-only 回测跑完但写文件失败）。
- 永久规则：**写文件前自动创建父目录**（`Path(output).parent.mkdir(parents=True, exist_ok=True)`），不假设环境已有 output/。worktree（§0.5）没有主仓库的 output 目录，跑回测 --output 必失败。
- 吸收位置：OPERATIONS.md「PATH-A 参数网格研究工具」。

## L-050 with-publish-lock 合并非 main 分支（发布流程缺陷）

- 原始编号：L-048

- 状态：encoded（已手动 cherry-pick 修复到 main）
- 适用：PRODUCTION/FINDING 发布流程、with-publish-lock
- 证据：主仓库 HEAD 在 `codex/backtest-infrastructure-fixes` 分支时，with-publish-lock 的 `git merge --no-ff` 把改动合进了**当前分支**而非 main，origin/main 不含修复（我 a4bb7f4 第一次发布失败，靠 `git cherry-pick` 到 main 补救）。
- 永久规则：**with-publish-lock 执行前必须先确认主仓库在 main 分支**（`git -C <主仓库> symbolic-ref --short HEAD`），不在则先 `git checkout main` 或改用 `git -C <主仓库> switch main && git merge --no-ff <branch> && git push`。发布后必须验证 `origin/main` 含新 commit（`git log origin/main -1`）。
- 吸收位置：OPERATIONS.md「发布锁合并」。

## L-051 REPLAY run 前端必须显示「回放」而非「实盘」（evaluation_scope 契约）

- 原始编号：L-047

- 状态：active（FINDING，已合并 main 1088c97）
- 适用：前端 run 徽章、REPLAY 识别、evaluation_scope 契约
- 证据：REPLAY/历史补跑 run 的 run_mode=formal，前端 RunModeBadge 一律显示「实盘」，用户无法区分真实盘推荐与回放验证。反例证明 run_name 前缀不可靠：run_20260801_091312_e0eeda（REPLAY-E2E-0522）scope=live_forward 但 run_name 带 REPLAY——只能靠 evaluation_scope 契约。DB 分布：formal×historical_replay=434（REPLAY/补跑）、formal×live_forward=130（实盘）、experiment×experiment=364、backtest×historical_replay=228。
- 永久规则：**前端展示 run 的「实盘/回放」必须用 evaluation_scope 契约**（formal+historical_replay=回放，formal+live_forward=实盘），禁止靠 run_name 前缀判断。`serialize_run_summary` 暴露 evaluation_scope（默认 historical_replay 匹配 DB server_default）；RunModeBadge 对 formal+historical_replay 显示「回放」。experiment/backtest 保持各自标签（experiment 的 scope=experiment 不是 historical_replay，backtest 保持「回测」）。
- 与 L-047/L-048/L-051 交叉：L-047/L-048 修「选哪个 run 展示」（live_forward 优先），L-051 修「展示时如何标注」（回放徽章）——REPLAY run 三个 UX 缺口已全部闭环。
- 回归测试：test_run_summary_evaluation_scope.py（3 个投影契约测试）。
- 吸收位置：主提示词「前端体验」+ OPERATIONS.md。

## L-052 prefect run_deployment async 上下文返回 coroutine，redispatch 静默失败

- 原始编号：L-049

- 状态：encoded（已修 + 回归测试 + 合并 main f9f5dba）
- 适用：Prefect 调度触发、async/sync 边界
- 证据：`on_startup`（sync def）在 FastAPI async event loop 中运行，调 `recover_interrupted_runs` → `_trigger_recommendation` → `run_deployment`。prefect 的 `@async_dispatch` 装饰器用 `is_in_async_context()`（无 prefect 上下文时 `asyncio.get_running_loop()` 判断）——on_startup 在 loop 内返回 True → 派发到 `arun_deployment` 返回**未 await 的 coroutine**，redispatch 实际未执行（err.log `'arun_deployment was never awaited'`），queued run 卡住。
- 永久规则：**prefect `run_deployment` 必须在 sync def 内显式传 `_sync=True`**（wrapper 用 `kwargs.pop("_sync", None)` 消费），不能依赖 `is_in_async_context` 自动判断——FastAPI startup/生命周期事件即使函数是 sync def 也在 event loop 中运行。所有 trigger 边界（trigger_scheduled_task / trigger_recommendation / _trigger_manual）统一 `_sync=True`。
- 与 L-046/queued-run-redispatch-fix 交叉：queued 续命修复只处理 heartbeat，未处理 coroutine 泄漏；两者叠加才是完整 redispatch 保障。
- 吸收位置：主提示词「工程稳定性」+ OPERATIONS.md。

## L-053 驾驶舱 failed run 面板状态矛盾会误导用户重复触发

- 原始编号：L-048

- 状态：active（FINDING，已合并 main 47826fe）
- 适用：前端驾驶舱运行状态面板、failed/interrupted 状态展示
- 证据：failed/interrupted run 时 RunStatusPanel 因 hasRun=false 显示「今日未运行」+「运行今日推荐」按钮（诱导重复触发），而 CandidatePanel 显示「今日推荐运行异常」+「查看运行复盘」——同页两面板矛盾指令。修复：RunStatusPanel 增加 abnormal 分支（failed/interrupted）：标题「今日推荐运行异常」+ 展示 error_message + 「查看运行复盘/重新运行」按钮；StatusBar runLabel 用 runStatusLabel 统一（failed→「运行失败」而非英文 failed）。
- 永久规则：**前端同一页面的状态展示必须一致**——驾驶舱 CandidateEmptyState 已处理 failed，RunStatusPanel 必须同步。failed/interrupted run 永远不能显示「今日未运行 + 运行今日推荐」（诱导用户以为没跑而重复触发）；必须显示失败状态 + 可读错误 + 复盘/重跑入口。runStatusLabel 统一状态文案（display.ts 的 STATUS_LABELS）。
- 与 L-047/L-048/L-051 交叉：REPLAY run 三个展示缺口（L-047/048/051）+ 本课 failed run 状态矛盾——驾驶舱 run 展示层的第四个 UX 修复。
- 回归验证：TSC exit 0、36 个前端测试全过、逻辑模拟 5 种 status（failed/interrupted/completed/running/无run）全正确。
- 吸收位置：主提示词「前端体验」+ OPERATIONS.md。

## L-054 stale run 心跳陈旧必须提示「疑似卡住」，不能显示「运行中」

- 原始编号：L-049

- 状态：active（FINDING，已合并 main adea362）
- 适用：前端驾驶舱、run 状态展示、心跳陈旧处理
- 证据：RunProgress.heartbeat_stale 字段后端已返回（build_run_progress 从 serialize 的 is_stale 传入），但前端三处（StatusBar/RunStatusPanel/CandidateEmptyState）完全未使用——running/queued run 心跳超时（系统已卡死，RUN_HEARTBEAT_STALE_SECONDS=1200）仍显示「运行中」，用户误以为系统在工作，实际已中断。历史上 180 个 interrupted run 全是这类场景的产物。
- 永久规则：**前端展示 running/queued run 时必须检查 heartbeat_stale**——stale 时显示「运行疑似卡住」+ 心跳提示 + 复盘/重跑入口，不能显示「运行中」。字段契约：RunProgress.heartbeat_stale（后端 is_stale = status in active + heartbeat_age > 1200s）。stale 场景真实存在（卡死 run 不自动清状态），前端必须防御。
- 与 L-047/L-048/L-051/L-053 交叉：驾驶舱 run 展示层第五个 UX 修复（L-047 current_run 抢占 / L-048 最近产出 / L-051 回放徽章 / L-053 failed 矛盾 / 本课 stale 误导）。
- 回归验证：TSC exit 0、36 前端测试全过、逻辑模拟 5 种状态全正确。
- 吸收位置：主提示词「前端体验」+ OPERATIONS.md。

## L-057 后端已算的 quality_scorecard 必须前端可见（驾驶舱系统质量徽章）

- 原始编号：L-050

- 状态：active（FINDING，已合并 main f30fb68）
- 适用：前端驾驶舱、后端已计算但前端未展示的数据字段、系统质量可观测性
- 证据：overview 已返回 quality_scorecard（数据/验证/执行/研究四维加权 + overall 评分 + highlights）但前端全站未使用（仅 types 定义），用户看不到「最近推荐准确率仅 43.8%」「严格准确率闸门 29 条信号失效」「最近最大回撤 -48.91%」等验证维度信息，无法判断系统推荐可信度。同批未用字段：validation（quality_gate 详情）、task_health（任务健康）。
- 永久规则：**后端已计算好的系统质量评分必须暴露给用户**——quality_scorecard 的 overall_score/status/highlights 是「系统是否可信」的一眼判断，前端不能只定义 types 不用。驾驶舱 StatusBar 已加「系统质量」徽章（复用 quality_scorecard，点击进 DataHealthPage）。发现「后端字段已返回但前端全站未用」时，先评估该字段是否有用户可观测价值，有则接线。
- 与 L-047/L-048/L-051/L-053/L-054 交叉：驾驶舱 run 展示层六个 UX 修复（L-047/L-048/L-051/L-053/L-054 run 状态）+ 本课系统质量可观测性（L-057）——驾驶舱「查看/解释」缺口第七个闭环。
- 回归验证：TSC exit 0、36 前端测试全过、徽章逻辑 4 种状态全正确。
- 吸收位置：主提示词「前端体验」+ OPERATIONS.md。

## L-058 策略"持续负期望"常是时间窗口选择偏差，须用跨月大样本符号一致性裁决

- 状态：active（FINDING，本轮探索）
- 适用：策略归因、按策略/特征禁用或调权、regime 分工决策
- 证据：真实回放(historical_replay, n=223, 仅覆盖 2026-05~08 弱势月)显示 daily-stock-analysis n=141 WR=29.1%、trend-pullback n=17 WR=11.8%，看似"持续负期望策略"。但 PATH-A 大样本(n=3050, 2026_H1 全 7 个月)逐月符号一致性检验显示**全部 4 个策略都是符号混杂**（small-cap `++-+-+-`、trend-pullback `++-+-++`、weak-reversal `++-++--`、chip `+--+++-`），无一逐月稳定负。所有策略共同在 3/7 月（最弱月）转负、4 月（反弹月）全转正——**盈亏由月度 regime 主导，非策略横截面差异**。真实回放的"DSA 差"是因为它恰好在弱势月样本最多。
- 永久规则：**判定"某策略持续负期望/该禁用"前，必须用覆盖多种 regime 的大样本做逐月符号一致性检验**；只在单一市场阶段（尤其连续弱势月）的小样本结论一律视为窗口偏差伪影，不得作为禁用依据。策略级盈亏归因的正确问题是"该策略相对当月大盘偏离是否稳定"（chip-single-peak 是唯一差月抗跌的正偏离策略），而非"该策略胜率绝对值低"。
- 与 L-004/chase-bucket-regime-confound/entry-pctchg-oos-falsified 交叉：同一过拟合模式的策略级变体——横截面/策略差异在 pooled 或小窗口数据上极易被 regime 混杂污染。
- 吸收位置：本轮探索报告（选股胜率调研系列）。

## L-059 同一数据域多条写入路径必须共享单位转换纯函数，否则会出现"一条对一条错"

- 状态：encoded（已修 + 回归测试）
- 适用：所有辅助数据域（capital_flow/margin/dragon_tiger 等）的多路径写入
- 证据：`capital_flow` 有两条写入路径——`backfill_moneyflow_concept.backfill_moneyflow`（每日同步，用 `moneyflow_net_yi`=/1e4 正确）与 `state_adapters.TushareStateProvider.capital_flow`（run 时 refresh 兜底，用 /1e8 错误）。2026-08-03 起 refresh 兜底被触发，写入小 4 个量级的错误值（000021 main=-0.0002 而非 -1.80 亿），且把累计字段覆盖。self_check 用 `main_net_inflow_yi` 判质量✅ 是误导——它没发现 refresh 路径的累计字段全 0。实测上游 `pro.moneyflow` 一直正常（返回全市场 5540 只、量级正确）。
- 永久规则：**同一数据域存在多条写入路径时，单位转换必须抽取为共享纯函数（如 `moneyflow_net_yi`），禁止各路径内联各自的 `/1eN`**。诊断"数据 stale/异常"时，先实测上游接口（绕开本地库）确认是"源断了"还是"本地某条写入路径写错"——两者解法完全不同。self_check 质量护栏要看衍生字段（累计值）而非只看原始字段。
- 回归测试：`tests/test_capital_flow_state_adapter_units.py`（锁定 /1e4 单位 + 各分档量级）。
- 吸收位置：state_adapters.capital_flow 注释 + 本测试。
- 相关：L-050 margin_detail 单位方向、[[amount-yi-data-inconsistency]]（同为 Tushare 单位陷阱）。

## L-060 regime/情绪抬线错杀正期望 regime，应按 regime×情绪精细区分而非一刀切

- 状态：active（FINDING + walk-forward OOS 验证通过，待 PRODUCTION 发布流程）
- 适用：production selection policy 的分数线调整（market_base/sentiment/climax_nonweak_extra）
- 证据：frozen snapshot holdout_244d（生产口径 final_score + 因果出场 + 费用）对比——当前规则（含 regime/情绪抬线到85-91）OOS 期 65% 日子 0 出票。按 regime×情绪分解被拦的票：**强牛·高潮 57%胜率+2.60%、震荡·活跃 51%+2.57%、强牛·活跃 61%+1.65%（均被错杀的正期望）**；偏多·高潮 36%-1.88%（真毒区，拦对了）。精细方案（放行强牛·高潮/强牛·活跃/震荡·活跃用72线，其余维持抬线）walk-forward OOS：WR 47.7%→51.4%、均净 +0.767%→+1.785%（2.3倍）、PF 1.31→1.72、出票天数 34→67、0出票 65%→32%。OOS 改善幅度>训练期，非过拟合。
- 永久规则：**regime 提线不得一刀切**——同一 regime（强牛）在高潮与活跃下期望相反（+2.6 vs +1.6 正），不同 regime 在同一情绪（高潮）下期望相反（强牛+2.6 vs 偏多-1.8）。历史"强牛·高潮58.3%胜率需高门槛"是 n=12 小样本误判，大样本证伪。任何 regime 规则必须按 regime×情绪二维大样本验证。
- **诚实修正（本轮）**：①frozen snapshot 的候选分数被 quality_cap 压到 ~60-72（历史回放数据不全），与生产 live（候选最高 78-89）分数尺度不同——故"出票天数翻倍"的绝对数字在快照上不可复现，只有"放行组合正期望/拦截组合负期望"的方向可信。②生产有三层分数线（市场级 resolve_score_line_context + 单票 evidence/execution 惩罚 + per-card 结构性强势+4），且 **_market_base 与 _snapshot_regime 是两套不一致的 regime 判断**（强牛→结构性强势）。override 必须同时豁免 per-card 的 strong/support_ma5 惩罚才生效，否则市场级放行被单票级抵消（已实现 `_combo_relaxed` 跳过）。③强牛·高潮日涨停票(≥60分) OOS n=11 WR82%/+5.33% 强于非涨停，涨停执行惩罚在此场景可能错杀动能票（样本小，仅方向参考，待前瞻）。
- 待办：走 canonical production-policy experiment + Canary + publish-champion 发布为 PRODUCTION；上线后必须靠 live_forward 真实样本最终确认（当前为零）。override 已落地 feature 分支 feat/winrate-data-pipeline-fix。
- 复现：`backend/scripts/winrate_scoreline_prod_backtest.py`（分数线对比）+ 本轮会话内 walk-forward 脚本。
- 相关：L-058（窗口偏差）、[[zero-recommendation-strong-day-2026-08]]（强势日0出票）、[[market-regime-win-rates]]（旧小样本regime胜率，部分被本条证伪）。

## L-061 收益率改善的入场/出场杠杆已系统性挖尽，跳空止损是 regime 产物非入场可识别

- 状态：active（FINDING，全市场口径 n=11012 大样本逐月符号检验）
- 适用：收益率/期望优化、止损、入场特征挖掘
- 证据：全市场 2026_H1（n=11012，全 12 策略、含费用、因果出场）——亏损完全由止损主导（stop_loss 系列 4409 笔 -23232%，其中跳空止损 stop_loss_gap -6.82%/-8424% 是最大单项）。但：①入场特征（score/chg20/pos60/vol5_20/sig_gap）赢亏差异全部≈0，无区分度；②跳空止损占比与月度盈亏强负相关（好月5%、差月15-20%），是弱势市场产物；③"高 chg20(>20) 更差"逐月符号 `++-+---` 混杂伪影（强势月高 chg20 反而更好）。frozen snapshot 候选池（n=12573）所有入场特征 IC 全在 ±0.05 内且逐月符号混杂。
- 永久规则：**禁止再从入场特征（chg20/pos60/score/量比等）挖收益率 alpha**——全部是月度 regime 混杂伪影，逐月符号从不一致。跳空止损（最大单项亏损）是市场整体跳空所致，无法用入场特征识别或止损参数压缩（L-011）。收益率的真实杠杆只剩：出票率（次数）和 regime 分工（L-060），以及新数据源/新因子（未挖尽的横截面维度）。
- 与 L-011/L-058/chase-bucket-regime-confound/trailing-stop-not-early-lock-verified 交叉：出场参数与入场特征两条路都已封死，勿重复扫描。
- 吸收位置：本轮收益率探索报告。

## L-062 PATH-A 回测缺辅助数据字段，会系统性误判依赖它们的策略为"不出票/不行"

- 状态：active（FINDING，评估策略前必查）
- 适用：用 PATH-A / 任何只重建价格技术指标的回测评估策略
- 证据：PATH-A `_bar_to_stock_dict` 只从 daily_kbars 重建价格/技术指标，全部 13 个策略的 universe 都依赖辅助字段（`dragon_tiger_is_on_list`/`announcement_negative_count`/`news_risk_count`/`twenty_cm_risk_gate`/`limit_up_overheat_score` 等）。在 PATH-A 里这些字段是空值 → dragon-tiger/sector-relative-strength/high-range-breakout 等对全市场 100% hard_veto"不在策略股票池"，看似"从不出票"。但真实生产（真 feature_builder 加载辅助数据）里 dragon-tiger 出过 32 票占 11.8%。辅助数据(dragon_tiger/capital_flow/limit_up_board)仅覆盖最近 ~4-5 个月。
- 永久规则：**评估策略表现前，先检查其 universe 依赖的字段是否在回测特征里可得**。依赖辅助数据的策略，PATH-A 结论一律不可信，必须用真 feature_builder 回测或真实生产 outcome 评估。辅助数据只覆盖近 4-5 个月——更早的历史窗口这些策略在生产上也无数据可触发，评估窗口不得超过数据覆盖。
- 与 L-001/L-005（口径/数据深度披露）交叉：回测结论必须标注"特征数据完备性"，缺辅助数据的回测要声明哪些策略被误判。
- 吸收位置：本轮策略评估报告。

## L-063 走势形态(持续走强vs首日冲高)在入场时无法区分——信号日特征无差异

- 状态：active（FINDING，frozen snapshot 探索口径）
- 适用：编新策略、入场信号设计、"选好票"类尝试
- 证据：frozen snapshot holdout_244d 候选池按 T+1 后 5 日走势形态分组——持续走强(92%WR/+9.4%)、震荡上行(83%/+5.3%) vs 首日冲高(8%/-5.9%)、冲高回落(17%/-3.6%)，形态收益差异巨大。但这些形态在**信号日的可见特征**(pos60/chg20/pct/score/amount)上差异全部≈0（pos60 Δ-1.78、chg20 Δ-0.65、score Δ+0.13）。**形态是事后才知道的，入场时无法预判。**
- 永久规则：**禁止用"识别会持续走强的票"作为策略设计前提**——5日走势形态在入场点不可分。同理"盘中 peak 高 ≠ 能拿到"(peak 是任意时刻极值,首日冲高的票 peak 也高但拿不到)。任何"提高胜率"的入场侧设计,若其区分特征在信号日不可见或事后才能算,一律不可行。
- 与 L-061/L-058 交叉：入场侧横截面 alpha 已多维度系统性挖尽;收益率改善只能走 出票率/regime分工(L-060)/仓位/新数据源。
- 吸收位置：本轮新策略设计报告。

## L-064 资金流因子在修复后干净数据+全市场 strictly-prior-day 口径下方向反转，不可用作选股信号

- 状态：active（FINDING，否定性）
- 适用：资金流因子、任何"主力流入"类选股信号
- 证据：数据修复(L-059)后,全市场 strictly-prior-day(L-014) 201339 样本五分位——3日累计主力净流入 Q5(大幅流入) 胜率45.4%/均收益+0.025% 最低，Q1(大幅流出) 胜率47.0%/+0.371% 最高。**方向与"流入=涨"完全相反**。记忆 r_pb=+0.118 是被选中票口径(选择效应伪影)+修复前错误数据,全市场口径反转。可能机制:主力大幅流入=拉高出货,次日回落。
- 永久规则：**资金流因子不得用作多头选股信号**（全市场口径方向已证伪）。任何因子必须先在全市场 strictly-prior-day 五分位验证单调性,再看生产选中票口径;两口径方向相反时以全市场为准(选中口径是选择效应)。
- 与 L-014/L-062/factor-eval-universe-vs-selected-caliber 交叉：口径+数据质量双重陷阱。
- 吸收位置：本轮新策略设计报告。

## L-065 日线级横截面选股 alpha 在当前数据下系统性挖尽——新策略必须换数据维度或换框架

- 状态：active（FINDING，多轮大样本证伪的总结）
- 适用：任何新策略设计、新因子挖掘的方向选择
- 证据：在"只选股不做仓位"+日线数据框架下，全部横截面方向被大样本+逐月符号一致性证伪：价格/技术特征(L-061)、走势形态识别(L-063,入场不可分)、资金流因子(L-064,全市场口径反转)、板块联动补涨(逐月`+-++`混杂)、regime闸门/止损参数/追高回避(历史)。所有"看似alpha"都是 regime 混杂伪影(逐月符号从不一致)或选择效应伪影(信号池vs精选池方向相反)。
- 永久规则：**在日线+横截面选股框架内，禁止再挖新"选股因子"**——已系统性挖尽。新策略只有三条合法出路：①换数据维度（盘口/分时/Level2/北向细节等当前没有的）；②换框架（仓位管理/多策略组合对冲/regime识别，而非横截面选股）；③承认 beta 属性，用 L-060 regime 分工 + 提高出票率放大已有微弱 alpha。任何新"选股因子"必须先过 feature_signal_guard 逐月符号一致性(≥80%同号)才允许进 OOS 实验。
- 吸收位置：本轮新策略探索总结。

## L-066 异步生成型行情接口不能按普通 HTTP 成败接入，完成响应仍须过业务契约门禁

- 状态：encoded（已修 + 实源复验 + 回归测试）
- 适用：Promax `stk_mins` 及其他先触发生成、后返回结果的行情接口
- 证据：2026-09-09 实测 Promax 同一接口存在三类响应：HTTP 202 `minute_data_pending`；标准 241 根整分钟 K；以及 HTTP 200 但混有秒级时间、午休/收盘后冻结快照、同分钟重复的异常结果。仅按 HTTP 200 判断会把快照当分钟 K，按普通 4xx 判断又会错过异步生成中的数据。
- 永久规则：**异步生成接口必须把“待生成”建模为可 fallback、非熔断状态；HTTP 200 仍必须通过领域契约门禁后才能落库。** 历史分钟最低门禁包括整分钟时间戳、连续交易时段内、分钟时间唯一；任一失败整批拒绝并尝试下一数据源，禁止靠抹秒或盲目去重伪造分钟 K。
- 回归验证：`test_tushare_dual_sources.py` 覆盖 202 状态、Promax 路由优先级、标准分钟接受和异常快照拒绝；真实 `000001.SZ@2026-08-31` 返回 241 根且全部整分钟，`2026-09-01` 异常快照被拒绝，`2026-09-08` 202 状态正确 fallback。
- 吸收位置：`tushare_transport.py` 的 `DATA_PENDING` 状态、`tushare_client.py` 的 Promax 分钟契约门禁、Promax 接入评估和主提示词数据源策略。

## L-067 健康检查请求链路禁止对超大表做函数化日期聚合和精确计数

- 状态：encoded（已修 + 回归测试 + 真实 MySQL 验收）
- 适用：数据健康、管理后台统计、任何用户请求内的超大表概览
- 证据：`/api/admin/data-health?force_refresh=true` 的无缓存诊断约 59 秒；其中 5200 万行 `minute_kbars` 的 `MIN/MAX(REPLACE(trade_date, ...))` 单条 18.0 秒，数据域精确 `COUNT(*)` 与函数化 `MAX` 合计再占 36.6 秒。改为规范 ISO 日期列直接走索引、存在性用 `LIMIT 1`、超大整表行数用带标识的 MySQL 估算后，真实无缓存诊断 2.293 秒，重启后 HTTP 强制诊断 2.442 秒，配置页可用 1.039 秒。
- 永久规则：**用户请求链路不得在索引列外包 `REPLACE/DATE/CAST` 后做 `MIN/MAX/ORDER BY`，也不得为“是否有数据”执行精确 `COUNT(*)`。** 日期写入统一为 `YYYY-MM-DD` 后直接使用索引；状态判断用 `EXISTS/LIMIT 1`；仅展示用的大表行数允许使用明确标注“约”的数据库估算，字段型域无需精确行数时显示“有数据”。
- 回归验证：`test_data_health_service.py` 锁定索引友好 SQL 与字段型域不计数；相关后端测试 80 passed；前端构建通过；真实 MySQL 和浏览器均低于 3 秒验收线。
- 吸收位置：`data_health_service.py` 数据域统计契约 + 配置页数据域行数展示。

## L-068 execution_version 的身份分量必须由跨进程同构的单一解析器产生，禁止只在某一种启动路径 pin

- 状态：encoded（已修 + 真实链路复验 + 回归测试）
- 适用：所有“入队进程（FastAPI）算一次身份指纹、执行进程（Prefect worker）重算并比对”的版本一致性护栏
- 证据：2026-09-11 驾驶舱手动推荐连续失败（`run_20260911_014741`、`run_20260911_122924`），报错“Run 已持久化的 execution_version 与当前策略/因子/执行策略身份不一致”，进度 0/0=NaN%。根因不是配置漂移：只有 `scripts/run_prefect_worker.py` 在启动时把 `ASHARE_APPLICATION_REVISION` pin 为当时 git HEAD（0114dab），而 launchd 启动的 API 从不设置该变量、恒解析为 `unknown`；两侧 application 身份不同导致 hash 必不相等。脚本内同步运行（入队与执行同进程）反而成功，掩盖了跨进程分叉。
- 永久规则：**进入 execution_version（或任何跨进程比对指纹）的每个分量，都必须由 API、worker、CLI 共用的同一解析器、同一解析顺序（显式环境变量 → 进程级缓存的 git HEAD → 规范兜底值）产生；禁止只在某一个启动脚本里 pin。** 指纹比对失败时必须能并排输出两侧分量；进程重启换代码后，所有参与比对的进程必须一起重启。
- 回归验证：新增 `app/core/deployment_identity.py` 统一解析；`test_deployment_identity.py` 锁定“显式 environ 纯函数、默认路径缓存并回写 os.environ、两侧一致”；`test_provenance_writer.py` 同步改造；修复后真实 HTTP 触发的两个 formal Run（`run_20260911_131116`、`run_20260911_131119`）均通过 PREPARE 并 completed，相关 89 个后端测试通过。
- 吸收位置：`app/core/deployment_identity.py`；`runs/contracts.py::application_identity_from_environment`、`evidence/provenance_writer.py`、`scripts/run_prefect_worker.py` 全部委托该解析器。

## L-069 涨停板微观结构情绪（炸板率/连板高度/梯队宽度）做日级择时五重证伪——仍是当日广度的重包装

- 状态：active（FINDING，否定性）
- 适用：任何想用 limit_up_board / 日线重建的炸板率、连板高度、梯队、涨停家数做 T 收盘择时或选股过滤的尝试
- 证据：从 daily_kbars 用精确四舍五入涨停价确定性重建涨停/炸板/连板序列（与官方 limit_up_board 重叠期收盘涨停家数 corr 0.999、MAE 0.75，重建可靠），预注册 10 个情绪特征（炸板率宽/严、封板率、连板高度、梯队宽度、涨停家数、涨跌停比等，均 T 收盘已知、T+1 入场前可用）。五重独立检验一致否定：①生产选中候选池（frozen holdout，生产因果出场）10 特征全部不过门禁（训练/OOS 方向翻转、逐月为正比例 0.25–0.75、Bonferroni 校正 CI 下界全负）；②理论预注册"退潮分数 ebb"的 abstain 反事实是**踏空而非避损**（毒日 OOS 均 +1.224%，abstain 反让累计 +8.08%→+4.98%）；③构念效度：这些特征连**全市场**未来 1/5 日收益都预测不了（|IC|≤0.07、逐月同号 0.29–0.52）；④分 regime 无稳定单调格（高炸板率在震荡市反而最好）；⑤T-1 滞后口径与三段时间窗口方向均翻转。
- 永久规则：**禁止再用涨停板微观结构情绪（炸板率/连板高度/梯队/涨停家数衍生）做日级择时或 abstain**。它与价格技术特征同构——都是当日市场广度的重包装，方向被 regime 主导、逐月符号从不一致（再次印证 L-061/L-065）。官方 limit_up_board 仅 2026-03 起、且"炸板"口径与日线重建仅 0.55 相关（官方主要记封死后炸开，日线无法区分摸板未封），样本与构念双重不足。情绪维度唯一被双窗口验证有效的用法是 L-070 的"非强牛高潮硬排除"（负向规避，而非正向择时）。
- 复现：`backend/scripts/research_limit_sentiment_panel.py`（重建+官方校验）、`research_limit_sentiment_timing.py`（主因果检验）、`research_limit_sentiment_followup.py`（构念效度/regime/滞后）；证据 `backend/output/research/limit_sentiment_timing.json`。
- 相关：L-058（窗口偏差）、L-061/L-063/L-064/L-065（日线 alpha 挖尽）。

## L-070 L-060 组合放松存在窗口过拟合；非强牛的"高潮"组合必须硬排除（production selection v2）

- 状态：encoded（双窗口因果重放 + bootstrap 显著 + 回归测试，已落 v2 artifact）
- 适用：production selection policy 的 regime_sentiment_line_overrides、任何按 regime×情绪调分数线/出票率的改动
- 证据：用**修复后的真实因果出场**（非 L-060 当时退化的"持有到期"口径）在两个独立窗口重放——冻结 holdout 244d（2025-01-15~2026-01-16，19980 笔）+ 真前瞻 117d（2026-02-09~08-06，canonical run btr_fa625 26877 笔，L-060 调参时从未见过）。①L-060 的部分放松是**窗口过拟合**：其 artifact 在冻结窗口最优（OOS cum +10.27%），但在真前瞻窗口整体亏损 cum -7.31%/MDD -10.13%，反不如统一 72 线（-4.68%）；用训练段重拟合每格线同样 OOS 退化。②跨两窗口唯一稳健、且 95% 显著的改进是**硬排除非强牛的三个"高潮"组合**：偏多·高潮（日 cohort 冻结 -0.454/前瞻 -0.981）、震荡·高潮（-0.522/-0.781）、弱势·高潮（-0.033/-0.708，逐笔前瞻 -3.10/胜率 29.8%）在**两窗口所有分数档都为负**——旧值 85 想拦但分数可达 100 仍漏高分亏损票，override 设 120（=score_line_maximum，最高分不可达）才补全拦截；唯有强牛·高潮两窗口为正（保留 64 放行）。③效果（日度 top6 等权、无票=现金，仅落地三个高潮硬排除、活跃档维持 L-060 不变）：冻结窗口零代价（cum +12.43%→+12.46%、配对日差 CI[-0.065,+0.060]≈0）；真前瞻 cum -7.31%→**-3.42%**、MDD -10.13%→-6.58%、出票 117→88 天（毒日 abstain），配对日差 **CI[+0.088,+0.520] 下界>0（95%显著）**；两窗口合并 361d cum +4.21%→**+8.62%**、MDD -10.13%→-6.58%。并行线程独立脚本复现同一弱势高潮否决（P0 日均 -0.537 完全一致、弱势高潮逐笔 -3.10/胜率 29.8%）。④敏感性（**未落地**）：若再把强牛·活跃 64→72、偏多·活跃 71.4→72，前瞻可再小幅改善到 -3.16%，但该增量配对 CI 跨 0、不显著，且与独立分析存在分歧，按"只落地双窗口显著项"原则不纳入 v2，留待 live_forward 再判。
- 永久规则：**(a) regime×情绪分数线必须在≥2 个独立窗口（尤其含一段调参后真前瞻）同向才允许落地，单窗口（含单一 train/OOS 切分）最优即过拟合（强化 L-058/L-060）。(b)"高潮（涨停潮）只在强牛市可交易"：偏多/震荡/弱势·高潮是见顶/反抽追高陷阱，两窗口全分档为负，必须硬排除到分数线上限，85 这类"抬线但低于满分"会漏高分票，不算拦住。(c) 冲突组合（如偏多·活跃/弱势·活跃：冻结为正、前瞻为负）不做方向性调整，维持 L-060 现值；不为前瞻窗口单独拟合极端值；不显著的微调即使方向正确也不与显著项捆绑落地。**
- 落地：新增版本化 `app/policies/production_selection.v2.json`（默认；相对 v1 仅把偏多/震荡/弱势·高潮改为 120 硬排除，其余含活跃档 64/71.4 全部不变），v1 保留可复现；loader 支持 version∈{1,2}；回归 `tests/test_production_selection_policy.py`（v2 硬排除 + v1 不可变）。研究脚本 `research_regime_scoreline_map.py`/`research_forward_validation.py`/`research_scoreline_two_window.py`/`research_scoreline_final_confirm.py`，证据 `output/research/final_policy_evidence.json`。
- 待办：v2 上线后仍须按 L-060 用 live_forward 真实样本持续确认；本结论窗口截至 2026-08-06，且前瞻 run 只保留 score≥70，60/64 放松档未在该窗口复验（沿用冻结结论）。
- 相关：L-058（窗口偏差）、L-060（regime×情绪分工，本条修正其过拟合部分）、L-065（出票率/regime 分工是仅剩杠杆）、L-015（abstain=现金的组合级度量）、L-071（研究口径必须与生产因果出场同构）。

## L-071 研究回测的可卖性闸门必须与生产同构：冻结快照缺 amount 会让因果出场静默退化为"持有到期"

- 状态：encoded（定位根因 + 修复两个研究脚本 + 用生产 harness 复核 v2）
- 适用：任何基于 frozen snapshot / 离线 K 线、自己拼 ExecutionBar 再走因果止损止盈的研究脚本；任何用研究 cohort 反推生产分数线/政策的结论
- 证据：冻结快照 `output/frozen_snapshots/holdout_244d.json` 的 outcome_bars 每根只有 OHLCV+tradable，**没有 amount、没有 pct_chg**。生产/治理路径 `simulation.simulate → _to_execution_request` 直接令 `sellable=bar.tradable`（不查 amount），因果止损/止盈/移动止盈正常；但研究路径 `outcome_simulation._resolve_exit_causal_result` 的可卖性走 `execution_constraints.no_liquidity_reason_from_bar`，要求 **volume>0 且 amount>0** 才可卖——快照 amount 缺失导致每根 bar 被判不可卖，盘中止损/止盈被全部跳过，最终退化为"T+1 开盘买、持有到 T+5 收盘"。用这套退化口径曾让 L-060 的冻结窗口符号发生翻转，并一度错误支持"强牛·活跃/偏多·活跃抬到 72"；改用生产同构 harness（`policy_experiment` 的 `_run_arm`，真实 `simulate`）后，抬活跃线反而让冻结训练段由 +0.064 转 -0.111、砍掉 212 笔正期望活跃票，遂否决该改动（见 L-070 ④）。
- 永久规则：**(a) 离线 K 线缺字段时必须显式补齐再过流动性闸门（amount≈volume×close 仅用于 >0 判定，成交价只用 OHLC；pct_chg 按前收顺序补，供一字板判定），并在日志打印 exit_reason 分布自检——若只剩 horizon_close 一类即说明因果出场被静默短路。(b) 政策级结论只信与生产同构的 canonical harness（`evaluate_selection_policy`/`simulate.simulate`，sellable=tradable）或 DB canonical run；手写 cohort 只能做探索，不能作为改生产阈值的最终依据。(c) 研究脚本与生产共用同一执行/可卖性逻辑，禁止在研究侧重写一套更严或更松的闸门。**
- 修复：`scripts/research_intraday_portfolio_levers.py`（`_bar_obj`/`_simulate_card` 补 amount、顺序算 pct_chg）、`scripts/winrate_scoreline_prod_backtest.py`（`_to_bar_obj`/`simulate` 同款修复）；生产同构双窗口证据 `output/research/v2_policy_two_window_evidence.json`（v2 训练段与 v1 逐笔相同、validation/OOS 同升），脚本 `scripts/research_v2_policy_evidence.py`、`scripts/research_canonical_policy_batch.py`、`scripts/research_forward_weakclimax_check.py`。
- 相关：L-070（v2 高潮硬排除，本条是其口径前提）、L-058（窗口/口径偏差）、L-015（组合级度量）。

## L-072 v2 经生产同构 canonical harness 双窗口复核成立；其余选股层杠杆（额外毒格/格内分数带/广度 abstain/出票上限）系统性证伪

- 状态：active（一条 encoded 确认 + 一组否定性 FINDING）
- 适用：任何想在 v2 之外再做 regime×情绪格否决、格内分数线微调、大盘广度择时 abstain、每日出票数调整的尝试
- 背景：L-070 的 v2 证据部分来自手写日度 cohort；按 L-071，政策级结论只信生产同构 canonical harness。本轮建 `research_canonical_two_window.py`：冻结窗走 `ProductionPolicyBacktestRunner._run_arm`（真实 evaluate_selection_policy→apply_pool_rules + simulation.simulate，sellable=tradable），前瞻窗（canonical run btr_fa625，score≥70）用真实 evaluate_selection_policy 重放"选哪些"再 join canonical 净收益（精简卡缺 chg20/support_ma5/旧regime串，单票惩罚无法复现，但 v1/v2 差异只来自 override 组合，缺字段只让 v1 侧偏宽松，结论偏保守）。
- v2 canonical 确认（v1→v2）：冻结窗逐笔 +0.164→+0.206、胜率 53.8%→54.0%、PF 1.084→1.107，validation/OOS 逐笔与日 cohort 同升、train 逐笔不变；前瞻窗逐笔 -0.471→-0.352、胜率 50.1%→51.9%、PF 0.782→0.833，日 cohort cum -9.50%→-3.89%、MDD -13.79%→-8.26%，配对 29 个变化日 CI[+0.263,+2.175] 下界>0，top4/6/8/10 CI 下界全>0；被拦 139 票均 -1.09（偏多·高潮 -1.25、弱势·高潮 -2.06）。三个高潮格在冻结【全候选】各分数带单调为负（偏多·高潮 -0.08/-0.40/-0.69、震荡·高潮 -1.11/-0.62/-0.82、弱势·高潮 -0.22/-0.87/-0.82，高分更差），证明 120 硬排除不是误伤、没有可保留的赢家分数带。
- 系统性证伪（均双窗口 canonical，脚本可复现）：①额外毒格否决——震荡·平淡前瞻选中 10 笔 win0%/-7.33 看似剧毒，实为**单日 2026-07-16 聚集**，冻结全候选 n=292 均 +1.32/win59%（[72,76)+0.80）为正，否决会砍均值回归边，拒；偏多·活跃/强牛·活跃/弱势·活跃前瞻为负但冻结为正（偏多·活跃冻结选中 +0.94/PF1.64 vs 前瞻 -2.30/PF0.275），属跨窗冲突格，整格或抬线都会砍冻结正期望票（L-071 已证），拒。②格内分数带——冲突格内部不存在"两窗口都负"的低分带（偏多·活跃冻结各带全正、高分更优），无可外科收紧处。③大盘广度 abstain——当日 up_ratio<.20 冻结 +0.15/前瞻 -0.57 翻号；3/5 日滚动 ma3/ma5 弱区同样冻结为正、前瞻为负，各阈值冻结配对 CI 下界全≤0，拒（弱势低广度是 2025 有效的反转票，2026 失效＝regime beta）。④每日出票上限 topK——冻结 top3-5 最优（集中）、前瞻 top10 最优（分散），方向相反，维持 daily_output_limit=10。⑤分策略桶否决——冻结快照卡只有匿名 strategy_signals、无 strategy_id，无法双窗口对照，单窗口即过拟合，不做。
- 永久规则：**(a) 2025 冻结窗与 2026 前瞻窗是两种 regime，几乎一切"收紧/放松"都"利一窗、伤另一窗"；唯一可落地的是在【两个窗口、且全候选各分数带】都为负的格做外科否决（v2 三高潮格即此类），单窗口负、另一窗口正一律视为 regime 暴露，禁止削。(b) 某格"剧毒"先查是否单日/少数日聚集（L-058），再查另一窗口同格，两步都过才谈否决。(c) 选股层在现有日线+市场广度数据下已到局部最优，再提风险调整后收益必须引新数据维度（分时/盘口/L2/北向，见 L-065），不得在同一批特征上换名字重挖。**
- 复现：`scripts/research_canonical_two_window.py`（双窗口 v1/v2 canonical 对照，证据 output/research/canonical_two_window_v1v2.json）、`research_canonical_cell_map.py`（选中层16格）、`research_cell_osc_plain_deep.py`（震荡·平淡单日聚集证伪）、`research_breadth_abstain.py`/`research_weak_breadth_abstain.py`/`research_multiday_breadth.py`（广度择时证伪）、`research_band_and_count.py`（格内分数带+出票上限证伪）。
- 待 live_forward：v2 三高潮硬排除的长期效果；震荡·高潮选中层前瞻 +0.195 与冻结全候选 -0.77 的轻微张力（现按"冻结为负+安全"保留硬排除，若 live_forward 累积其选中层稳定为正再议）。
- 相关：L-071（canonical 口径前提）、L-070（v2）、L-058（窗口/单日偏差）、L-060（regime×情绪分工）、L-065（日线 alpha 挖尽须换数据）、L-015（abstain 组合级度量）。

## L-073 出票端结构与单卡惩罚杠杆双窗口系统证伪；"放松弱市低 chg20 惩罚"是冻结窗假 alpha，真前瞻反转（接飞刀前提成立）

- 状态：active（否定性 FINDING，本轮不改生产、v2 维持）
- 适用：任何想在 v2 之上调 climax_output_divisor / daily_output_limit / weak_ice_output_limit / 动态软降线 / 四个单卡惩罚（strong、support_ma5、weak_low_chg20、panic）/ weak_regime_score_cap / 活跃档分数线 / 行业分散上限，或想靠"日内 final_score 排名"集中出票的尝试
- 方法：全部走生产同构 canonical harness（`ProductionPolicyBacktestRunner._run_arm`，真实 evaluate_selection_policy→apply_pool_rules + simulation.simulate），冻结 holdout_244d 分 train/validation/OOS + 真前瞻 117d（canonical run btr_fa625，score≥70，调参时未见）；逐笔等权 + 日度 cohort 等权（空仓记 0）双口径，配对日差 bootstrap 5000 次 95% CI、逐月同号、单日聚集、rank 分层。
- 头条证伪——放松 weak_low_chg20_penalty（12→0）：**冻结窗极其诱人**，ALL 逐笔 +0.206→+0.381、日 cohort +0.061→+0.244（约 4 倍），train/val/OOS 三段全升，新增 354 笔均 +0.491/胜率 62.1%，且 chg20 越低越高（[0,5)+0.965 > [5,10)+0.637 > [10,15)+0.225），看似干净的"弱市超跌反转"。**真前瞻决定性反转**：被放松放进来的边际票 98 笔均 **-0.836/胜率 45.9%**、三个 chg20 档全负、6 个月里 5 个月负（2/6/7 月 -4.8/-4.2/-4.7），配对日差 Δ-0.043 CI[-0.161,+0.070]、逐月为正率 0.29，放松后日 cohort -0.153→-0.196 更差。前瞻【弱市·非涨停全候选】按 chg20 单调——chg20 越低越差（[-∞,0) -0.621、[0,5) -0.370、[10,15) -0.302、[15,25) -0.257、[25,+∞) -0.122），正是第 150 轮 LIVE formal 的"弱市低 chg20 半强股继续跌"前提；对照【强牛·非涨停】各 chg20 档全正（+0.39~+0.99），说明该惩罚 regime-conditional 设计正确，**必须维持 12，不得放松**。冻结窗的"超跌反弹"是该段特有的非平稳 regime（与 L-072(a) 同一机理的又一具体实例）。惩罚值网格 pen12/8/4/0 在冻结非单调（8 最好），进一步说明冻结最优值不可外推。
- 其余出票/单卡杠杆一并证伪（均双窗口，数字为冻结 canonical，前瞻同向否决或不可验）：①climax_output_divisor 2→1（v2 后只作用于强牛·高潮）——冻结逐笔 +0.206→+0.229、OOS 升，但配对日 cohort 差 train/val/OOS/ALL 的 CI **全部跨 0**、逐月同号仅 0.77、新增票仅 50% 的天为正、最大单日占 36.1%；前瞻 cap5→6/8/10 的 CI 全跨 0 且更多天数变差（11~12 优/16~17 劣），rank6 单独 -0.721、rank 收益非单调。②日内 final_score 排名在选中池无区分度（前瞻 rank1-3 -0.164、rank4-5 -0.475 最差、rank9-10 +0.303，非单调，再证 L-061）。③daily_output_limit 6/8/15 两窗口非单调（再证 L-072④），维持 10。④weak_ice_output_limit 2→4/10 完全 inert（冻结 cap2 下弱势·冰点选中 0 笔，前瞻 ≥70 也无）。⑤关动态软降线：OOS 逐笔 +0.106→+0.063 变差，保留。⑥strong_score_penalty / strong_support_ma5_penalty 归零：冻结完全 inert（当前候选从不触发），属纵深防御，保留。⑦panic 阈值 100→90/80、惩罚 12→20：冻结仅 2 天 ld>100、80-100 区间 0 天，已饱和、中性，保留。⑧活跃档线微调（震荡·活跃 70/74/76、偏多·活跃 70/74）：放松变差或 inert、收紧无候选，72/71.4 已局部最优。⑨weak_regime_score_cap 60→55 在冻结复制出与 weakchg 同款的"弱市反弹"改善（日 cohort +0.061→+0.243），同属冻结假 alpha，且作用分 ≤65 前瞻 run（≥70）不可验，否决；65 无收益。⑩行业分散 _MAX_PER_INDUSTRY 3/8/999 与去惩罚：收紧到 3 明确变差（ALL 逐笔 +0.206→+0.127，印证第 79 轮"误杀"），放松中性偏负，维持 max5、pen{2:5,3:10}。⑪选中池内"新逐卡因子"（pos60 六十日位置、amount_yi 成交额、evidence_quality_score）五分位在 train/val/OOS 全部非单调、Spearman ρ≈0 且翻号（pos60 -0.028/-0.025/+0.064，amount -0.034/+0.016/-0.099），最优分位各段不一致，连单窗口单调性都不满足，选中池内无 limit_up_open_times>0 票；即新日线横截面因子在选中池同样无稳定信息（再证 L-061），不值得进前瞻。
- 永久规则：**(a) 冻结 244d 内含一段"弱市超跌反弹"regime，会让一切"放进更多弱市低分/低 chg20 票"的放松（弱市 chg20 惩罚、weak cap、弱市分数线）在冻结窗虚高；该人群在 2026 真前瞻为负且按 chg20 单调更差。禁止仅凭冻结证据放松任何弱市闸门，必须真前瞻同向；弱市低 chg20=接飞刀的 LIVE 前提在前瞻成立，weak_low_chg20_penalty=12 是被前瞻验证的有效否决，不得再以"超跌反转"名义删改。(b) v2 后 climax divisor 只砍强牛·高潮，"最强日别砍半"逐笔好看但组合层两窗口都不显著、收益靠少数日聚集、日内排名无单调性，不追。(c) 从不触发的单卡惩罚（strong/support）与已饱和的 panic 闸门是纵深防御，不因"当前无效果"而清理；真正绑定的 weak chg20 反而是有效的。(d) 继 L-072 后，出票结构层（除数/上限/冰点限/软降线/单卡惩罚/弱市 cap/活跃线/行业分散/日内排名）也到局部最优，现有日线+市场广度数据下不再有稳健风险调整后收益，下一步只能引新数据维度（分时/盘口/L2/北向，L-065），禁止在同一批旋钮上换名重挖。**
- 复现：`scripts/research_output_structure_forward.py`（前瞻 rank-decay/强牛高潮 top-N）、`scripts/research_output_levers_grid.py`（冻结 canonical 单规则杠杆网格）；佐证日志 `output/research/`：output_struct_fwd.log、output_levers_grid.log、h1_frozen.log/h1_robust.log（强牛高潮除数）、weakchg_deep.log/weakchg_fwd.log（弱市 chg20，含前瞻 chg20 单调梯度）、lastgrid_frozen.log（panic/活跃线/weakcap）、industry_frozen.log（行业分散）、cardfactor_frozen.log（选中池 pos60/amount/evidence_quality 五分位证伪）。
- 相关：L-072（格/分数带/广度/topK 证伪，本条补全出票结构与单卡惩罚层，并为其 (a) 提供 weakchg 具体实例）、L-071（canonical 口径前提）、L-061/L-065（日线横截面 alpha 挖尽、排名无区分度）、L-058（窗口偏差）、L-070（v2）、L-039/L-004（单日聚集伪 alpha）。

## L-074 分时新维度首测：信号日"下午追高"(pm_ret) 在选中池双窗口同向、经济意义明确但配对日 CI 临界，列 live_forward 影子候选、本轮不落地硬否决

- 状态：active（一条"有前景但未达落地门槛"的新数据维度 FINDING + 一组分时特征阴性结果）；生产政策维持 v2 不改。
- 适用：任何想把分时/盘口特征接进选股否决或打分的尝试；尤其"尾盘/下午涨幅、收盘位置、日内波动"类。
- 数据口径（新维度覆盖，先记清）：minute_kbars 中 **15min 密覆盖 2024-02~2026-05（冻结窗全、前瞻仅 2-5 月，约 2599 股，冻结候选匹配率 49.4%，匹配/未匹配净收益 +0.193/+0.219 无覆盖偏差）**；**5min 密覆盖 2026-01-30~09（前瞻窗全、5221 股、匹配 99.6%）**；1min 稀疏 89 天；auction_data 仅 24 天不可用；capital_flow/dragon_tiger 仅 2026 段无法双窗口。把 5min 三根合一根重采样为 15min 等价序列（09:35/40/45→09:45 桶，open=first/high=max/low=min/close=last/量额求和），即可让两窗口特征口径一致。
- 测了什么：对 v2 选中候选，用信号日 15min 等价序列构造 12 个分时特征（隔夜跳空 gap、上午收益 am_ret、下午收益 pm_ret、上午-下午差、振幅 amplitude、bar 波动 rvol、日内最大回撤、上午量占比、VWAP 上方量占比、收盘离高点 close_off_high、尾盘 30 分、量价相关），日度横截面 Spearman IC+三分/五分位+月度同号双窗口筛选。**只有"下午追高"家族（pm_ret 为主，rvol/close_off_high 辅）两窗口 IC 同向为负**；gap/am_ret/am_minus_pm/振幅/日内回撤/量占比/VWAP/量价相关均跨窗翻号或非单调（再证 L-061，且与上一轮 6 个收盘类分时特征阴性一致）。
- pm_ret 机制与增量：pm_ret=信号日 13:15 开盘→15:00 收盘涨幅。五分位 Q4（下午最追高）两窗都最差（冻结 +0.31→Q4 -0.74/胜43%，前瞻 -0.38→Q4 -0.80）。**控制信号日全天 pct_chg 双排序后增量仍在且单调**：冻结中涨档 pm 高 -0.67 vs 低 +0.46、高涨档 -1.04 vs +0.71；前瞻三个涨幅档都是 pm 高更差——不是"当天涨多了"的代理，而是"涨幅集中在尾盘追进收盘"的独立 fade 信号。分格：弱势·活跃/强牛·活跃/强牛·高潮 的 pm 顶档两窗皆毒；偏多·活跃（冻结 +0.54 正）、震荡·活跃/弱势·平淡（前瞻为正）冲突，不可一刀切。
- canonical 终审（生产同构，真实 evaluate_selection_policy 两臂=全卡 vs 排序前剔除追高卡让第 11 名递补，冻结走 _simulate_card 真实因果出场、前瞻 join canonical 净收益；缺 pm_ret 的卡保守保留）：最稳配置=**仅在 {弱势·活跃,强牛·高潮} 格内否决 pm_ret>T，T∈[1.25,1.75]% 为连续高原**。以 T=1.25 为例：冻结逐笔 +0.206→+0.269、胜 .540→.552、PF1.107→1.143、日 cum+1.29→+3.17、MDD 改善，配对 42 日 CI[+0.008,+0.522]（下界微正），砍 67 笔均 -1.04/胜39%；前瞻逐笔 -0.352→-0.249、PF0.833→0.881、cum -3.89→-1.84、MDD -8.26→-6.97、**7 个月全正**，但配对 33 日 CI[-0.004,+0.743] 下界微负；替换机制（被砍 vs 递补）冻结 -1.04/胜39% vs -0.14/胜58%、前瞻 -1.46/胜45% vs -0.10/胜48%，替换增益 +0.90/+1.36pp 但 bootstrap CI[-0.86,+2.62]/[-0.80,+3.47] 跨 0。
- 为何本轮**不落地**：方向、逐笔/PF/MDD、被砍≪递补、控制涨幅后增量、前瞻全月正——这些两窗全部同向；但项目铁律要求"双窗口配对日差 CI 下界都>0 且不阈值敏感"，而 canonical 配对日/替换 CI 下界贴 0（一窗微正一窗微负）、T 相邻档位即转负、冻结分时匹配仅 49%、弱市递补票本身仍为负稀释日级效果。手写 cohort（不递补）此前显示两窗 CI 都>0，canonical 一加"第 11 名递补"就降到临界——正是 L-071 警告的口径差，故以 canonical 为准，判为**经济真实、统计未定论**，不做硬否决、不与显著项捆绑。
- 永久规则：**(a) 分时"下午追高"是日线 alpha 挖尽后（L-065）第一个在【选中池】两窗口同向的新维度信号，方向=尾盘追进收盘者未来 5 日 fade，作用格=弱势·活跃/强牛·高潮，阈值 pm_ret≈1.25-1.75%，复现脚本齐备；它应走 live_forward 影子观测累积，达到 release_gate（≥100 笔、≥20 信号日、前瞻 canary 同向、配对日 CI 下界稳定>0）后再议绑定，禁止现在凭单窗/手写 cohort 落地。(b) 分时特征筛选必须两窗口口径一致（5min 重采样 15min），且先控制信号日全天涨幅再谈增量，否则会把"当日动量"当新因子。(c) 其余 11 个分时特征已跨窗证伪，勿换名重挖。**
- 复现：`scripts/research_intraday_selected_panel.py`（选中池 12 特征面板，5min→15min）、`research_intraday_selected_screen.py`（双窗 IC/分位筛选）、`research_intraday_chase_deep/threshold/conditional/grid/robust.py`（复合/控制涨幅/分格/高原/稳健性）、`research_intraday_pmret_universe.py`（全宇宙 pm_ret 查找）、`research_intraday_chase_canonical.py`（**canonical 终审**，证据 output/research/intraday_chase_canonical.json）、`research_intraday_chase_swap.py`（被砍 vs 递补）、`research_intraday_chase_joint.py`（pm×当日涨幅联合：加 conjunct 使被砍票更毒但样本骤减、canonical CI 仍跨 0，不能救回显著性，停止继续挖阈值以免过拟合）。
- 待 live_forward：把 pm_ret 接进 feature_builder/return_improvement_shadow 做**非绑定影子标注**（production_effect_applied=False，热路径目前无分时数据，需先接 15min/5min 读取），累积弱势·活跃/强牛·高潮 格内 pm_ret>1.25% 的 would-veto 票的真实 5 日净收益，达标后再决定 v3。
- 相关：L-065（须换新数据维度，本条是其首个两窗同向候选）、L-071（canonical vs 手写 cohort 口径差，本条被其拦下）、L-058（窗口/阈值过拟合）、L-072/L-073（日线选股/出票层已局部最优）、L-069（涨停微观情绪择时证伪）、L-061（横截面排名无区分度）。

## L-075 新数据维度解锁后首测：daily_basic(规模/换手/PE)与 limit_up_board 个股封板属性双窗口全部证伪；"极端高换手"毒格已被现有流动性闸门短路，不落地

- 状态：active（FINDING，否定性 + 数据工程解锁）；生产政策维持 v2 不改，无 v3。
- 适用：任何想用估值/规模/换手率/连板高度/炸板/封单额做横截面选股或否决的尝试；评估"辅助表是否够做双窗口"前先查覆盖矩阵。
- 数据覆盖矩阵（本轮先立的前提，冻结=2025-01-15~2026-01-16 共244日，前瞻=2026-02-09~2026-08-05 共117日，≥100/≥80 日才算覆盖）：开工时**只有 daily_kbars、index_daily、stk_limits 真正跨双窗口**；daily_basic 仅冻结、capital_flow 仅前瞻、limit_up_board/dragon_tiger/auction_data/margin_detail 等全部单窗或零星。stk_limits 只有 up/down_limit 涨跌停价（由价格确定性推导，零新信息）；index_daily 仅 000300/000905 覆盖全冻结（其余指数 2025-05-06 起），风格轮动本质是给规模因子择时，低优先。**结论：辅助数据覆盖断档才是"新因子无法双窗口验证"的真实瓶颈，不是没想到因子。**
- 数据工程解锁（幂等、增量、不删旧，已实际写入本地 MySQL）：①`scripts/backfill_daily_basic.py` 回填 2026-01-19~2026-09-08 共 **858348 行/156 交易日**，daily_basic 从此跨双窗口；②`scripts/backfill_limit_up_history.py --start 2025-01-01 --end 2026-03-20` 回填 limit_up_board 冻结窗，成功 **135/244 交易日/16056 行**（其余日因 `state_adapters.py:309` 把 tushare 偶发超长 last_time 直接 str() 超 `tables.py:265` String(8) 整日回滚跳过——数据管道健壮性小 bug，属他人 data-pipeline 范畴，本轮不改，仅记录；不影响 135 日大样本结论）。资金流因 L-064 已全市场证伪方向，不回填、不重挖。
- 全市场 strictly-prior 五分位双窗口筛（`scripts/research_newfactor_fullmarket.py`，T收盘已知、T+1open→T+5close 毛 fwd5 仅用于快速杀因子，缓存 output/research/newfactor_fullmarket.pkl 200万行）：
  1. **规模 log circ_mv 方向两窗反转→否决**：冻结 Q0(小)+1.154→Q4(大)+0.690（Q5-Q1=-0.464、逐月同号0.77、小盘占优），前瞻翻为 Q0-0.372→Q4-0.146（Q5-Q1=**+0.226**、大盘更抗跌）。典型 regime beta，复刻 L-072(a)；极端尾部同样反转（最大5%档冻结-0.43、前瞻+0.27）。
  2. **换手率 turnover_rate 无单调、驼峰→否决斜率**：冻结中段最好(Q2+1.125)、两端差，Q5-Q1+0.027/同号0.46（本就无信号）；前瞻 Q5-Q1-0.092/同号0.29。
  3. **估值 pe_ttm 两窗翻号→否决**：冻结 Q5-Q1+0.392（成长占优），前瞻-0.084；极端尾部两窗差≈0。
  4. **limit_up_board 个股封板属性两窗方向全反转→否决**（仅当日涨停股，冻结n14948/前瞻n11147）：连板高度 limit_times——冻结 1板+2.45 最好、4板-4.42 最差，前瞻反过来 1板-0.91、4板+1.94/5板+1.28（高连板龙头仅稀疏 n90/35 为正）；炸板次数 open_times 冻结 0炸+0.69→5炸+3.83 递增、前瞻无此梯度且多为负；封单额 fd_amount 冻结小封单最好(Q0+2.87>Q2+0.38)、前瞻小封单最差(Q0-1.39)；首次封板时间 first_time——09:30前秒板/一字两窗最强(+1.105/+1.872)但属 T+1 开盘买不进、胜率仅0.40-0.45的彩票尾，14-15尾盘偷封冻结+0.053(持平)、前瞻-2.200(仅前瞻弱、非同向)，中段10-11/13-14档两窗翻号、尾盘-早封逐月同号仅0.62/0.57。全部 regime 依赖，与 L-069 微观情绪证伪一致。
- "极端高换手否决"的生产同构终审（唯一全市场两窗同向的线索，`scripts/research_turnover_veto_canonical.py`，真实 evaluate_selection_policy 两臂=全卡 vs 排序前剔除高换手卡让下一名递补，冻结 _simulate_card 因果、前瞻 join canonical 净，配对日差 bootstrap5000）：全市场层面日截面 topX% 换手桶两窗同向最差且阈值单调（top1% 冻结差-1.39/85%月份为负、前瞻差-0.93；越极端越差）。**但选中池经 v2 流动性/质量闸门后换手分位极少触顶**（冻结选中 max pct0.960、仅15张>0.95；前瞻选中 max0.938、>0.95 为 0 张）：阈值 pct>0.95 时冻结剔15张毒卡（均-3.095/胜0.267、递补+1.317、日cohort +0.0364→+0.0639、配对CI[+0.006,+0.056] 下界微正），**前瞻却 0 剔卡完全空转，无法双窗口验证**；放宽到两窗都剔卡的 pct>0.90，两窗 Δcohort 虽为正(+0.082/+0.035)但配对 CI 全跨0（[-0.034,+0.202]/[-0.062,+0.131]）、逐月正占比仅0.46/0.43；pct>0.88 前瞻翻负(-0.021)、pct>0.85 冻结被砍票均收益转正(+0.163，开始砍赢家)。**非单调、不显著、靠砍正期望票换数字→否决，不落地。**
- 永久规则：**(a) 估值/规模/换手水平/连板高度/炸板次数/封单额/首次封板时间这一族 daily_basic+limit_up_board 横截面因子，在当前两窗口下方向被 regime 主导（2025 趋势上行 vs 2026 震荡走弱，斜率两窗必翻；唯一两窗都强的"秒板/一字"买不进且胜率<0.5），禁止换名重挖；要挖必须先证明它在选中池两窗同向且 canonical 配对 CI 下界>0。(b) 全市场成立的"极端尾部毒格"不等于选中池可改：生产既有闸门可能已把该尾部挡在门外（高换手即如此，前瞻选中池根本到不了 top5%），canonical 终审必须先报告选中池在该因子上的实际分位分布与剔卡数，空转的否决不许落地。(c) 新增辅助因子研究前先跑双窗口覆盖矩阵，单窗覆盖的表只能做探索、不得作为改生产阈值依据（L-071/L-058）。(d) limit_up_board 历史回填受 last_time 超长 bug 污染会整日缺数，复现封板类结论前先 COUNT(DISTINCT trade_date) 确认覆盖。**
- 复现：覆盖矩阵=对 information_schema/各表 `COUNT(DISTINCT trade_date)`；`scripts/research_newfactor_fullmarket.py`（全市场五分位双窗口，删 newfactor_fullmarket.pkl 可重建，扫 daily_kbars 约1-2分钟）；`scripts/research_turnover_veto_canonical.py`（高换手否决 canonical 终审，阈值在 THRS）；日志 output/research/newfactor_fullmarket{,2,3}.log、turnover_veto_canonical{,2}.log、backfill_daily_basic_fwd.log、backfill_limitup_frozen.log。
- 相关：L-064（资金流全市场方向反转，故不回填资金流）、L-069（涨停微观情绪择时证伪，本条补个股封板属性横截面同样证伪）、L-061/L-065（日线横截面 alpha 挖尽、量比类已封死）、L-072(a)/L-073（regime 翻号、出票/单卡层局部最优）、L-074（分时 pm_ret 是唯一两窗同向但 CI 临界的影子候选，与本条互补：一个走分时、本条封死日级估值/封板）、L-071（canonical 口径为政策级唯一依据）、L-062（辅助数据覆盖前提）。

## L-076 第三段真前瞻窗（08-07~09-02，从未分析）复核：v2 未失效；三窗口证明无稳定毒格、"新毒格/状态转移"是跨窗翻号+单日聚集双重伪信号；pm_ret 第三窗验证被分钟覆盖堵死

- 状态：active（验证性 + 否定性 FINDING）；生产政策维持 v2 不改，无 v3。
- 适用：任何想"再找一个 regime×情绪毒格做外科否决"、或用 regime 状态转移（前一日→当日）做择时门控、或想给 L-074 pm_ret 补第三窗的尝试。
- 第三窗来源与可信度：旧 canonical run btr_fa625 止于 2026-08-05；新 run **btr_2bec6fed（03-09~09-02，23765 候选全带净收益）与旧 run 在 22361 个重叠 (日,股) 键上净收益差恒为 0**，证明同一因果引擎的一致超集，可取 08-07~09-02 共 19 交易日作真正未见窗（市场快照由日线广度构建，19 日全有）。
- v1 vs v2（未见窗，生产同构 evaluate_selection_policy 重放+join canonical 净）：v1 n130 逐笔 -0.426/胜.454/PF.808/日cum-8.53/MDD-18.20/出票19天；v2 n95 逐笔 -0.558/胜.442/PF.764/**日cum-8.07/MDD-18.16/出票仅12天**。这是普跌弱市窗，两版都亏；v2 靠门控少出票（12 vs 19 天）使日度 cohort/MDD 略优，逐笔均值反而更低（留下的票在普跌日同样跌）。**结论：v2 未失效，其价值在弱市主动收缩，不在于逐笔选股更准；不要因弱市逐笔为负就回退 v2。** 机会成本拆解：两版共同出票的 12 天选股逐笔完全一致（均 -0.558，override 只在空仓日起作用）；v2 少出的 7 天里 v1 共 35 笔平均 **-0.066/胜.49（近乎打平、略负，含 08-31 +3.04 与 08-27/09-01 各-1.7）**——收缩躲的是近零期望日、方向正确且未系统性错杀正期望票。
- 三窗口格地图（v2 选中逐笔，冻结/原前瞻/第三窗 avg·win）——**没有任何一格三窗同负**：弱势·平淡 冻结-0.66/.53(n17)→原前瞻**+0.32/.64(n74)**→第三窗-4.84/.00(n10)；震荡·活跃 原前瞻**+0.38/.61(n104)**→第三窗-3.54/.10(n10)（冻结选中无此格）；弱势·活跃 +0.07→-0.38→**+0.77/.63(n40)**；偏多·活跃 +0.94→-2.30→+2.76；强牛·高潮 +0.05→-0.06→-1.11。每个看似"新毒格"的格都在另一窗为正，整格否决必砍他窗正期望票（复刻 L-072①）。
- **单日聚集证伪（决定性）**：第三窗两个"灾难格"全部是单日聚集——弱势·平淡 10 笔全在 **2026-08-19**、震荡·活跃 10 笔全在 **2026-08-21**，即这两天全市场普跌、当日 10 张票一起亏（L-039/L-004/L-072 震荡·平淡 07-16 同款单日伪 alpha）。regime"状态转移"分解同理，最负的转移 n 都=5/10 且落在这两天，样本由 output_limit 凑成整十、不具独立性。
- pm_ret 第三窗为何做不了：第三窗选中候选 281 笔，但分钟线 5 月后采集退化——15min 仅 08-03/04、5min 在 08-07 后仅零散 8 天、1min 只覆盖约 300 只**固定样本**（跨各板块、非指数成分，与选中中小盘 08-07~09-02 的 score≥70 候选匹配率仅 **1%**，281 笔只匹配 3 笔）。数据覆盖不足，**不得用 1% 匹配的样本硬造 pm_ret 第三窗结论**；这是数据工程堵点（分钟源 xiaoshi_minute_source 在改），需先把选中宇宙的分钟线补齐到日频全市场，L-074 的 live_forward/第三窗才成立。
- 永久规则：**(a) 三窗口（冻结2025趋势、原前瞻2026震荡、第三窗08-07~09-02普跌）一致表明 regime×情绪格的期望符号由大盘 regime 决定、格级/格转移否决不存在三窗稳定毒格，禁止再以"某格在最近弱市亏"为由整格否决或抬线；判毒格必须同时过三窗同向 + 非单日聚集（亏损分散在≥5 个独立交易日）两关。(b) 评估某格前先按日展开，若该格亏损 n 集中在 1-2 天即判单日聚集伪信号，不进入 canonical 终审。(c) 新 canonical run 与旧 run 重叠键净收益必须逐键一致（差=0）才可作外延窗，否则先排查口径。(d) 分钟/新维度第三窗先查选中宇宙匹配率，匹配率过低（如<50%）直接判数据不足、不得结论。**
- 复现：`scripts/research_third_window.py`（第三窗 v1/v2 对照+格+状态转移，证据 output/research/run3_canonical.pkl、run3_v2_selected.pkl）；三窗格地图由 canon_frozen_v2_selected.pkl/canon_forward_v2_selected.pkl/run3_v2_selected.pkl 现算；run 一致性=对 backtest_candidates⋈backtest_trades 按(日,股)配对 AVG(net_new-net_old)。
- 相关：L-072①（格级否决跨窗冲突、震荡·平淡单日聚集，本条用第三独立窗再证并补"状态转移同样伪"）、L-039/L-004（单日聚集伪 alpha）、L-074（pm_ret 唯一同向影子候选，本条量化其第三窗数据堵点）、L-075（覆盖矩阵/辅助数据瓶颈）、L-071（canonical 口径）、L-058（窗口偏差，第三窗是防过拟合的真正未见样本）。

## L-077 信号新鲜度（重复/连续入选）三窗口证伪；并精确定位 pm_ret 第三窗的分钟数据缺口

- 状态：active（否定性 FINDING + 数据缺口清单）；生产维持 v2 不改。
- 适用：任何想用"该股前几日是否已在候选池/是否连续多日入选"做新鲜度加分或陈旧否决的尝试；以及评估分钟线能否支撑新第三窗。
- 假设：信号日 T 前 k 个交易日内该股出现在 score≥70 候选池的天数 prev_k；首次出现=新鲜突破，连续出现=陈旧/延伸，预期二者净收益有别。三窗口（冻结/原前瞻/第三窗，候选历史分别取 holdout 快照全卡、两个 canonical run 的 backtest_candidates 并集）。
- 证据（v2 选中逐笔，新鲜 prev=0 vs 重复 prev≥1 的 avg/胜）：**方向三窗翻号、各 k 非单调**。prev1：冻结 新鲜+0.252 vs 重复-0.267（新鲜好），前瞻 -0.572 vs -0.173（重复好，翻），第三窗 -0.504 vs -0.719（新鲜略好）；prev3：冻结+0.234 vs +0.087、前瞻-0.284 vs -0.379、第三窗-0.841 vs -0.185（第三窗重复明显更好，又翻）；prev5 冻结重复更好(+0.304 vs +0.171)、前瞻新鲜更好、第三窗重复更好。prev3 逐档（0/1/2/3）每窗都非单调（冻结 +0.234/-0.058/+0.420/+3.14，前瞻 -0.284/-0.041/-0.809/-0.345）。无稳定符号、无单调性，否决。
- 永久规则：**信号新鲜度/重复入选（含连板式"连续 N 日在候选池"）在选中池无三窗稳定信息，禁止换名（"首板入选/老票/持续强势"）重挖；这与 L-061/L-073 一致——经 v2 选出后，任何由历史入选记录派生的横截面标签都不再有单调区分度。**
- pm_ret 第三窗分钟缺口（精确清单，解锁 L-074 的前置数据任务）：5min 在 2026-06（21日/5209 只）、07（23日/5206）仍是全市场；**08 月起变间歇——全市场 5min 仅 08-19/08-20/08-28 三天，09 月退化为约 300 只**；15min 2026-05 后基本停止（07/08 各 1-2 个零散日）；1min 全程只有约 400-560 只固定子集、从非全市场。故第三窗 08-07~09-02 用 5min 最多覆盖 3 个独立日（且 08-19 正是 L-076 的单日普跌聚集日），独立日不足，pm_ret 第三窗不可结论。**要解锁：回填 2026-08-07 起缺测交易日的【全市场 5min】（数据管线 xiaoshi_minute_source 域），回填后重跑 research_intraday_chase_canonical 把第三窗并入，检验配对日 CI 下界能否稳定>0。**
- 复现：`scripts/research_signal_freshness.py`（三窗新鲜度，缓存 output/research/freshness_{frozen,forward,run3}.pkl）；分钟缺口=对 minute_kbars `LEFT(trade_date,7),freq` 聚合 COUNT(DISTINCT code/trade_date)。
- 相关：L-061/L-073（选中池横截面/历史标签无区分度）、L-076（第三窗与单日聚集）、L-074（pm_ret 影子候选与其数据堵点）、L-075（辅助数据覆盖矩阵）、L-058（多窗口防过拟合）。

## L-078 解锁龙虎榜/两融双窗口全量回填后，连同集合竞价一起在生产同构选中池三方向全部证伪；并记录"覆盖不全假阳性"与"事件表过稀"两条数据教训；v2 维持不改

- 状态：active（否定性 FINDING + 数据工程沉淀）；生产维持 v2，不出 v3。
- 适用：任何想用龙虎榜上榜/买卖比、两融融资买入拥挤度、集合竞价（竞价换手/竞价跳空/竞价额/竞价量比）做选股否决或加分的尝试；以及评估"按交易日接口的辅助表"历史回填是否可信。
- 数据工程（本轮把三张按交易日接口的辅助表回填到双窗口，幂等增量、0 删除）：dragon_tiger 冻结 244/前瞻 117 日全跨（28966 个(date,code)）；margin_detail 冻结 244/前瞻 117 全跨（冻结 1217485+前瞻 504071 行）；auction_data(stk_auction) 因 tushare 限速 8-20s/日、circuit_open 瞬时空返回、官方偶发同(code,date)多行，只回填到冻结约 43 日/前瞻约 81 日（部分覆盖，但已足够在选中池做结构性否决，见下）。驱动脚本：`backfill_aux_dataset.py`（通用，经 run_auxiliary_application）、`backfill_auction_direct.py`（直连 stk_auction、按 ts_code 去重、删当日再插、已填充日>5000 行跳过可续跑、空结果退避重试 3 次）。
- 龙虎榜：**全市场"上榜后 fade"两窗同向为真**（冻结上榜 n13397 均-0.470/胜.399 vs 非+0.942，日差-1.38、逐月同号仅.08=92%月份为负；前瞻 n6975 均-1.299 vs 非-0.299，日差-0.91）；榜内买卖比 bs=buy/sell 两窗都是 Q0 最差/Q4 最好（冻结 Qhi-Qlo+1.07、前瞻+1.70）。**但传不进 v2 选中池**（基线冻结 n1010 均+0.206/cohort+0.0364；前瞻 n721 均-0.352/cohort-0.2007）：①一刀切上榜否决，冻结剔18(-0.94)但递补12(-1.645)更差、Δcohort+0.0062/CI[-0.020,+0.033]跨0、逐月.38，前瞻剔7张均**+0.494（赢家）**、Δ-0.0018/CI跨0；②只剔弱势榜 bs<1，冻结剔7(-3.427 毒)、Δ+0.0127/CI[-0.005,+0.034]跨0，前瞻仅剔2张均**+3.491（赢家）**、Δ-0.0033；bs<0.7 前瞻剔到的唯一一张是 +5.824 赢家。全部两窗冲突/CI 跨 0/样本极小，否决。
- 两融：标的 vs 非无差异（日差-0.018/+0.126）。**融资买入额 rzmre 的全市场负斜率是"覆盖不全假阳性"**：前瞻只回填约 60 日时曾见 Qhi-Qlo≈-0.61（高融资买入=拥挤=差），补齐到 117 日后斜率消失为 -0.009（冻结-0.329/前瞻-0.009）；rzmre_norm(买入/余额)两窗都≈0（-0.027/-0.025），rzye_chg(余额环比)两窗翻号（-0.068/+0.325）。canonical 否决同样不过：rzmre top5% 冻结 Δcohort **-0.0215 反而变差**（剔的232张均+0.128 是赢家、递补147张-0.236），前瞻 Δ+0.0724/CI[-0.061,+0.207]跨0、剔的71张均+0.469 仍是赢家；top10% 两窗 CI 全跨0（冻结[-0.10,+0.23]/前瞻[-0.09,+0.18]）、前瞻剔的108张均+0.326 为赢家。否决。
- 集合竞价（部分覆盖：冻结约43日/前瞻约81日，全市场毛 fwd5）："有无竞价数据"事件差冻结+4.13/前瞻+1.56、逐月同号1.00，**但这是可交易性/覆盖伪影**——有 stk_auction 行的都是当日正常可交易票，停牌/代码不匹配的无行且未来收益差；而 v2 选中卡 T+1 open 必可交易、必然都有竞价行，在选中池零区分度，不可用。子集内：log_auc_amt 两窗翻号（-0.22/+0.65，Spearman≈0）、volume_ratio 翻号（-0.21/+0.19）；auc_turn 冻结非单调（Qhi-Qlo+0.19 但 Spearman-0.044）、前瞻+0.62；auc_gap 两窗尾部略正（+0.30/+0.35）但 Spearman≈0.01（仅尾差、非单调），且 auc_gap≈开盘跳空，L-074 已在选中池证伪。canonical 否决（缺竞价数据保守保留）：**auc_turn 对选中池几乎无咬口**——bottom10% 两窗各仅剔2张、bottom5% 剔0张（动量入选票结构性地不在全市场竞价换手尾部，此性质不随覆盖增加改变）；auc_gap bottom10% 冻结剔的5张**全是赢家(均+5.824)、Δ-0.0082 有害**，前瞻剔104(-0.494) Δ+0.0167/CI[-0.087,+0.125]跨0/逐月.43，bottom5% 同样冻结剔赢家、前瞻 CI 跨0。否决。
- 其余事件表评估为"过稀/单窗不可用"：holder_trades（重要股东增减持，冻结628行仅67股/前瞻197行31股）、repurchases（冻结11750行仅50股、前瞻0）、share_floats（冻结207行12股、前瞻0）、block_trades（冻结129行61股/前瞻627行197股）——独立个股数太少或只单窗，无法在选中池做稳健双窗口，不回填不重挖。
- 永久规则：①**龙虎榜全市场 fade 真实存在，但与 L-064 资金流、L-075 换手/规模同一性质——全市场横截面异象经 v2 强势动量+质量筛选后被选择效应抵消甚至反向，禁止再以"上榜=出货/弱势榜=毒"做选中池否决（除非有榜内席位级、且双窗 CI 下界>0 的新机制证据）。**②**任何按交易日回填的辅助表，因子斜率必须在窗口覆盖补齐后复核；覆盖不全（尤其前瞻只填几十日）会制造跨窗假阳性，rzmre 从-0.61 变-0.009 即铁证。**③**"某表有无数据"的事件差在选股系统里几乎必然是可交易性/存活伪影（选中票都可交易→都有数据→零区分度），不得当作 alpha。**④动量入选票在"全市场低位尾部"类因子（竞价换手/换手/融资买入）上结构性缺咬口，这类"否决底部"思路在选中池反复失败，不要再换同义因子。**
- 数据管道已知 bug（属 data-pipeline 域，本轮未改生产代码，仅记录）：state_adapters.py limit_up `last_time` 直接 str() 超 tables.py String(8) 会整日回滚；stk_auction adapter 未按(code,trade_date)去重，官方多行触发 duplicate natural key 整日失败（backfill_auction_direct.py 在脚本侧绕过）。
- 复现：`scripts/backfill_aux_dataset.py`、`scripts/backfill_auction_direct.py`（回填）；`scripts/research_newfactor2_aux.py`（三表全市场双窗口五分位，面板缓存 output/research/newfactor2_aux_panel.pkl，权威日志 newfactor2_aux_now.log）；`scripts/research_aux_veto_canonical.py`（龙虎榜一刀切/bs<1/bs<0.7 + rzmre top5/10% 生产同构终审，日志 aux_veto_canonical_final.log）；`scripts/research_auction_canonical.py`（竞价换手/跳空选中池尾部否决，日志 auction_canonical_now.log）。
- 相关：L-064（资金流全市场反向、选择效应）、L-075（规模/换手/PE/封板证伪、辅助数据覆盖矩阵）、L-074（开盘跳空/分时在选中池证伪、pm_ret 影子候选）、L-061/L-065/L-073（日线横截面 alpha 挖尽、选中池历史标签无区分度）、L-071（研究口径必须与生产同构）、L-058（多窗口/覆盖防过拟合）。

## L-079 解锁 L-077 分钟缺口后，pm_ret 尾盘追高否决经【第三真前瞻窗】翻号证伪，正式退役 L-074 影子候选；v2 维持不改

- 状态：active（否定性 FINDING，关闭一个挂起的 live_forward 影子候选）；生产维持 v2，不出 v3。
- 适用：任何想用"信号日午后涨幅 pm_ret（15:00 收/13:15 开-1）在特定 regime·情绪格内过高→尾盘追高→否决"做硬否决/择时的尝试，以及评估 L-074 遗留影子候选是否落地。
- 数据解锁（完成 L-077 指定的前置任务）：baostock 源对 2026-08-07~09-02 有完整 5min（8 股可行性测试 0 失败）；写 `backfill_5min_codes.py` 只对第三窗 run3(btr_2bec6fed) 全候选 774 只按股幂等回填，共存 704160 行、跳过 1、0 失败、56 分钟。回填后第三窗 1375 个候选对、95 个 v2 选中对的 5min 覆盖率均 100%（每对≥45 根），结论无覆盖伪影。
- 三窗口 canonical（生产同构 evaluate_selection_policy 两臂：全卡 vs 排序前硬否决 cell∈{弱势·活跃,强牛·高潮} 且 pm_ret>T，第 11 名递补；冻结因果出场、前瞻/run3 join canonical 净）：
  - 冻结 244d（旧）：T1.25 逐笔 +0.206→+0.269、cohort 日均 +0.061→+0.137、配对 CI[**+0.008**,+0.522]（下界刚过 0），被砍 67 张均 **-1.04（毒）**。
  - 前瞻 117d（旧）：T1.25 -0.352→-0.249、cohort -0.267→-0.122、CI[**-0.004**,+0.743]（下界刚破 0），被砍 56 张均 **-1.46（毒）**。
  - **第三窗 19d（本轮新，真前瞻）：T1.25 逐笔 -0.558→-0.961（更差）、win.442→.410、PF.764→.617、cohort 日均 -0.673→-0.986，配对 6 变化日 Δ均-0.628、CI[-2.04,+0.72]跨0、better-day.33、月正 0-2；被砍 10 张均 +3.15、win.70（弱势·活跃 +8.01/n4、强牛·高潮 -0.08/n6）——砍的是赢家。**T1.5/T1.75 同向有害（Δ-0.817/-0.536，被砍均 +3.15/+2.79）。
- 判读：被砍卡条件均值从冻结/前瞻的 -1.0/-1.46（毒）在最新真前瞻窗翻成 +3.15（赢家），符号反转且跨阈值一致；尤其"弱势·活跃"格内午后拉升的票在 08-09 窗次日强势反弹（+8%），否决正好砍掉最大赢家。冻结/前瞻那点临界 CI 本就贴着 0，是特定 regime 的样本噪声，第三窗证伪。这与 L-058（多窗口防过拟合）、L-076（毒格/状态跨窗翻号）同一性质。
- 永久规则：**pm_ret 尾盘追高否决（含"午后涨幅过大=追高=次日回落"及其在弱势·活跃/强牛·高潮格内的任何变体）三窗口证伪、正式退役，不再作为 live_forward 影子候选，禁止换名（"尾盘拉升/午后冲高/日内追涨惩罚"）重挖；高 pm_ret 的次日方向是 regime 条件量、无稳定符号。** 至此 L-074 的分时线索全部关闭（其余分时特征 L-074 已证伪，pm_ret 本条证伪）。
- 复现：`scripts/backfill_5min_codes.py --pairs-pkl output/research/run3_canonical.pkl --start 2026-08-07 --end 2026-09-02`（回填，日志 backfill_5min_run3.log）；`scripts/research_pmret_run3_canonical.py`（第三窗终审，日志 pmret_run3_canonical.log）；旧两窗对照 output/research/intraday_chase_canonical.json；第三窗候选/净收益来自 scripts/research_third_window.py 的 load_run3（run btr_2bec6fed）。
- 相关：L-074（pm_ret 影子候选出处、其余分时证伪）、L-077（定位本窗分钟缺口）、L-076（第三窗与毒格翻号）、L-058（多窗口防过拟合）、L-071（生产同构）、L-069（涨停微观情绪择时证伪）。

## L-080 外部指数慢趋势门控（000905 价格 vs MA20/10日动量）三窗口证伪：冻结/前瞻显著改善、第三窗砍超跌反弹赢家而翻号；"急跌豁免"不同步，不落地

- 状态：active（否定性 FINDING，关闭一个看似最有希望的独立数据源择时门控）；生产维持 v2，不出 v3。
- 动机与独立性：生产 `_market_base` regime **只用当日横截面上涨家数 up_ratio（snapshot 无任何指数价格字段）**，L-072③测的广度 ma3/ma5 仍是内部广度；本方向首次用**独立外部数据源** index_daily（000905 中证500 贴近选中中小盘，2023-05 起 801 日三窗全覆盖）构造慢趋势：收盘 vs MA20/MA60、MA20 斜率、5/10/20 日指数收益、20 日回撤。假设慢趋势不像当日广度那样逐日翻号，或能稳健识别"退潮期"收缩。
- 探索层（v2 选中逐笔）：above20（指数在 MA20 上/下）冻结 +0.335/-0.098、前瞻 +0.210/-0.730（上方明显好），**但第三窗硬翻：上方 -2.264/胜.25（8 天见顶回落，含 08-19/21 崩盘）、下方 +2.367/胜.77（4 天超跌反弹）**；ix_ret10 三分位冻结/前瞻单调（越高越好）、第三窗 Q0（10 日最跌）+1.942 最好，同样翻号。
- canonical 全日门控终审（门控日整日不选出、空日记 0；逐笔/日 cohort SLEEVE/配对日差 bootstrap5000）：
  - **above20 门控（只在 MA20 上出票）**：冻结逐笔 +0.206→+0.335、cum+1.48→+4.38，但配对 CI[-0.62,+1.48]跨0、月正4-5；**前瞻 -0.352→+0.210（由负转正）、cum-3.91→+1.52、配对 CI[+0.012,+1.273] 下界>0、月正5-2**；**第三窗 -0.558→-2.265、cum-1.34→-3.03，空出的 4 天恰是 +2.367/胜.77 的反弹赢家，配对 Δ-2.52**。
  - **ix_ret10≥-3% 门控**：冻结 +0.312、前瞻 -0.352→+0.015、CI[+0.018,+1.515]，第三窗却 -0.558→-2.234、空出 3 天 **+4.135/胜.88** 的赢家、CI[-5.77,-2.76] 显著为负。
  - strong_down（双均线下方空仓）冻结空到的是 +0.266 赢家、第三窗空到 +2.367 赢家，两窗有害；dist20≥-2% 前瞻 CI[-0.014] 贴 0、第三窗仅动 1 天。
- 判读：**这是"双窗口会错误放行、第三窗拦下"的教科书案例**——above20/ix_ret10 门控仅凭冻结+前瞻本应落地（前瞻还由负转正、CI 下界>0），但 2026-08 下旬是超跌反弹 regime：指数跌破 MA20、10 日最跌的日子次日反弹最猛，门控正好砍掉最大赢家。顺势/逆势的有效性本身随 regime 切换，MA20 状态无法事前区分。
- "急跌豁免"救不回（关键反证）：试图只空"阴跌破位"、保留"近 3 日急跌 capitulation 反弹"。ret3≤-4% 深急跌在冻结 +1.485/胜.72(n18/3天)、前瞻 +0.253/胜.60(n94/11天) 确同向为正，但**第三窗无此日**；而第三窗反弹赢家（08-24~26）发生在急跌窗口之后、ret3 已修复，被归为"阴跌"，与冻结/前瞻"阴跌该空"矛盾；ret3≤-2% 档第三窗反而是 -2.05 的输家。不存在三窗自洽的豁免阈值。深急跌反弹仅 3+11 个日簇（L-039 单日聚集风险）、第三窗不可验、且方向是"在最恐慌日加仓"无可执行选股动作，不采用。
- 永久规则：**(a) 外部指数价格趋势（价 vs MA20/MA60、指数 N 日动量/回撤）做全日择时门控，与内部广度门控同属 regime beta，三窗翻号，禁止以"趋势向上才出票/跌破均线空仓"及其斜率/动量变体落地；双窗口（含 CI 下界>0）不足以放行，必须第三独立窗同向。(b) 一个门控若在某窗"由负转正、CI>0"而在另一窗砍掉正期望赢家，即判 regime 条件量、不是 alpha。(c) 用"急跌/超跌豁免"修补趋势门控前，必须证明豁免条件在三窗命中同一批赢家；只在历史两窗同向、第三窗 0 命中的，属不可验，不落地。**
- 复现：`scripts/research_index_trend_gate.py`（三窗指数慢趋势分层，缓存 ixgate_*.pkl）、`scripts/research_index_gate_canonical.py`（全日门控终审，日志 output/research/index_gate_canonical.log）；指数源 index_daily 000905.SH。
- 相关：L-072③（内部广度 abstain 翻号，本条换独立外部指数源仍翻号）、L-076（第三窗毒格/状态翻号、08-19/21 与 08-24/26 行情结构）、L-058（多窗口防过拟合，本条是第三窗价值的直接例证）、L-079/L-074（分时门控翻号退役）、L-071（canonical 口径）。

## L-081 板块/行业横截面（动量/广度/涨停拥挤/板块内相对强弱）与 daily_kbars 残余日线列三窗口证伪；北向两表覆盖即死；v2 维持不改

- 状态：active（否定性 FINDING，关闭"板块轮动/拥挤"与最后一批未测日线列）；生产维持 v2，不出 v3。
- 适用：任何想用"个股所属行业的近期动量/当日强度/上涨广度/涨停家数拥挤/板块内相对强弱"做选股否决或加分，以及想再从 daily_kbars 找漏网日线因子的尝试。
- 数据：code→industry 取 stocks 表（112 行业、5527/5527 全覆盖），板块特征由 newfactor_fullmarket.pkl 全市场面板（412 日、毛 fwd5=T+1open→T+5close）按 (日,行业) 现算，三窗＝冻结 2025-01-15~2026-01-16 / 前瞻 2026-02-09~08-05 / run3 2026-08-07~09-02。concept_sector(_stock) 成员表 2026-05 才建、冻结窗缺失，不可用，故用 stocks.industry 标签。
- 全市场三窗快筛（L-065 门槛逐月同号≥0.8，无一达标）：行业当日均涨幅 sec_today 三窗 -0.08/+0.63/**-0.82 翻号**、同号 .62/.71/.50；上涨广度 sec_upfrac -0.16/+0.37/-0.69 翻号；行业 20 日动量 -0.23/+0.73/**-1.71 翻号**；板块内相对强弱 rs5 -0.27/+0.32/+0.12 弱且翻；行业 5 日动量 sec_ret5 冻结 +0.12（平、非单调）、前瞻 +0.96（同号.71）、run3 +0.41（同号 1.00）但 Spearman≈0（.03/.006）、冻结非单调，仍不过门槛；**行业当日涨停家数 sec_nlu：冻结 +0.69（正）、前瞻 -6.77（顶部档 -7.09）、run3 -2.29（负），两近窗同向为负但冻结反向。**
- 生产同构三窗 canonical（base vs 排序前硬否决、第 11 名递补；冻结因果、前瞻/run3 join canonical 净）：①涨停拥挤 top10% 否决——冻结剔 599 张均 +0.214（赢家）、n1010→571、cohort 日均 +0.061→-0.083（Δ-0.152）重创出票，前瞻剔 237 张 -0.693（毒、Δ+0.081/CI 跨0），run3 Δ-0.262 变差；top5% 更极端——冻结仍剔 495 张赢家（Δ-0.092），前瞻 Δ+0.093/CI[-0.20,+0.37]跨0，run3 Δ+0.451/CI[-0.031,+0.99]下界仍破0。②弱势行业动量 bottom10%/5% 否决——冻结剔 +0.158/+1.297 赢家、run3 剔 +2.825/+4.322 赢家，三窗冲突/CI 全跨0。**结论：板块"涨停高潮后崩塌"只在 2026 震荡退潮市成立，2025 强势市拥挤=动量本身、否决会大面积砍掉 v2 选出的赢家并饿毙出票，是 regime 条件量不是稳定 alpha（同 L-080/L-058），双窗规则下冻结反向即否决落地。**
- daily_kbars 残余日线列三窗快筛（补 L-061/065 漏网）：chip_winner_rate 获利盘比例 冻结 +0.12（非单调）/前瞻 +0.56（同号.67）/run3 +0.07（平）、Spearman≈0；profit_efficiency、atr **全空（未计算）**；upper_shadow_count20 冻结/run3 单档常量、仅前瞻有值（-0.27/同号.5）；holder_pct 三窗同号仅 .46/.50/.50、前瞻覆盖仅 33%。全部不达标。
- 北向数据覆盖即死（无需回填即排除）：hk_holdings 全表仅 132 只、冻结 4 日/前瞻 2 日/第三窗 0；hsgt_top10 设计上每日仅约 10 只、冻结窗仅 1 日 20 股；hsgt_flows 是大盘级总量（无 code，属 L-080/L-060 已证伪的 regime beta 择时）。北向个股维度无法做三窗选中池检验，不回填。
- 永久规则：**(a) 板块/行业横截面（动量/广度/涨停拥挤/板块内相对强弱/行业分散已在 L-073）在 v2 选中池无三窗稳定信息，"行业涨停拥挤=高潮崩塌"是 2026 退潮市专属、2025 强势市反向，禁止不做三窗就把近窗规律当 alpha 落地；这与 L-065"板块联动补涨已证伪"、L-080"指数趋势门控三窗翻号"共同关闭板块/择时门控族。(b) daily_kbars 现有列（含筹码分布 chip_winner_rate、holder_pct、上影计数）已逐一筛过，无漏网日线横截面因子；profit_efficiency/atr 是未计算空列，要用先补数据且仍须过三窗。(c) 北向个股级数据当前覆盖不足以研究，hsgt 总量不做选股信号。**
- 复现：`scripts/research_sector_factor_screen.py`（板块八因子三窗全市场快筛，缓存 sector_factor_panel.pkl，日志 sector_factor_screen.log）、`scripts/research_sector_veto_canonical.py`（拥挤/弱势三窗 canonical，日志 sector_veto_canonical.log）、`scripts/research_daily_residual_screen.py`（残余日线列快筛）。
- 相关：L-065（日线横截面/板块联动系统性挖尽）、L-061（价格技术因子）、L-080（外部指数趋势门控三窗翻号）、L-072/L-073（regime 门控/行业分散上限/出票结构）、L-058（多窗口防过拟合）、L-064/L-078（全市场→选中池选择效应）、L-071（canonical 口径）。

## L-082 全库三窗覆盖普查：冻结窗可用数据源已穷尽；唯一漏测的 stk_limits（板型/距涨停）三窗翻号证伪；v2 维持不改

- 状态：active（否定性 FINDING + 数据源穷尽地图，防止后续换名重挖无冻结覆盖的表）；生产维持 v2，不出 v3。
- 动机：L-058~L-081 逐轴证伪后，系统回答"是否还有冻结窗有足量数据、却没筛过的数据源"。对全部 87 张表按日期列统计三窗行数（脚本 `research_coverage_sweep.py`，日志 coverage_sweep.log）。
- 冻结窗（2025-01-15~2026-01-16）行数>1000、可做三窗 canonical 的表及其归宿：daily_kbars（L-061/063/065/081）、daily_basic（L-075）、stk_limits（本条）、limit_up_board（L-075）、dragon_tiger / margin_detail / auction_data（L-078）、minute_kbars（L-074/079）、index_daily（L-080）、stocks.industry 板块（L-081）。**至此全部筛完。**
- 唯一漏测的 stk_limits：仅 up_limit/down_limit 两列，衍生①板型宽度 bw=(up-down)/(up+down) 归并 ST5/main10/gemstar20(20cm)/bse30，②收盘距涨停 dist_up=(up_limit/close-1)。三窗全市场：20cm-vs-10cm 均 fwd5 差 冻结 +0.31、前瞻 +0.17、**run3 -0.57 翻号**；dist_up 五分位 Qhi-Qlo 冻结 +0.449（同号.69）、前瞻 -0.200（.43）、run3 -0.018（.50），且 dist_up 本质是 pct_chg 的单调变换（L-061 已证伪价格动量）。板型 beta 全市场存在但跨窗翻号、选中池已被 quality_cap/score 过滤，不落地。
- 覆盖即死、不回填的表（冻结 0 或单窗/过稀，无法满足三窗 canonical）：capital_flow/concept_sector(_stock)/news_announcement/llm_review_cache/stock_quotes/recommendation(s) 冻结 0（news_announcement 仅 2026-03-10 起、negative 标签全库仅 1114 条≈3.4%）；hk_holdings/hsgt_top10/hsgt_flows 北向（L-081）；institutional_surveys（冻结1120/前瞻342/run3 0，日均≈4 条）、broker_recommends（月度稀疏）、pledge_stats/repurchases/share_floats/stock_fina_indicator/holder_numbers/holder_trades/block_trades 单窗或 run3 0。
- 永久规则：**(a) 选股因子研究只允许在"冻结窗有足量覆盖"的表上做三窗 canonical；冻结 0 / 单窗 / 日均个位数的表先解决覆盖再谈因子，禁止用近两窗规律绕过冻结窗落地（L-058）。(b) 板型（10/20/30cm）与"距涨停距离"无三窗稳定 alpha，后者是 pct_chg 变换，不再重测。(c) 新增数据源要先过 `research_coverage_sweep.py` 的覆盖门：三窗都得有足量选中池交集，否则直接记覆盖即死。**
- 复现：`scripts/research_coverage_sweep.py`（全表三窗覆盖普查 + stk_limits 板型/dist_up 快筛，缓存日志 output/research/coverage_sweep.log）。
- 相关：L-081（板块/残余日线/北向）、L-065（日线横截面挖尽）、L-075（daily_basic/limit_up_board）、L-078（事件三表）、L-061（pct_chg 价格动量）、L-058（多窗口）、L-071（canonical 口径）。

## L-083 信号"自身质量/置信度"轴（数据完整度/真实性/缺失字段数、confidence/setup_quality/独立性/risk_flags）证伪；并记录候选快照持久化 schema 不对称；v2 维持不改

- 状态：active（否定性 FINDING，关闭 meta-quality / per-signal confidence 方向）；生产维持 v2，不出 v3。
- 动机：L-082 穷尽外部数据源后，转向"信号自身可靠性"这一不依赖新行情数据的框架轴——直觉上数据越全、置信度越高、风险标记越少的信号应越可靠。两类字段分别存在于两套快照（持久化 schema 不对称）：A 冻结聚合卡上的 data_completeness_score/data_authenticity/evidence_quality_score/data_quality.missing_fields；B 前瞻/run3 backtest_candidates.signal_json 单策略信号上的 confidence/setup_quality/strategy_independence/risk_flags。
- A 冻结选中池（n=1010，因果净）：data_completeness/evidence_quality 都只有二元、Spearman ±0.04；data_authenticity **常量无区分**；missing_fields 数 nmiss 仅取 {5,6,7}，nmiss=5 +0.388 / nmiss=6 +0.200 / nmiss=7（120 张，12%）-0.219，弱单调但 rho 仅 -0.042。**nmiss 本质是"信号生成当时哪些数据管道已上线"的覆盖伪影**：L-078 已向冻结窗回填 dragon_tiger/margin、L-079 回填 5min，历史"缺失"状态已被当前库污染、不可还原；且 signal_json 根本没持久化这些字段，前瞻/run3 无法对齐，三窗不可复现，判死。
- B 两个 2026 窗（signal_json，选中=run 内全候选 score≥70）：confidence Spearman +0.007/+0.011、risk_flags 数 +0.009/+0.007≈0 且非单调；strategy_independence **常量**；setup_quality 仅二元、rho +0.027/+0.032（高档 +0.22/+0.26 vs 低档 -0.28/-0.34），方向两窗一致但极弱，且 setup_quality 本就是 final_score 的上游输入（再用=重复计数），冻结快照又未持久化、无法三窗。整体无独立信息，即使补冻结持久化也救不活。
- 永久规则：**(a) 信号 meta-quality（数据完整度/真实性/缺失字段数/置信度/独立性/risk_flags 数）在选中池无 |Spearman|≥0.05 的稳定区分度，不作为选股否决/加分；"缺数据=信号差"是管道上线进度的覆盖伪影，禁止用当前库反推历史缺失态。(b) 候选/信号快照持久化 schema 不对称（冻结聚合卡 vs 前瞻 signal_json 字段不同），任何想跨三窗用的卡级字段，必须先确认三套快照都持久化了它，否则在研究脚本里就判覆盖即死，不要做到一半发现对齐不了。(c) 已经喂给 final_score 的上游子分（如 setup_quality/evidence）再做否决属重复计数，除非能证明其残差方向三窗稳定。**
- 复现：`scripts/research_signal_quality_screen.py`（A 冻结因果 + B 两窗 signal_json，自包含）。
- 相关：L-073⑪（evidence_quality_score 冻结五分位已证伪，本条补全同族字段并扩到两窗）、L-082（覆盖门/快照源）、L-078/L-079（回填污染历史缺失态的具体来源）、L-065（选中池横截面无信息）、L-071（canonical 口径）。

## L-084 信号日隔夜跳空 gap（open/前收-1）：唯一通过廉价三窗同号+条件独立初筛的日线特征，在生产同构"真实递补"canonical 下被第三窗翻号否决；并记录评分内部三杠杆因快照 schema 不可三窗

- 状态：active（否定性 FINDING + 一条 L-071 的强例证）；生产维持 v2，不出 v3。
- 背景：L-082 已普查冻结窗数据源穷尽、L-083 记录快照持久化 schema 不对称。本轮先复核"评分内部"三个杠杆，均在数据层不可三窗、不进入检验：①组件聚合权重再校准——回测/纯规则路径（无 agent review）在 aggregator 里被强制固定为 strategy_rule.55/market.20/risk.15/evidence.10，regime 权重被覆盖，且冻结卡 raw_component_scores 只存 evidence、canonical run signal_json 是逐策略单分，缺其余分量，无法离线重算 final_score；②逐策略 strategy_id 毒格——冻结 strategy_signals 匿名无 id（L-072⑤已判），canonical run 每(日,股)仅一行、strategy_id 全同；③单卡策略共识数/信号离散度——canonical run 入库前已把多策略折叠成每(日,股)一行，仅冻结保留多信号，而冻结单窗 n_sig≥3 非单调（n_sig=2 最差）、corr 仅 +0.04，单窗假相。三者要做只能整条重放打分管线且前瞻窗与生产不同构，不满足 L-071，放弃。
- 廉价初筛（手写 cohort，仅探索）：对选中池补算 daily_kbars 残余 OHLC 衍生特征（ivol/atr/bias/rsi/wr/max_dd/chip/上影/量比/换手/gap/收盘位置clr/振幅/ret5/vol20）三窗 Spearman IC + 三分位单调。绝大多数三窗翻号或近 0；**唯一幸存者 gap=信号日 open/前收-1**：IC 三窗全正（+0.055/+0.110/+0.136）、三分位三窗全单调向上（冻结 -0.14/-0.015/+0.773，前瞻 -0.858/-0.488/+0.287，run3 -1.06/-1.03/+0.397），跨 120/71/9 个交易日非单日聚集。
- 条件独立性（看似很强）：gap 与信号日 pct_chg 相关仅 0.28/0.43、与 chg20≈0；**控制 pct_chg 双重排序后"高开优于平/低开"在三窗 9/9 档全成立**，控制 chg20 后 8/9 成立；gap>0 vs gap≤0 简单拆分三窗逐笔 +0.557/-0.053、+0.250/-0.641、+0.081/-0.799，0 为天然非拟合阈值。经济叙事（隔夜仍敢高开=真买盘 conviction）成立。
- 但两道稳健性检查已现裂痕：①regime 格内翻号——冻结"强牛·活跃"高开 -0.197 反不如平低开 +1.248，run3 唯一足量格"弱势·活跃"高开 +0.384 也不如平低开 +0.981（run3 总效果靠格间 Simpson 构成）；②日聚集——冻结配对日 59 正/60 负近掷币、前 3 日贡献 54%，前瞻前 3 日贡献 74%，run3 日配对总和为负。
- **生产同构 canonical 终审（硬预过滤 gap≤thr 等价硬池否决，由真实 evaluate_selection_policy/_run_arm 自然递补下一张卡，不人为留空）——决定性否决，thr=0**：冻结逐笔 +0.206→+0.436、日cum(SLEEVE)+1.29→+13.48、MDD -9.57→-5.91、配对日差 **CI[+0.155,+0.870] 下界>0**、月正 11/13（强）；**前瞻仅 -0.352→-0.106（仍负）、cum -3.89→-3.01、配对 CI[-0.452,+0.549] 跨 0 不显著**；**第三窗翻号：逐笔 -0.558→-0.565、cum -1.35→-2.48 反而更差、日配对 Δ-0.578**（超跌反弹日里 gap≤0 的反而是反弹赢家，剔掉后递补不上、组合更差）。thr=-0.01/-0.02 更弱或反向（thr=-0.02 冻结砍到 +0.694 的赢家）。
- 关键教训（L-071 直接例证）：**手写 cohort"剔掉就留空"把 gap 前瞻虚高成由负转正(+0.25)、run3 虚高成 +0.08；一旦让生产代码按分数递补真实候选，前瞻增益缩到不显著、第三窗直接翻负。** 廉价三窗同号+控制动量 9/9 同号+天然阈值，全都不足以放行；软重排序杠杆在 v2（每日极少>10 候选）inert，regime 条件化也被第三弱势窗翻号。
- 永久规则：**(a) 信号日隔夜跳空/开盘强弱（open/前收、距涨停开盘、集合竞价跳空同族）不得作为选股硬否决或加分落地：它在选中池的表观优势=格间 regime 构成(Simpson)+手写留空偏差，真实递补下第三窗翻号。(b) 任何特征即使过了"三窗 IC 同号+三分位单调+控制已知动量后仍成立+天然阈值"这一整串廉价初筛，仍必须过生产同构【含候选递补】canonical 的三窗配对 CI 下界>0；手写 leave-empty cohort 只能用于预筛、永远不能作为落地依据（再次固化 L-071）。(c) 评分内部（组件聚合权重/逐策略标签/单卡共识数）在当前持久化快照 schema 下不可三窗复现，禁止凭单窗或两窗改这些旋钮；要改须先改造快照持久化全分量并证明三窗可对齐。**
- 复现：`scripts/research_selected_daily_feat_screen.py`（残余日线特征三窗 IC/单调扫描）、`research_signal_consensus.py`（共识数，证明 run 端折叠）、`research_gap_robust.py`（gap 条件独立/格内/聚集）、`research_gap_levers.py`（手写留空杠杆，展示虚高）、`research_gap_canonical.py`（真实递补 canonical 终审，结果 output/research/gap_canonical.json）。
- 相关：L-071（canonical/递补口径，本条是其最直接例证）、L-058（多窗口防过拟合）、L-080（外部门控第三窗翻号，同型）、L-083（快照 schema 不对称，本条补全评分内部三杠杆不可复现）、L-081（残余日线列已扫，gap 为其漏网且终审证伪）、L-061/065（选中池横截面无 edge）、L-073（出票结构/日内排名 inert）。

## L-089 LIVE 聚合路径上的三层"末端反转/非龙头"毒区：强势多头发散、长周期 RSI 低位、短线龙头低分，软 cap 重排序把 top30 WR +8.3pp（生产yaml权重）且零整票剔除

- 状态：active（LIVE 已结算路径的正向 FINDING，已落地生产 12 个 strategy.yaml，2026-09-13 随 `feat/strong-bull-ivol-veto-v3` 合入 `main`）；与 L-058~L-084 的"冻结三窗证伪"轨道互补——本条用的是 LIVE 真实已结算推荐（去重 241、已结算 205），仲裁的是生产聚合排序而非全市场因子。 **合并时修订（2026-09-13）：规则随 `feat/strong-bull-ivol-veto-v3` 合入 `main` 后，`backend/skills/*/strategy.yaml` 对三层毒区 phase 名命中 **12/13** 个文件，"已落地"声明成立；但本条头条 "+8.3pp" 只在含 `historical_reconstruction` 的汇总上成立，canonical `live_forward` 层增量为精确 0（分层审计见 L-096）。故本条只背书"规则已落地"，不背书该收益数字。**
- 三层毒区（已结算 205，全样本 WR45.9/avg-0.72）：①`dsa_trend_score==30`（=trend_status 强势多头/均线完全发散）n15 WR20.0/avg-3.21，逐月 25/0/16.7/33 全差，形态最强实为延伸末端反转，cap58；②`rsi_24∈(0,48)`（中期趋势偏弱、反弹缺支撑）n29 WR34.5/avg-1.97，对照 rsi24>=56 n108 WR53.7/+0.19，cap58；③`shortline_leader_score∈(0,35]`（非板块真龙头缺接力）在前两层残差上仍单调、对照 >52 n75 WR53.3/+0.20，温和 cap62。三层均 soft（hard_veto:false），只把毒区票排到真龙头之后。
- 金标准（对每笔 rec×背书策略真实 run_strategy 取新鲜分、**逐策略权重直接取 run_strategy 返回的 yaml weight**——生产 `aggregator._compute_strategy_rule_score` 用 signal['weight']，不是任何离线硬编码字典）反事实（当前 yaml 权重）：top 半区 46.7→48.3、top40 47.9→52.1、**top30 48.6→56.9（+8.3pp）**，hard_veto 整票剔除 0。三套权重（HEAD/旧PROD/当前yaml）下每个口径都为正、top30 治疗后收敛到同一 56.9，证明增益不依赖权重调参。收益集中在 top40/top30（生产每日只取最前几只）。
- 权重口径坑：离线若用过时硬编码权重会得到偏乐观数字（旧字典口径 49.2/55.2/58.3）；stored brk 里的 weight 是推荐当时的冻结值，又与当前 yaml 不同。**离线评估一律用当前 run_strategy 返回的权重。**
- 停在三层防过拟合：剥净后残差基线已升到 49.6/+0.21；第四层候选（price_volume_fallback 月度反转、放量上涨/high_volume_days 不在白名单且桶小、中单流入非单调、breakout60/上影/broker_rec 小桶疑已被捕获）全部否决。
- 永久规则：**(a) LIVE 排序优化优先用 soft score_cap 重排序而非 hard_veto 整票杀——主线充足时毒区自然落选、主线不足时仍可补位，下行可控。(b) 一个毒区要同时满足"逐月无强势月 + 跨 regime 同向 + 与已落地因子正交"，且必须在剥掉前序毒区的残差上仍成立才算增量。(c) 阈值/成员集用反事实金标准选（≤35 vs ≤40、full7 vs 去 small-cap、cap58/60/62），不看单桶边际 WR 就拍板。**
- 复现：离线 `/tmp/live_dedup_data.json`（241 笔）、`/tmp/live_gold_wcmp.py`（base/treat 金标准，yaml 权重）、`/tmp/live_gold_w3.py`（三套权重稳健性）、`/tmp/live_gold4.py`（四向 guard 对比）、`/tmp/live_lomo.py`（逐月留一月稳健性：top30/top40 无任何月份变差）、`/tmp/rebuild_poison_front.py`（幂等重建）；报告 `docs/LIVE路径仲裁-三层毒区相位排序纠错-2026-09-12.md`。
- 相关：L-090（让本条真正生效的 phase 排序/白名单/缺字段机制）、L-091（金标准 vs 冻结分、小样本 veto 翻案）、L-071（canonical 口径）、L-073（出票结构/重排序杠杆）。

## L-090 策略 phases 是"首个 when 命中即 break"：新增软规则必须置 phases 最前、用 not(hard_veto) 让位、对缺字段加 >0 守卫；evaluator 白名单外字段静默 False

- 状态：active（引擎机制 FINDING，解释了为何"写了规则却不生效"，已据此修正 L-089 落地方式）。
- 首命中 break 陷阱：`strategy_runner` 对 phases 从上到下找第一个 when 为真者、命中即 break、只应用一个 phase。把新软 cap 按惯例插在 setups 前（phases 末尾）会被更早的宽松软 cap（弱市降权 cap68、趋势跟踪 cap100）抢占——L-089 三层最初因此大量不生效（强势 16 中 7、RSI 41 中 15、leader 34 中 24 被抢占），"对每策略取 min(各 cap)"的离线算法会高估（预测 52.5 实际 50.0）。**新增 phase 若要优先，必须显式放到 phases 最前。**
- hard_veto 让位：前置软 cap 会反过来抢到既有 hard_veto 前面、架空核心风控（raw 纯条件金标略优但非法覆盖 532 处 veto，多为 high-range 弱市追高否决）。规则：**新软 phase 的 when 必须 `and not(<每个既有 hard_veto 的 when>)`，保证永不覆盖绝对否决；普通软 cap 之间交错顺序差异仅几分、可不让位。** 改共享引擎做"两遍扫描 veto 优先"会改动 125 处现有行为，否决，只在 yaml 内局部 guard。
- evaluator 两个静默陷阱：①`ALLOWED_NAMES` 白名单外的名字（如字符串字段 trend_status/ma_alignment/volume_status）raise 后被 `evaluate()` 静默吞成 False，规则永远不命中且不报错——必须改用白名单内数值字段（dsa_trend_score==30 严格替代 trend_status=='强势多头'，先验证枚举一一对应、零 null）。②缺失字段被 `_coerce(None)=0.0`，"X<=阈值/ X<阈值"类条件会把缺数据误判成最低值而误触发；生产稳健写法是 `X > 0 and X < 阈值`（真实 LIVE 三字段零缺失，但两个回归测试 fixture 不设字段即暴露此问题）。等值条件 `X==30` 对缺失天然安全。
- 永久规则：**(a) 改任何 strategy.yaml 的 phases 前，先确认引擎是"首命中 break"还是"全量叠加"；要让某 phase 优先就放最前，并离线逐笔统计它被更早 phase 抢占的比例。(b) 任何前置/新加软 cap 必须对同策略全部既有 hard_veto 做 not(...) 让位，并逐笔验证"我首命中时后续无 hard_veto 命中"的风险数=0。(c) when 里只用 evaluator 白名单内字段，字符串枚举先找对应数值分；所有"小于/低于阈值"条件对可能缺失的字段加 `>0` 数据有效性守卫。**
- 复现：`/tmp/live_faithful_sim.py`（冻结分首命中模拟，暴露抢占）、`/tmp/live_phase_preempt.py`（抢占诊断）、`app/engine/{strategy_runner,evaluator}.py`。
- 相关：L-089（被本机制救活的三层毒区）、L-091（冻结分与真实 run 的分歧）、L-071（口径必须与生产同构）。

## L-091 离线评估必须用"真实 run_strategy 金标准"而非历史冻结分；小样本 hard_veto（n=2 WR0%）会被去重大样本（n=16 WR56%）推翻

- 状态：active（方法 FINDING + 一条小样本风控翻案）。
- 冻结分歧义：recommendations 里逐策略 brk 分数是**历史各版本冻结输出**，不是当前 yaml 重算结果；用 min(冻结分, 新 cap) 或"冻结分首命中"模拟新规则，会与"当前 yaml 首命中"不一致（本轮 33 处），且偏乐观。**权威做法是金标准：对每笔 rec×每个背书策略用当前代码真实调用 `run_strategy` 取新鲜分**（StrategySkill 是 frozen dataclass，做变体用 `dataclasses.replace(sk, contract=con)`），彻底绕开冻结分歧义。本轮最终数字一律以金标准为准。
- 小样本 hard_veto 翻案：DSA 旧「资金背离否决」证据仅 n=2/WR0%；去重 241 大样本复核资金背离 n=16 **WR56.2%**、主力共振 n=38 WR52.6/+0.85，方向反了（都是正 alpha），遂删除该 hard_veto。hard_veto 是整票杀、代价最高，最不该由个位数样本立。
- 永久规则：**(a) 评估策略改动对历史推荐的影响，必须用当前生产代码逐笔真实重跑（金标准），禁止用入库冻结分做 min/首命中推算并据此下结论。(b) 任何 hard_veto 立项要求足量去重样本 + 逐月/跨 regime 一致；个位数样本（n<~10）只能做观察标记、不得整票否决，已立的小样本 veto 要在样本扩大后主动复核翻案。(c) 去重口径按生产 `(trade_date,code)` 同 code 合并，未去重的重复推荐会同时虚增样本、扭曲 WR。**
- 复现：`/tmp/live_gold_wcmp.py`、`/tmp/live_gold_w3.py`、`/tmp/live_gold4.py`（金标准，权重取 run_strategy 返回值）；DSA strategy.yaml 原位保留资金背离翻案注释。
- 相关：L-089/L-090（同轮落地与机制）、L-071（生产同构口径）、L-058（多窗口/样本量防过拟合）。

## L-092 三层毒区参数平台稳健性确认；三个后续杠杆（chip/dsa 归零、强势 hard_veto、breakout60 加分）逐一证伪——边际 churn 不落地

- 状态：active（否定性 FINDING + 已上线参数的稳健性背书）；生产维持 L-089 三层毒区现状，本轮不改任何生产策略。
- 平台扫描（内存构造参数化变体跑金标准，不动文件）：rsi 阈值 48 落在 48–52 平台（g30 同为 56.9）、leader 阈值 35 落在 33–38 平台中央、毒区 cap 58/62 落在 55–58 平台、强势 cap 在 48–58 完全无 flip——**已上线参数都在平台而非刀尖，前向 OOS 稳健，无需调参。**
- 证伪①chip/dsa 权重归零：chip 唯一背书 0/3 全亏、dsa 无 HQ 组 WR31 看似该砍，但金标准 flip 显示归零只是"移出 1 输家+1 中性、却放进另一输家 -3.09"的边界 churn（全局 +0.8pp=1 票噪声），不是干净的赢家替换；**判据：权重/阈值改动必须看 top 集合的逐笔进出，净换入赢家/换出输家才算 alpha，只动 1–2 张边界票且含换入输家=过拟合，不落地。**
- 证伪②强势毒区改 hard_veto：会整票剔 16 张，其中误杀 3 个赢家（+5.79/+8.99/+1.0），g30/lomo 反降；soft cap58 在 48–58 任意档结果相同。**再次固化 L-089：末端反转毒区用软降权、不用整票否决，给少数真突破留活口。**
- 证伪③breakout60 正向加分：全样本 breakout60=True n21 WR62/+1.72 vs False WR44/-1.00 看似强正 alpha，但卡级 +4 模拟的增益来自绕过 phase cap（不该顶破毒区）；按生产真实路径在 scoring.rules 逐策略 +4（L122 min(score,cap) 之前、被毒区 cap 罩住）后只剩全局 g30 +1.4（1 票）、g40/LOMO/逐月全不变，为 1 票动 10 个 yaml 不划算。**判据：卡级/聚合层模拟出的增益必须用"能真正写进 strategy.yaml、且不绕过 score_cap"的实现复算一遍，复算后增益消失即证伪。**
- 成员集复核：S8/R7 被排除的声明式策略在对应毒区队列零背书（limit-up/overnight/small-cap/weak 等），high-position 是 imperative 无 strategy.yaml 无法加 phase，weak-market rsi 仅 n1——成员集完整无遗漏。
- 永久规则：**(a) 已上线规则要做参数平台扫描（±2~4 档），落在平台中央才算稳健、落在单点尖峰要回退到平台中心。(b) 任何后续杠杆先看 top 集合逐笔 flip 与逐月 LOMO：换入输家或仅 1 票 churn、LOMO/逐月不升的，一律不落地，无论全样本单桶 WR 多好看。(c) 聚合层/card 级"如果加 X 分"的模拟只用于预筛，落地前必须等价改写到 strategy.yaml 评分路径并保留 score_cap 约束再复算。**
- 复现：`/tmp/live_sens.py`（阈值/cap 平台）、`/tmp/live_wgrid_iso.py`+`/tmp/live_wgrid_lq.py`（chip/dsa 权重网格与逐月）、`/tmp/live_strong_scan.py`（强势强度/hard_veto）、`/tmp/live_breakout_boost.py`（卡级）与 `/tmp/live_bk_impl.py`（忠实 yaml 实现复算，证伪）。
- 相关：L-089（被背书的三层毒区）、L-090（phase/cap 机制）、L-091（金标准口径）、L-058/L-071（多窗口与生产同构防过拟合）。

## L-093【正面 FINDING，已落地 v3】强牛 regime × 高特质波动 ivol20 横截面尾部硬否决+自然递补：三窗口生产同构 Δcohort 全正；首个通过"含真实递补 canonical"的日线横截面风险否决

- 状态：active（正面 FINDING，production_selection 升 v3 并设为默认；v1/v2 artifact 保留可复现）。
- 因子构造（非存量列、可从日线严格自构、2021 起全覆盖、T 收盘可得无前视）：ivol20 = 个股日收益 r=pct_chg/100 对中证500(000905.SH)收益 m 做 20 日滚动回归 beta=Cov(r,m)/Var(m)（min_periods16）后的残差 e=r−beta·m 的 20 日标准差，即剔除市场 beta 后的**个股特质波动**；落库为 daily_kbars.ivol20（`scripts/backfill_ivol20.py`，月分块幂等，挂在 backfill_daily_bars 末尾增量），决策时取**当日全市场横截面 pct rank**（`app/services/risk_features.py`；绝对阈值跨日漂移 0.025~0.045 不可用，必须横截面分位）。规则：仅当决策时 `_market_base(market_snapshot)=="强牛"`，把 ivol20_pct≥0.80（top20%）的候选硬剔、由其后按 final_score 的下一名自然递补（不空仓、不整日 abstain）。
- 与既往证伪的显式切割（务必区分，勿当重复重测）：①**不是 L-080 指数趋势择时门控**——L-080 用外部指数价格趋势决定"整日是否出票"，在 W3 超跌反弹日空掉最大赢家而翻号；本规则只用决策时内部广度 regime 标签决定"在哪些日子做横截面剔除"，强牛日照样出票、只换票，且 W3 为正（+0.363）不翻号。②**不是 L-081 的存量列筛查**——L-081 只筛 daily_kbars 已存储列；ivol20 是用 pct_chg+指数新回归构造的衍生因子。③**不是 L-084 廉价初筛里的 ivol**：那是在 v2 选中池上做"不分 regime 的 Spearman IC + 三分位单调"，实测 IC −0.026/+0.012/+0.056 翻号、三分位('.','UP','.')非单调被正确否决；本规则是**全市场日截面分位 × 仅强牛 × top20% 尾部 × 真实递补**，非线性、regime 条件化——不分 regime 的一刀切在 W1 反而有害（强趋势牛市高波动是领涨赢家），这正解释了线性筛查为何看着弱。同数据、不同假设，是干净对照而非矛盾。④同族 carrier（总波动 vol20/MAX1/偏度 skew/60 日动量）做同样门控更弱或会砍到 W1 正期望票，ivol 机制最干净。
- 三窗口生产同构终审（**用真实生产代码** evaluate_selection_policy：base=v2，对照=给同一批卡附持久化 ivol20 分位后跑 v3，apply_pool_rules 内部否决+自然递补；冻结 holdout 用 _simulate_card 因果出场，前瞻/第三窗 join canonical run 净收益；脚本 `scripts/verify_v3_production_canonical.py`）：
  - 冻结 W1（244d，n1010）：cohort +0.0364→+0.1110，**Δ+0.0746**，PF1.11→1.17，被砍 93 张均 −0.296、递补 86 张均 +0.848；配对日 CI[−0.012,+0.166]（下界微破 0，见弱点）。
  - 前瞻 W2（n721）：cohort −0.2007→−0.0579，**Δ+0.1429**，PF0.83→0.88，配对日 CI[+0.006,+0.298] **下界>0**，被砍 54 均 −0.726、递补 52 均 +0.746。
  - 第三 W3（n95）：cohort −0.4247→−0.0622，**Δ+0.3626**，PF0.76→0.91，被砍 10 均 −3.156、递补 10 均 +0.288。
  - 三窗 Δ 全正、被砍组三窗全负、递补组三窗全正。研究手筛口径（top20%、强牛）为 +0.079/+0.155/+0.288，生产值与之吻合（差异来自生产回填用 120 日 warmup、早期稀疏日覆盖更全）。
- 稳健性（研究阶段全部通过）：5 种构造（20/30/40 日窗 × 中证500/沪深300/等权市场）强牛门控下 Δcohort **15/15 全正、被砍单元 15/15 全负**，原始 20 日×中证500 综合最优；三窗合并日 bootstrap CI[+0.059,+0.295]、分层 block-bootstrap CI[+0.088,+0.490]、P(Δ>0)=1.000；阈值 0.75–0.80 为高原；ivol 分位与 final_score 相关仅 −0.031、在每个 score 档内高 ivol 都更差（85+档差 −1.42），不与评分冗余；76–88% 强牛出票日触发、砍 25–29% 强牛卡；W1 被砍约 95 张散在约 50 个交易日、非单日聚集。持久化值与研究面板逐格 corr≈1（max|diff|5e-7，仅 round6 误差）。
- 机制（为何只在强牛、为何是左尾）：生产为非对称因果出场（+6% 止盈/移动止盈截断赢家、−4.5% 止损+隔夜跳空放大输家，冠军基线亏损集中在 stop_loss_gap）。强牛高潮单元追的是高 beta 高弹性票，其中**剔除市场 beta 后的个股特质风险**最高的一档，次日个股层面崩塌/跳空止损概率显著更高（全市场 Q1→Q5 的"5 日内≤−5% 大亏率"三窗单调 4%→23%/14%→35%/4%→26%）；弱市/偏多/震荡里高 ivol 反而常是超跌反弹弹性来源，故必须强牛门控。
- 诚实残留弱点（落地时一并记录，不夸大）：①W1 单窗配对 CI 下界 −0.012 略破 0，强证据主要靠三窗同向 + W2 CI 严格为正 + 合并/block bootstrap；②W3 仅 3–4 个改动日、n=10，单窗不显著（CI 下界贴 0）；③规则只在强牛生效（1826 选中卡里约 587 为强牛），对非强牛出票零影响；④本地库 2025-02 前日线覆盖稀疏，冻结窗最前几周 ivol 覆盖低（缺字段的卡保守不剔，不影响 v1/v2 行为）。
- 永久规则：**(a) 日线横截面并非全部穷尽——L-061~L-085 证伪的是"存量列 + 不分 regime 的线性/单调关联 + 整日择时门控"；用日线自构的衍生风险因子（对基准回归取残差）、做"regime 条件化 + 极端尾部 + 真实递补"仍可能有 edge，但必须过含候选递补的三窗生产同构 canonical（L-071/L-084 终审口径），廉价 IC/三分位同号不足为据。(b) 风险否决优先用"全市场当日横截面分位"而非绝对阈值（跨日分布漂移）；缺该字段的卡保守放行，保证旧版本逐位可复现。(c) 新版本策略 dataclass 新规则默认必须关闭，仅在新 version JSON 打开，v1/v2 artifact 永不改，loader 显式放开版本号。**
- 复现：`scripts/research_anomaly_factors.py`（异象族面板+三窗快筛）、`build_ivol_variants.py`（5 构造）、`research_risk_regime_diagnose.py`（regime×ivol 交叉，定强牛门控）、`research_riskveto_canonical.py`（手筛终审）、`research_riskveto_pooled.py`（合并 bootstrap）、`verify_v3_production_canonical.py`（**生产代码 v3 三窗终审**，结果 output/research/v3_production_canonical.json）；生产：`app/services/risk_features.py`、daily_kbars.ivol20 列 + `scripts/backfill_ivol20.py`、`app/engine/{production_policy,aggregator}.py`、两咽喉 `services/runs/stages/adapters.py` 与 `services/backtest/policy_experiment.py`、policy artifact `app/policies/production_selection.v3.json`、测试 `tests/test_strong_high_ivol_veto.py`。
- 相关：L-070（唯一稳健胜利同为负向否决/硬排除）、L-080（指数趋势择时门控翻号，本条与其切割）、L-081（存量列挖尽，本条是新构造衍生因子）、L-084（隔夜 gap 真实递补终审翻号 + 廉价 ivol 线性筛查证伪，本条用更锐利的条件化尾部假设通过同一终审）、L-071（生产同构/真实递补口径）、L-065（日线横截面"穷尽"结论的边界）、L-058（多窗口防过拟合）、L-061（价格动量/总波动线性轴证伪）。

## L-094 对 L-093（v3 强牛高 ivol 否决）的独立终审背书：政策级真实代码三窗复现 + 月 block bootstrap 合并显著 + 尾部有界非对称 + live 数据就绪与回退判据

- 状态：active（独立复核 FINDING，结论与 L-093 一致：支持默认 v3，但给出单窗弱点、尾部标尺与 live 回退条件）；本轮不改任何生产代码/政策（v3 已由并行线程落地为默认），只新增两个只读复核脚本与本条。
- 复核动机：L-093 由作者自证并切默认，需用"另一条独立路径、直接加载真实 v2/v3 政策（非研究重构，符合 L-088 金标准）"终审，重点回答三问：①三窗是否真同向、②合并显著性能否扛住同月日相关（block bootstrap）、③最坏单日/途中逆向是否有界。
- 政策级复现（`scripts/research_v3_policy_audit.py`，冻结走 `_run_arm` 自动注入 ivol、前瞻/第三窗给真实卡 `attach_ivol20_pct` 后走真实 evaluate_selection_policy，thr=0.8）：逐笔等权 冻结 +0.2061→+0.3077(n1010→1003)、前瞻 −0.3522→−0.2447(n721→719)、第三 −0.5580→−0.1955(n95)；日 cohort(SLEEVE) 累计 +1.29→+4.42、−3.89→−1.18、−1.35→−0.21；被砍票三窗均负（−0.296/−0.726/−3.156）、递补票三窗均正（+0.848/+0.746/+0.288）。与 L-093 数字吻合（差异仅回填 warmup 口径），证明非研究重构假象。
- 阈值敏感性（0.70/0.75/0.80/0.85/0.90，同脚本）：方向在所有阈值×三窗全正（非 knife-edge、非翻号），但**没有任何单一阈值让三个单窗配对 CI 下界同时>0**——0.70 让冻结显著(CI[+0.13,+1.07]、月正0.85)却打坏前瞻/第三窗，0.85/0.90 衰减（0.90 冻结递补均−0.604、月正0.38）。**0.80 是最佳平衡（前瞻/第三窗 CI 下界>0、冻结仅微破），落在 L-092 所说的高原中央，无需调参。**
- 合并显著性与尾部（`scripts/research_v3_tail_pooled.py`）：三窗合并 76 个变动日，日Δ均 +0.550，普通 bootstrap CI[+0.188,+0.920]、**按月 block-bootstrap（整月重采样，扛同月日相关）CI[+0.218,+0.836] 下界仍>0**，月正 0.737（14/19 月）。分窗：冻结 46 变动日 Δ+0.396、普通/block CI 均微跨0（[−0.046,+0.849]/[−0.039,+0.813]）、月正0.69，拖累集中在 2025-04-10(−4.88)、04-14(−2.20) 两日；前瞻 27 日 Δ+0.619、block CI[+0.298,+0.880] 下界>0；第三窗 3 日全正零负日、CI[+0.299,+3.394]。**尾部有界且非对称**：最差单日 −4.88pp vs 最佳 +5.72pp；累计日 cohort 差 +18.2/+16.7/+6.9pp，途中最大逆向偏移仅冻结 −7.68pp（最终回正）、前瞻 −2.21pp、第三窗 0。
- 情绪定位（强牛内部按 sentiment 分解变动日）：收益集中在**强牛·高潮**（冻结 36 日 23好/13坏 Δ+0.476、前瞻 22 日 12/10 Δ+0.734、第三窗 3 日全好），强牛·活跃近掷币（冻结 10 日 4/6 Δ+0.109、前瞻 5 日 Δ+0.115）。经济上自洽（强牛高潮的高特质波动彩票票次日崩塌概率最高）；但活跃样本过小（10/5）、第三窗无活跃变动日，**不再叠加"仅高潮"二次条件——那是在已欠功效数据上再加自由度，属过拟合，保持对全部强牛生效。**
- live 数据就绪核查：daily_kbars.ivol20 已回填至 2026-09-11（最新日线日），近月每日 5190+/5546 ≈94% 覆盖（缺口为新股/北交所，本就排除），冻结窗 2025-02 前稀疏（缺字段卡保守放行=v2 行为，已在 L-093 记录）；两咽喉（live adapters / 回测 policy_experiment）均会附 ivol20_pct，**2026-09-14 起 v3 会在强牛日真实触发，无静默失效**。
- 诚实弱点（维持 L-093 披露，不夸大）：①冻结单窗配对 CI 下界微破 0（−0.046），单窗显著性靠合并 block bootstrap + 前瞻/第三窗补；②第三窗仅 3 个变动日、n=10，单窗功效弱；③规则只在强牛生效，对非强牛零影响。**与 L-080/L-084 的本质区别：那些是"某窗方向翻负"（regime flip），本规则无任何一窗点估计变负、被砍组三窗全负、合并 block CI 严格为正，故判为"真实但欠功效"而非"翻号假阳性"，支持保留默认 v3。**
- 永久规则：**(a) 作者自证的政策升级，须由独立路径直接加载真实版本政策（非研究重构）复算，并补"按月 block bootstrap"（普通日 bootstrap 会低估同月日相关导致 CI 偏窄）；合并 block CI 下界>0 且无任一窗点估计翻负，才可背书为默认。(b) 单窗 CI 微破 0 但合并 block CI 为正、方向三窗一致、被砍/递补经济方向一致、尾部有界非对称（最差单日与途中逆向可列示且被累计覆盖）时，可保留为默认，但必须写明 live 回退判据，不得宣称"已证显著"。(c) live 回退判据（v3→v2）：规则上线后，若强牛日"剔除高 ivol"的月度 cohort Δ 连续 2 个强牛月为负，或累计 Δ 的途中逆向超过 −8pp 且月末未回正，或 live 结算显示被砍票均值≥递补票均值，则回退默认 v2；在此之前标"待 live_forward 确认"。(d) 数据列驱动的否决上线前必须核查该列已回填到最新交易日且覆盖率达标，否则规则会静默不触发。**
- 复现：`scripts/research_v3_policy_audit.py`（真实 v2/v3 政策三窗 + 阈值 0.7–0.9 敏感性，结果 output/research/v3_policy_audit.json）、`scripts/research_v3_tail_pooled.py`（合并/月 block bootstrap + 逐日最好最差/途中逆向，结果 v3_tail_pooled.json）。
- 相关：L-093（被独立背书的 v3 强牛高 ivol 否决）、L-088/L-091（金标准、禁用研究重构下结论）、L-058（多窗口/block 重采样防过拟合）、L-092（参数高原 vs 刀尖）、L-080/L-084（对照：真正的第三窗翻号证伪）、L-071（生产同构/真实递补）。

## L-095 入场口径必须由策略显式声明，未成交反事实不得进入收益列

- 状态：encoded（已修 + 回归测试）
- 适用：策略信号、历史重建、outcome 模拟、复盘页收益展示
- 证据：R6 历史重建把所有策略统一按「未来两日低点触碰全局理想买点」判定入场，导致强势高开票未回踩即记为 missed、下跌票穿过低吸价反而成交；复盘页又按推荐价为 missed 生成不含成本的虚拟观察收益，与真实成交净收益同列展示，形成「未触发普遍大赚、触发普遍失败」的选择偏差。
- 永久规则：**每个 setup 必须用结构化 `entry_mode` 声明可回放的成交方式；outcome 只按持久化的入场方式执行。未触发记录的 `executed_return` 永远不可展示为收益，也不得用推荐价反事实替代。** 缺失旧字段时只能依据已持久化的策略身份做确定性迁移，其余保留旧口径；不得修改已封印的推荐快照。
- 回归验证：策略信号契约、次日开盘成交模拟、历史收益纯函数和前端生产构建。

## L-096 发布证据不得跨 `evidence_class` 汇总：非 canonical 层可把 canonical 层的 0.0pp 放大到 +13.7pp

- 状态：active（A 级测量/口径 FINDING，可作当前约束，不声称收益提升）。
- 适用：所有 challenger 的晋级/发布证据审计；金标准反事实；凡以"去重生产口径 N 笔（已结算 M）"这类**跨来源汇总**样本得出增益的结论。
- 证据（2026-09-13，纯只读审计，0 LLM 调用）：把 L-089 三层毒区（强势多头发散 `dsa_trend_score==30` cap58 / 长周期 RSI 低位 `rsi_24∈(0,48)` cap58 / 短线龙头低分 `shortline_leader_score∈(0,35]` cap62，均 soft）的证据样本**按 `evidence_class` 分层**重做金标准反事实。population 从 DB 重建 **215 pair / 已结算 172**，与 L-089 的 `/tmp/live_dedup_data.json`（241 对）交叉核对命中 **89.2%**。
  - **构成**：canonical `live_forward` 只占已结算 population **9/172 = 5.2%**；**80.8%（139/172）是 `historical_reconstruction`**，另有 `None` 17、`unverified` 7。L-089 的"已结算 205"里约八成属 §2 明确不接受的类别。
  - **机制（关键：类别标签不够，必须核生成时点）**：`historical_reconstruction` 的 `actual_generated_at − trade_date` **median 30 天**（min 2 / max 93，332 行**无一条当日生成**）；`live_forward` 为 **median 0、9/9 当日生成**；`unverified` 恒 1 天。（`data_as_of <= trade_date` 在所有层都是 100%——**声明的**新鲜度都因果，差异只在**真实生成时点**。）
  - **分层反事实**（base=剥离三层 / treat=前置三层，含 `not(hard_veto)` 让位与 `>0` 数据守卫）：`ALL` top30 dWR **+3.9pp**；`historical_reconstruction` top30 **+9.7pp**；**canonical `live_forward` 三个窗口（top_half/top40/top30）dWR 全 = +0.0pp、dAvg 全 = +0.000**。
  - **为何恰为 0**：canonical 层第 1 层（`dsa_trend_score==30`）与第 3 层（`leader∈(0,35]`）**命中数为 0**；第 2 层命中 3 笔，但把其分数压到 cap58 后**没有任何一只跨过窗口边界**。即三层在 canonical population 上**一个选股决策都没改变**。
  - **符号反转**：同三个"毒"条件在 `None` 层胜率分别为 **100% / 75% / 66.7%**（均值 +0.93 / −0.2 / +0.198）——三层里两层在该层是**正**的。真实微观结构毒性不会因推荐来源类别而反号 ⇒ 分层产物而非市场事实。
  - **基线敏感性**：同一脚本、同一 population，在特征分支（`feat/strong-bull-ivol-veto-v3`）与 `main` 上各跑一次（treat 侧注入相同，差异只来自 base 侧 yaml 组成），`ALL` top30 dWR 从 **+3.9pp 摆到 +13.7pp（3.5×）**；而 canonical 层的 **0.0pp 与命中模式（dsa=0 / rsi=3 / leader=0）在两种基线下完全一致**。
- 永久规则：**(a) 晋级/发布证据必须按 `evidence_class` 分层报告，并以 canonical `live_forward` 单独结果为准；跨类汇总（含"去重生产口径 N 笔已结算 M"）不得作为发布依据——它是 L-001"不同 source 禁算差值"在 provenance 轴上的同一条规则。(b) 判定"非 canonical"不能只看 `evidence_class` 标签，必须同时核 `actual_generated_at` 相对 `trade_date` 的滞后；显著滞后本身就是事后重建的机制证据（本案例 median 30 天）。(c) 当 canonical 层增量精确为 0 且条件命中数为 0 时，唯一正确的结论是"该层既不能证真也不能证伪"（记 MEASUREMENT_FIX / 待样本积累），**既不得因非 canonical 层的正增量而发布，也不得据此宣称规则已被证伪。**(d) 增益对评测基线组成敏感（同一改动在两个分支上摆动 3.5×）是污染信号；真实因子应在两种基线下同号同量级。(e) 审计工具必须自带方向相反的**可证伪性对照**——既能报正增益、也能报精确 0——否则无法区分"确实无效应"与"审计本身失灵"。**
- 复现：`backend/scripts/si_l086_caliber_audit.py`（分层金标准反事实，三层定义内联于 `POISON_DEF`，treat 侧跨分支一致）、`backend/scripts/si_probe_l086_population.py`（分层构成 + 生成时点滞后 + `data_as_of` 因果性）、`backend/tests/test_si_l086_caliber_audit.py`（**15 passed**，在 `0bb5140` 与 `667d357` 两个基线上各跑一次均通过，含上述两个方向相反的可证伪性对照）、报告 `backend/scripts/si_l086_caliber_audit_report.md`、结果 `output/l086_caliber_audit_{main_667d357,feat_0bb5140}.json`。
- 相关：L-001（跨 source 禁算差值——本条是其延伸并给出量化机制）、L-002（口径变化旧 champion 失效）、L-095（入场口径/反事实不得进收益列，同为"口径隔离"家族）、L-058（窗口/口径偏差）。
- **登记册编号（已于 2026-09-13 合并时归并，本节为合并记录）**：本条在 `main` 上原编 **L-094**，其"相关"引用的"入场口径"条目在 `main` 上原编 **L-072**（当时 `main` 登记册只到 L-072）。2026-09-13 合并 `feat/strong-bull-ivol-veto-v3` 时按登记册规则校验编号唯一性，发现 **2 处碰撞**：①**L-072**——分支同号条目为"v2 经生产同构 canonical harness 双窗口复核"（与本条引用的入场口径条目内容不同）；②**L-094**——分支同号条目为"对 L-093 的独立终审背书"。处理原则：**分支链 L-072~L-094 保持原号**（分支内 13+ 处交叉引用 L-072 系列，改号风险最高）；**`main` 独有的两条让号并依"新条目追加到末尾"追加**——入场口径条目 → **L-095**，本条目 → **L-096**。归并后全册编号唯一，分支的 L-093/L-094"候选"引用无需改动。
- **L-089 状态修订（已于 2026-09-13 合并时执行）**：`main` 曾核验 `backend/skills/*/strategy.yaml` 对三层毒区 phase 名命中 0 文件、与 L-089 原文"已落地生产 12 个 strategy.yaml"不符。合并 `feat/strong-bull-ivol-veto-v3` 后重跑核验：**12/13 个 `strategy.yaml` 已含三层毒区 phase，L-089 的"已落地"声明成立**；但其头条 **+8.3pp 只在含 `historical_reconstruction` 的汇总上成立，canonical `live_forward` 层增量为精确 0（见本条）**。故 L-089 状态已同步修订为"规则已落地（12 个 yaml，2026-09-13 合入 `main`）；头条增益未获 canonical 背书"，其永久规则 (a)(b)(c)（soft cap 优先于 hard_veto、毒区需逐月+跨 regime+正交、阈值用反事实金标准选）**方法学部分保留不变**。
