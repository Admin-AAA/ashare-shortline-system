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

## L-072 入场口径必须由策略显式声明，未成交反事实不得进入收益列

- 状态：encoded（已修 + 回归测试）
- 适用：策略信号、历史重建、outcome 模拟、复盘页收益展示
- 证据：R6 历史重建把所有策略统一按「未来两日低点触碰全局理想买点」判定入场，导致强势高开票未回踩即记为 missed、下跌票穿过低吸价反而成交；复盘页又按推荐价为 missed 生成不含成本的虚拟观察收益，与真实成交净收益同列展示，形成「未触发普遍大赚、触发普遍失败」的选择偏差。
- 永久规则：**每个 setup 必须用结构化 `entry_mode` 声明可回放的成交方式；outcome 只按持久化的入场方式执行。未触发记录的 `executed_return` 永远不可展示为收益，也不得用推荐价反事实替代。** 缺失旧字段时只能依据已持久化的策略身份做确定性迁移，其余保留旧口径；不得修改已封印的推荐快照。
- 回归验证：策略信号契约、次日开盘成交模拟、历史收益纯函数和前端生产构建。

## L-094 发布证据不得跨 `evidence_class` 汇总：非 canonical 层可把 canonical 层的 0.0pp 放大到 +13.7pp

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
- 相关：L-001（跨 source 禁算差值——本条是其延伸并给出量化机制）、L-002（口径变化旧 champion 失效）、L-072（入场口径/反事实不得进收益列，同为"口径隔离"家族）、L-058（窗口/口径偏差）。
- **登记册编号提示（合并时须处理）**：本条目在 `main` 上直接跳到 **L-094**，因为 `main` 的 `docs/lessons/LESSONS.md` **只到 L-072**，而 **L-073~L-092（20 条）只存在于未合并分支 `feat/strong-bull-ivol-veto-v3`**（该分支的 `LESSONS.md` 为 884 行、main 为 683 行）；工作区另有一条未提交的 L-093。选 L-094 是为避开与分支 L-073~L-093 的编号冲突。**该分支合并时必须把两份登记册归并、校验编号唯一性。**
- **须在合并时修订 L-089**：分支上的 L-089 原文称三层毒区"**已落地生产 12 个 strategy.yaml**"，但 2026-09-13 核验：`main` 的 `backend/skills/*/strategy.yaml` 对三层毒区 phase 名 `git grep` **命中 0 文件**，它们只在 `feat/strong-bull-ivol-veto-v3`（`83b98b1`→`695674d`，与 main 双向分叉 16/16）。该状态声明与 main 实际不符，且其 +8.3pp 在 canonical 口径上不可复现（见上）。归并时应把 L-089 的状态改为"已落地于待合并分支、未进 main，头条增益待 canonical 重验"，其永久规则 (a)(b)(c)（soft cap 优先于 hard_veto、毒区需逐月+跨 regime+正交、阈值用反事实金标准选）**方法学部分仍然有效，保留**。
