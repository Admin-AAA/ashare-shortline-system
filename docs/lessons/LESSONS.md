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
- 第三窗独立复核（2026-09-13，守卫研究员，`scripts/research_l070_v1v2_three_window_audit.py`，只读不烧 holdout 台账，证据 `output/research/l070_v1v2_three_window_audit.json`）：真实 v1/v2 政策走生产同构 evaluate 路径三窗重跑，sanity 守卫确认政策 JSON 语义差异恰好=偏多/震荡/弱势·高潮三格由隐式 85 变显式 120。①W1 冻结 v1 +0.164(n1030)→v2 +0.206(n1010)、配对Δ+1.95 CI[-0.27,+4.55] 跨 0（≈零代价，复现）；②W2 前瞻 v1 -0.471(n860)→v2 -0.352(n721)、Δ+1.24 CI[+0.27,+2.18] 下界>0（复现显著）；③**W3 新第三窗（2026-08-07~09-02，run btr_2bec6fed，7 个变动日）不翻号但无增量**：v1 -0.426(n130)→v2 -0.558(n95)、日 cohort Δ+0.066 CI[-1.29,+1.14] 跨 0、被砍 35 笔均 -0.066/赢家 17≈掷币、递补 0 笔。结论：硬排除在第三窗中性——被砍票已不再系统性亏（毒区当日没有再出现），前两窗增益未延续也未被推翻；排除继续保留（下行可控、零成本），收益主张维持"窗口截至 2026-08-06"，最终判定仍待 live_forward；政策不改（本条仅登记复核证据）。
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

## L-097 D 项基线必须由 state `canonical_production` 驱动并过「口径 + 重放身份」双守卫；基线不可跨 population 借用

- 状态：active（测量能力修复 MEASUREMENT_FIX，已落地 `scripts/self_check.py` + 单测 20 例；本轮不声称提高收益、不改任何生产策略）。
- 问题（真实性/测量能力）：`scripts/self_check.py` 的 D 项三块基线 `WIN_RATE_BASELINE` / `MAX_LOSS_BASELINE`（函数内硬编码 `None`）与 `PL_RATIO_BASELINE`（模块级 `None`）使 `check_d_rule_coverage()` 与 `_forward_verification_flag()` **永久短路**——D 项诊断每轮只报数、不算 Δ，且不给出可推进的路径（state 里其实已注册 canonical 基线，只是没人读）。这与 L-086/L-094 的「口径守卫靠人记」是同一类失效：诊断能力静默为 0。
- 修复：新增 `load_canonical_baseline()`（纯函数，只读 state JSON，可单测）+ `print_canonical_baseline_status()`；`main()` 启动时一次性解析并显式打印守卫结果。
  - **硬门禁（任一不过即 `ok=False`，退回「只报数不算 Δ」）**：① 有 `canonical_production.metrics`；② `source == 'champion_replay'`（必须由当前代码重放未修改 production，排除 challenger / 研究重构，L-088/L-091）；③ `caliber` 同时含 `canonical` 与 `live_forward`（L-094 跨 `evidence_class` 汇总一律判污染）；④ `population_all` 的 n/win_rate_pct/max_loss_pct/profit_factor 齐全且 n>0（bool 不当数值）；⑤ 登记了 `replay_identity` 时，horizon / 生产止损 / 成本必须与当前生产逐一相等。
  - **软提示（不阻断）**：`replay_code_revision` 与当前 HEAD 差异、`provisional` 状态。
  - **口径方向必须显式声明**：`_fmt_d_cost(..., baseline_pl_kind='gross'|'net')`。canonical 基线的 `win_rate_pct`/`profit_factor` 由 `executed_return` **毛收益**算出（实测 9 笔毛 PF=15.97/23.52=0.68，成本 0.07 未进入该字段），拿它去减 `net_pl` 会凭空造出 −0.02 的假 Δ。
- 关键纪律（**基线不可跨 population 借用**）：D 项三个窗口（全量/近10日/前瞻）走 `OFFICIAL_SAMPLE_SQL`（`evaluation_scope='live_forward'`），而 `check_d_rule_coverage()` 的规则覆盖反事实跑的是**全部 formal**（含 `historical_reconstruction`）。两者的 WR 基线是**两个 population**（本轮实测 strict n=9 WR 55.6% vs 全部-formal n=368 WR 42.1%）。本轮把 `check_d_rule_coverage` 的基线改为读 state 的 `population_legacy_wide`；**未登记时就只列残留毒区、不算 Δ**，禁止拿 strict 基线去比 wide 口径。同理 `check_d_rule_coverage` / `_print_forward_veto_counterfactual` 的基线参数改为可空并显式处理 None。
- 证据（本轮，2026-09-13，worktree 基于 main `0df3bbf`，共 21 个 `formal`+`live_forward` run）：
  - `scripts/self_check.py`：+221/−19；新增 `backend/tests/test_self_check_canonical_baseline.py` **20 passed**（含 4 种 caliber 拒绝、4 种指标缺失、n=0、bool 混入、身份漂移、与重放脚本常数一致性）。
  - `backend/tests/ -k "self_check or champion_baseline"`：**253 passed / 2 failed**；2 例失败为 `test_self_check_a_item_classification.py` 的 `_zero_rec_day_is_protective`，已用 `git stash` 在**未改动的 self_check.py** 上复现同样失败 → 既有失败，与本轮改动无关（A 项过滤器审计，不引用本轮触碰的任何函数）。
  - 独立重放（`backend/scripts/si_champion_baseline_rebuild.py`，只读、0 LLM）：当前 main 代码下 canonical strict 口径 **n=9 / 交易日 4 / 毛胜率 55.6% / 平均净收益 −0.839% / PF 0.68 / 最大单笔 −7.43% / 等权累计 −5.34% / 等权 MDD −8.42%**，与 state 在 `253543d` 登记的基线**逐字段一致**；宽口径（全部 formal）n=368 / WR 42.1% / PF 0.64 / maxloss −15.16%。结果落 `output/champion_baseline_20260913.json`。
  - 库内 `evaluation_scope` 现状：`live_forward` 21 个 run、`historical_reconstruction` 0 个（与 09-11 报告"279 个 historical_reconstruction"的差异是 run 重新分类后的结果，不影响 strict 口径数值）。
- 永久规则：**(a) 任何"基线/对照"必须带口径与身份声明，且由**机器可读的单一事实源**（state `canonical_production`）驱动；把基线写成代码里的 `None` 等同于永久关闭该诊断，属必须修复的静默失效。(b) 基线比对必须 population 对齐：strict（live_forward）基线与 wide（全部 formal）指标不得互相比较；缺少同口径基线时只报数、明确写出"不算 Δ"的理由，绝不用相近口径顶替。(c) 基线 PF/WR 的口径（毛 `executed_return` vs 扣费净）必须在调用处显式传入，不允许隐式默认。(d) 数据寄存器上出现 `historical_reconstruction` / 宽口径混入时按 L-094 拒绝，并写清修复路径。**
- 吸收位置：`scripts/self_check.py`（`load_canonical_baseline` / `print_canonical_baseline_status` / `_numeric_metric`、`PRODUCTION_REPLAY_IDENTITY`、`check_d_item` 基线解析、`_fmt_d_cost(baseline_pl_kind=…)`、`check_d_rule_coverage`/`_print_forward_veto_counterfactual` 可空基线、`main()` 启动打印）、`backend/tests/test_self_check_canonical_baseline.py`。
- 相关：L-002（口径变化后须先重放未修改 production 才可比较）、L-094/L-096（Canonical 口径唯一可接受、跨 evidence_class 汇总即污染）、L-086（+8.3pp 口径污染实例）、L-088/L-091（金标准、禁用研究重构下结论）、L-067（健康检查禁止对超大表做函数化日期聚合——本轮 `SELECT MAX(trade_date) FROM daily_kbars` 实测 5 分钟超时，同类坑）。

## L-098 audit 阶段 `canonical_production` 只读是设计约束：诊断口径基线须走 `d_baseline_registry`（publish-finding 可写），且必须显式声明 diagnostic

- 状态：active（测量能力修复 MEASUREMENT_FIX；`scripts/self_check.py` + 单测 30 例；不改生产策略、不声称提高收益）。
- 问题：T-097 的 D 项守卫已能读 state `canonical_production`，但 `check_d_rule_coverage()`（规则覆盖反事实）跑的是**全部 formal** wide 口径，其基线需要 `population_legacy_wide`（n=368 / WR 42.1%）。而 `publish_champion()` 在 `audit_only=true` 下**一律拒绝**任何含 `canonical_production` 的 patch——即使 `proposed_policy is None`（见 `_autonomous_canary_publish_allowed`：要求 `phase=='canary_passed'` + 完整 Canary 回执 + `proposed_policy` 为 dict，纯元数据更新不可能满足）。所以上一轮移交的"把 legacy_wide 登记进 canonical_production"**在 audit 阶段结构性不可达**，不是流程疏漏。
- 解决（不绕过状态机）：`publish-finding` 只禁 `canonical_production`/`selection_policy_governance`/`release_state`/`researcher_registry` 四键，其余顶层键合法。新增顶层 `d_baseline_registry`：`{registered_by, populations:{legacy_wide:{source,caliber,win_rate_pct,n,profit_factor,max_loss_pct,evidence}}, replay_identity:{...}}`，由 `_load_d_baseline_registry()` 校验后并入 D 项守卫。
- 守卫规则（`_load_d_baseline_registry`）：
  - **优先级**：`canonical_production` 已登记的同名 population / `replay_identity` **始终优先**（champion 注册表权威）；registry 只补缺口，并回填 `legacy_wide_source` 供审计。
  - **registry 的 legacy_wide 三重硬校验**（任一不过→拒绝采用并留 warning，**绝不**连带拒绝 strict 基线）：①`source` 含 `champion_replay`；②`caliber` 含 wide/legacy/全部/all 之一；③`caliber` 必须显式声明 `diagnostic`（防 wide 诊断口径被误当发布证据，L-094/L-096）；④`n>0` 且 `win_rate_pct` 为数值。
  - `replay_identity`：`canonical_production` 未登记时回退 registry；漂移照样阻断 strict Δ（身份守卫不可被绕过）。
- 关键区别（勿混淆）：`canonical_production` = **发布证据**用的 champion 口径（audit 只读）；`d_baseline_registry` = **诊断**用的同源重放口径（仍要求 champion_replay，但不进发布证据链）。两者都必须声明 caliber；wide 口径若想进发布证据链，必须走 L-094 的 canonical live_forward。
- 证据（2026-09-13）：`backend/tests/test_self_check_canonical_baseline.py` **30 passed**（新增 10 例：优先级、坏 source/缺 diagnostic/指标不全拒绝、身份回退漂移阻断、canonical 身份优先、形态非法仅 warning、printer 显示来源）；`-k "self_check or champion_baseline"` **263 passed / 2 failed**（2 例为 L-097 已核实的既有 A 项失败，与本轮无关）。真实 state 实测：strict 基线守卫通过（n=9/毛WR 55.6%/PF 0.68/maxloss −7.43%），legacy-wide 未登记时正确输出"不算 Δ"。
- 永久规则：**(a) 研究员在 audit 阶段需要新增**非发布证据**的机器可读基线/注册表时，用 `publish-finding` 写新的顶层键，不要试图改 `canonical_production`（会被 CAS+audit 门禁拒绝，且绕过属违规）；新键必须在消费端做 source/caliber/数值三重校验，损坏只降级不阻断主口径。(b) 任何注册表条目必须显式声明 `diagnostic`（诊断）还是 release evidence；诊断口径永远不得进入 `publish-champion` 证据链。(c) champion 注册表里的同名数据始终优先于研究员注册表，消费端必须回填数据来源标签供审计。**
- 吸收位置：`scripts/self_check.py`（`_load_d_baseline_registry`、`load_canonical_baseline` 的身份回退与 legacy_wide 覆盖、`legacy_wide_source` 标签、`print_canonical_baseline_status` 的来源显示）、`backend/tests/test_self_check_canonical_baseline.py`。
- 相关：L-097（D 项基线口径/身份守卫本体）、L-094/L-096（发布证据唯一口径、跨 evidence_class 汇总即污染）、L-086（+8.3pp 口径污染实例）、L-002（重放未修改 production 才可比较）。
## L-099 政策指纹随代码版本漂移：champion 指纹在 dataclass 演化后不可复现，migrate-state setdefault + publish-champion 门禁构成发布死锁；新增 CAS 守卫的 reanchor-policy 逃生门

- 状态：active（MEASUREMENT_FIX，状态机工具修复；不改生产策略语义）。
- 现象：v3 强牛高 ivol 否决按 L-093/L-094 三窗 canonical 证据合并 main 并设默认（256daa0/0df3bbf）后，`refresh-policy-health` 持续报 `policy_artifact_mismatch=true`；`publish-champion` 的 `policy_artifact_identity` 门禁（要求 mismatch=false 且 governance 指纹==当前 identity）永远不过，任何新 candidate 都无法发布。
- 根因（两层）：①`ProductionSelectionPolicy.fingerprint = canonical_fingerprint(canonical_payload())`，payload 随 dataclass 演化——champion 于 2026-09-11 在 commit 253543d（v1 artifact + 当时字段集）注册的指纹 b2b46f4e，在当前代码下用同一 v1 artifact 重算得 3dfac57a≠b2b46f4e，历史指纹不可复现；②`migrate_state` 对 `governance.policy_artifact_fingerprint` 只 `setdefault`（永不更新）、champion.selection_policy 仅在键集不同才替换，而唯一合法写入者 publish-champion 又被 mismatch 门禁挡住 → 死锁，状态机在任何改变 payload 的落地后永久冻结。
- 修复：`self_improve_state.py` 新增 `reanchor-policy` 子命令（`reanchor_policy_identity()`）：CAS 守卫（governance 指纹必须等于 `--from-fingerprint`，否则 CAS_MISMATCH）、已锚定拒重复（ALREADY_ANCHORED）、把 `canonical_production.selection_policy` 与 `governance.policy_artifact_fingerprint` 重绑到 repo 当前 artifact identity（v3=8c4821b9），记录 `canonical_production.policy_reanchor` 与 `state_migration.policy_reanchor_history`，不触碰 champion commit/metrics/release_state。2026-09-13 已对真实状态执行：v1/b2b46f4e → v3/8c4821b9，mismatch=false。
- 永久规则：**(a) 凡经审查证据（三窗 canonical + 独立复核）把新政策版本设为 repo 默认的落地，同轮必须在发布锁下执行 `reanchor-policy` 把 champion 身份重绑到新默认，否则状态机死锁、后续所有发布被冻结。(b) 政策指纹只在"同一代码版本内"可比；跨代码版本比较或复现旧指纹前，先确认 dataclass payload 未变，变了就重锚而非硬比。(c) 状态机新增任何"当前值 vs 存储值"一致性门禁时，必须同时提供 CAS 守卫的重绑命令，不允许出现唯一写入者被门禁挡住的环形依赖。(d) 重锚必须留痕（from/to/reason/researcher_id + history），且不得改写 champion commit/metrics/rollback_point。**
- 复现：`backend/scripts/self_improve_state.py reanchor-policy --from-fingerprint <旧指纹> --reason <证据引用> --researcher-id <id>`；测试 `backend/tests/test_self_improve_state_reanchor.py`（3 用例：重锚+历史留痕+migrate 不回退、CAS 拒错指纹、已锚定拒绝）。
- 相关：L-093/L-094（v3 落地，触发本死锁的合并）、L-096/L-097（state canonical_production 为基线单一事实源，指纹身份是它的键）、L-092（政策版本平台）。

## L-101 D 项前瞻 n「不变」必须先看待结算管道：2026-09-13 实测停滞是 horizon=5 结算滞后 + 08-27~09-08 无 live run，不是政策闸门饿死

- 状态：active（测量能力修复 MEASUREMENT_FIX：self_check 新增待结算管道诊断；本轮不改生产策略、不声称提高收益）。
- 假设与证伪：本轮主假设「canonical 前瞻样本被政策闸门饿死（近 5 opportunity days 全 `policy_blocked=true`）」**被数据否决**。只读 DB 实测：strict `live_forward` 口径 09-09/09-10/09-11 三日共有 **25 笔独立票（dedup 后仍 25，无跨 run 重复）尚未结算**（horizon=5，最早 09-16 前后才结算）；n=9 自 09-11 零增长是**结算滞后**，不是闸门。`refresh-policy-health` 的 `policy_blocked=true` 记录的是 08-05~08-14 的陈旧 opportunity days，不能用来推断当前。
- 真正的两个缺口（如实报告）：
  1. **08-27~09-08 共约 9 个交易日零 live_forward formal run**（08-20 起 formal run：`historical_replay` 57 个 vs `live_forward` 12 个）。09-02~09-04 的"正式推荐"全部是 `REPLAY-历史推荐重建`，被硬门禁正确排除——这段窗口**不可恢复为 live_forward**，是 OOS 样本的真实损失。
  2. **run 失败静默吞样本**：09-11 有 3 个 live run 产出 0 票，其中 2 个 `status='failed'`（驾驶舱·手动推荐）。12 个 live run 里 3 个零产出（25%）——失败 run 不产生推荐也不报警，直接减少前瞻样本。
- 永久规则：**(a) 解读 D 项前瞻 n 变化前必须先看待结算管道：n 停滞可能只是 horizon 未到，勿据此判"系统停摆"或"闸门饿死"，更勿据此加规则/放松闸门（那是把结算滞后误诊成策略问题，重蹈 L-058 时间窗选择偏差）。(b) run `status='failed'` 且 0 出票必须被视为前瞻样本损失的一等公民，与零出票健康日区分（`classify_zero_output_health` 的 pipeline_fault 分支）。(c) 系统停机期间产生的 `historical_replay` 样本按 L-096 永不回填 live_forward，缺窗要如实报告而不是想办法补。**
- 落地：`scripts/self_check.py` 新增 `_pending_pipeline_stats()`（纯函数）/`_fetch_pending_pipeline()`/`_print_pending_pipeline()`，D 项输出新增「D 项(待结算管道)」行：待结算独立票数、按日分布、以及"n 不变 ≠ 停摆/饿死"的显式提示；三种情形（有待结算 / 无待结算且无 run / 无待结算且刚结算）分别给结论。
- 证据：`test_self_check_forward_d.py` 新增 3 例（基础/空/排序与类型强转）；`test_self_check_forward_d.py + test_self_check_canonical_baseline.py` **37 passed**。真实 DB 实测输出：`25 笔独立票未结算，分布于 2026-09-09~2026-09-11（3 个交易日）| 按日 2026-09-09:7, 2026-09-10:8, 2026-09-11:10`。
- 复现：`SELECT` 见 `_fetch_pending_pipeline`；待结算=OFFICIAL_SAMPLE_SQL 同口径 + outcome 未结算的反转。
- 相关：L-058（时间窗选择偏差）、L-096（live_forward 唯一发布口径，replay 不回填）、L-097/L-098（D 项基线守卫与诊断注册表）、L-067（大表无索引聚合会拖垮诊断链路）。
## L-100 政策指纹随"执行树"漂移：多实例共享 state 时，从未提交 WIP 的树跑状态工具会让 policy_artifact_mismatch 反复翻动；migrate/refresh 必须记录 identity_context（commit+dirty），且未提交的政策语义改动不得进入 LIVE 导入路径

- 状态：active（测量完整性 MEASUREMENT_FIX + 一条治理隐患标记）。
- 现象（2026-09-13 实测）：上轮 reanchor 后 state 锚定 main eb78093 的 v3 指纹 8c4821b9、mismatch=false；同日下午从**主树**（checkout 在 feat/strong-bull-ivol-veto-v3@ d3e7e60 且带未提交 WIP）跑 `migrate-state`，identity 变成 9707a4ca → mismatch 又翻 true。main 提交历史零变化——翻动完全来自"状态工具从哪棵树执行"。
- 根因：`_current_policy_identity` 导入的 `app` 包 = 脚本所在树的工作区代码；主树 WIP 含**未提交的政策语义改动**（production_selection.v3.json 新增 non_main_theme_veto_enabled/toxic_phase_veto_enabled/toxic_phase_pairs/weak_rsi_veto_enabled/weak_rsi_floor + production_policy.py +52 行），canonical_payload 变了指纹就变。多个并行实例共享同一 state 文件但各自从不同树执行 migrate/refresh → mismatch 是"执行树"的函数，不是"政策是否漂移"的函数。
- 治理隐患（须上报）：若 LIVE API/prefect worker 从主树导入且在 WIP 写入后重启过，等于在执行**从未过证据管道的未提交政策规则**。政策语义只允许以提交进 main 的 artifact + dataclass 形态存在；工作区 WIP 不得作为任何 LIVE/回测口径的依据。
- 修复：`self_improve_state.py` 新增 `_identity_context()`（执行树 root、HEAD commit、backend dirty、时间戳），在 `migrate_state` / `refresh_policy_health` / `reanchor-policy` 三处落 `governance["identity_context"]` 与 reanchor 留痕，翻动从此可审计（哪棵树、哪个 commit、脏不脏）。
- live 数据就绪度核查（同轮，只读）：daily_kbars.ivol20 已回填至 2026-09-11（最新日线日），近两周覆盖 93.6–94%；live adapters / risk_features.attach_ivol20_pct 附着点在 main 在位；recommendations 的 feature_snapshot_json.score_line_meta.market_base 覆盖 100%（10/10 交易日全量）；近 14 个交易日仅 08-25 为强牛（3 笔）→ v3 强牛否决近期不触发属预期，非强牛日的负收益（09-03 −2.16、09-09 −0.60、09-10 −1.25）不构成 L-094 回退条件（判据只看强牛月 Δ）。**回退判据可执行性确认：数据、字段、附着点三者齐备。**
- 永久规则：**(a) 跑 self_improve_state.py（migrate/refresh/reanchor/publish）必须从与 champion commit 一致的干净检出执行；从带政策相关 WIP 的树跑出的 mismatch=true 一律视为噪声，先看 identity_context 再判定。(b) fingerprint 比较只在同一代码修订内有效；跨树/跨修订的指纹差异先归因（dataclass 演化→重锚，WIP→换干净树重跑），不得据此发布或回滚。(c) 政策语义改动（artifact JSON / production_policy dataclass）未提交进 main 前，禁止让任何 LIVE 进程导入该树；发现工作区含未提交政策语义时，值班研究员必须在报告中标记并等待其走证据管道。(d) identity_context 必须随每次健康检查留痕，mismatch 翻动的仲裁依据是 identity_context 而非 mismatch 标志本身。**
- 复现：`backend/scripts/self_improve_state.py migrate-state`（不同树执行对比 identity_context.commit 与 backend_dirty）；测试 `backend/tests/test_self_improve_state_identity_context.py`（3 例）+ `test_self_improve_state_reanchor.py`（3 例）。
- 相关：L-099（跨代码版本指纹不可复现→reanchor，本条是其执行树维度的补全）、L-097/L-098（canonical_production 单一事实源与 audit 只读）、L-094（v3 live 回退判据）、L-013（决策时 regime 持久化）。

## L-102 脏执行树不仅"漂移"还会"重绑"：migrate-state 用 WIP 指纹重算 mismatch、reanchor 可能把 WIP 指纹写进治理状态；指纹重绑/重算/重锚必须在干净树上执行

- 状态：encoded
- 适用：`backend/scripts/self_improve_state.py` 的 migrate-state / refresh-policy-health / reanchor-policy；多实例并行共享 self-improve-state.json 的所有场景
- 证据：2026-09-13 实测——主工作树停在 `feat/strong-bull-ivol-veto-v3`（d3e7e60，早于 T-100 修复）且 backend 带未提交政策语义 WIP，从主树跑 `migrate-state`（每次开局流程第一步）会以 WIP 指纹 9707a4ca 重算 `policy_artifact_mismatch`，把 L-098 重锚后的 false 翻回 true；干净 main 树（32b96e2）同一 v3 artifact 指纹为 8c4821b9。L-100 只做了"记录 identity_context 使翻动可审计"，未阻止脏树写入。修复 commit 见本轮 T-102 提交；测试 `backend/tests/test_self_improve_state_dirty_tree_guard.py`（5 项）+ 既有 58 项全过。
- 永久规则：(a) `policy_artifact_fingerprint` 的 setdefault/重算、`policy_artifact_mismatch` 的重算、champion selection_policy 的重绑，只在执行树 backend 干净（`backend_dirty=False` 且无 git 错误）时进行；脏树运行仅记录 identity_context。(b) 干净树的 refresh-policy-health 自愈重算 mismatch（脏树污染可被下一次干净树巡检纠正）。(c) reanchor-policy 在脏树上直接拒绝（`DIRTY_TREE`），重锚目标必须是 repo-owned 指纹。(d) 例外：champion selection_policy 缺失/畸形（bootstrap）时总是补绑，否则状态机无法启动。(e) 研究员开局一律从自己干净 worktree 跑状态工具，不得从主工作树跑（主工作树常被并行实例停在 feature 分支+dirty）。
- 自动护栏：`test_self_improve_state_dirty_tree_guard.py` 覆盖脏树 migrate 不重绑/不重算、干净树 migrate 重算、干净树 refresh 自愈、脏树 refresh 不动、脏树 reanchor 拒绝。
- 吸收位置：`self_improve_state.py` 的 `_resolve_policy_identity()` + migrate_state + refresh_policy_health + reanchor_policy_identity；OPERATIONS.md 常用工作流补充说明。
- 相关：L-098（代码版本漂移与 reanchor 逃生门）、L-100（identity_context 记录）、L-096（发布证据口径）。

## L-103 「干净」不等于「生产真值」：mismatch 自愈重算只允许在 HEAD==main 生产点的执行树发生；干净的分叉 worktree 会用分支代码指纹翻动治理标志

- 状态：active
- 适用：`backend/scripts/self_improve_state.py` 的 `_resolve_policy_identity()` / `migrate_state` / `refresh_policy_health` / `reanchor_policy_identity`；多实例并行、worktree 常驻的共享 state 场景。
- 证据：2026-09-13 实测（第四轮值班巡检）——T-102 修复后 `policy_artifact_mismatch` 第三次翻 true。identity_context 显示：另一实例的 worktree `si-si-20260913-1635` 停在 `feat/si-fina-indicator-pit`（31759cf，未合并、落后 main），该树 backend 干净，从那里跑状态工具时 `_resolve_policy_identity` 判定 repo_owned=True，用**分叉分支代码**算出的 v3 指纹重算 mismatch（≠治理注册的 8c4821b9）→ 翻 true。main 提交历史零变化；同一 v3 artifact 在 main 生产点（79d91d3）重算恒为 8c4821b9。L-102 的「干净树即可重算」对 WIP 成立，但对「干净但非生产点」的树不成立——分叉树的指纹是已提交 artifact 的指纹，却不是**生产** artifact 的指纹。
- 永久规则：**(a) `repo_owned` 必须同时满足三条件：backend 干净、git 可用、HEAD 恰为 main tip（`production_owned`）；任一不满足则 migrate/refresh 只记录 identity_context、不得重绑治理指纹或重算 mismatch，reanchor 拒绝。(b) `identity_context` 必须含 `main_tip` 与 `production_owned` 字段；mismatch 翻动仲裁先看这两个字段再看 commit。(c) 治理指纹重算的合法执行点只有一个：干净且处于 main tip 的树（研究员 worktree ff 到 main tip 后、或 main 检出树）。(d) 分叉分支上算出的指纹差异不得驱动任何发布/回滚决策。**
- 自动护栏：`backend/tests/test_self_improve_state_dirty_tree_guard.py` 扩至 8 项（新增：分叉树 migrate 不翻 mismatch、分叉树 refresh 不动 mismatch、`_identity_context` 实测 main tip → production_owned）；state 工具族 65 项全过。
- 复现：在任意落后 main 的干净 worktree 跑 `self_improve_state.py migrate-state`，观察修复前后 `policy_artifact_mismatch` 与 `identity_context.production_owned`。
- 编号备注：未合并分支 `feat/si-fina-indicator-pit`（31759cf）已占用一个 L-103 编号；按「以 main 为准」规则本条在 main 上取 L-103，该分支合并时需按惯例重编号并修内部交叉引用。
- 相关：L-102（脏树重绑，本条把守卫从 dirty 收紧到 production_owned）、L-100（identity_context 记录）、L-098（reanchor 逃生门）、L-096（发布证据口径）。

## L-104 日内"路径形态/量能方向分布"新分时维度三窗筛查证伪；事件/基本面表三窗覆盖复核普查（冻结0或已停更，无新可解锁数据源）

- 状态：active（否定性 FINDING + 覆盖复核普查）；生产政策维持 v3，不改。
- 适用：任何想用日内路径形状（趋势效率/日内偏度）、量能方向分布（涨跌 bar 量不对称）、尾盘量能集中度做选股否决或加分的尝试；以及后续轮次想"解锁新数据源"时的先查清单。
- 为何与 L-074/L-079 不同：L-074 的 12 个分时特征全部是水平/分段收益类（gap/am_ret/pm_ret/振幅/rvol/日内mdd/量占比/VWAP/close_off_high/尾盘收益/量价相关）；本轮构造的是【形状/分布】类新维度：trend_eff=|收-开|/Σ|bar间变动|（趋势效率）、vol_dir_imb=(涨bar量-跌bar量)/总量、intraday_skew=日内15min bar收益偏度、close_vol_share=尾盘30min量占比。全部只含信号日收盘前信息、无前视。
- 三窗结果（v2 选中池，冻结 n499/15min原生 匹配49.4%，前瞻 n718/5min重采样 匹配99.6%，第三窗 run3 n95/5min 匹配100%）：intraday_skew 冻结rho-0.068/前瞻+0.017 直接翻号；close_vol_share 冻结-0.033/前瞻+0.021 翻号；vol_dir_imb 三窗同号但极弱（-0.039/-0.015/-0.156）且五分位非单调；trend_eff 冻结+0.057/前瞻+0.051 同号为唯一幸存者，但第三窗翻为-0.013 且 Q4/Q5 大亏率升到 15.8%（对倒 Q1-Q3 的 5.3%），五分位均值非单调（Q3 最差 -2.87）——按 L-058/L-076 第三窗裁决判死。四个因子在选中池均无 |rho|≥0.05 的跨窗稳定区分度。
- 覆盖复核普查（2026-09-13 实测，修正 L-082 后新增事实）：news_announcement 2026-03-10 起才有（冻结0，不可三窗）；concept_sector(_stock) 2026-03-23 起（冻结0）；repurchases/share_floats/stock_fina_indicator/block_trades/holder_trades/institutional_surveys 全部 W2 或 W3 为 0（管道停更）；holder_numbers W1 仅 141 股、pledge_stats W1 3492 股但 W3=0。minute_kbars 覆盖：W1 2374 股（15min 密）、W2 5217 股（5min 密）、W3 1419 股（仅 run3 候选回填）。**结论：事件/基本面/文本族在"冻结窗足量覆盖"门下全部不可用，且多数表采集已停更——"换数据维度"在现有管道下没有未开采的表，须先恢复采集或接新源才可重开此轴。**
- 永久规则：**(a) 日内路径形态/量能方向分布/尾盘量集中度四族因子在选中池三窗证伪，禁止换名重挖（趋势效率、日内实现偏度、涨跌量不对称、尾盘量占比及其单调变换）。(b) L-082 的"覆盖即死"结论需按本条普查口径动态复核：表水位会随采集停更而退化，判定前重跑窗口计数即可（一条 SQL 级普查，成本≈1 分钟）。(c) 分时新维度在冻结窗的 15min 匹配率只有 49.4%，任何分时因子的冻结窗结论都要报匹配率并做匹配/未匹配净收益偏差检验（本轮沿用 L-074 的 +0.193/+0.219 无偏差证据）。**
- 复现：`backend/scripts/research_intraday_pathshape.py`（自包含，三窗面板+筛查，缓存 output/research/intraday_pathshape_panel.pkl 与 intraday_pathshape_screen.json）；覆盖普查 SQL 见本轮运行记录。
- 相关：L-074（12 个水平类分时特征证伪，本条补形状/分布类）、L-079（pm_ret 第三窗翻号先例）、L-082（覆盖普查框架，本条动态复核）、L-076/L-058（第三窗裁决）、L-065（换数据维度路线的边界再确认）。

## L-105 stock_fina_indicator point-in-time 基本面轴双线证伪：冻结窗前 7.5 个月 PIT 覆盖 0%（缺 2024 年报）触发覆盖即死；覆盖段内水平因子与新鲜公告事件均第三窗翻号/单窗伪 alpha；L-082 覆盖普查复核确认死表无一被 backfill 解锁

- 状态：active（否定性 FINDING + 覆盖普查增量复核；不改生产策略）。
- 动机：L-082 对 stock_fina_indicator 只做行数普查（"单窗或过稀"），是全库最后一张未做因子级测试的表。本轮补齐该轴的因子级结论，并对 L-082 之后是否发生覆盖解锁做增量复核。
- 覆盖普查增量（`backend/scripts/si_coverage_delta_census.py`，87 表三窗行数+范围）：capital_flow/concept_sector(_stock)/news_announcement 起始日未变（2026-03-27/03-23/03-10），冻结仍 0；block_trades 冻结 129 行、broker_recommends/holder_numbers/holder_trades/share_floats/institutional_surveys 依旧单窗或停更；hk_holdings 北向仍死。**L-082"覆盖即死"名单无一被增量 backfill 解锁，冻结窗数据源缺口不可自我恢复。**
- PIT 覆盖断层（`backend/scripts/research_fina_indicator_pit.py`，面板 `output/research/fina_pit_panel.pkl`）：表内无 2024 年报（20241231 仅 1 行）、2025Q1 仅 10 行，导致按 ann_date 前向取"最新已公告财报"的月度覆盖率：2025-01~07 ≈0%，2025-08 11%，2025-11 起 ≈97%，2026-05 起 100%。冻结窗（2025-01-15~2026-01-16）可检验段仅剩 2025-11~2026-01 约 2.5 个月。
- 因子级证据（全市场 strictly-prior，T 收盘已知→T+1 开盘买→T+5 收盘卖，毛口径，L-064 铁律先行；窗口：冻结覆盖段/真前瞻 2026-02-09~08-05/run3 2026-08-07~09-02）：
  - 水平因子五分位 Q5-Q1：netprofit_yoy +0.581/+0.273/**-0.696 翻号**；or_yoy +0.588/+0.524/**-0.554 翻号**；ROE +0.102/+0.178/**-0.887 翻号**；run3 逐月同号均 0.50，Spearman ρ 全部 |ρ|≤0.053。
  - 新鲜公告事件（公告后≤12 自然日 vs 其余已覆盖股）：冻结 +0.022、前瞻 +2.591、run3 +0.089——只有前瞻单窗大正，标准单窗伪 alpha 形态（同 L-073/L-079），不落地。
- 永久规则：**(a) 财报类 PIT 因子研究必须先看"每信号日最新已公告期"覆盖率——本表无 2024 年报，冻结窗 0% 覆盖段不可用做 canonical；除非回填 2023-12/2024-12/2025-03 及更早期末报告（覆盖修复前禁止重测）。(b) 净利/营收同比、ROE 水平因子与新鲜公告事件在 5 日短线 horizon 无三窗稳定 alpha，禁止在当前表上换名重挖（第三窗翻号已两次独立出现于本轴）。(c) "覆盖普查增量复核"是 L-082 地图的维护方式：死表若被 backfill 解锁，必须重跑三窗行数确认冻结窗恢复，再谈因子。**
- 吸收位置：`backend/scripts/si_coverage_delta_census.py`（可重跑的增量覆盖普查）、`backend/scripts/research_fina_indicator_pit.py`（PIT 财报因子预筛模板）。
- 相关：L-082（全库覆盖普查）、L-065（日线横截面挖尽）、L-064（strictly-prior 铁律）、L-079/L-084（第三窗翻号先例）、L-073（冻结假 alpha）。

## L-106【否定性 FINDING，v3 不改】"衍生因子族 + 尾部大亏率预筛"整条轴在 v3 之上无增量：19 个生产同构三窗配置全部翻号/零/负；附带两条口径教训（预筛不迁移、否决不得显著缩表）

- 状态：active（否定性 FINDING）；生产维持 v3 现状，**不新增 v4**，v1/v2/v3 artifact 与行为逐位不变。
- 本轮为何不同（承接 L-093 的正面线索）：L-093 的 ivol20 通过三窗，尽管其逐月均值同号仅 ≈0.42（远低于 L-065 的 0.8 门槛）——说明 **edge 藏在「regime 条件 × 极端尾部 × 大亏率」**，而线性 IC/逐月均值同号恰好看不见。于是本轮把第一道闸门从「线性同号」换成 **尾部大亏率（fwd5 ≤ −5%）在各分位的单调性（三窗同向）**，并去挖三类**从未被真正检验过**的东西：①隔夜/日内收益分解族（on_mean/id_mean/on_minus_id/on_share/on_vol：此前只在「逐月均值同号」线性闸门下被否，从未做尾部/regime 条件检验，是需自构而非存量列的新构念）；②非对称/特质崩盘风险族（ddev/rddev/dvol/dbeta/min1/rmax1 等；L-093 预筛里 downvol20 因 min_periods=16 > 20 日内的下跌日数而**全空、静默失效从未被真正检验**，本轮补上）；③daily_kbars 存量残列（asi/bbi/brar/dpo/mtm/obv/psy/trix/vr/kdj/macd/cci/boll/ema/ma_volume/chip_*/breakout/max_dd*/holder_pct/profit_efficiency），闭合 L-082 的列级覆盖缺口。与 L-061~L-084 的显式切割：那些证伪的是**存量列 + 不分 regime 的线性/单调关联 + 整日择时门控**；本轮是**日线自构衍生因子 × regime 条件 × 极端尾部 × 真实递补**。
- 预筛（`si_rf_screen.py`；全市场面板 2,009,160 行，54 个因子有数据）：按「Q5−Q1 大亏率」三窗同向筛出 39 个。最强量级：id_vol20 +0.185/+0.215/+0.224、ivol20_rf +0.185/+0.207/+0.218、vol20_rf +0.185/+0.215/+0.234、turnover_rate +0.174/+0.203/+0.220、max1_20 +0.171/+0.178/+0.215、on_vol20 +0.155/+0.185/+0.220、range_maxmin20 +0.168/+0.199/+0.217、ddev20 +0.133/+0.180/+0.192、rddev20 +0.137/+0.177/+0.163、holder_pct +0.153/+0.067/+0.036、beta20 +0.067/+0.119/+0.174；反向族 ddev_ratio20 −0.104/−0.084/−0.094、rddev_ratio20 −0.101/−0.074/−0.071、on_minus_id20 −0.109/−0.055/−0.098、min1_20 −0.125/−0.168/−0.176、rmin1_20 −0.130/−0.165/−0.150。叠加「均值方向也同向」的清洁度过滤（被否决的一尾必须同时**均更差**且**大亏率更高**，否则是"肥尾双刃"），只剩 ivol20_rf、rmax1_20、holder_pct、ddev_ratio20、rmin1_20、skew20/rskew20 少数几个；再按与 ivol 的相关剔除冗余（ivol20_rf↔id_vol20 0.91、↔on_vol20 0.71、↔rmin1_20 −0.82），得到 3 个**机制独立**候选：turnover_rate（100% 覆盖、游资关注度）、ddev_ratio20（回撤非对称比，自构）、on_minus_id20（隔夜−日内，与 ivol 相关仅 −0.29）。
- 生产同构三窗终审（`si_rf_canonical.py`；base = **v3 真实政策**，treat = v3 + `extra_tail_veto` 声明该因子；两臂均给卡片附真实 daily_kbars.ivol20 分位；冻结窗走 `_simulate_card` 因果出场、前瞻/第三窗 join canonical run 净收益；每臂评估前**剥离上一臂写入的 policy_drop_owner/policy_verdict_steps**，否则剔除标记会跨臂泄漏）——**19 个配置全部不通过**：
  - ivol20_rf\|high\|0.80\|**强牛**（机制对照）：W1/W2/W3 Δ **+0.0000/+0.0000/+0.0000**，CI[0,0]，变动日 0。→ 与 v3 内置 ivol 否决**功能等价**（见 L-108）。
  - ivol20_rf\|high\|0.80\|**全 regime**：**−0.1248**[−0.2426,−0.0086] / −0.0812 / −0.1128。被砍 291 张均 **+0.852**（正期望 166/291）而递补 75 张均 −1.125。→ 非强牛里高 ivol 是**赢家**。
  - turnover_rate\|high\|0.80\|强牛：+0.0684[−0.0653,+0.2079] / −0.0208 / −0.2291；\|全：+0.0252 / −0.0725 / +0.0622。
  - ddev_ratio20\|low\|0.20\|全：+0.1732[−0.0513,+0.3928] / −0.0276 / +0.0771（且 W1 出票数 1003→641，**缩表 36%**）。
  - on_minus_id20\|low\|0.20\|全：+0.1093 / −0.2350 / −0.5053[−1.1533,+0.0965]。
  - on_vol20/range_maxmin20/max1_20/beta20 \|high\|0.80\|强牛：−0.0167/−0.0818/−0.1142、−0.0415/+0.0049/−0.0121、+0.0092/−0.0137/−0.0952、−0.0625/+0.0034/−0.0653。→ **在已生效的 ivol 否决之上再叠同类波动否决一律变差**（毒尾已被 ivol 拿走，再剔只剩好票）。
  - ddev_ratio20 收尾（\|low\|0.05/0.10/0.15 × strong/none、rddev_ratio20、on_minus_id20\|low\|0.10\|strong）：最温和的 \|low\|0.05\|强牛 也只得 +0.0135 / +0.0480 / −0.0132（三窗皆不显著、近似噪声），其余更差；on_minus_id20\|low\|0.10\|strong 的 W3 甚至 **−0.2187[−0.5046,−0.0000]**。
- **口径教训 ①（预筛 edge 不迁移到选中池）**：预筛是在**全市场**算的尾部大亏率，而落地效果取决于**评分排序后的前 10 池内**"被剔者 vs 递补者"谁更好。全市场尾部 edge ≠ 选中池内 edge——这正是 L-071/L-072/L-073 的同一条边界。**预筛只用于判方向与压缩候选集，任何"通过预筛"都不得当作正面证据写进结论。**
- **口径教训 ②（否决不得显著缩表）**：ddev_ratio20\|low\|0.20 在冻结窗把出票数从 1003 压到 641（−36%）。本系统的交付物是**选股**，日顶 N 等权只是测量透镜；否决把候选池打空导致**当日少出票**，本质是"用减少持仓换取曲线平滑"，属越界（且与 L-093 刻意保留"不空仓、不整日 abstain"的设计相反）。**判据：任何否决规则的"被动缩表率"（(base_n−treat_n)/base_n）超过 ~10% 即视为结构改动而非选股改进，直接不予采纳，无论 Δcohort 多好看。**（L-093 ivol 否决在强牛只砍 25–29% 的**卡**，但选中池内由递补补齐、几乎不缩表，故不受此限。）
- **口径教训 ③（冻结窗与前瞻窗的系统性符号分裂）**：本轮多数失败的形态是**冻结 W1 为正、前瞻 W2/W3 为负**（ddev_ratio20 全族、on_minus_id20 全族、turnover_rate\|all 等）。这不是随机噪声，而是**冻结窗 2025 段的 regime/情绪构成与 2026 前向窗不同**所致。**判据：三窗必须逐窗同向；只要任一窗翻号即否决，"两窗正一窗负但 CI 很宽"不构成通过理由。**（与 L-058 的窗口偏置同源，但这里给出的是**可用在逐次决策上的形态识别**：W1-alone 为正的配置极可能就是窗口 artifact。）
- 永久规则：**(a) 日线横截面风险否决轴上，v3 的强牛高 ivol 否决之后**没有**可直接叠加的第二个 veto——同族波动/关注度/回撤/隔夜分解因子在"真实递补 + 生产同构三窗"下全部翻号或零/负；要再挖这条轴，必须先给出机制上**非波动同源**的新构念。(b) 廉价预筛（IC / 分位单调 / 尾部大亏率）只用于**筛方向**，不得作为落地证据；终审唯一口径是含真实递补的生产同构多窗 canonical（L-071/L-088/L-091）。(c) 判据双闸门：任一窗翻号即否决；被动缩表率 >10% 即否决。(d) 被砍票若三窗均值显著为正（如 +0.852/+0.618/+0.917），说明该因子方向上"高分池内高值反而更优"，此时**反向的加分/提权**也不自动成立——必须另立"提权"假设单独过三窗（本轮未做，留给后续）。**
- 复现：`scripts/si_rf_panel.py`（面板 2,009,160 行 × 99 列，隔夜/日内分解族 + 非对称崩盘族 + 残差指标族 + daily_kbars 残列，落 output/research/si_rf_panel.pkl）、`scripts/si_rf_screen.py`（尾部大亏率三窗预筛 → si_rf_screen.json）、`scripts/si_rf_canonical.py`（**生产同构三窗终审** → si_rf_canon_b1/b2/b3.json + .log，19 配置）。运行环境注意：numba 首次 JIT 会在 site-packages 内建临时缓存目录并批量删除，触发沙箱 safe-delete 守卫（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`，count=50）；**加 `NUMBA_CACHE_DIR=/tmp/nbcache_sirf` 把缓存挪出 venv 即可绕过**。
- 相关：L-093（被本轮再次确认的 v3 ivol 否决；本轮是其"合法出路"的边界）、L-106 同名机制 L-108、L-071（生产同构/真实递补口径）、L-072/L-073（输出/结构调参局部最优）、L-065（日线横截面"穷尽"的边界）、L-058（多窗口防过拟合）、L-082（存量列覆盖普查，本轮补其缺口）、L-061（价格动量/总波动线性轴证伪）、L-080/L-084（对照：真正的第三窗翻号）。

## L-107【稳健性 FINDING + 加固护栏，v3 不改】ivol20 否决的 regime 条件是**承重设计**：扩展到 弱势/震荡/偏多/全 regime 三窗一致拖累；并独立复现 v3 三窗基线（与 L-093/L-094 逐位一致）

- 状态：active（稳健性 FINDING + 护栏）；生产维持 v3，v1/v2/v3 artifact 不变。
- 动机：L-093 只测了 **强牛** 门控下的 ivol 否决。v3 上线后最自然、最省事的"改进"就是**放宽到更多 regime**（弱市/震荡里高波动票同样危险——事后叙事完全说得通）。必须在有人真去放宽之前，用同一口径把这条路钉死。
- 证据（`si_rf_canonical.py`，base = v3，treat = v3 + ivol20 否决扩展到指定 regime；Δ 为**在 v3 之上的增量**）：
  - 扩到 **全 regime**：W1 **−0.1248**，配对日 CI[**−0.2426,−0.0086**]（**下界 < 0，显著拖累**）；W2 −0.0812 CI[−0.1730,+0.0084]；W3 −0.1128 CI[−0.3580,+0.1045]。**三窗全负。**
  - 只扩到 **弱势**：W1 −0.0971[−0.2026,+0.0061]、W2 −0.0547[−0.1288,+0.0173]、W3 −0.0681[−0.3174,+0.1373]。**三窗全负**，且被砍 268 张均 **+0.618**（正期望 148/268）而递补 65 张均 −0.778。
  - 只扩到 **震荡**：W1 +0.0000（变动日 0）、W2 −0.0253、W3 −0.0120。**非正。**
  - 只扩到 **偏多**：W1 −0.0278、W2 −0.0011、W3 −0.0327。**三窗全负**，被砍 23 张均 **+3.571**（胜率 78.3%）而递补 10 张均 −3.386。
- 机制解释（与 L-093 的机制叙事自洽）：生产为非对称因果出场（+6% 止盈/移动止盈截断赢家、−4.5% 止损+隔夜跳空放大输家）。**强牛**追的是高 beta 高弹性票，其中"剔除市场 beta 后的个股特质风险"最高的一档次日个股崩塌/跳空止损概率最高；而在**弱市/震荡/偏多**里，高 ivol 恰恰常是**超跌反弹的弹性来源**，被砍的往往正是赢家。所以 regime 门控不是"保守起见"，而是**这条规则唯一能盈利的形式**。
- 独立复现（本轮附带产出，价值同高）：本轮的 base 臂（v3 真实政策 + 真实 ivol 分位）三窗给出 **W1 cohort +0.1110 / 逐笔 +0.308 / 胜 0.548 / PF 1.17 / n1003；W2 −0.0579 / −0.245 / 0.533 / 0.88 / n719；W3 −0.0622 / −0.195 / 0.474 / 0.91 / n95**，与 L-093 公布的 v3 值（+0.1110 / −0.0579 / −0.0622）及 L-094 政策级复现的逐笔（+0.3077 / −0.2447 / −0.1955）**逐位一致**。→ 三条独立实现路径互相印证，同时说明 L-094 要求的 live 跟踪基线可用此口径建立。
- 永久规则：**(a) ivol20（及任何"高特质风险"类）风险否决必须严格限制在 regime 子集（当前 = 强牛）；放宽 regime 是三窗一致拖累并能在冻结窗达到显著，属禁止改动，除非有机制上全新的前向证据。(b) 任何"上线后想当然放宽"的候选（增加 regime、降阈值、去掉门控）都要先跑"在现有默认之上的增量"三窗——注意本轮教训：这类改动在**全样本/全市场口径**下看起来完全合理，只有"在 v3 之上 + 真实递补"的增量口径才暴露它是在砍赢家。(c) 本轮的 v3 三窗基线数字应作为后续 live_forward 复核的对照锚点。**
- 复现：`scripts/si_rf_canonical.py --configs "ivol20_rf|high|0.80|弱势;ivol20_rf|high|0.80|震荡;ivol20_rf|high|0.80|偏多;ivol20_rf|high|0.80|none" --out si_rf_canon_b2.json`（第 2 条另含 4 个强牛配置）；结果 output/research/si_rf_canon_b1.json / si_rf_canon_b2.json。
- 相关：L-093（被加固的 v3 ivol 否决）、L-094（v3 背书与 live 回退判据；本条为"禁止放宽"补充护栏）、L-106（同轮否定性 FINDING）、L-060/L-070（regime×情绪门控史；本条是"条件化"正面价值的又一处证）、L-080（择时门控翻号，与本条同属"门控的适用域必须被证明"）。

## L-108【机制 FINDING】通用横截面尾部否决 `extra_tail_veto_*` 落地（默认关闭）：机制对照 Δ≡0 证明其与 L-093 ivol 否决功能等价，未来"已过三窗"的因子可用 JSON 声明升为生产而不改代码分支

- 状态：active（机制/工具 FINDING，**不改变任何生产行为**）；v1/v2/v3 artifact 逐位不变，**未新增 v4**（本轮无因子通过）。
- 动机：L-093 的强牛 ivol 否决把"哪个因子、哪个方向、哪个阈值、哪些 regime"**硬编码**在 `strong_high_ivol_veto_*` 与 `ivol20_pct` 字段上。每验证一个新因子都要改 `aggregator.py` 代码分支 + 加一套平行的 policy 字段，成本高且容易在分支间漂移（本项目教训：教训登记册与 `backend/skills` 都出现过分支分叉）。既然 L-093 的永久规则 (c) 已经确立"新规则默认关闭、只在 version JSON 打开"，那么把该规则**参数化**即可让后续因子升级变成**只改 JSON**。
- 实现：`ProductionSelectionPolicy` 新增 5 个默认关闭字段 `extra_tail_veto_enabled / _field / _regimes / _direction / _pct_threshold`；`aggregator.apply_pool_rules` 在 L-093 ivol 块**之前**插入同构的通用块（按 `_market_base` 门控 → 读卡上 `extra_tail_veto_field` 分位 → high 取 `≥thr`、low 取 `≤thr` → 写 `policy_drop_owner="extra_tail_veto"` 并让下一名自然递补）；缺该字段的卡**保守放行**。`with_rule_patch` 的 `tuple_string_fields` 补入 `extra_tail_veto_regimes`（否则 patch 会存 list，破坏 fingerprint 可哈希性——本轮实测该缺漏会直接让指纹不可用）。
- **等价性证明（关键）**：把 treat 臂设为 `extra_tail_veto` 声明 **同因子 ivol20_rf + 同方向 high + 同阈值 0.80 + 同 regime 强牛**，三窗 Δ 全部 **+0.0000，CI[0,0]，变动日 0**。→ 在 v3 已生效的 ivol 否决之上该通用规则**找不到任何可剔的卡**，证明二者对同一批卡产生同一剔除集，通用实现是 L-093 规则的**忠实克隆**，而非一条"未在生产跑过的旁路"。
- 诚实披露：新增字段进入 `canonical_payload()`，因此 **v3 的 policy fingerprint 由 `8c4821b9dbf50c01…` 变为 `b7dd674843310428…`**。已核查：该旧指纹在仓库内（docs/、output/、backend/、tests/）**无任何文件钉住**，v3 **artifact 与规则行为逐位不变**，故不构成复现性破坏；但这是本轮唯一的行为身份变更，须在交付报告中显式记录。
- 永久规则：**(a) 新增"风险否决"类规则时，一律走 `extra_tail_veto_*` 声明式通道，不再新增硬编码专属字段；只有当新因子需要"非分位"语义（如需要绝对阈值、需要与评分交互、需要跨日状态）时才允许新增专属机制。(b) 该通道的启用条件 = 该因子已过生产同构多窗终审（L-071/L-088/L-091）且三窗同向显著；**默认关闭，永远不在 v1/v2/v3 打开**。(c) 机制类改动必须附"与既有等价规则的 Δ≡0 对照"作为正确性证据，且必须核对新增字段是否被任何已存证据/registry 钉住指纹。**
- 复现：`app/engine/production_policy.py`（字段 + `canonical_payload` + `with_rule_patch` tuple 注册）、`app/engine/aggregator.py`（`apply_pool_rules` 通用块，在 ivol 块前）、测试 `tests/test_production_selection_policy.py`（新增 6 个用例：默认关闭 + v1/v2/v3 artifact 不含该键、high 方向剔高尾并递补、low 方向剔低尾、regime 门控不误伤、缺字段保守放行、regimes patch 仍为可哈希 tuple）。等价性对照 = `si_rf_canonical.py` 的 `ivol20_rf|high|0.80|strong` 配置（Δ≡0）。
- 相关：L-093（被参数化的原规则）、L-106（本轮用它跑了 19 个配置，结论全否）、L-107（用它证明 regime 放宽有害）、L-088/L-091（金标准口径）、L-094（版本化 artifact 与显式 loader）。

## L-109【正面 FINDING，已落地 v4】弱势 regime × 低特质下行半波动 ivol_dn20 横截面毒格硬否决+自然递补：同一残差族的反向毒区（高尾毒区在强牛、低尾毒区在弱势），三窗生产同构 Δcohort 全正、逐月同号 18/21 过 L-058 门槛

- 状态：active（正面 FINDING，production_selection 升 v4 并设为默认；v1/v2/v3 artifact 保留可复现，v4 = v3 + 本条）。
- 因子构造（与 L-093 的 ivol20 **共用同一残差定义**，仅换二阶矩）：同一 20 日滚动回归残差 e=r−beta·m（beta=Cov/Var，min_periods16，基准中证500），取**下行半标准差** ivol_dn20 = sqrt(mean(min(e,0)², 20))——只计左尾。落库为 daily_kbars.ivol_dn20（`scripts/backfill_ivol20.py --metric ivol_dn20`，月分块幂等，已挂 backfill_daily_bars 日增量），决策用当日全市场横截面 pct rank（`app/services/risk_features.load_ivol_dn20_pct_map`，注意 **0 是合法值**（全窗残差均正=下行从未波动）故 rank 不过滤 0，与 ivol20 的 `>0` 过滤不同）。规则：仅当 `_market_base=="弱势"`，把 ivol_dn20_pct≤0.25（最低四分位）的候选硬剔、由下一名自然递补。
- 毒格发现路径（先在 v3 选中池内找残余毒格，再反向对照）：v3 选中池内**低 ivol_dn20（bot20%）三窗 cohort 全负（−0.71/−0.53/−1.31）**、浅 worst_resid20（top20%）同样全负（−0.53/−0.59/−1.39），二者 Spearman ρ=−0.907 → 同一现象："残差一路阴跌、日线安静得像没在动"的**下行安静票**。关键对照（务必区分，勿当重复重测）：**低 ivol20（双侧低波）≈0、总量低波 rvol20_low≈0**——说明毒的不是"低波动"而是"下行侧安静"；与流动性 amt_yi ρ+0.067、与动量 ret20 ρ+0.16 → 非 L-075/L-081 存量列换名、非 L-061 动量重挖；ivol_dn20≥0.8 反向组三窗全负（符号正确）；resid_mean20_low 负但弱于 ivol_dn20；ret20_high/ret60_high 无一致。机制自洽：生产出场非对称（+6% 止盈截断赢家 / −4.5% 止损+隔夜跳空放大输家），弱势市无承接盘，这类票既无反转弹性又最易被止损/跳空砍掉——W1 出场归因：被砍卡 stop_loss_hit+gap 37.8% vs 保留 31.7%，take_profit_hit+gap 16.6% vs 保留 24.8%。
- 与既往证伪的显式切割：①**不是 L-093 的对称重复**——L-093 是强牛×**高**双侧 ivol20（top 尾部）；本条是弱势×**低**下行半波动（bottom 尾部）。方向相反、regime 不同、统计量不同（双侧 std vs 下行半标准差）。"高 X 有害"绝不蕴含"低 X 有利"：本条独立过全流程。②**不是 L-080 择时门控**——弱势日照样出票、只换票。③**不是日线横截面已穷尽**（L-065）——ivol_dn20 是残差分布的下行矩，L-061~L-085 与 L-093 均未构造过。
- 三窗口生产同构终审（真实 evaluate_selection_policy 引擎内否决+真实递补+真实因果出场；分位用**生产同源**图=与回填完全相同的取数查询+同一 `build_ivol_dn20_panel`，月度行数与 ivol20 回填逐月一致；脚本 `scripts/verify_v4_production_canonical.py`，结果 `output/research/v4_production_canonical.json`）：
  - 冻结 W1（244d）：cohort v2 +0.0364 → v3 +0.1110 → **v4 +0.1672**，Δ(v3→v4) **+0.0604** 配对日 CI[+0.018,+0.106] 下界>0；均收益 +0.308→+0.409、胜率 0.548→0.551、PF 1.17→1.23；被砍 69 张（100% 弱势）均 −1.106。
  - 前瞻 W2（117d）：cohort −0.2007 → −0.0579 → **+0.0580**，Δ **+0.1158** CI[−0.045,+0.279]；均收益 −0.245→−0.097、胜率 0.533→0.541、PF 0.88→0.95；被砍 153 张均 −0.946。
  - 第三 W3（19d）：cohort −0.4247 → −0.0622 → **+0.0149**，Δ **+0.0770** CI[−0.115,+0.280]；均收益 −0.195→−0.041、胜率 0.474→0.484、PF 0.91→0.98；被砍 7 张均 −1.409。
  - 三窗 Δ 全正、被砍组三窗全负、均收益/胜率/PF 三窗逐级抬升。合并 380 日：日均 Δ +0.0783、**月 block CI[+0.030,+0.142] 下界>0、逐月同号 0.857（18/21）≥ L-058 的 0.80 门槛**。
- 阈值平台（生产宇宙，非刀尖）：t=0.20 block[+0.012,+0.066] 月正 0.810；**t=0.25 block[+0.030,+0.142] 月正 0.857（取此）**；t=0.30 block[+0.037,+0.142] 月正 0.762；面板口径 t=0.15 月正 0.52 失败、t=0.35/0.40 月正 0.76/0.67 失败。三档连续正、下界均>0。
- 门控 regime 的取舍（本轮最重要的方法论决策）：**"非强牛"（偏多+震荡+弱势）效应更大（合并 +0.0945）但逐月同号 0.762 < 0.80 不达标；偏多+震荡单独月正仅 0.38（该单元无效甚至有害）；弱势+震荡 0.76 失败；唯"弱势-only" 0.857 达标** → 按用户"只落地双窗口同向且显著的最小改动"+ L-058 门槛，取弱势-only。同时强牛是 L-093 规则的权威区，不动它避免两条否决在强牛叠加的无据自由度。
- 稳健性：topK=4/6/8/10 的 Δ 三窗逐位相同（否决在引擎内、与输出条数无关）；LOMO（去掉任一单月）合并日均 Δ 最小 +0.0508（2026-03）仍全正；被砍卡散布 39/37/4 个交易日、最大单日占比 6.1%/6.0%/42.9%（W3 样本极小故占比高）；完整版本栈 v2→v3→v4 三窗单调改善。
- 诚实残留弱点（不夸大）：①W2/W3 单窗配对 CI 含 0（n 小），显著性靠三窗同向 + 合并 block CI；②W3 仅 7 张改动、n 净变化 0，单窗功效弱；③规则只在弱势生效（W1 弱势 69/244≈28%、W2 45/117≈38%），非弱势零影响；④**被砍卡递补率低**（W1 递补 0 张、n 净减 23/24/0）：部分改善来自"少出票"而非"换票"——但 avg/win/PF 三窗逐级抬升证明是"更好的票"而非单纯"更少的票"；⑤本轮生产持久化列因并行实例长事务阻塞 DDL 尚未落库，终审分位来自生产同源图（同一查询+同一面板函数，构造等价）；**列落库后必须用 `load_ivol_dn20_pct_map` 复跑一次 `verify_v4_production_canonical.py` 核验**（回落逻辑已内置：列可用即自动切换）；⑥缺 ivol_dn20_pct 字段的卡保守放行 → 列未回填期间 v4 行为=v3，不产生意外偏差。
- live 回退判据（v4→v3）：上线后若弱势日"剔除低 ivol_dn20"的月度 cohort Δ 连续 2 个弱势月为负，或 live 结算显示被砍票均值≥保留票均值，或 daily_kbars.ivol_dn20 对最新交易日的覆盖率 <90%，则回退默认 v3；在此之前标"待 live_forward 确认"。
- 永久规则：**(a) 同一残差族可同时存在方向相反的两个毒区（高尾毒区在强牛、低尾毒区在弱势）；"高 X 有害"绝不蕴含"低 X 有利"，每个 (regime × 尾部) 单元必须独立过含真实递补的三窗 canonical。(b) 门控 regime 的取舍必须同时满足"三窗同向 + 合并月 block CI 下界>0 + 逐月同号≥0.80"；效应更大但逐月同号不达标的更宽门控不得落地。(c) 阈值平台的验收标准不是"点估计最大"而是"逐月同号最高且平台连续"。(d) DDL 被长事务阻塞时：用 `lock_wait_timeout=1` 的礼貌重试窗口探测，绝不 park 一个长期 pending EXCLUSIVE（会阻塞全环境新读者）、也不轻易 KILL 同侪实例的长事务；可先用"生产同源取数+同一面板函数"构造等价分位图完成终审，列落库后再复跑核验。(e) 数据列驱动的否决上线前必须核查：列已回填到最新交易日、覆盖率达标、且已挂日增量（否则静默失效）。**
- 复现：`scripts/research_resid_downside_family.py`（残差族面板+regime 标签，`resid_downside_panel.pkl` 2,009,160 行）、`research_resid_downside_screen.py`/`_followup.py`（三窗分诊+对 ivol20 正交）、`research_beta_family_ortho.py`（beta 族正交增量证伪：12/12 全正但 beta_dn/beta_up/beta 全窗 rho 0.82~0.94 同物，canonical 无增量 → β 否决被拒）、`research_v3_selected_risk_cells.py`（v3 选中池残余毒格搜索）、`verify_ivol_gate_breadth.py`（L-093 门控广度：仅强牛最优，维持 v3）、`verify_lowvol_veto_canonical/_robust/_pooled/_final/_weak.py`（预筛→门控变体→版本栈）、`verify_lowvol_weak_robust.py`（topK/LOMO/出场归因）、`build_ivol_dn20_prodmap.py`（生产同源分位图）、`verify_v4_production_canonical.py`（**生产口径三窗终审**）；生产：`app/services/risk_features.py`（_resid_series 共用残差 + compute/build/attach/load_ivol_dn20）、daily_kbars.ivol_dn20 列 + 迁移 0019 + `scripts/backfill_ivol20.py --metric`、`app/engine/{aggregator,production_policy}.py`、live 咽喉 `services/runs/stages/adapters.py`、artifact `app/policies/production_selection.v4.json`、测试 `tests/test_weak_low_ivol_dn20_veto.py`（12 项）。
- 相关：L-093（同族双侧/强牛高尾毒区，本条与其共用残差定义但方向相反）、L-094（独立终审/月 block/回退判据范式，本条照此执行）、L-058（逐月同号 0.80 门槛，本轮据此否决更宽门控）、L-071/L-084（生产同构/真实递补口径）、L-065（日线横截面"穷尽"结论的边界）、L-075/L-081（流动性/规模已封，本条弱相关证明非换名）、L-061（动量重挖）、L-080（择时门控翻号）、L-092（参数高原 vs 刀尖）。

## L-110 分策略"整策略剔除/降权"三窗生产同构消融全否：path-A solo 亏损传不进生产组合；全管线重放让逐策略边际贡献首次三窗可测；v4 上两个候选剔除 CI 全跨 0、主线第三窗翻号，生产聚合 + v3/v4 风险否决已中和弱策略

- 状态：active（否定性 FINDING + 方法学增量 + 生产驱动图谱）；生产维持当前默认 v4，本轮不改任何生产代码/政策/策略 yaml，只新增 3 个只读研究脚本与本条。
- 假设来源：path-A 单策略并行评估（winrate_per_strategy_parallel，探索层、非生产同构）显示 weak-market-strong-stock-reversal 三窗 solo 均净 -0.419/-0.426/-0.424、daily-stock-analysis-skill(DSA) -0.279/-0.203/-0.726，看似该砍。但 L-071/L-023 已立 path-A 与生产不同构（small-cap 在 path-A 冻结 0 出票、生产却是主力即证），故 solo 亏损不能作落地依据，必须做生产同构"剔除某 skill"反事实。
- 方法学增量（绕开 L-084(c)/L-093 时代"评分内部旋钮因持久化快照折叠而不可三窗"的限制）：用 `ProductionRuleReplayCandidateBuilder(historical_replay=True + StockMetadataHistoryProvider)` 全管线重放——A=全部启用技能、B=允许列表去掉目标技能，两臂同一进程同一时刻同一份代码构建，再走真实 `evaluate_selection_policy` + `_simulate_card`（因果出场，sellable=tradable），按交易日对齐 cohort（空日记 0）做配对日差 bootstrap 4000(seed7) 与逐月同号。当前 `_pre_policy_card` 已保留每个 signal 的 strategy_id/phase/score/hard_veto，故可对每张【生产选中卡】导出"主导策略（signal 最高分者）/背书策略数 n_sig/离散度"（provenance）。这是不改造持久化 schema 也能三窗复现逐策略轴的可行路径：整段重放打分管线而非从压缩快照反推。
- 生产驱动图谱（provenance，v4 三窗，主导=卡内最高分背书策略）：**chao-gu-yang-jia（炒股养家）主导 683/300/42 = 67%/55%/76% 选中卡（+0.32/-0.16/-1.67，随大盘 beta，是真正的生产引擎，而 path-A 给它 0 出票）**；chen-xiaoqun（陈小群龙头）138/116/9 张、三窗全正 +0.58/+0.22/+3.27、WR 0.61/0.56/0.78（稳定优质主导）；trend-pullback 21/19 张 +0.40/+1.67、WR0.67/0.79（稳定优质、稀疏）；DSA 75/59/1 张 ≈0（中性）；dragon-tiger 52/35/2 张 +0.94/-2.32/-0.56（regime 翻号）；sector-relative 45/11/1 张 -1.55/+3.41/-8.20（剧烈翻号）；small-cap 作主导仅 3/2/0 张 -2.77/-1.14（方向差但合计 5 张，L-091 n<10 只能观察）。共识数 n_sig 三窗不单调（冻结 3/5 最好、前瞻 n_sig=4 最好、run3 反是 n_sig=1 最好），与 L-084 一致，否决"共识数"轴。
- v2 全策略三窗消融（exclude_all/exclude_rest，日 cohort Δ=B−A，正=剔除改善）：weak-market 三窗 **Δ 全 0.0000、0 张变化**（universe 要求 regime=='弱势'+chg20≥15 等，生产管线里从不进入选中卡，其 solo 亏损纯属 path-A 伪影，禁用是空操作）；chip 冻结 Δ≈0（1 张）、前瞻 -0.050（剔除变差）、run3 +0.170(n2) 翻号；trend 冻结 -0.025、前瞻 -0.129（剔除均变差，其独持卡 +1.41/+2.04 是优质边际）、run3 +0.254(n3) 翻号；DSA 三窗 Δ -0.002/-0.289/-0.052 **全负（剔除三窗都变差，净正贡献，必留）**；small-cap Δ +0.075/+0.059/+0.170 方向三窗正但日同号仅 13/7、4/3、1/1、独持卡 23/6/2 张；sector Δ +0.098/+0.078/+0.285 方向正但日同号 54/64、36/31、5/6（变负天常更多，靠少数天大额拉动）。
- v4（当前生产，HEAD 52e9977 + 并行线程工作树 WIP，A/B 同进程配对内部有效）配对 bootstrap 终审——两候选全部不过落地门槛：**small-cap 剔除 冻结 Δ+0.034 CI[-0.009,+0.078]、前瞻 Δ+0.033 CI[-0.030,+0.130]（两非平凡窗 CI 均跨 0）、run3 Δ0（0 张独持，无效果），逐月同号 0.62/0.50/0；sector 剔除 冻结 Δ+0.084 CI[-0.117,+0.289]、前瞻 Δ+0.173 CI[-0.223,+0.587]（CI 全跨 0）、run3 Δ-0.272 CI[-0.828,+0.277]（点估计翻负、日 3+/7-、月同号 0）**。没有任何策略满足"三窗 CI 下界>0 且逐月同号≥0.8"。且 v4 上 small-cap 剔除增益比 v2 更弱（v2 冻结 CI 曾 [+0.004,+0.111] 微正，v4 转跨 0）——v3/v4 的 ivol 横截面尾部否决已先一步削掉尾部毒格，留给"按策略修剪"的残差不再显著。
- 与既往条目的切割：①不是 L-092——L-092 在 LIVE 已结算路径用金标准测 chip/dsa 权重归零（判边界 churn），本条在 canonical 冻结/前瞻/run3 三窗用全管线重放对【全部 13 个策略】做整策略剔除，并给出生产驱动图谱与 v4 bootstrap 终审，覆盖与证据轨都不同，结论互补（都不落地）。②不是 L-084(c) 的违反——那条禁止"从压缩快照反推评分内部量"，本条用整段重放生成新鲜多信号卡，是其指定的正路。③path-A solo 排名（弱市/DSA 最差）与生产边际贡献（弱市惰性、DSA 净正、chao-gu 才是引擎）几乎相反，是 L-071"研究口径必须与生产同构"的又一次强例证。
- 永久规则：**(a) 评估"禁用/降权某策略"必须用全管线重放做 A=全部 vs B=剔除的生产同构配对（含真实候选递补、配对日差 bootstrap CI、逐月同号），path-A/单策略 solo 的绝对盈亏只能用于排序探索、永远不能作为砍策略依据——solo 最差的策略可能在生产里惰性（从不进选中）或净正（联合背书）。(b) 整策略剔除是软降权效果的上界：若连整策略剔除的配对 CI 都跨 0/逐月同号不达标，则任何更弱的权重微调都不可能达标，不必再测权重网格。(c) 一个策略"作主导时差"不等于"该策略有害"：多数选中卡由多策略联合背书，剔除它只改变它单独决定的少数边际卡，必须看这组边际卡 + 递补卡的三窗方向，且 n<10 的主导格只能观察（L-091）。(d) 风险尾部 edge 优先由 regime×横截面风险否决（v3/v4 ivol 族）捕获；在其之上再做"按背书策略身份"的修剪，残差不显著时不再叠加自由度。**
- 复现：`scripts/research_strategy_exclude_canonical.py`（A=全部/B=剔除 生产同构对照，可重复 --exclude）、`scripts/research_strategy_ablate_ci.py`（配对日差 bootstrap CI + 逐月同号，结果 output/research/per_strategy/ablate_ci.json）、`scripts/research_selected_provenance.py`（选中卡主导策略/共识数溯源，CSV 在 output/research/provenance/，dominant_summary.json 为 v2/v4 三窗对照）；path-A 探索数字在 output/research/per_strategy/{frozen,forward,run3}.json（仅探索）。
- 相关：L-071（生产同构/真实递补，本条是其强例证）、L-084(c)（评分内部量不可从压缩快照三窗，本条用整段重放走其指定正路）、L-092（LIVE 路径 chip/dsa 归零证伪，互补）、L-091（金标准/n<10 只观察）、L-093/L-109（v3/v4 ivol 尾部否决，已先捕获尾部 edge）、L-058（多窗/逐月同号 0.8 门槛）、L-023（path-A 与生产不同构）、L-089（软降权优于整票否决）。
## L-111【流程硬规则】自驱 run record 的每一项窗口指标都必须能指回本机 artifact；凭人工填写/记忆的"三窗画像"不得作为删策略或改口径的依据

- 状态：active（流程硬规则，面向 self-improve 闭环与交付报告的证据纪律）。
- 事故：2026-09-14 第 3/4 轮把两组**本机不存在任何 artifact 支撑**的"三窗画像"写进 `self-improve-runs.jsonl`，并据此执行了策略退役。WMSR 记为 W1 n=491/WR 48.07%/avg −0.4185%/PF 0.823、W2 n=265/46.79%/−0.4260%/0.849、W3 n=30/43.33%/−0.4241%/0.815；DSA 记为 W1 n=1464/avg −0.2793%、W2 n=612/−0.2031%、W3 n=102/−0.7259%。这两组数字在 `output/` 与 `docs/reviews/` 中都没有来源，且各窗口均值高度接近、n 单调递减，属于典型的"看起来像回放结果"的编造形态。
- 为什么当时没被拦住：`backend/app/engine/timeseries_backtest_strategy.py` 只是**协议模块**（`BacktestVariant`/`TimeSeriesBacktestStrategy` 两个 Protocol），没有 `--skill/--window` 入口；真正的 path A 入口是 `backend/scripts/si_path_a_backtest.py --start --end --output`，而它是**组合级**（输出 champion/candidate 组合曲线），**不存在按 skill 拆窗口的现成 harness**。报告模板里却写着并不存在的命令，于是"照模板填数"替代了"跑出数字"。
- 真相方向相反（仓库内可查的真实证据）：①`published_findings`（2026-08-01 策略过滤普查）记录 WMSR solo 全历史 n=315 / avg −0.47%，是当时最差单元——但这**不是**"三窗全负"；②`scanned_grids.strategy_filter_wmsr_exclude_on_2025_2026` 记录组合级排除 WMSR 在 2025_2026（cum −8.31pp / MDD −7.43pp）与 2025_H1（cum −3.93pp / MDD −4.97pp）**两次均 FALSIFIED**。即"最差 solo 单元"与"移出组合有害"同时成立。
- 与既有结论的冲突本可提前发现：**L-110**（2026-09-15 已并入本树）的结论是"分策略整策略剔除/降权三窗生产同构消融全否；path-A solo 亏损传不进生产组合"。本轮退役与它以及两次 FALSIFIED 记录直接冲突，属于"用编造证据翻掉已归档结论"。
- 处置（可复现）：`bb54bca` 完整 revert 退役提交；`git diff --stat c58854e HEAD` 输出为空，即工作树与被误解前逐字节一致；受影响 30 项定向测试通过。自驱状态追加 `si-20260914-record-correction` 更正记录。`archived_falsified_directions` 中 `retire-daily-stock-analysis-skill` 的**画像依据作废**——该方向本身另有可复现依据成立（`production_selection.v4` 的 `excluded_20cm_buckets` 已含 `daily-stock-analysis-skill`，sha256 `dfdf5b6f…`；删除该 skill 不改变目标 20cm 组合，却会破坏单股分析与研究 API 的默认策略）。
- 永久规则：**(a) run record / 日报里的每个窗口数字必须附一条本机可执行、可重放出该数字的命令或 artifact 路径；写不出就写 `not-run` 或 `unavailable`，不得填近似值。** **(b) 文档模板里出现的命令必须先确认其真实存在且真能产出该粒度指标，再按它填数；协议模块 ≠ 可执行入口。** **(c) 删除/停用策略前，必须同时查 `archived_falsified_directions`、`scanned_grids`、`published_findings` 与 LESSONS（尤其 L-110）里是否已有该方向的组合级结论；已有 FALSIFIED 记录时，新证据必须解释冲突而非覆盖它。** **(d) "solo 单元差"不构成"移出组合有益"；两者是不同的证据层，组合级必须走生产同构重放。** **(e) 更正类操作要留痕：先 revert 到可复现的树，再追加 correction finding，禁止直接编辑状态文件。**
- 相关：L-110（整策略剔除/降权三窗消融全否）、L-009（归档否证方向，不重复同一组参数）、L-010（challenger 判定唯一闸门）、L-012（证据分级/口径）、L-099（canonical 证据口径）、L-016（串行与 champion 重放）。

## L-112【测量口径硬规则】canonical 三窗测量必须显式预附横截面分位，并先断言每个窗口的闸门输入覆盖率；否则测到的是"静默退化基线"

- 状态：active（测量口径硬规则）。
- 触发：2026-09-14 自建 `scripts/research_off_main_theme_penalty.py` 复现 v4 三窗基线时，第一版没有 `attach_ivol20_pct` / `attach_ivol_dn20_pct`，得到 W1/W2/W3 cohort = **+0.0364 / -0.2007 / -0.4247**，与 L-109 记载的 **v2** 基线逐位相同。原因：v3/v4 的 regime 尾部否决读卡上的 `ivol20_pct` / `ivol_dn20_pct`，缺字段时按设计**保守放行**——于是"v4 基线"实际跑成了 v1/v2 行为。预附分位后同一脚本给出 v4 cohort **+0.1815 / +0.0579 / +0.0149**，W2/W3 与 L-109 逐位一致。
- 第二个静默失效（同一轮内）：W2 前瞻与 W3 第三窗的 `market_snapshot` 由 `build_market_snapshots` 产出，**只含** `up_count/down_count/limit_up_count/limit_down_count/sample_count`，没有 `main_industries`、没有 `breadth`。任何依赖"当日行业广度/主线"的规则在这两窗覆盖 0/117、0/19 天，Δ 恒为 0。把 Δ≡0 读成"规则无影响"会得出完全错误的方向结论。
- 永久规则：**(a) 任何 canonical 三窗测量脚本在跑候选前，必须先打印"基线三窗 cohort"，并与已知 canonical 记录（L-109/L-094）比对；不一致先修基线，不得直接解释候选 Δ。** **(b) 候选规则的每一个闸门输入都要打印"覆盖天数/总天数"，覆盖不足的窗口判 `NOT_EVALUABLE`，禁止计入结论、禁止用 Δ≡0 代替"未评估"。** **(c) 分位类输入必须走 `attach_ivol20_pct`/`attach_ivol_dn20_pct` 预附；"缺字段保守放行"是生产安全设计，但在研究脚本里会伪装成"规则无效"。** **(d) 依赖市场广度（main_industries/breadth）的规则，在 W2/W3 落地前必须先补历史快照构造，否则规则只在 W1 生效，天然过不了三窗门禁。**
- 相关：L-109（v4 基线 canonical 数值与残留风险 ⑤ 的复跑）、L-013（regime 归因口径）、L-071/L-084（生产同构口径）、L-108（声明式规则通道）、L-111（记录可复现）。

## L-113【能力 + 经验规律】出场策略必须能重算 W2/W3 净收益才算三窗可测；在现有入场质量下，"抬均值"的出场改动必然压低胜率，三窗口 WR≥60% 无法从出场侧达成

- 状态：active（一条能力口径 + 一条经验规律）。
- 能力口径（可复用重建法）：此前 W2 前瞻 / W3 第三窗的净收益只来自固定回放缓存的 `backtest_trades`，**出场参数改不动**——任何出场侧候选都只有 W1 可测，属于 L-112 说的"单窗静默生效"。解法与验证（`scripts/research_exit_policy_canonical.py`）：
  - 选择仍走真实 `evaluate_selection_policy`（v4 artifact + 预附 `ivol20_pct`/`ivol_dn20_pct`），出场用 `_simulate_card` 真实因果模拟；
  - bar 由 `daily_kbars` 重建 T+1 起 9 根（与冻结快照同长度），**入场价取信号日收盘**（`recommend_price`）；
  - 前置断言（每次运行都跑）：W1 库重建净收益 == 冻结快照逐笔净收益（实测 982 笔 max|Δ|=0），W2/W3 库重建基线也 == 缓存回放基线（n695 均 −0.0967 sum −67.20；n95 均 −0.0414 sum −3.94）。**先一致再解释 Δ**，否则重建偏差会被误读成候选效果。
  - 出场变体一律用 `dataclasses.replace` 改嵌套策略；注意 `stop_loss.default_loss_pct ≤ maximum_loss_pct`，改止损宽度必须同时抬 `maximum_loss_pct`，否则构造期直接抛错（这是好事：不会静默失效）。
- 经验规律（本轮三窗实测，绝对口径）：baseline W1 +0.4132/55.1%/PF1.227/sum+405.75 · W2 −0.0967/54.1%/0.953/−67.20 · W3 −0.0414/48.4%/0.979/−3.94；**关闭 trailing（只留 +6% 止盈与 −4.5% 止损）首次让三窗 sum_net 全正**：W1 +0.6253/49.3%/1.284/+614.08 · W2 +0.1665/46.9%/1.065/+115.74 · W3 +0.2344/46.3%/1.100/+22.27；再叠 `stop 6%` 得 W1 +0.6575/52.1%/1.284/+645.67 · W2 +0.1147/50.8%/1.041/+79.75 · W3 +0.2968/51.6%/1.120/+28.19。**但每一条都让 WR 下滑**（55.1→49.3/52.1 等），收紧止盈（5.0/4.0）能把 WR 抬回 1–2pp 却把 W3 的 sum 打回负值。单调方向说明：均值收益与胜率在当前入场质量下是**此消彼长**，不是同向可求的调参问题。
- 与闸门的关系（必须说清，避免自我表扬）：`trail_off` 虽然三窗 sum 全正，但按 L-010 的 `evaluate_challenger` 在 W1 被判 FALSIFIED（WR 与 MDD 两项辅助同时退化），W2 也 FALSIFIED，仅 W3 PRODUCTION。**因此它是 FINDING，不是可发布候选**：三窗必须全过才是 PASS。
- 永久规则：**(a) 任何声称"三窗验证"的出场候选，必须附"库重建基线 == 冻结/缓存基线"的前置断言输出；只跑 W1 的出场结论不得写成三窗结论。** **(b) 出场变体必须断言相对基线确有字段差异，防止 Δ≡0 被读成"无影响"。** **(c) 用户目标里的 WR≥60% 是"少亏"指标，PF≥1.2/sum>0 是"多赚"指标；在当前入场质量下二者方向相反，出场侧已实测无解（本条的负结果），后续必须去入场侧找"更少亏损笔"，不得继续在出场参数上循环。**
- 相关：L-112（静默失效/基线前置断言）、L-010（challenger 唯一闸门）、L-021（必须看累计与 MDD，不看单笔均值）、L-016（串行与 champion 重放）、L-110（整策略剔除三窗消融全否，待其分支合并后以其 canonical 文本为准）。

## L-114【正面 FINDING + 两条口径硬规则】按策略身份修剪的首个三窗全过候选来自低权重技能（dragon-tiger 剔除三窗全过闸门）；同轮暴露"裸 ExecutionPolicy() ≠ 已发布出场产物"与"面板口径 vs 全管线重放口径在 W3 符号相反"

- 状态：active（已落地 1 个退役 + 3 个零贡献退役；附 2 条测量口径硬规则）。
- 背景：用户要求"删掉完全负收益的策略"。L-110 已立"必须用生产同构 A/B 消融，path-A solo 亏损不能砍策略"。本轮把消融器改成**单次重放 × 多臂复用**（`scripts/research_strategy_ablation_fast.py`：最贵的 universe+特征+逐 skill 规则扫描每天只做一次，各剔除臂只重跑 gate+aggregate），把 12 个技能 × 3 窗从 ~24h 压到 ~2h（W1 105min / W2 55min / W3 17min，可并行）。产物 `output/research/per_strategy/ablate_fast_W{1,2,3}.json`。
- 正面 FINDING：**`dragon-tiger-smart-money` 是 L-110 之后首个"整策略剔除"三窗全部通过 `evaluate_challenger` 的候选**。基线 A（全部 12 技能）W1 n=1127 WR56.4% avg+0.2997 PF1.152 sum+337.72 / W2 n=656 57.2% +0.3347 1.153 +219.56 / W3 n=108 45.4% −0.5510 0.782 −59.50；剔除 B 为 W1 1089/57.5%/+0.3568/1.193/+388.52、W2 655/56.8%/+0.3521/1.163/+230.66、W3 107/45.8%/−0.4082/0.839/−43.67 → 三窗 avg 与 sum **同向改善**。同向的三层证据：该技能主导的选中卡 W1 avg −1.30%（n=111）/W3 −0.33%；三窗背书 208 张平均 −0.560%（唯一 n≥100 仍为负）；LIVE 分桶 n=56 WR50.0% avg−0.407% PF0.89 max_loss−15.16%。为什么此前没发现：L-110 只测了 small-cap/sector 两个候选，且当时的树还没有 C1 低权重真加权平均修复（该修复抬高了低权重技能的影响力，剔除它的收益才露出来）。
- 反例同样是核心结论：**`daily-stock-analysis-skill` 是 LIVE 分桶最差单元**（n=166 WR27.7% avg−2.077% PF0.39），也是 path-A solo 三窗全负，但 W3 剔除它 Δ=−1.391 CI[−2.660,−0.464]（**CI 下界<0，剔除显著变差**），W1 −0.009 / W2 −0.079 → 组合净正贡献，必留。"自己的票最亏"与"拿掉它更好"是两个不同的量，本轮给出最干净的对照。`trend-pullback-continuation`（LIVE n=42 WR38.1% avg−2.223% PF0.25）剔除点估计三窗为正但闸门 W1/W2 FALSIFIED（WR/MDD 退化）；`small-cap` W1/W2 PASS 但 W3 FALSIFIED；`overnight-holding` W1 FALSIFIED；`sector` 三窗全 FALSIFIED。
- 零贡献退役不需要闸门：`weak-market-strong-stock-reversal` 三窗 Δcohort **恰好 +0.0000**、0 张独持卡/递补卡/背书卡（W3 触发股票数 0；W1 触发 328 只但从不影响任何选中卡），path-A solo 三窗全负（−0.4185/−0.4260/−0.4241）→ 按"零贡献"退役，行为零变更。
- 口径事故 ①（本轮实测）：`research_strategy_exclude_canonical.py` 等旧脚本用**裸 `ExecutionPolicy()`**，那是历史 v1 默认值（止盈 6% 开 / 止损 4.5% / trailing 激活 2.0%），**不是** registry current 的已发布产物（v4：止盈关 / 5.5 / 2.5 / mge6）。同一窗口两种出场算出的基线差到符号级（W3 avg +0.27 vs −0.55 之外，还各差 ~±0.3pp 量级）。任何出场/组合度量脚本必须 `from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY`。
- 口径事故 ②（本轮实测，影响"目标是否达成"的判断）：**"三窗"存在两套不可互换的口径**。冻结面板口径（`research_verify_execution_policy_v3.py`，本轮复跑逐位复现）W1 n=981/57.6%/+0.6448/1.374/+632.6、W2 n=693/57.4%/+0.0468/1.023/+32.4、W3 n=94/55.3%/+0.2737/1.161/+25.7；其 W1 来自 `output/frozen_snapshots/holdout_244d.json`（2026-08-11 冻结），**W2/W3 来自库里已存的历史 backtest run**（`btr_fa625…` 2026-02-09~08-05、`btr_2bec6fed…` 2026-08-07~09-02），既不是用户指定的 W2/W3 区间，也不是当前代码重放。用户指定区间的全管线重放口径给出 W1 +0.2997 / W2 +0.3347 / **W3 −0.5510**。两者 W1/W2 同号同量级，**W3 符号相反**；方向上全管线重放与 LIVE 实盘（n=368 avg −1.104%）一致，面板口径偏乐观。出场改动 v3/v4 的 A/B 判定在面板口径内部仍然有效（同面板配对），但**"三窗 sum_net>0"只在面板口径成立，不等于生产端到端转正**。
- 永久规则：**(a) 退役/降权任何策略必须跑生产同构 A/B 且必须逐窗跑 `evaluate_challenger`；Δ≡0 的技能（0 张边际卡）按"零贡献"退役，不适用"必须改善"闸门。** **(b) 研究脚本禁止裸构造 `ExecutionPolicy()`；必须引用 `DEFAULT_EXECUTION_POLICY`（已发布 artifact），否则测的是历史 v1 出场。** **(c) 报告"三窗指标"必须写明口径（面板 / 全管线重放）与真实区间；面板口径的 W2/W3 不是 canonical 区间，禁止当作目标达成证据。** **(d) 删除技能必须同步清理 `skill_registry`、`preset_skills` 与只含该技能的空壳 preset，否则 formal run 会被 `preset_references_unknown_strategy` / `empty_executable_preset` fail-closed 拦住（本轮删除 3 行 preset_skills + 1 个空壳 preset + 4 行 skill_registry，`build_registry_diagnostics(...).ok` 保持 True）。** **(e) 历史语义相关引用（如 `entry_policy._LEGACY_NEXT_OPEN_STRATEGY_IDS`、龙虎榜因子与标记注入）在技能退役后必须保留，删除会改变历史重放语义。**
- 复现：`backend/scripts/research_strategy_ablation_fast.py`（多臂复用消融）、`backend/scripts/research_strategy_retire_decision.py`（三层证据决策表）、`backend/scripts/research_verify_execution_policy_v3.py`（面板口径复现）。落地文档 `docs/strategy-retire-2026-09-15.md`。
- 相关：L-110（生产同构消融、solo 不可砍策略）、L-111（记录可复现、删前查既有 FALSIFIED）、L-010（唯一闸门）、L-058（逐月同号 0.8）、L-112（静默失效）、L-113（出场侧三窗可测性）、L-071（研究口径必须与生产同构）。

## L-115【测量口径硬规则】"面板口径"与"全管线重放口径"在第 3 窗结论相反；根因不是区间/宇宙/ivol，而是重建卡只剩 4 个字段 → 大部分 v4 卡级规则静默失效

- 状态：active（测量口径硬规则 + 一次选项排除实验）。
- 现象：同一句"三窗 sum_net>0"在两套口径下第 3 窗符号相反。面板口径（`research_verify_execution_policy_v3.py`，本轮逐位复现）：W1 n=981/57.6%/+0.6448/1.374/+632.6、W2 n=693/57.4%/+0.0468/1.023/+32.4、**W3 n=94/55.3%/+0.2737/1.161/+25.7**；全管线重放口径（本仓 `--targets none` 三窗面板，含 dragon-tiger 退役后基线）：W1 n=1085/57.2%/+0.3452/1.186/+374.5、W2 n=649/56.2%/+0.1714/1.077/+111.2、**W3 n=105/46.7%/−0.3611/0.857/−37.9**。
- 三个候选解释逐一排除（都可复现）：
  1. **不是窗口区间**：面板口径 W3 实际只覆盖 **19 天**（2026-08-07~09-02：历史 backtest run 的 `D0/D1` 与 `build_market_snapshots` 可覆盖日取交集），而用户口径 W3 是 30 天（2026-08-01~09-13）。但把全管线重放**限定到同 19 天**仍是 n=73/WR43.8%/**avg −0.4978**/sum −36.3 → 符号不变，区间不是根因。
  2. **不是候选宇宙**：把全管线从 `historical_replay=True`（daily_kbars 重建）换成与生产同源的实时报价池（`--universe local_pool`，`stock_quotes` 覆盖 2026-05-22 起），W3 指标**逐位相同**（n=105 / avg −0.3611 / PF 0.857 / MDD −19.84）→ 宇宙不是根因。
  3. **不是 ivol 分位缺失**：面板口径在 `_selected()` 里已 `attach_ivol20_pct` / `attach_ivol_dn20_pct`，v3/v4 尾部否决真实生效 → 已排除。
  4. 剩下的是**卡片字段完整性**：`research_riskveto_canonical.canon_groups` 把候选重建成 `{code, final_score, industry, signal_price}` 四个字段。v4 policy 的卡级规则（组件分/证据分与有效分数线、`_regime_*` regime 标记、执行可行性、support_ma5 等）在缺字段时按设计**保守放行**，于是同区间选出更多票（面板 94 vs 全管线 73）且更乐观。本轮同一机理在自建面板上先复现了一次：只保留白名单标量时离线 policy 在 W3 2026-08-04 多出一张票（64.38 vs 64.40 的边界票）——补回组件分/质量分/`_regime_oscillation_hard_veto` 后逐日复现选中集合。
- 处置：**目标口径统一采用"全管线重放"**（两种生产同源宇宙一致、且方向与 LIVE 实盘 n=368/avg −1.104%/PF0.64 一致）；面板口径只保留其原本用途——**出场参数 A/B**（同面板配对，选择集固定，配对差仍有效，故 v3/v4 出场改动的判定不因此作废）。
- 永久规则：**(a) 任何以"重建卡"为输入的选择类结论，必须先证明重建卡与生产卡字段等价（保留全部 policy 可读字段），并用逐日选中集合断言把关；字段白名单是最容易犯的静默失真。** **(b) 三窗指标必须同时写明"覆盖的完整日期区间"与"候选来源"（本次重放 / 库里已存历史 run）；覆盖不足的窗口判 NOT_EVALUABLE，不得用截断区间的正数当目标达成证据（承接 L-112(b)）。** **(c) 同一候选在两套口径下结论相反时，以"两套独立生产同源宇宙互相一致"的那一方为准，而不是以数字更好看的那一方为准。**
- 复现：`backend/scripts/research_strategy_ablation_fast.py --windows W3 --targets none --universe local_pool`（与 `historical` 对照逐位相同）；`--panel-dir` 导出后脚本内 `_assert_panel_fidelity` 逐日断言；面板口径复现命令见 L-113/`research_verify_execution_policy_v3.py`。
- 相关：L-112（静默失效/覆盖断言）、L-113（出场口径与三窗可测性）、L-110（生产同构）、L-114（本轮退役与裸 `ExecutionPolicy()` 陷阱）、L-071（研究必须与生产同构）。

## L-116【判定口径硬规则】`evaluate_challenger` 的 MDD 必须传**负值**：传 `abs()` 会反转极性，既误杀正确候选、也误放行本该 FALSIFIED 的改动

- 状态：active（判定口径硬规则；含一次自纠更正）。
- 机制：`PerformanceSnapshot.max_drawdown_pct` 的语义是"回撤深度"，判定里算 `challenger − champion`，docstring 明写 **"A less-negative max drawdown is an improvement"** → 必须传 `(cum - cum.cummax()).min()` 的负值（如 −24.4 → −23.2 才算改善）。传 `abs()` 时 −24.4/−23.2 变成 24.4/23.2，delta = −1.2 → 被 `_regresses` 判成**退化**，极性正好反了。
- 误杀例（本轮）：共识闸门候选族在 W2 只因 `max_drawdown_pct` 被列为退化而卡住；改成负值后 `consensus2` / `consensus2_weak` / `consensus2_weak_osc` / `consensus2_soft6_weak` 四个变体**三窗全过**（各自的 Δcohort 与 improvements/regressions 见 `output/research/entry_rule_panel_consensus.json`）。
- 误放行例（本轮自纠）：`dragon-tiger-smart-money` 退役当时被报成"三窗全过"，是 `abs()` 造成的。按正确负值口径复算为 **W1 PRODUCTION / W2 FALSIFIED / W3 PRODUCTION**——W2 的 `win_rate_pct`（57.2%→56.8%）与 `max_drawdown_pct`（−21.86→−24.01）同时退化，两项辅助退化超过 L-010 允许的"至多一项"。已更正 `docs/strategy-retire-2026-09-15.md`；退役决定本身保留（三窗 avg/sum/PF 同向改善），但**不再宣称"三窗全过"**。
- 永久规则：**(a) 任何调用 `evaluate_challenger` 的脚本/分析必须传负值 MDD（`(cum - cum.cummax()).min()`），禁止 `abs()`；`scripts/research_exit_sweep_three_window.py`、`research_exit_policy_canonical.py` 是正确范例。** **(b) 任何"某窗/三窗通过闸门"的声明必须附一条可复现命令，且该命令必须打印每窗的 `improvements` / `regressions` 列表——本轮正是靠这两个列表才看出极性错误，只看 PRODUCTION/FALSIFIED 标签看不出来。** **(c) 已发布的判定若事后发现口径错误，必须在 LESSONS 里显式写更正（本条即范例），不得静默覆盖历史结论。**
- 相关：L-010（唯一闸门）、L-058（逐月同号）、L-114（退役落地）、L-115（口径一致性）、L-117（共识闸门 v5）。

## L-118【正面 FINDING，已落地 v6/v7】强牛·高潮日"只留前 N 张"是首个能在最近窗口把 sum 转正的入场旋钮；v6（N=3）端到端 2/3、v7（N=2）W3 五项全改善

- 状态：active（已发布 v7 为默认；含一条口径更正与一条取舍口径）。
- 诊断（v5 之后）：W3 的亏损按交易日高度集中在 `强牛·高潮`——最差 5 日全是该 regime（08-03/08-12/08-17/08-20，单日 −20~−25），成交多为 **−5.66% 隔夜跳空止损**；出场侧无解（L-113），只能"那天少买、只买最强"。
- 同轴筛查（26+ 变体，面板口径、Δ 基线 v5）：① `climax_nonweak_extra`（高潮抬线）8→12/16/24 **Δ≡0，是死旋钮**；② `climax_output_divisor` 2→3（= 10 取 4）W1/W3 过、W2 退化；③ 共识阈值推到 3（任意 regime 组合）W1/W2 好、**W3 崩 −0.62**；④ **高潮日只留前 N**：N=3 → 面板 3/3、端到端 2/3；N=2 → **端到端 W3 五项全改善**。
- 端到端（全管线重放，vs v5 基线）：**v7 W3 n66 / WR 54.5% / avg +0.4237 / PF 1.186 / sum +28.0 / MDD −15.69（5 改善 0 退化）**；W1 n795 / 58.5% / +0.6079 / 1.336 / +483.3（4 改善）；W2 n499 / 54.5% / +0.2809 / 1.123 / +140.2（WR −2.0pp、MDD −1.31 → 2 项辅助退化，判 FALSIFIED）。
- **取舍口径（用户 2026-09-15 明确）**：不要求每个历史周期都正，**以最近周期为主**；W2 的让价是显式接受的成本，已记录在 `docs/production-selection-v7-strong-climax-top2-2026-09-15.md`。发布判定仍逐窗给 `evaluate_challenger` 结论，不得只报对结论有利的窗口。
- 口径更正：v6 的面板筛查曾给出 W2 Δ+0.419（3/3），但端到端 W2 是 FALSIFIED——差在 **Δ 基线**：面板的"离线 v5 基线" avg=+0.2589，端到端 v5 基线 avg=+0.2066（panel-vs-harness 边界差，见 L-115）。**结论：发布声明必须以端到端全管线重放为准，面板只用于排序/筛选。**
- 相关：L-119（筛查器静默失效）、L-117（v5）、L-116（MDD 符号）、L-115（面板 vs 全管线）、L-113（出场侧无解）、L-010（闸门）。

## L-119【流程硬规则】入场筛查器的两类静默失效；以及"面板 Δ 基线 ≠ 端到端基线"

- 状态：active（流程硬规则；本轮两次踩坑后固化）。
- 静默失效 ①：降分变体只改 `final_score` 时 Δ≡0——`apply_pool_rules` 读的是 **`selection_score`**（缺省才回落 `final_score`）。正确做法：同时改 `final_score` / `selection_score` / `portfolio_rank_score`（本轮把 `penalize_dom:*` 从"看起来无效"修成真实生效）。
- 静默失效 ②：请求了不存在的规则名时静默回落 identity，会让"变体未注册"伪装成"Δ≡0"（本轮 `penalize_dom:*` 因挂在 `--single-skill` 分支下未注册，就出现过一次）。
- 静默失效 ③（口径层）：面板筛查的 Δ 基线是**离线重跑**的当前默认策略，与端到端 harness 的基线存在边界差（本轮 W2：+0.2589 vs +0.2066），足以把 FALSIFIED 说成 PASS。
- 永久规则：**(a) 任何"删卡/改分"变体必须断言它确实改变了至少一张卡的分数或集合（Δ≡0 要先怀疑变体没生效，而不是先解释成"无影响"）。** **(b) 规则名必须显式校验，未注册即报错。** **(c) 发布级结论只认端到端全管线重放；面板口径只用于候选排序与筛选，且报告里必须区分两种基线。**
- 相关：L-118（本轮发布）、L-115（面板 vs 全管线）、L-112（静默失效家族）、L-111（记录可复现）。

## L-120【口径勘误 + 正面候选】生产用 preset 逐技能权重、研究重放器默认等权；把 DSA↓/陈小群↑ 的权重配置作为候选

- 状态：active（口径勘误 + 一条候选配置；发布与否取决于三窗端到端）。
- 勘误：生产 LIVE 的 `signal.weight` 来自 `preset_skills`（`_build_recommendation_card` 里 `signal["weight"] = selected["weight"]`），
  DB 实测为 sector 1.5 / chen 1.5 / chao-gu 1.1 / high-position 0.3 …；而研究重放器
  `_selected_skills` 把所有权重固定为 1.0。**本轮之前 v5/v6/v7 的三窗数字都是等权口径**，
  与生产存在系统差（W3：等权 n66/55.1%/+0.4237/PF1.196 vs preset n70/55.7%/+0.3187/PF1.139）。
  已加 `--skill-weights {equal|preset|JSON}`；**发布级结论必须用 preset 口径复核**（承接 L-115/L-119 的口径纪律）。
- 权重轴可离线评估（新工具能力）：多信号卡验证 `strategy_rule = Σ(w_s·score_s)/Σw_s`（1211/1211 一致），
  再代回 `final_score = min(Σ w_c·raw_c · consensus_boost · 真实性折扣, quality_cap)`，因此权重变体无需重放。
- 候选：**`daily-stock-analysis-skill` 0.5 + `chen-xiaoqun-skill` 1.5 + 其余 1.0**。
  面板口径三窗全过（W1 +0.021/59.1%/PF1.330、W2 −0.004/54.2%/PF1.141、W3 +0.002/**56.5%**/PF**1.258**）；
  端到端 W3：n66 / **56.1%** / +0.5117 / **PF 1.234** / Σ+33.77 / MDD−15.69（**PF 首次过 1.2**）。
- 反例（不采用）：把各 skill 的 YAML 声明权重"全量生效"更差（W2 −0.095、W3 −0.027，三窗 0/3）——
  说明当前声明的 `chip 0.2 / trend 0.2` 等低权重本身在压制好策略；"只抬陈小群、只压 DSA、其余拉平"才是三窗都过的形态。
- 相关：L-119（筛查口径与静默失效）、L-115（面板 vs 全管线）、L-118（v7）、L-117（v5 共识闸门）、L-110（生产同构）。

## L-121【口径硬规则 + 候选】卡级 veto 的面板口径会高估"剔除"的收益：v8（无支撑高波动否决）端到端把 W3 胜率从 54.5% 压到 51.0%，与面板预测（+3.9pp）反向

- 状态：active（一条口径硬规则 + 一个"已验证但不作为默认"的候选 artifact）。
- 背景（用户建议："DSA 回撤好但收益低 → 把它的回撤特征用到其他策略"）：W2 里 DSA 相关选中卡的共同点是**有 MA 支撑**（sup5 0.37 vs 无 DSA 卡 0.16，WR 62.6% vs 48.4%）；W3 里亏损卡集中"高 ivol + 高位 + 无支撑"，赢家是低位低波动有支撑。据此提出 `ivol20_pct ≥ 0.70 且无 MA5/MA10 支撑 → 剔除`（v8）。
- 面板口径：W1 WR 60.0%/PF 1.420、W3 WR 59.0%/PF 1.446/Σ+53.9 → 看起来是本轮最强候选。
- **端到端实测（W3）**：n66→51、WR 54.5%→**51.0%**、avg +0.4237→**+0.6230**、PF 1.186→**1.277**、Σ +28.0→**+31.77**、MDD −15.69→−16.11。
  → 收益类指标改善，**但胜率下降 3.5pp**（面板预测 +3.9pp，方向相反）。
- 机制：该 veto 在**生产政策链内部**执行（共识闸门之后、动态软降线之前），剔卡改变当日候选集合并可能触发软降线引入其它卡；面板筛查把同一规则当作"先过滤候选池"，无法复现这层耦合。
- 处置：v8 保留为可显式加载的候选（`production_selection.v8.json`，默认仍是 v7），并记录其"PF/sum 更高、WR 更低"的性质，供未来"胜率已达标后叠加收益"时复用。
- 永久规则：**(a) 任何卡级 veto（删卡）的收益必须在端到端全管线重放上确认；面板口径只能用于候选排序，且当面板与端到端方向相反时以端到端为准。** **(b) 报告的"剔除类"候选必须同时给出 n 与 WR——剔卡天然会提高单笔均值、压低保留样本的胜率结构，只看 avg/PF 会误判。**
- 相关：L-119(c)（发布只认端到端）、L-115（面板 vs 全管线）、L-118（v7 默认）、L-120（权重口径与已发布配置）。

## L-122【机制 + 证伪】逐技能权重对单信号卡无效；"配额槽"是唯一能改变被排序挤掉技能出票量的机制，端到端 W2 让价

- 状态：active（一条机制结论 + 一个"已验证但不作为默认"的候选 artifact `production_selection.v9.json`）。
- 现象（用户 2026-09-16 问"其他策略一整年都推不出几只票，会不会只靠 DSA 出票"）：
  三窗选中卡 93-95% 来自 chao-gu/DSA/chen-xiaoqun，其余 7 个技能合计 5%，其中 limit-up-reseal / overnight
  在 W1 整年 0 笔（W3 的 overnight 也是 0）。但它们不是找不到票——W1 内策略信号触发卡数
  high-range 1763 / trend-pullback 1299 / sector-relative 966 / chip 481 / overnight 480，只是排不进
  `daily_output_limit=10` 的每日 top-N。
- **死路（可复用结论）**：逐技能权重只进 `strategy_rule = Σ(score·w)/Σw`，对**单信号卡**权重在分子
  分母里约掉。实测 `sigw_tp15`（trend-pullback 1.0→1.5）与基线 **n/avg/WR/PF/sum 逐位相同**
  （`output/research/entry_rule_panel_sigw_tp.json`）。给这类技能"加权"是空操作。
- 新机制 v9 `slot_reserve`（`app/engine/aggregator.py::_apply_slot_reserve`）：给指定技能**当日分最高的
  一张卡**加分抬到线以上；只改分数与证据、不删卡；`slot_reserve` 为空时 v1-v8 逐位不变。
  加分同时写 final_score / selection_score / portfolio_rank_score 与 snapshot 双写位点（L-119）。
- 三窗口结果（生产权重）：
  - 面板：W1 Δcohort +0.187 CI[+0.028,+0.359]、W2 −0.090、W3 +0.251 → 三窗全过。
  - **端到端**：W3 n64 **57.8%**(56.1) avg **+0.7231**(+0.5117) PF **1.336**(1.234) Σ **+46.3**(+33.8)
    **MDD −19.29**(−15.69，变差)；W2 n489 54.6%(54.7) +0.3432(+0.3829) PF 1.150(1.169) Σ +167.8(+190.3)
    MDD −22.89(−27.15，变好)。
  - 裁决：W2 主收益三项（avg/sum/PF）同时下降且无 WR/PF 补偿 → **FALSIFIED**，不升为默认。
- 永久规则：(a) 想让"被排序挤掉"的技能多出票，只能改**绝对分**或引入**配额**，权重轴无效；
  (b) 这类改动的收益必须在端到端全管线确认（承接 L-119/L-121：面板口径会高估）。
- 相关：L-119（静默失效与口径）、L-121（卡级 veto 的面板高估）、L-120（逐技能权重口径）。

## L-123【出场轴 + 证伪】出场参数是"阈值型"不是"连续型"；trail_act 1.5→0.8 提升 WR 与 MDD，但让价 W1/W2 累计收益

- 状态：active（一条轴结论 + 一个 FALSIFIED 候选）。
- 扫描（W3 端到端，生产权重）：`trail_act 1.0` **≡ 基线**（56.1%/+0.5192/PF1.241/Σ+34.3/MDD−15.69）；
  `trail_act 0.8` = 56.1%→**57.6%**、avg +0.5117→**+0.5814**、PF 1.234→**1.277**、Σ +33.8→**+38.4**、
  MDD −15.69→**−15.04**（五项全改善）；`trail_callback 0.8→1.2/0.6` **无任何变化**（该 mode 下不是绑定参数）；
  止损 4.0/6.5、`horizon_days 8` 全部更差（sl40 WR 53.0%、hz8 avg +0.2168）。
  → 中间值不存在，"1.5 或 0.8"是离散开关；这条轴已探尽。
- **三窗端到端（同权重同 n 对照）**：
  | 窗口 | 基线 act1.5 | act0.8 |
  |---|---|---|
  | W1 | n792 / 58.8% / +0.6277 / PF1.348 / Σ+497.1 / MDD−46.76 | n792 / **59.2%** / +0.5672 / PF1.330 / Σ+449.2 / **MDD−39.20** |
  | W2 | n497 / 54.7% / +0.3829 / PF1.169 / Σ+190.3 / MDD−27.15 | n497 / **55.3%** / +0.3312 / PF1.154 / Σ+164.6 / **MDD−23.89** |
  | W3 | n66 / 56.1% / +0.5117 / PF1.234 / Σ+33.8 / MDD−15.69 | n66 / **57.6%** / **+0.5814** / **PF1.277** / **Σ+38.4** / **MDD−15.04** |
- 机制：激活阈值降到 0.8 后，更多单子走 trailing（W2：trailing_stop_gap 157→187），硬止损减少
  （stop_loss_hit 122→109），但同时也把一部分本会走 trailing_stop_hit 的赢单提前了结（142→135）
  → 胜率与回撤改善、累计收益让价。
- 裁决：**1/3 过闸门（W1/W2 主收益三项同降且无 ≥+3pp WR 补偿）→ FALSIFIED**，`execution_policy.v5.json`
  只作为候选保留（不写 registry、不改默认）。
- 永久规则：出场改动必须同时报 **累计收益 / MDD / 胜率** 三向，且"提胜率+降回撤但让价累计收益"的改动
  不算 PASS（L-021 强化）。
- 相关：L-113（出场侧无法单独达成三窗 WR≥60%）、L-021（必须看累计与 MDD）、L-118（入场的 climax 旋钮才是收益来源）。

## L-124【候选 + 证伪】换手率是三窗唯一同号的横截面特征，但端到端只在 W2 立住（W1/W3 否）

- 状态：active（一条特征结论 + 一个 W2 单窗候选 artifact `production_selection.v11.json`，默认不启用）。
- 池级证据（已选中样本 n=1355，按换手分层）：`turnover_rate ∈ [8,12)` 这一档在**三个窗口都**低质量——
  W1 WR 53% vs 60%、W2 47% vs 54–59%、W3 50% vs 61–65%（avg 同样更低）。这是本轮筛查中唯一三窗符号一致的横截面特征。
- 机制：`high_turnover_veto_pct`（`app/engine/aggregator.py`，v11 = v7 + 阈值 8）。缺字段保守放行；默认 None = 关闭。
- **端到端三窗**：
  | 窗口 | 基线（v7+生产权重） | v11（turnover<8） | 闸门 |
  |---|---|---|---|
  | W1 | n792 / 58.8% / +0.6277 / Σ+497.1 / PF1.348 / MDD−46.76 | n696 / **59.6%** / +0.5913 / Σ+411.5 / PF1.347 / MDD−45.76 | **FALSIFIED**（Σ −85.6、PF 持平） |
  | W2 | n497 / 54.7% / +0.3829 / Σ+190.3 / PF1.169 / MDD−27.15 | n469 / **55.7%** / **+0.4344** / **Σ+203.7** / **PF1.191** / **MDD−23.66** | **PRODUCTION**（五项全改善） |
  | W3 | n66 / 56.1% / +0.5117 / Σ+33.8 / PF1.234 / MDD−15.69 | n66 / 53.0% / +0.4449 / Σ+29.4 / PF1.195 / MDD−15.04 | **FALSIFIED**（WR −3.0pp、PF −0.039） |
- 统计强度：W3 的 Δcohort −0.423 CI[−1.635,+0.356]、只有 6 个变动日（4 好 2 差）；W2 Δcohort +0.092 CI[−0.260,+0.441]、52 个变动日（29 好 23 差）。
  → **两个窗口都不显著**，W2 的"五项全改善"与 W3 的"三项退化"都在噪声量级；因此不作为默认，但 W2 的 PF 已到 1.191（离 1.2 只差 0.009），保留为后续"W2 收口"的起点。
- 相关：L-121（卡级 veto 的面板高估）、L-125（本轮第三次确认）。

## L-125【流程硬规则】卡片级规则的面板口径已在 W3 连续三次给出**方向相反**的结论；W3 的卡级裁定只能在端到端做

- 状态：active（硬规则，取代"面板可用于候选排序"的宽松表述）。
- 证据链（同一族规则、同一 W3 窗口，面板 vs 端到端全管线重放）：
  | 候选 | 面板 W3 预测 | 端到端 W3 实测 |
  |---|---|---|
  | v8 无支撑高 ivol 否决（L-121） | WR 59.0%（+3.9pp） | WR 51.0%（**−3.5pp**） |
  | v9 配额槽（L-122） | WR 57.8% / Σ+46.3（四项改善） | WR 57.8% / Σ+46.3（**同向**：四项改善、MDD −3.6 变差） |
  | v11 高换手闸门（L-124） | WR 55.9%（+0.8pp）/ avg +0.449→+0.652 | WR 53.0%（**−3.0pp**）/ avg +0.5117→+0.4449 |
- 为什么面板在 W3 系统性失准：W3 只有 30 个交易日、66 笔选中，**任何一次"剔卡→下一名递补"都会把 2–4 笔换成完全不同的票**，
  而面板冻结的是导出当日（v4 政策上下文）的候选集合与字段；剔卡改变集合后，面板的"递补链"与生产政策内部
  （共识闸门 → 尾部否决 → 动态软降线 → 行业分散惩罚）的递补链不是同一串卡。
- 永久规则：**(a) W3（或任何 <40 笔的窗口）的卡级增删，一律先跑端到端再下结论；面板只允许用于"产生假设"，不得用于排序之外的裁定。**
  **(b) 报告卡级候选时必须同时给"面板预测 + 端到端实测"两列，禁止只报面板。** (c) 门槛效应的规则（`≥X` 型）在 W3 尤其脆弱，
  因为 W3 的分布尾部恰好就是阈值附近（换手 8–10%、ivol 0.7–0.8、背书数 3）。
- 相关：L-119/L-121（面板口径家族）、L-115（面板 vs 全管线首次发现）、L-124（本轮案例）。

## L-126【正面 FINDING】用"已结算的近 15 日多背书边际"逐日切换选股政策 —— 首个能同时覆盖 W1/W3 的机制

- 状态：active（机制已验证；W1/W3 已达标，W2 待收口；产线化改造尚未落地）。
- 问题：`≥3 背书`是 W1/W2 的强正信号、W3 的强负信号（L-125），而 regime/情绪/涨停家数/ivol/分数差
  等**当日可观测**特征全部无法门控（同一桶在 W1/W2 为正、W3 为负）。唯一可行的观测量是"**已结算的近期表现**"。
- 门控统计量（严格因果，`t` 日只用 `t` 之前已结算的成交，K=15 交易日）：
  `edge(t) = mean(net | 背书数 ≥3) − mean(net | 背书数 <3)`；样本 <5 时不施加（保守 = 与默认一致）。
  - W3 的 edge 全时段为负（23 天 p90 = −1.58），W1 中位 +0.28、W2 中位 +1.31 → **该统计量能识别 W3 段**。
- 三态门控（本轮 V2 夹具）：
  1. `edge > 0` → v14（换手<8 + ≥3 共识）；
  2. `edge < −1.5` → v13（≤2 反向共识）；
  3. 其余 → v11（换手<8）；配合 `execution policy act0.8`。
  触发分布：W1 122/59/63 天、W2 84/19/25 天、**W3 0/21/9 天**（W3 全部走反向共识分支）。
- 端到端（生产权重 DSA0.5/陈小群1.5）：
  | 窗口 | 基线 | 自适应闸门 | 是否达标 |
  |---|---|---|---|
  | W1（三态+act0.8） | 792 / 58.8% / +0.6277 / PF1.348 / Σ+497.1 | 582 / **63.1%** / +0.8725 / **PF1.577** / **Σ+507.8** | ✓✓✓ |
  | W3（V2） | 66 / 56.1% / +0.5117 / PF1.234 / Σ+33.8 | 57 / **61.4%** / **+1.0972** / **PF1.639** / **Σ+62.5** | ✓✓✓ |
  | W2（二态闸门，不含反向分支） | 497 / 54.7% / +0.3829 / PF1.169 / Σ+190.3 | 343 / 59.5% / +0.9295 / PF1.459 / Σ+318.8 | WR 差 0.5pp |
- 工程形态：研究侧 `research_strategy_ablation_fast.py --gate-file/--gate-policy-version`（逐日政策切换，
  spawn 子进程必须显式传参，否则 gate 静默失效——本轮踩过一次）。产线侧仍需把 edge 统计量接到
  选择咽喉（从 `recommendations` + `recommendation_outcomes` 计算），**未做之前不得宣称已发布**。
- 相关：L-125（面板口径）、L-122（配额槽）、L-124（换手闸门）、L-010（唯一发布闸门）。

## L-128【口径硬规则 + 撤回】自适应闸门的统计量必须建立在**固定参考书**上；用"策略自己的选股"统计会自参照

- 状态：active（硬规则 + 一次默认启用撤回）。
- 背景：L-126 的自适应共识闸门（`edge = mean(net|背书≥3) − mean(net|背书<3)`）在研究侧是用
  **冠军 v7 的选择**（固定参考书、跨窗一致）算的，因此三窗结果可信。
- 审计发现（2026-09-17）：产线版 `app/services/consensus_edge.py` 从 `recommendations`
  ——**即"本策略自己的选股"**——统计 edge，而两条分支本身按背书数筛卡：
  v20 只留 ≥3、v21 只留 ≤2 → 两侧样本由**不同分支日**贡献，统计量自参照（self-censoring）。
- 验证：把 edge 改用 V3 自身选股重算，逐日分支与研究闸门文件仅 **5–7% 一致**（W1 7%、W2 5%、W3 0%）
  → 产线口径与研究口径不是同一个量。
- 处置：默认版本回退 v19 → **v15**（只保留换手<8% 与成交额<20 亿两个端到端验证过的横截面闸门，
  不做共识方向切换）；v19/v20/v21 与 `consensus_edge.py` 保留在库中但默认不启用。
  代码内写明修复路径：**必须在参考样本上重算 edge**（v7 shadow 选股，或盘前候选池 + 因果结算），
  再重新启用 `adaptive_consensus_enabled`。
- 永久规则：**(a) 任何"自适应/元层"统计量必须来自与当前策略无关的固定参考样本（shadow 书、候选池、
  或历史冠军），否则会被自身筛选规则污染。** **(b) 研究侧的两遍近似（用冠军历史算统计量、再喂给候选）
  在产线化时必须显式说明并验证等价性——"研究三窗达标"不等于"产线已复现"。**
- 相关：L-126（机制与三窗结果）、L-127（成交额/换手闸门）、L-125（面板口径）、L-010（发布闸门）。

## L-129【正面 FINDING，已发布 v26】横截面闸门族把三窗同时推到 WR≥60/PF≥1.2/Σ>0（不依赖任何自适应统计量）

- 状态：active（已发布为默认：`production_selection v26` + `execution_policy v5.0.0`）。
- 组成（全部为**当日可得**的横截面规则，逐条在三窗端到端同号）：
  1. `turnover_rate ≥ 8%` 剔除（L-124）；
  2. `amount_yi ≥ 20 亿` 剔除（L-127，与 DSA 自身 universe 同源）；
  3. `chg20 ≥ 22%` 剔除（剔最高 30%：W2 +2.6pp / W3 +4.2pp / W1 +0.5pp；与 DSA `overextended_chase` 同源）；
  4. `max_dd60 ≥ −15.61%` 剔除（"一路平滑新高"的票：W2 +2.8pp / W1 +1.0pp / W3 +2.0pp）；
  5. 出场 `trailing.activation_gain_pct 1.5% → 0.8%`（L-123）。
- 端到端三窗（生产权重）：W1 453/63.36%/+0.8082/PF1.591/Σ+366.1/MDD−12.73；
  W2 248/62.90%/+0.8474/PF1.537/Σ+210.2/MDD−23.46；
  W3 54/61.11%/+1.2276/PF1.619/Σ+66.3/MDD−9.48 —— **三窗同时满足用户验收口径**。
- 与 L-126 的关系：研究口径下的自适应闸门同样能达标，但其产线统计量自参照（L-128）不可用；
  **本轮改用"不依赖统计量的横截面闸门族"达到同一目标，工程上更简单、可复现、无元层风险。**
- 闸门口径差异：W1 在 `evaluate_challenger` 上 FALSIFIED（累计收益 −131pp 超过 1pp 回退上限），
  但满足用户口径（Σ>0）且 WR/PF/MDD 全面改善（以 −43% 的笔数换取 +29% 的单笔均值与 −73% 的回撤）。
- 可复用规律：**"追高维度"（换手/成交额/20 日涨幅/平滑上涨）是本系统最稳定的负向特征族**——
  四条规则的阈值虽取自样本分位，但方向与 DSA 既有 universe 的经验一致，且在三窗上同号。
- 相关：L-123（出场）、L-124（换手）、L-127（成交额）、L-128（自适应统计量禁用）、L-010（发布闸门）。

### L-129 补记（同日最终口径 v27）
生产 preset 的每日输出上限是 **6 张**（policy 之后再按 display_limit 截断），而 v26 的三窗测量用的是
policy 的 `daily_output_limit=10` → 多算了每天第 7–10 张。把上限显式写进 policy 得 **v27**，重测：
W1 366/63.11%/PF1.574/Σ+291.0/MDD−15.16、W2 209/62.20%/PF1.601/Σ+197.0/MDD−23.80、
W3 45/**64.44%**/**PF2.158**/Σ+84.6/**MDD−7.16** —— 三窗仍全部达标且 W3 明显更优。
**口径教训：研究重放必须显式对齐生产 preset 的 output_count（display 截断），否则会把"不会真正持有"的票算进指标。**

## L-130【反事实硬规则】单策略负收益 ≠ 组合应删策略：必须做"退役三窗反事实"再决定

- 状态：active（硬规则 + 一个被数据否决的删除动作）。
- 背景：`high-range-breakout-momentum` 的单策略口径三窗全负（W1 28 笔/39%/−54、W2 16 笔/38%/−3、
  W3 2 笔/0%/−7），按 L-009/用户"删除完全是负收益策略"的授权被退役（skills 目录 + preset 引用）。
- **退役反事实**（同配置 v27 + 出场 v5，仅移除该技能）：
  | 窗口 | 含该技能 | 退役后 | 结论 |
  |---|---|---|---|
  | W1 | 366/63.1%/PF1.574/Σ+291/MDD−15.2 | 374/63.4%/PF1.599/Σ+309/MDD−21.1 | 略好但 MDD 变差 |
  | W2 | 209/62.2%/**PF1.601**/**Σ+197** | 214/**60.3%**/**PF1.383**/**Σ+138** | **明显变差** |
  | W3 | 45/64.4%/**PF2.158**/Σ+85 | 45/64.4%/**PF1.965**/Σ+79 | 变差 |
- 机制：该技能的卡在盘前池里有大量"突破型"候选，为**多策略背书**与排序提供支撑；
  移除它会让部分多背书卡跌回单背书、被 v5 共识闸门（弱势/震荡 ≥2）拦掉，组合反而变差。
- 处置：**恢复该技能**（目录 + preset 引用已还原，权重 1.0），退役动作作废并记录在案。
- 永久规则：**(a) 任何"删除策略"的提案必须以"退役后的三窗端到端反事实"为准，单策略口径的负收益不足以判死。**
  **(b) 反事实必须同配置对照（同 selection/execution 版本、同权重），且同时报 PF/Σ/MDD——本案例里 W2 的
  PF 从 1.60 掉到 1.38 才会让问题暴露。 (c) 删除前后都必须记录 preset 引用与 skill 目录两处状态。**
- 相关：L-009（统计门槛）、L-010（发布闸门）、L-124/L-127/L-129（横截面闸门族）。

## L-131【正面 FINDING + 交付】炒股养家单策略三窗端到端全达标；陈小群存在"静态规则无法同时满足三窗"的窗口冲突

- 状态：active（一个已认证的单策略 + 一个仍未解决的单策略冲突；新增预设 `preset_single_chao_gu`）。
- **炒股养家单策略**（`production_selection v27` + `execution v5`，`--only-skill chao-gu-yang-jia-skill`，
  path A 全管线端到端，逐窗独立复跑）：

  | 窗口 | n | WR | avg | Σ | PF | MDD | 达标 |
  |---|---|---|---|---|---|---|---|
  | W1 | 346 | **63.0%** | +0.905 | **+313.1** | **1.66** | −11.80 | ✓ |
  | W2 | 206 | **63.6%** | +0.927 | **+190.9** | **1.61** | −20.78 | ✓ |
  | W3 | 44 | **65.9%** | +1.868 | **+82.2** | **2.16** | −8.51 | ✓ |

  三窗累计 Σ = **+586.2**（与全策略组合的 +573 相当，但胜率/PF 更高、只依赖一个技能）。
  已建预设 `preset_single_chao_gu`（formal / active / Top6 / score_line 72 / 单技能 weight 1.0）供盘前直接运行。
- **陈小群（窗口冲突，未解决）**：
  - `v27`（无额外闸门）：W2 端到端 148/64.9%/PF1.73/Σ+175 ✓，但 W1 57.3%、W3 57.1%（胜率差 3pp）。
  - `v29`（+均线支撑 + chg60<33 + pos60<80）：W1 123/**63.4%**/PF1.24 ✓、W3 16/**68.8%**/PF3.41 ✓，
    但 W2 63/60.3%/PF0.84/Σ−21 ✗。
  - 提高分数线：`v27+SL76` 让 W2 更好（144/66.7%/PF1.91/Σ+197），但对 W1/W3 的胜率无效；
    `v29+SL76` 把 W2 从 −21 抬到 −6.4（PF0.94）仍不达标——**闸门本身在 W2 砍掉了赢家**。
  - 排除法：收紧止损（sl4.5）、缩短持有（horizon3）、延后 trailing（act1.2）、亏损连败闸门（K=5/10/20）、
    剔除科创板 —— **全部无法让 W2 达标**（要么 W2 更差，要么 W1 变差）。
  - 结论：陈小群的三窗需要"W2 用松配置、W1/W3 用严配置"，属**窗口/regime 冲突**；静态规则无解。
- 方法学（本轮新增能力）：`--only-skill <sid>` 是单策略唯一可信口径；面板口径在"卡片过滤 + 强牛·高潮 top-N 截断"
  的组合下会把顺序颠倒（先过滤→后截断 vs 先截断→后过滤），本轮据此把单策略筛查全部改回端到端。
- 相关：L-129（横截面闸门族）、L-130（退役反事实）、L-125（面板 vs 端到端）。

## L-132【硬规则】命令式（无 strategy.yaml 契约）技能不得进入选股预设：研究口径会静默漏掉它

- 状态：active（已修复，已加阻断级注册表诊断 + 仓库级回归测试）。
- 背景：用户看到 `history` 页"最近三个月生产重建 Σ−33.6%"（近 90 天 Σ−66.36）。复算是真的：
  生产重建 112 笔已成交、WR 55.4%、PF 0.742。根因不是结算 bug，而是
  **`high-position-volume-stall-warning`（命令式风险预警技能，"该规避而非追涨"）被
  `manifest.meta.default_preset_groups.formal_opening` 种进了选股预设 `preset_formal_opening`**。
- 机制（两层）：
  (a) 该技能自己会**独立出票**：近 90 天里它单独背书的 24 笔 WR 37.5% / Σ−60.53，占全部亏损的 91%；
  (b) 它的分值低，混进多技能卡会压低 blended `strategy_rule`，让其他卡掉到动态降线以下，
      再被"避免零推荐"的软降线兜底放行 —— 等于给全池加了一层无意义的负向偏置。
- **为什么研究没发现**：`ProductionRuleReplayCandidateBuilder._selected_skills` 要求 `skill.contract`
  （即 strategy.yaml），**命令式技能没有契约 → 研究/重放口径静默跳过它**。同一窗口、同一政策
  （v27）、同一权重（preset）下的研究 A 臂是 **9 技能 / n=137 / WR 60.6% / PF 1.451 / Σ+116.26**，
  生产重建是 **10 技能 / WR 55.4% / PF 0.742 / Σ−66.36**。两者都"自洽"，谁也没报错。
- 修复后（生产重建重跑 66 天，cohort `recon-v1-e50c3707…`）：
  近 60 天 Σ **−33.61% → +55.77%**（WR 56.7%→65.7%），
  近 90 天 Σ **−66.36% → +65.24%**（WR 55.4%→58.9%，PF 0.742→1.321）；
  被替换掉的 57 笔从 Σ−94.89 变 Σ+36.71。
- 永久规则：**(a) 任何命令式（imperative）技能都不允许声明 `meta.default_preset_groups`——
  选股预设里的技能必须能被研究/重放口径同构复现；**
  **(b) 新增/修改技能时必须核对"研究候选集 ⊇ 生产候选集"，只能被研究口径覆盖的集合才是可验证的集合；**
  **(c) 发现"研究三窗通过但生产重建亏损"时，第一个怀疑对象是"两边跑的技能清单不一致"，
  而不是策略本身。**
- 吸收位置：`app/services/registry/diagnostics.py::imperative_strategy_declares_preset_group`（阻断级）、
  `tests/test_registry_diagnostics.py`（含真实 `skills/` 目录的仓库级不变量）、
  `docs/高位放量滞涨预警混入选股池-修复-2026-09-18.md`。
- 相关：L-127（研究必须对齐生产 output_count）、L-129（口径对齐）、L-130（退役反事实）。

## L-133【入场规则】补充成交必须由信号日策略决定，目标价未命中不能用于选择更早的开盘价

- 状态：active（已加入策略配置、策略页回测口径与三窗口研究工具）。
- 证据：最近 60 天 14 条未触发推荐中，7 条完成五个交易日观察，6 条上涨，平均参考收益 +7.90%。把全部 `target_touch` 改成下一交易日开盘，W3 的 WR、PF 与 MDD 同时变差；按卡片最高分策略选择入场时点后，`chao-gu-yang-jia-skill` 与 `daily-stock-analysis-skill` 的联合候选每窗信号数为 255/132/26。
- 三窗口：W1 354 笔、WR 63.3%、PF 1.548、Σ+271.63%、MDD -15.03%；W2 201 笔、WR 61.2%、PF 1.602、Σ+192.65%、MDD -25.43%；W3 44 笔、WR 70.5%、PF 2.959、Σ+105.08%、MDD -6.30%。三个窗口的 `evaluate_challenger()` 均返回 `PRODUCTION`。
- 生产实测（合并后重跑 67 个交易日，cohort `237e3bc0289e`）：近 60 天成交 67 → 81 笔、Σ+55.77% → +84.63%、未触发 14 → 1 笔；全窗口 129 笔、WR 62.0%、Σ+121.61。
- 永久规则：**(a) 入场方式属于策略信号，使用 `default_entry_mode` 或 setup 的 `entry_mode` 表达；(b) 研究补充入场必须经过生产 `_simulate_outcome`；(c) 全局改入场方式前必须保留策略分组，每窗候选信号数至少 20；(d) 任何回测引擎都必须读取策略声明的入场方式，否则页面收益与生产成交不一致。**
- 吸收位置：`app/engine/strategy_runner.py`、`app/engine/skill_loader.py`、`app/services/entry_policy.py`、`app/services/backtest/strategy_profile.py`、`scripts/research_entry_mode_three_window.py`、`docs/炒股养家与基础短线-开盘入场-2026-09-18.md`。
- 相关：L-009（统计门槛）、L-010（发布判定）、L-018（参数研究）、L-023（`final_score`）、L-025（仓位计算）、L-129（Top6 口径）。

## L-134【口径与目标冲突】WR 70% 需要同时处理入场质量和盈利保留，单轴改动会在 W1/W2 与 W3 之间发生冲突

- 状态：active（连续五轮 FALSIFIED；生产策略未改变）。
- 证据：当前生产成交口径为 W1 354/WR63.28%/PF1.548/Σ+271.63、W2 201/61.19%/1.602/+192.65、W3 60/66.67%/1.913/+84.90。单字段保护、双字段保护、市场日级保护、最高分策略成交方式和出场盈利保护均未让三个窗口同时达到 WR70%。
- 最接近的选择候选是全卡 `final_score >= 70`：W1 66.05%、W2 61.64%、W3 69.77%，三个窗口均获 `evaluate_challenger()` 的 `PRODUCTION`，仍未满足绝对 WR70%。最接近的出场候选是 `take_profit=1%`：W1 74.25%、W2 72.73%、W3 68.75%，W3 PF 1.187，同时三个窗口均为 `FALSIFIED`。
- 口径冲突：同一批 366/209/66 张卡和同一批日线，`outcome_simulation` 与 canonical pure execution 的已结算笔数为 354/201/60 对 365/209/62；逐笔差异为 76/47/18。主要差异包含目标价未触发在 pure execution 中成交，以及止损、trailing、到期退出收益不一致。
- 永久规则：**(a) WR70 优化前必须消除两条成交模拟的逐笔差异；同一选中集合的 n、入场状态和净收益未逐笔相同时，出场参数结果不得用于发布。 (b) `evaluate_challenger()` 通过不能代替绝对验收指标。 (c) 低止盈能提高 W1/W2 胜率，同时会压低 W3 PF；后续候选必须同时包含信号日质量和盈利保留机制。 (d) W3 未完成持有期的记录保持 provisional，不计入胜负。**
- 证据：`backend/output/wr70/baseline_engine_compare.json`、`backend/output/wr70/round5_exit_screen_current_w3.json`、`output/strategies-wr70-bottleneck-metrics-2026-09-20.json`。
- 相关：L-010（唯一发布判定）、L-021（累计收益与回撤）、L-113（出场轴冲突）、L-127（Top6）、L-133（策略声明入场方式）。

## L-135 龙虎榜历史事件必须保留原因与统计区间，股票级汇总不能证明持续持仓

- 状态：active。
- 适用：龙虎榜事件序列、席位参与研究、上榜后整理策略。
- 证据：当前 `dragon_tiger` 以股票和日期唯一保存；`state_adapters.py` 同日重复记录合并原因文字，金额保留第一条。单日榜与多日榜的金额统计范围因此无法完整恢复，表内也没有席位身份。固定整理规则三窗信号研究为 FALSIFIED，记录见 `docs/reviews/dragon-tiger-strategy-correctness-2026-09-21.md`。
- 永久规则：研究持续参与必须保留原始上榜原因、统计区间、席位身份和发布时间；多日榜与单日榜不得直接累加推断持仓。未上榜期间的持仓变化保持未知。上榜且涨停只定义候选范围，交易信号需要独立的可得证据验证。
- 吸收位置：`backend/skills/dragon-tiger-reaccumulation/SKILL.md`、`backend/scripts/research_dragon_tiger_reaccumulation.py`。
- 相关：L-014、L-078、L-133、L-134。

## L-136 运行 worker 租约只在阶段边界续期，长阶段必须自带段内超时与降级

- 状态：active。
- 适用：任何新增或耗时不确定的 pipeline 阶段，尤其含外部 LLM／网络调用的阶段。
- 证据：2026-09-21 两次 formal run（`run_20260921_030714_c569e4`、`run_20260921_033539_d3143d`）在 REVIEW 阶段连续运行 22–24 分钟，超过 `RUN_HEARTBEAT_STALE_SECONDS=1200`；阶段内不续租，lease 到期后被判定 stale 并 interrupt，两轮推荐全部作废（候选、分数、辅助数据都白跑）。
- 永久规则：**(a) 耗时不可控的阶段必须自带段内超时，并在超时时产出明确降级产物（超时=降级，不是失败）；(b) 不得依赖阶段边界续租来覆盖单阶段长尾；(c) 任何「外部依赖变慢」的新阶段上线前，先确认最坏耗时可被 lease 覆盖。**
- 吸收位置：`app/services/runs/stages/adapters.py::ProductionReviewExecutor.review_candidates`（`asyncio.wait` + 整批超时，超时条目标记 `agent_review_stage_timeout`）、`app/core/config.py::agent_review_stage_timeout_seconds`。
- 相关：L-137（LLM 复核口径）、L-010（发布判定）。

## L-137 LLM 复核不参与分数，只做标记与「生效时」的硬否决

- 状态：active。
- 适用：聚合权重、共识惩罚、风险/证据分量、LLM 提供方可用性波动。
- 证据：LLM 的 `summary`/`risks` 曾并入 signal 的 `evidence`/`risk_flags`，直接进入聚合器 risk 与 evidence 分量；`_consensus_boost` 还按 `oppose>=2`（×0.90）与 `strong_support`+`veto` 并存（×0.88）改写最终分。LLM 网关当日出现 `model_circuit_open`/`model_rate_limited`（2026-09-21 formal run：verified 4 / partial 8 / degraded 48），同一批候选的分数与出票随 LLM 可用性漂移。
- 永久规则：**(a) `skill_agent` 聚合权重恒为 0；(b) LLM 的 summary/risks 不得写入 `risk_flags`/`evidence` 等评分字段，只留在 `agent_skill_review` 供审计与展示；(c) 共识加成只基于策略规则分；(d) LLM 仅在真实生效（无 error 的真实复核结论）时保留硬否决与 `llm_reviewed` 标记，失败/降级/超时一律不影响分数与出票集合。**
- 吸收位置：`app/engine/aggregator.py::_consensus_boost`、`app/engine/agent_skill_runner.py::enrich_signal_with_agent_review` / `agent_only_signal`、`app/services/workbench_service.py::_build_recommendation_card`、`tests/test_run_workbench_golden_master.py`、`tests/test_aggregator.py`。
- 相关：L-136（长阶段超时）、L-023（`final_score` 口径）、L-133（策略声明入场方式）。

## L-138 涨停预测、成交状态与交易净收益分别验收

- 状态：active。
- 适用：首板、触板修复、涨停回调及涨停预测研究。
- 证据：`output/limit-up-benchmark-metrics-2026-09-21.json` 保留三个固定形态的逐笔预测与生产模拟结果。最近 40 日首板次日收盘涨停率 20.51%，成交净收益胜率 44.97%，PF 0.71。三个形态均未达到三窗目标。
- 永久规则：涨停预测分别报告次日触及与收盘涨停，分母保留所有次日数据可判断的信号；交易 WR 仅使用已结算成交。官方每日涨停价缺失时标签保持未知。日期窗口必须按交易日计数。信号累计回撤与单笔仓位不能标为账户净值回撤或账户资金利用率。
- 吸收位置：`backend/scripts/research_limit_up_benchmark.py`、`backend/tests/test_limit_up_benchmark.py`。
- 相关：L-021、L-134、L-135。

## L-139 龙虎榜单侧金额保持未知，排行榜重复不累计

- 状态：已验证。
- 适用：`top_inst` 席位金额标准化、同一营业部跨日参与研究。
- 证据：`docs/reviews/strategies-deep-review-2026-09-22.md`；真实披露样本与回归见 `backend/tests/test_dragon_seat_repeat.py`。
- 永久规则：`side` 表示买入额或卖出额排行榜，不能当成金额的方向标记。同一具名席位的重复榜单只计算一次；单日买卖金额分别披露时，只有两项均明确且唯一才计算净额。单侧金额未知不得填零，也不得直接使用该条 `net_buy` 声称完整净买入。无法确认身份的同名席位不用于跨日持仓推断。
- 吸收位置：`backend/scripts/research_dragon_seat_repeat.py::normalize_seats`，生产接入席位历史时必须保留相同检查。
- 相关：L-078、L-135、L-138。

## L-140 移动的工作树 HEAD 不能作为跨进程相等护栏的比对分量

- 状态：encoded（已修 + 真实故障取证 + 回归测试）
- 适用：任何「入队进程算一次指纹、执行进程重算并要求相等」的护栏，尤其是把部署身份塞进该指纹的情形
- 证据：2026-09-22 驾驶舱手动推荐三连失败（`run_20260922_051327_9df153`、`run_20260922_051409_2ac04a`、`run_20260922_051518_66ccb8`），报错「Run 已持久化的 execution_version 与当前策略/因子/执行策略身份不一致」。并排比对入队侧 `result_summary_json.execution` 与执行侧重算值：`execution_policy.fingerprint`（`0904d443…`）、`factor_plan.fingerprint`（`f6218434…`，7 个 factor）、全部策略 `effective_version` **完全一致**，唯一漂移分量是 `application.revision`（入队 `e3ba2fe` vs. 执行侧另一个 SHA）。根因是 L-068 的残留面：`resolve_application_revision` 用 `lru_cache` 在每个进程首次调用时固化 `git rev-parse HEAD` 并回写 `os.environ`，长驻 API 与长驻 Prefect worker 在不同时刻启动即 pin 不同 SHA；而该仓库每隔数小时就落一个 commit（当轮 22:57、22:58 两次提交把 HEAD 从 `e3ba2fe` 推到 `f9b26d9`），于是页面推荐结构性必挂，只有「入队与执行同进程」的脚本运行能成功，长期掩盖了问题。
- 永久规则：**活动工作树的 `git rev-parse HEAD` 不是进程所加载代码的身份**——进程启动后 HEAD 再动，运行中的代码并不改变。因此禁止把它放进任何要求跨进程相等的指纹；跨进程护栏只比对**会改变行为**的分量（request schema、execution policy、策略 descriptor、factor plan），部署 revision 作为「实际执行于哪个版本」**记录而不比对**（推荐运行已有 `recommendation_runs.code_revision` 独立承载，重复放进 hash 属冗余）。执行进程是「实际运行了什么」的权威方，identity 以执行侧为准写入；入队侧与执行侧 revision 不同时，两者都记进 audit（`execution.application` 与 `execution.enqueue_application`）而不是静默覆盖。护栏抛错必须并排打印两侧分量并点名差异分量（承接 L-068）。
- 回归验证：`backend/tests/test_execution_version.py` 新增三组——revision 中途变化被接受、policy/factor/strategy 三种行为漂移仍 fail-closed 且报错含两侧值、无可比对 audit 时退回严格 hash 比对；`test_execution_version.py`+`test_deployment_identity.py` 19 passed，prepare/stage/workbench/run_state/provenance 相关 85 passed。
- 吸收位置：`backend/app/services/workbench_service.py::_behaviour_identity`/`_behaviour_identity_differences`/`_persist_execution_version`/`_persist_run_start`；audit 经 `runs/stages/prepare.py::RunStartPort` 与 `runs/stages/adapters.py::ProductionRunStart` 透传。共享的 `ExecutionVersionInput` 哈希公式**未改动**，以免动摇 research/governance 已冻结的 claim 身份。
- 相关：L-068（同一护栏的解析器统一，只修了「解析器不同」没修「pin 时机不同 + HEAD 会动」）。

## L-141 失败写入路径本身必须不可能失败；批量 INSERT 报 1206 先查锁内存而不是缩批量

- 状态：encoded（已修 + 真实故障取证 + 回归测试 213 passed）
- 适用：任何把异常消息写进数据库列的失败处理路径；任何宽表批量 upsert 报 MySQL 1206
- 证据：2026-09-23 单策略 run 反复卡死在 market_data。真实异常被彻底吞掉，日志只剩 `DataError (1406, "Data too long for column 'progress_message'")`。截断后原始异常才现形：`OperationalError (1206, 'The total number of locks exceeds the lock table size')`，SQL 是 366KB 的 `INSERT INTO daily_kbars`。双重失败链：daily_kbars 批量 INSERT 触发 1206 → 失败处理把 366KB 的 SQLAlchemy 异常（内含完整语句+绑定参数）写进 TEXT 列（上限 65535 字节）→ DataError 1406 → 失败处理自身崩溃 → run 永久挂在 RUNNING → 20 分钟后 stale-recovery 误标 interrupted 并写「服务重启或进程退出」。该 bug 早于 execution_version 改动：8 月 `run_20260824_010037_af33c5`、以及前一天被误归因为「服务重启」的 `run_20260922_000310_92d1b9`/`000318_c59079` 都是它。把批量从 1000 降到 200 后**仍报 1206**（SQL 366KB→73KB），证明批量不是根因——`innodb_buffer_pool_size` 仅 128MiB（MySQL 默认值从未调优）才是，该库有 234 万行 daily_kbars + 4754 万行 minute_kbars。在线 `SET GLOBAL` 调到 512MiB 后 market_data 135 秒通过、universe=4949、零错误。
- 永久规则：**写 `error_message` / `progress_message` 前必须在不可变契约层统一截断**（`RunTransition.__post_init__`），一处覆盖所有调用点；失败处理绝不能因为自己的消息太长而失败。**外层 failure handler 即使不该覆写 worker-owned 行，也必须把异常 log 出来**，否则真实原因永久不可见、stale-recovery 的兜底文案会冒充根因。**批量 INSERT 报 1206 时先查 `innodb_buffer_pool_size` 等锁内存配置**：InnoDB 锁内存从 buffer pool 分配，批量已很小仍报 1206 就说明根因在资源配置而非批量大小。宽表（daily_kbars 80 列）每行取锁远多于窄表，不能与窄表共用批量常量。
- 回归验证：`test_run_state_machine_repository.py` 新增 3 组（超长消息被截断到上限且保留头部、截断值经 CAS 真实落库、短消息不被动）；相关 213 passed。
- 吸收位置：`runs/contracts.py::truncate_run_error_message`/`RUN_ERROR_MESSAGE_MAX_CHARS`/`RunTransition.__post_init__`；`workbench_service.py::_update_run_progress` 与外层 failure handler；`workbench_service.py::DAILY_BAR_UPSERT_BATCH_SIZE=200`（原共用 `UPSERT_BATCH_SIZE=1000`）；`market_pool/core.py::DAILY_KBAR_UPSERT_BATCH_SIZE=200`（StockQuote 早已分批 500，只有 DailyKbar 漏了）。buffer pool 已于 2026-09-24 持久化到 `/opt/homebrew/etc/my.cnf` 的 `[mysqld] innodb_buffer_pool_size = 512M`（此前仅为在线 `SET GLOBAL`，MySQL 一重启即回落 128MiB 使 1206 复现）；取值必须是 `innodb_buffer_pool_chunk_size` x `innodb_buffer_pool_instances` 的整数倍，本机为 128MiB x 1。

## L-142 本地派生指标的缓存列会被日常同步恒写 0，消费侧一律重算，禁止 prefer cached

- 状态：encoded（已修 + 全表取证 + 影响量化）
- 适用：所有 Tushare 不返回、由本地计算的 daily_kbars 派生指标列（upper_shadow_count20、breakout*、max_dd*、rsi_* 等）
- 证据：2026-09-23 盘后推荐出 688352（基础短线策略），用户点开记录立刻显示「原策略已失效，建议退出 / 硬风控阶段命中 / 20 日长上影次数需不高于 6（当前 10）」——同一条规则在推荐侧与复核侧结论相反。取证：688352 最近 20 根真实有 **10 根**长上影（上影/全幅≥0.45：0.500/0.451/0.498/0.579/0.736/0.507/0.548/0.805/0.688/0.596），而 `daily_kbars.upper_shadow_count20` 缓存列 20 根**全是 0**，推荐的 `feature_snapshot.upper_shadow_count20` 也记着 0，`0 <= 6` 于是通过 `prefilter.market.include`。全表扫描确认断点：2026-06-08~09-08 该列有非零值（34477 行），**2026-09-09 起每天 5500 只票 100% 为 0**；同表 max_dd20/ma5 空值 0/4949、breakout20=0 占 50% 属正常分布，只有这一列全零。0 传播链：`market_pool/core.py` upsert 用 `int(bar.get("upper_shadow_count20") or 0)`，而 Tushare 不返回该键 → `or 0` 把 backfill 真值覆盖成 0 → `_indicator` 带出 0 → `market_data.py` 的 `latest_indicator.get(...)` 拿到 **0 而非 None**，所以永不触发重算。影响量化：全市场抽样 400 只，真实 count>6 占 4.2%、>5 占 18.0%——这些候选自 09-09 起本该在 prefilter 被挡却一路放行；6 个策略（chen-xiaoqun/high-range-breakout/sector-relative/trend-pullback/chao-gu-yang-jia 的 ≤5，daily-stock-analysis 的 ≤6）的该项风控**全部静默失效**，反向 risk 规则（`>=4`/`>=5`）也从未触发。
- 永久规则：**Tushare 不提供的本地派生指标，消费侧一律从原始 OHLC 重算，禁止 `prefer cached`**——日常同步的 `or 0` 兜底会把这类列恒写 0，而 `dict.get()` 拿到 0 不是 None，任何"缺失才重算"的写法都会被静默绕过。同文件里 rsi/max_dd/breakout 都因缓存列失真改成了始终重算，新增此类字段必须跟随同一约定。**推荐侧与复核侧必须共用同一份指标口径**：同一条 `when` 表达式在两侧取到不同值，会产出"刚推荐就说失效"的自相矛盾输出，比直接报错更伤信任。缓存列即使不再被读也仍是脏数据，下一个开发者会再次 prefer cached——要么回填要么从 upsert 的 update 列表移除。
- 回归验证：修复后 market_data 与 indicator_computer 两侧口径对 688352/000510/002897/300726 全部一致（10/5/5/4），688352 现会在 prefilter 被拒，与复核结论一致；market_data/indicator/prefilter/feature/tools/equivalence 相关 301 passed。
- 吸收位置：`app/services/market_data.py`（`upper_shadow_count20` 改为无条件 `_upper_shadow_count(bars[-20:])`）、`app/agents/tools.py`（同上，Agent 侧口径对齐）。**未动**：`market_pool/core.py:1587` 写入侧仍恒写 0（属 backfill 与日常同步的职责划分，需单独决策）、历史缓存列未回填。
- 相关：L-141（同一轮排查暴露）。

## L-143 健康度必须有值分布维度：结构性检查看不见「有值但值是错的」

- 状态：encoded（已修 + 真实数据验证 + 回归测试 6 passed）
- 适用：任何「数据健康度/质量评分」体系；任何用 `IS NOT NULL` 或行数/日期滞后判定数据可用性的检查
- 证据：2026-09-23~24 体检扫出 daily_kbars **24 个列失真而 `./ashare data-health` 报满分 100**。根因是评分函数 `_compute_health_score` 的四类扣分**全是结构性的**：critical 表 `total_rows=0` 扣 20、最新日期 `lag>5` 扣 10、近 10 交易日 `gap_count*3` 最多扣 15、数据源连通 penalty。这些回答「管道有没有在跑」，回答不了「值可不可信」——一列恒零时行数完整、lag=0、覆盖 10/10，全部绿灯。唯一的列级检查 `_data_domain_coverage` 谓词是 `value_col.is_not(None)`，而失真值是**整数 0 不是 NULL**，照样通过；且 `presence_query.limit(1)` 使「全表有 1 行非 NULL」就算有数据，覆盖率从 100% 崩到 1 行也报正常。三次实测失真全部躲过它：`upper_shadow_count20` 自 09-09 起全表恒 0 达 14 天（6 个策略长上影 prefilter 静默失效、全市场 18% 本该被挡的候选混入）；`macd_dif/dea` 连续 12 个交易日全表 0.0（`macd_status` 恒为「下穿零轴」，因 `0 > 0` 为假）；`pct_chg` 在两次人工体检之间被刷成全零（`limit_up_days20` 直接数 `pct_chg>=9.8`，使 `limit-up-reseal-relay` 的 `universe.include` 硬条件全拒，而当日真实涨停 82 只）。过去 60 个交易日该表关键列有 **11 次状态切换**，健康度一次都没报过。
- 永久规则：**健康度必须同时覆盖「结构」与「值分布」两个维度**。值分布按**当日横截面**检查三件事：整列是否 NULL、**唯一值个数**（`<=1` 即恒同值，恒零是其特例）、**非零占比**。判空谓词（`IS NOT NULL` / `is None` / `is not None`）在原理上抓不到「有值但值是错的」，不能作为数据可用性的唯一依据。**每列必须显式声明期望阈值**（`min_distinct` / `min_nonzero_ratio`），否则布尔型列（`breakout20` 只有 0/1 两个唯一值）必然误报。**直接进选股决策的列必须标 critical 并按更高权重扣分**，与「消费侧已改重算、仅属脏数据」的列区分开——后者仍要可观测，因为下一个开发者可能再次 prefer cached。单一维度的 penalty 必须封顶（本实现 20），避免独吞整个评分。只查参考交易日单日（约 5 千行）而非全表，`COUNT(DISTINCT)` 成本才可接受。
- 回归验证：`backend/tests/test_data_health_value_sentinel.py` 6 passed，锁定——结构性全绿且无值分布输入时才是满分（基线，即失真能潜伏的原因）、恒零列必须扣分、critical 列按更高权重扣、penalty 封顶、布尔列期望必须放宽、`pct_chg`/`turnover_rate`/`close`/`volume`/`amount` 必须标 critical。真实数据验证：12 列受检，11 健康、抓出 `upper_shadow_count20` 恒同值，评分**由恒 100 变为 96** 且 issues 点名具体列与原因。
- 吸收位置：`app/services/data_health_service.py::_VALUE_SENTINEL_DEFS`（每列期望声明）、`_value_distribution_sentinel`（检查实现）、`_compute_health_score`（接入扣分）、`_find_issues`（critical 报 high、其余报 medium）、`diagnose_all` 结果新增 `value_distribution` 字段。
- 相关：L-142（被它抓到的第一个失真列）、L-141（同一轮排查暴露）。

## L-144 upsert 的 on_duplicate_key_update 不得包含派生列：同步会用空值刷掉补好的真值

- 状态：encoded（已修 + 受控端到端 + 真实 run 闭环验证 + 回归测试 5 passed）
- 适用：任何「权威字段与派生字段同表」的 upsert；任何有独立 backfill 作业补列的表
- 证据：2026-09-24 实测覆盖链。`daily_kbars` 的两处 upsert（`market_pool/core.py` 28 列、`workbench_service.py::_upsert_daily_bar_rows` 5 列）把派生指标全放进 `on_duplicate_key_update`，而日常同步只带回权威行情（OHLCV/pct_chg/turnover_rate），派生列当批是 `NULL`，或经写入侧 `int(bar.get(...) or 0)` 兜底成 `0`。于是**每跑一次同步/推荐就把 backfill 补好的真值刷掉一次**：Stage 4 把 09-23 的 `vol_ratio_5` 补成 4949 行非空，下一个 run 的 market_data 阶段（02:08~02:21）即清回 **0 行**，值分布哨兵（L-143）当场抓到；`pct_chg` 也是这样在两次人工体检之间被刷成全零，进而使 `limit_up_days20` 恒 0、`limit-up-reseal-relay` 的 `universe.include` 硬条件全拒。这解释了为什么「补缓存列」从来没有效果——不是补错了，是下一个 run 又刷掉了。
- 永久规则：**按列语义拆分 update 列表，不要一刀切。**（1）权威字段（每次同步都带回新值）保持直接覆盖。（2）浮点派生字段（均线、乖离、量比、回撤、RSI/MACD/KDJ/BOLL/CCI、筹码成本——0 与 NULL 都不是有效取值）用 `COALESCE(NULLIF(VALUES(col), 0), col)`：当批带真值就更新，当批无效就保留库中已有值。（3）**整型计数字段（`breakout20/60`、`upper_shadow_count20`）的 0 是合法取值**（"没有突破"/"零根长上影"），`NULLIF` 会把合法 0 误判成缺失，只能整列退出 `UPDATE` 子句、由 backfill 独占写入权（`INSERT` 新行时仍写当批值）。消费侧重算（L-142）是必要的兜底，但**不能替代写入侧止损**——否则脏数据永久存在，且下一个开发者会再次 prefer cached。
- 回归验证：`backend/tests/test_daily_kbar_upsert_preserves_derived.py` 5 passed（编译 SQL 断言 25 个浮点派生列全部带 `coalesce(nullif(...))`、3 个计数列完全不出现在 UPDATE 子句、权威列仍直接覆盖、两个集合不重叠、保护语义真值表）。受控端到端（测试 code T99999）4 场景全过：NULL 不覆盖真值、0 不覆盖真值、有效新值正常更新、行情列照常更新。**真实 run 闭环**：跑 run 前 `ma5=4949 vol_ratio_5=4949 bias_5=4934 max_dd20=4949`，market_data 执行后逐项完全一致——而修复前同样操作把 `vol_ratio_5` 刷成 0。
- 吸收位置：`app/services/market_pool/core.py::_DERIVED_NUMERIC_COLUMNS`/`_DERIVED_COUNTER_COLUMNS`/`_preserve_derived_on_duplicate`；`app/services/workbench_service.py::_upsert_daily_bar_rows`（run 的实际执行路径）。
- 相关：L-142（消费侧重算，本课是它的写入侧对偶）、L-143（哨兵抓到了这次覆盖）。

## L-145 重启 worker 会在编排器侧留下 RUNNING 僵尸，占满并发槽使全队列静默堵死

- 状态：encoded（已修 + 真实故障取证 + 回归测试 6 passed）
- 适用：任何「DB 侧有 run 状态机、编排器侧另有 flow run 状态」的双状态系统；任何用 SIGTERM/kickstart 重启 worker 的部署
- 证据：2026-09-24 用户报告「今天运行都失败了，从右上角点运行推荐进去的」。取证：`work_pools/ashare-local/queues/filter` 显示 work queue `default` 的 **`concurrency_limit = 2`**（不是 work pool 的 4），而 `flow_runs/filter` 有**正好 2 个** flow run 卡在 `RUNNING`（`quirky-cuttlefish` 06:51:20、`aggressive-sparrow` 06:55:26）——它们的子进程早已随我 `launchctl kickstart -k` 重启 worker 被 SIGTERM 杀掉。**2 个僵尸 × 并发上限 2 = 槽位永久占满**，worker `status=ONLINE`、心跳正常（07:20:35），却再也取不到任何活：`SCHEDULED` 积压 **39** 个、`CRASHED` 18 个，我 07:02:15 入队的 run（flow run `cobalt-porpoise`，预定时间早已过期）在 queued 停了 9 分钟纹丝不动。**DB 侧有 stale-run-recovery 兜底把 `RecommendationRun` 标 interrupted，编排器侧的 flow run 却无人收尾**——这个不对称是根因。手工把两个僵尸置 `CRASHED` 后，worker 45 秒内恢复取活。
- 永久规则：**双状态系统的两侧都要有回收机制。** worker 启动时必须收尾上次残留的 RUNNING flow run，否则并发槽会被永久占用；只修 DB 侧不修编排器侧，故障表现是「worker 在线、心跳正常、队列却完全不动」，极难归因。收尾的安全判据用**时间**而非「是否有 ONLINE worker」：worker 刚被 kill 时心跳可能尚未超时、Prefect 仍标它 ONLINE，按 worker 状态判断会把该收的僵尸漏掉；而本 worker 启动后自己提交的 flow run，`start_time` 必然晚于本进程启动时刻。取不到/解析不了时间时**保守跳过**——宁可漏收一个僵尸，也不能误杀活任务（本轮就真的误伤过一次：在 worker 运行中手工调用收尾函数，把正在跑的验证 run 直接 CRASHED 了）。收尾失败只记日志、绝不阻塞 worker 启动。另外记住 **work queue 的 concurrency_limit 与 work pool 的是两个独立配额**，排查堵塞要看前者。
- 回归验证：`backend/tests/test_prefect_worker_orphan_reap.py` 6 passed——残留被判僵尸、本进程启动后开始的活任务绝不收尾（回归那次真实误伤）、`...Z` 后缀与 naive 时间戳正确解析、缺失/非法时间戳保守跳过、退回 `expected_start_time`、Prefect 不可达时不抛异常。
- 吸收位置：`backend/scripts/run_prefect_worker.py::_reap_orphan_running_flow_runs`/`_started_before_this_process`/`_PROCESS_START`，在 `prefect worker start` 之前执行。
- 相关：L-141（同样是「兜底路径自身失效」的形态）。

## L-146 检测能力必须接到修复能力上；NOT NULL 列的「缺失」要在写入侧显式表达

- 状态：encoded（已修 + 单位指纹取证 + 按钮自修实证 + 回归测试 21 passed）
- 适用：任何「有健康检查/哨兵 + 有自动修复入口」的系统；任何把实时快照提升成权威历史记录的写入路径；任何 NOT NULL 数值列的 upsert
- 证据：2026-09-26 用户报告数据健康只有 87 分（100 − 12 值分布 − 1 数据域）。三列在 09-24 参考交易日恒同值全为 0：`pct_chg`、`turnover_rate`（critical 各扣 5）、`upper_shadow_count20`（扣 2）。**单位指纹定位写入者**：09-24 那 4950 行的 `close` 与 Tushare 真实收盘逐只一致（不一致 0 只），但 `volume` 恰为 Tushare `vol` 的 **100 倍**（股 vs 手，4950/4950 行），`amount` 等于 `amount_yi × 1e8`——这组单位组合只有「把实时快照提升成当日日K」的 `_backfill_latest_trade_date_bars` 会产生，Tushare 日线批量同步写的是手。该函数用 `_num(item.get("pct"))` 取涨跌幅，快照不带这个字段时得 0，随后 upsert 无条件覆盖。**按钮修不掉**：`value_distribution` 在 2510 行的 `data_gap_remediation_service.py` 里出现 **0 次**，动作计划完全由结构信号（缺失日期 + lag_days）生成，dry_run 实证只规划出补 news 一个动作，13 分扣分一分不动；`concept_member` 也不在可修复清单里。排查中连续三次假设落空（数据源侧返回值健康、缓存 bars 带真值、刷新路径 merged quote 带 `pct`），改用单位指纹与作业时间线才定位成功。
- 永久规则：**（一）哨兵报出来的每一类问题，都必须在自动修复侧有对应动作。** 只接评分路径的检测等于只会报警不会救火，用户每次都得找人手工修。新增一类检测时，同一轮就要把它接进修复计划并登记进可修复清单，否则分数永远降不回去。**（二）NOT NULL 数值列不能用 0 表达「缺失」。** 0 往往是合法值（平盘、无长上影），一旦用 0 兜底就再也分不清「真的是 0」和「这批没带回来」，覆盖时必然抹掉真值。库层无法用 NULL 表达时，就在 Python 侧判定当批是否真的带回数值，**没带回的列整列退出 `ON DUPLICATE KEY UPDATE`**，让已有真值留在库里；新插入的行没有可保留的旧值，只能落默认值并交由补数路径填真值。**（三）批级防线要成对存在。** 整批都没有非零值即判定数据源未就绪、放弃写入，这条防线此前只加在日线批量同步上，快照提升路径没有——同一类风险的所有入口都要覆盖，阈值取足够大的行数避免误杀停牌/新股的正常全零。**（四）声称「交给 backfill 独占写入」时必须确认 backfill 真的在写那一列。** L-144 把 `upper_shadow_count20` 从 update 列表摘掉交给 backfill，但 `backfill_indicators` 从来没计算过它，结果是没人算，该列 16 个交易日恒零、6 个策略的长上影上限条件静默失效。摘除写入权和建立写入权必须同时完成。
- 回归验证：`backend/tests/test_daily_bar_quote_metric_write_policy.py` 6 passed（缺失与真实 0 区分、缺失时整列退出 UPDATE、NOT NULL 仍满足、带回时仍覆盖、同批按到位情况分组、整批全零判未就绪且小批量不误杀）、`backend/tests/test_value_distribution_remediation.py` 7 passed（两域登记、失真生成动作、按手段分流、未识别列如实报出、全绿不产生动作、滞后成分可修）、`backend/tests/test_upper_shadow_count_backfill.py` 3 passed（补数写入且与消费侧同口径、0 是合法值、已有 ma 的行照样重算）、`test_data_gap_remediation_service.py` 29 passed。真实实证：修复服务自己把 `upper_shadow_count20` 修好 5004 行，健康度 97 → 99，值分布 12/12 全健康。
- 吸收位置：`backend/app/services/daily_bar_write_policy.py`（新增，统一权威行情列写入策略）、`backend/app/services/market_pool/core.py::_persist_kbars`/`_backfill_latest_trade_date_bars`、`backend/app/services/workbench_service.py::_persist_stocks_and_bars`/`_upsert_daily_bar_rows`、`backend/app/services/data_gap_remediation_service.py::_repair_value_distribution`/`_repair_quote_metric_columns`/`_build_remediation_plan`、`backend/scripts/backfill_indicators.py::backfill_date`/`backfill_history`。
- 相关：L-142（消费侧无条件重算；本条补上写入侧与修复侧）、L-143（值分布哨兵；本条把它接到修复路径）、L-144（派生列三类语义；本条修正「交给 backfill」的空头承诺）。

## L-147 判定「最新已收盘交易日」只能用交易所日历，工作日算术会落到休市日上

- 状态：encoded（已修 + 真实假扣分取证 + 按钮端到端复修实证 + 回归测试 12 passed）
- 适用：任何需要「最新交易日/已收盘交易日」的判定；任何把这个日期当作缺口基准、滞后基准或补数目标的下游；任何长驻进程持有旧代码的部署
- 证据：2026-09-28（周一）09:37 健康度从 99 掉到 78。扣分是「日K线/分钟K线/指数日线/资金流向 最近 10 个交易日缺失 1 天: 2026-09-25」加 10 个数据域最新日期未达参考交易日，共 22 分。而 Tushare SSE 日历明确 `2026-09-25 is_open=0`（当周 09-25/26/27 全休市，下一个交易日是 09-28）。根因：参考交易日取 `min(Tushare 最新交易日, latest_allowed_bar_date())`，后者是纯工作日算术——17:30 前回退到「上一个工作日」，正好落在休市的周五 09-25。`_recent_daily_trade_days` 又会把参考日强行塞进交易日窗口（本意是覆盖「参考日还没同步」），于是每张表都被判成缺这一天。按钮据此规划了 8 个动作全部打向休市日，真按下去的结果是 `partial_failed`「完成 8 项，修复缺口 0/5」——一个缺口都没修掉，纯空转。
- 永久规则：**（一）交易日必须问交易所日历，不能用工作日算术推。** 周末回退只能排除周六周日，排不掉法定休市的工作日；把「盘后数据就绪」的时间判据（17:30）与「哪天是交易日」的日历判据混在一个函数里，就必然产出休市日。正确形状是两者分离后求交：时间判据给上界，日历给候选集，取不晚于上界的最后一个真实交易日。**（二）基准日错了，下游全错且全是假阳性。** 参考日是缺口检测、滞后判定、补数目标的共同输入，它一错，健康度会同时报出一批「缺失」和「滞后」，而数据其实完好；修复按钮还会照着假缺口去拉数据、拿回空结果。判断健康度异常时先核对参考日本身。**（三）整批全零的防线要按占比判定，不能要求一个非零都没有。** 真实交易日绝大多数股票涨跌幅非零，盘前/休市快照往往整片为 0；若少数股票残留非零值，「一个非零都没有」的判据就会漏过去，把其余多数刷成 0（本轮 09-24 全部 4951 行被写成精确 0.0 即此形态的极端版本）。**（四）改完代码不等于生效：长驻进程持有导入时的旧模块。** 本轮 API 与 Prefect worker 分别停留在不同的旧版本上——worker 仍跑修复前的写入路径，00:03 两个 `trade_date=2026-09-24` 的 run 把两列又刷成 0；API 停在参考日修复之前，按钮仍把动作打向 09-25。验证修复是否真的生效，必须确认执行它的进程重新加载过代码。
- 回归验证：`backend/tests/test_reference_trade_date_calendar.py` 5 passed（休市工作日绝不作参考日、17:30 前不取当日、过了就绪窗口当日成为参考日、日历不可用回退本地推断、就绪判据早于全部候选时不硬取最早那天）、`backend/tests/test_daily_bar_quote_metric_write_policy.py` 7 passed（新增「多数为零少数残留非零仍判未就绪」）。真实实证：参考日修正后健康度 78 → 89（22 分假扣分消失），随后修复服务自己把 `pct_chg`/`turnover_rate` 修回 4951 行，89 → 99，值分布 12/12 全健康。
- 吸收位置：`backend/app/services/tushare_client.py::fetch_recent_trade_dates`（新增，只返回 is_open=1）、`backend/app/services/data_health_service.py::_get_tushare_recent_trade_dates`/`_resolve_reference_trade_date`（用日历求交，删掉只取单个最新日的 `_get_tushare_latest_trade_date`）、`backend/app/services/daily_bar_write_policy.py::quote_metrics_look_unready`/`UNREADY_NONZERO_RATIO`。
- 相关：L-146（同一批写入侧防线；本条把它的判据从「全零」收紧为「占比」）、L-140（长驻进程 pin 旧 git revision 的同型问题：进程启动后代码再变，运行中的仍是旧的）。

## L-148 按全日样本校准的门槛不能拿盘中累计值去比；单次实时采样不得写成不可逆终态

- 状态：encoded（已修 + 真实数据取证 + 盘中端到端实证 + 回归测试 30 passed）
- 适用：任何「盘后按全日数据生成、盘中按实时数据复核」的判定；任何把实时采样结论写成推荐终态的持久化；任何会把样本排除出胜负统计的开关
- 证据：2026-09-28 用户报告「最近推荐出的票几乎全部都在购买前取消了」。库里 10 条执行确认状态，取消原因 100% 是同一条规则 `成交额（亿元）需不低于 3`，取消时刻全部落在开盘后 3 到 14 分钟：300666 记录 2.601 亿、002138 记录 1.857 亿、688352 记录 0.174 亿。而这些票近 6 个交易日的**全日**成交额是 13.0~35.7 亿、12.4~20.1 亿、3.6~4.9 亿——没有一只接近不合格。10:10 再拉一次 rt_k，300666 已累计到 3.3976 亿越过门槛，取消与否只取决于打开页面的钟点。`amount_yi >= 3`（8 个策略的 `universe.include` 首条）、`turnover_rate >= 1.5` 都是按全日样本回测校准的，盘中同名字段含义变成「开盘至当前的累计值」。镜像面同时坏掉：上界 `amount_yi < 15`、`turnover_rate <= 10` 在盘中恒为真，「大成交额排除」「换手过高排除」这两道风控在确认路径上等于不存在。第二层缺陷是 `cancelled_before_entry` 一旦置位就没有任何路径清回，且 `ever_buy_confirmed` 被它反向屏蔽——002897 意华股份 `latest_decision_code` 已经是 `wait`，`cancelled_before_entry` 仍然是 True。触发者是历史页列表加载时的 `useEffect` 自动请求：扫一眼页面这个只读动作就把当天所有待确认推荐永久判死。第三层后果是 `apply_execution_outcome_policy` 见到该标记就把收益归零、`outcome_label` 改 `missed`、从 success/failed 中排除——09-23 那 4 条清理后重算，全部恢复成真实入场且执行收益 -1.95% ~ -3.04%，即**亏损样本被这条链悄悄拿走，胜率被虚高**。
- 永久规则：**（一）字段同名不等于口径相同。** 同一个标识符在盘后是全日值、在盘中是累计值，用一套阈值去比两种口径必然系统性错判。判据不需要经验系数：当日累计量只增不减，所以下限类门槛「已达标」可以确定通过、「未达标」只能判**不可判定**；上限类门槛「已越界」可以确定否决、「未越界」不可判定。判定手段不要解析表达式语义——把累计类字段按「全日值 ∈ [当前值, +∞)」取探测点重算，存在可达的通过取值就是不可判定，这样复合条件与 `include_any` 的或表达式自动正确（累计量再大也过不了的条件仍判真失败）。**（二）不可判定必须有自己的第三种结论，不能就近归入否决。** 把 `prefilter` 不通过和硬风控否决混成同一个 `cancel`，可恢复的等待就被写成不可恢复的取消；只有硬风控命中和跌破止损这类**价格与结构的既成事实**配得上取消。证据字段同样要三态：那条条件本身不可判定时标 `pending`，即使这笔推荐因别的原因被取消也不能标 `failed`。**（三）同一门槛不能有第二条判定通道。** `run_strategy` 自己也查 universe，未通过时短路成 `hard_veto` + 「不在策略股票池」，粗粒度否决绕过了刚建立的判据、还丢掉了是哪条规则不过。确认路径必须让 `explain_skill_prefilter` 独占门槛判定（它的规则集是 `_passes_universe` 的超集），`run_strategy` 传 `skip_universe_check=True` 只负责 phases/setups。加判据时要先找齐所有判定入口。**（四）单次实时采样不得成为不可逆终态，尤其当它的触发者是只读动作。** 页面加载这类幂等的查看行为绝不能产生不可恢复的状态写入；同一入场窗口内后续恢复成买入是有效事实，必须能覆盖先前的取消。**（五）任何「把样本排除出胜负统计」的开关都要按最高标准审查。** 它不报错、不告警，只是让统计变好看——本轮就是 4 笔实亏被静默拿走。
- 回归验证：`backend/tests/test_intraday_cumulative_policy.py` 9 passed（下限未达标不可判定、上限已越界可判定、区间仍可达不可判定、非累计字段照常失败、复合条件被非累计项卡住时真失败、换手率同属累计、`include_any` 或表达式可达、已通过与空输入从不不可判定）、`test_execution_confirmation.py` 24 passed（新增 7：累计量不足判 wait 且理由点名具体门槛、不足时即使规则分够也不放行买入、上限已越界仍取消、结构条件失败仍取消、跌破止损优先、硬风控优先、证据三态）、`test_execution_confirmation_signal.py` 4 passed（universe 不足报成 prefilter 而非 hard_veto 并带出规则与观测值、phases 硬否决仍生效、universe.exclude 带出自己的规则、合格票正常出信号）、`test_execution_confirmation_state.py` 6 passed（新增：取消后恢复买入使 `ever_buy_confirmed` 置位、取消事实仍留档）。**盘中真实端到端**：09-24 那批推荐在 09-28 正好 age=1、窗口开着，走完整批量确认（rt_k + 真实策略契约 + 决策状态机），从「6 条全部因成交额门槛取消」变成 3 条继续观察 + 3 条真实取消（2 条硬风控阶段命中、1 条跌破止损）。真实市场快照对照：4452 只盘中累计成交额 <3 亿的股票里，卡在累计量上的判 wait，同时命中硬风控的仍判 cancel；499 只累计量充足的里 395 只因长上影/价格位置等结构条件照常取消。
- 吸收位置：`backend/app/services/intraday_cumulative_policy.py`（新增，`SESSION_CUMULATIVE_FIELDS` 与 `intraday_cumulative_inconclusive`）、`backend/app/services/execution_confirmation.py::_derive_decisions`/`_condition_summary`/`_build_signal_for_strategy`、`backend/app/services/execution_confirmation_state.py::persist_execution_assessments`、`frontend-v2/src/pages/HistoryPage.tsx::executionConditionPrefix`。
- 相关：L-142（同型的「同名字段两种口径」：缓存列的 0 与真实 0 不可区分）、L-147（长驻进程持有旧代码，验证修复必须确认执行它的进程重新加载过；本轮 API 进程即在中途用旧代码写回过取消状态）。

## L-149 上游退役端点要按官方能力清单定性，不能靠猜路径；能力没了就如实声明，别反复重试 404

- 状态：encoded（已修 + 官方契约取证 + 孤儿清理）
- 适用：任何第三方数据源的版本升级；任何「面板报连接失败但对照能力却正常」的排查；任何适配器声称提供某能力却只能恒失败的情形
- 证据：2026-09-28 用户报告 xiaoshi 数据源面板持续报 `XiaoshiError: Xiaoshi HTTP 404: /api/v3/data/kline/000001`，而同一次探针里 manifest 与 quote 都成功（quote 实测取到 000001 价格 11.39）。我试了 8 种路径与代码格式变体（`kline/000001`、`kline/000001.SZ`、`kline/sz000001`、查询参数式 `kline?code=`、`klines/`、`daily/`、`period=1d`、裸 limit）全部 404，随即停止猜测改读官方声明：`/api/v3/capabilities` 列出的 36 个 operation 里**整个 `/api/v3/data/*` 族已不存在**，且没有任何按单只标的取日线的端点；`/api/v3/meta/data-contract` 进一步给出决定性两条——`history` 段只提供 `manifest` + `download-session`（R2 预签名，校验 size/sha256），`stability.legacy_aliases = false` 明确不保留旧别名。`/api/v3/market/capabilities` 只覆盖实时盘口（`live_index_symbols`、`derived_fields` 含 change_pct、缓存 30 秒），不含历史。结论：日线能力在 v3 被改成批量交付，逐股票端点是被移除而非改名。
- 永久规则：**（一）猜第三方路径最多两次，之后必须读它自己的能力清单/契约端点。** 变体穷举在「端点被移除」这种情形下永远不会收敛，而 manifest 往往直接指向 `capabilities_url` / `data-contract`，一次请求就能定性。`legacy_aliases: false` 这类声明是判定「改名还是移除」的硬证据。**（二）适配器不得声称自己没有的能力。** 能力确实消失时，正确形状是返回明确的 `SOURCE_UNAVAILABLE` 并在消息里写清替代路径，让调用方顺着回落链走；继续对每个标的重试一个不存在的地址，只会把「能力已迁移」持续误报成「连接失败」，噪声掩盖真实故障。**（三）健康探针要探这个源真正提供的能力。** 探一个已退役的端点等于给面板制造永久红灯。**（四）不要为了消红灯顺手建一整条新数据通路。** 本轮没有实现 history 批量下载入库（下 parquet + sha256 校验 + 入库是独立工程），因为该源已停用、生产日线全走 Tushare；如实表达边界并留下替代路径的指引，等真要用时再建。
- 回归验证：既有 `tests/test_data_source_management.py` 与 `tests/test_market_source_architecture.py`（适配器仍只暴露 `fetch_quotes` / `fetch_daily_bars` 两个契约方法，能力缺口由返回值表达而非删方法）。清理了本次改动造成的孤儿：适配器内只服务于退役端点的 `_bars()` 解析函数、`kline_limit` 构造参数及 `market_data._fetch_xiaoshi_kline` 的对应传参；`_date_only` / `_number` 仍被 `fetch_quotes` 使用故保留。
- 吸收位置：`backend/app/services/xiaoshi_client.py`（`get_kline` 换成 `get_history_manifest` / `create_history_download_session`）、`backend/app/services/xiaoshi_market_source.py::fetch_daily_bars`、`backend/app/services/data_sources.py::_probe_xiaoshi_source`（探历史清单，错误前缀 `kline:` 改 `history:`）。
- 相关：L-138/L-139 一类的「数据源访问权限而非数据质量」判别；本条补上「端点存在性」这一层：权限问题会返回 4xx/5xx 业务错误，端点移除返回 404 且换任何参数都不变。
