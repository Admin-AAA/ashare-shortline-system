# 项目教训注册表

本文件是项目中可跨轮复用、会改变后续决策的教训唯一事实源。单次运行状态、临时样本数字和普通实验结果写入结构化运行记录，不写入本文件。

## 状态含义

- `active`：仍需在研究或实现时显式约束。
- `encoded`：已有代码、测试、护栏或操作手册承载；本条保留上位原则。
- `superseded`：已被更准确的教训替代，保留用于历史追溯。

## 迁移说明

本文件由项目外的历史 self-improve 教训快照迁入。历史快照有重复的 `L-045`～`L-050` 编号；迁移时按出现顺序重新分配为唯一的 `L-001`～`L-057`，每条保留“原始编号”字段。后续只能使用本文件的 canonical 编号，新条目追加到文件末尾。

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

## L-020 profit_floor_ratio 网格在 path A 上已穷举，0.50 保持最优

- 原始编号：L-020

- 状态：active
- 适用：trailing/profit_floor 相关参数网格的后续轮次选题
- 证据：PATH-A 2025_2026 全历史（n=3611，381 交易日）扫 trailing_profit_lock_ratio：0.30 与 0.70 均 FALSIFIED（0.90 早已在 [[trailing-callback-falsified-2026-07]]/L-016 串行重放否证）。0.30 累计净收益 +17pp 但 WR -0.33pp 与 MDD -0.37pp 双退化，违反「至多一项辅助退化」；0.70 累计净收益 -2.79pp 超 1pp cap 且 MDD -3.08pp 退化。两候选 paired bootstrap CI 均含 0。regime 指纹：两候选在强势/弱势周期均微改善，但震荡周期均恶化。
- 永久规则：profit_floor_ratio ∈ {0.30, 0.50, 0.70, 0.90} 已全部检验，**0.50 是当前 champion 且无已证更优点**。后续不再对该参数扫网格；如需改进出场侧，转向 stop_loss 宽度 / take_profit / horizon 等未穷举维度。
- 发布判定启示：单指标大幅改善（cum +17pp）不足以发布——只要辅助三项中有两项退化（c030 的 WR+MDD）即违反「至多一项辅助退化」门禁。大额累计收益改善若伴随胜率与回撤双退化，通常是改变了收益分布形状而非提升可执行质量。
- 吸收位置：主提示词「生产发布条件」的辅助退化门禁。

## L-021 单指标大幅改善≠可发布：辅助双退化门禁实例

- 原始编号：L-021

- 状态：active
- 适用：任何候选发布判定，尤其网格扫描中「累计收益变化显著」的候选
- 证据：profit_floor_ratio 0.30 相对 0.50，累计净收益 +17.00pp（从 -28.31% 到 -11.31%），幅度远超任何容差，但 WR -0.33pp 与 MDD -0.37pp 双退化，evaluate_challenger 判定 FALSIFIED。累计收益改善来自等权日组合的链式累乘——一个极端正日（或日期分布差异）即可放大累计值，不反映单笔质量。
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

## L-026 stop_loss 宽度在 path A 全历史上已穷举，-4.5% 保持最优

- 原始编号：L-026

- 状态：active
- 适用：止损宽度相关参数的后续轮次选题
- 证据：PATH-A 2025_2026 全历史（n=3611，381 交易日）扫 stop_loss_pct ∈ {-3.5, -6.5}（champion -4.5）：-3.5 收紧全面退化（WR -4.01pp、avg -0.065pp、PF -0.034，辅助两项退化）；-6.5 放宽 avg +0.137pp、WR +5.51pp、PF +0.063 改善但 cum -17.58pp 超 1pp cap 且 MDD -22.07pp 显著退化（等权日组合 MDD -85% vs -63%）。regime 指纹（-6.5）：弱势/强势 avg 改善但震荡恶化，组合回撤灾难性放大。与 L-011（跳空主导损失，止损距离几乎无助）一致。
- 永久规则：stop_loss ∈ {-3.5, -4.5, -6.5} 已全部检验，**-4.5 是当前 champion 且无已证更优点**。后续不再对止损宽度扫网格。注意「avg/WR/PF 改善但 cum/MDD 恶化」模式：更宽止损摊薄单笔均值但放大极端亏损尾部——avg 层面改善不构成可执行质量提升，必须看组合资金曲线 MDD。
- 与 L-021 交叉：单指标改善 + 组合回撤退化 = 收益分布形状改变，非质量提升。
- 吸收位置：主提示词「生产发布条件」+ L-020 的未穷举维度清单（stop_loss 已排除，剩 take_profit）。

## L-027 score_line 网格在 path A 上已穷举，72 保持最优

- 原始编号：L-027

- 状态：active
- 适用：score_line/分数线相关参数的后续轮次选题
- 证据：path A 全历史 2025_2026（n=3611）扫 score_line 72→76（提高门槛）：avg -0.030pp、cum -2.96pp、WR -0.46pp、PF -0.012 全面退化，砍 342 笔（-9.5% 覆盖率）但平均收益反降——被砍交易非系统性亏损。regime 指纹：弱势微改善（+0.048pp）但**强势灾难退化**（avg -0.156→-0.483，砍 35% 强势交易，提高门槛在强势周期砍掉追高动能）。结合此前 72→68（si20260731-003 零效应）、Spearman rho=-0.13（si20260731-164955 无排序力），**score_line 高低两侧均 FALSIFIED，72 是 path A 最优点**。
- 永久规则：score_line ∈ {68, 72, 76} 已全部检验，**72 是当前 champion 且无已证更优点**。后续不再对该参数扫网格。特别注意「提高 score_line 在强势周期灾难退化」模式——追高动能的强势交易分数高，提高门槛系统性误杀。
- 吸收位置：主提示词「生产发布条件」+ 网格去重护栏。

## L-028 方向池去重必须用主题词匹配扫描键，非参数名子串

- 原始编号：L-028

- 状态：encoded（已修 + 回归测试 + 合并 main）
- 适用：方向池分配、网格去重、已 FALSIFIED 方向归档
- 证据：`_list_unscanned_grid_params` 用参数列表名（`score_line_threshold`、`stop_loss`）做子串匹配 scanned_keys，但历史扫描键用主题词命名（`score_line_on_2025_2026`、`stop_loss_on_2025_2026`）。`"score_line_threshold" in "score_line_on_2025_2026"` → False，导致已 FALSIFIED 的 score_line 网格被方向池反复分配（L-027 发布后，下轮又 claim 到 grid_scan:score_line_threshold）。同理 take_profit/trailing_activate/trailing_callback/trailing_min_lock/strategy_filter 全部无匹配，会被重复扫描。
- 永久规则：scanned_grids 记录键必须用**主题词**命名（score_line_on_...、stop_loss_on_...、horizon_on_...），`_GRID_PARAM_TOPICS` 映射参数名→主题词列表，任一主题词命中扫描键即视为已扫。发布 FINDING 时检查记录键是否会被方向池误判为未扫描。
- 修复：`_GRID_PARAM_TOPICS` + 回归测试 `test_claim_does_not_reallocate_scanned_grid_direction`。修复后剩余未扫描参数：take_profit / trailing_activate / trailing_callback / trailing_min_lock / strategy_filter。
- 吸收位置：主提示词「网格参数扫描的去重与防过拟合护栏」+ OPERATIONS.md。

## L-029 take_profit 网格在 path A 上已穷举，6.0% 保持最优

- 原始编号：L-029

- 状态：active
- 适用：止盈宽度相关参数的后续轮次选题
- 证据：PATH-A 2025_2026 全历史（n=3611，381 交易日）扫 take_profit_pct ∈ {7.0, 8.0}（champion 6.0）：放宽止盈 avg +0.017/+0.020pp、cum +17.36/+19.59pp 改善但 WR -0.27/-0.52pp 与 MDD -0.77/-3.49pp 双退化（违反「至多一项辅助退化」）。regime 指纹：tp7 三 regime 全微改善（强势 +0.041 最大）、tp8 强势 +0.059 但震荡 -0.026 退化。结合此前缩窄 4/5% FALSIFIED（si20260731-010 两周窗口），**take_profit ∈ {4,5,6,7,8} 已全部检验，6.0 是 path A 最优点**。
- 永久规则：take_profit 网格高低两侧均证伪，**6.0 保持最优**。后续不再对该参数扫网格。注意「放宽止盈在强势周期让利润跑远（cum 大幅改善）但整体 WR/MDD 双退化」模式——cum 改善由强势周期少数大赢单驱动，不代表可执行质量提升。
- 与 L-021/L-026 交叉：cum 大幅改善 + WR/MDD 双退化 = 收益分布形状改变，非质量提升。
- 吸收位置：主提示词「生产发布条件」。

## L-030 peak_callback trailing 相对生产 profit_floor 不发布（策略切换非调参）

- 原始编号：L-030

- 状态：active
- 适用：trailing 家族后续选题；path A 现在支持 trailing_callback 参数化（合并 main 6e14812）
- 证据：path A 全历史 2025_2026（n=3611）扫 peak_callback：cb1.0 通过 evaluate_challenger（avg +0.023pp、cum +1.36pp、WR +0.28pp，仅 MDD -2.89pp 退化）但判定 FINDING 不发布——(1) 策略切换（生产 profit_floor → legacy peak_callback）非参数调优；(2) avg 改善 +0.023pp 远小于 MDD 退化 -2.89pp（125 倍）；(3) trailing_stop_gap 从 champion 17.3%→26.3% 暴增（更宽回调让更多亏损以跳空放大）；(4) callback 家族已在 [[trailing-callback-falsified-2026-07]] 反事实否证。cb2.0 FALSIFIED（WR -1.58pp）。regime：cb1.0 弱势+0.043/强势+0.034/震荡-0.021。
- 永久规则：**候选跨 trailing 策略家族（profit_floor↔peak_callback）时，即使通过 evaluate_challenger 也判 FINDING 不发布**——策略切换改变退出语义，avg 微改善 + MDD 显著退化 + 出场分布剧变（gap 暴增）= 亏损尾部放大，非质量提升。生产 profit_floor 0.50 保持最优。
- 测量工具：`_trailing_stop_return`/`_resolve_exit_causal` 支持 `trailing_callback` 参数化（向后兼容默认全局常量），path A 可扫 callback 网格。与并行研究员 `activate_pct` 参数共存（纯机械合并）。
- 吸收位置：主提示词「串行 champion 规则」+「生产发布条件」。

## L-031 trailing_min_lock 网格已穷举，0.3 保持最优

- 原始编号：L-031

- 状态：active
- 适用：trailing 利润地板最低锁定参数后续选题
- 证据：path A 全历史 2025_2026（n=3611）扫 trailing_min_lock：ml0.8 五项完全无变化（0.8 从未成为 max(0.8, 浮盈×0.5) 的约束，因浮盈>1.6% 时 0.5×浮盈>0.8）；ml1.5 通过 evaluate_challenger（五项全改善：avg +0.0015pp、cum +0.083pp、WR +0.06pp、PF +0.0006、MDD +0.67pp）但改善极其微小——avg/PF 都在容差 0.01 边缘，bootstrap CI=[-0.008, +0.009] 完全含 0，regime 改善集中在强势周期（+0.006pp）其他周期持平。
- 永久规则：min_lock ∈ {0.3, 0.8, 1.5} 已检验，**0.3 保持最优**（ml1.5 改善远小于容差，判 FINDING 不发布）。注意 min_lock 的「约束盲区」：只有当 浮盈×profit_lock_ratio < min_lock 时参数才生效，而 profit_lock_ratio=0.5 时浮盈需 <2×min_lock，绝大多数交易浮盈超过该值，min_lock 基本不生效。
- 与 L-024 交叉：改善幅度远小于容差 + bootstrap 含 0 = 判 FINDING 而非机械 PRODUCTION。
- 吸收位置：主提示词「生产发布条件」。

## L-032 因子方向去重必须匹配 new_strategy:<factor> 前缀，非 "factor" 子串

- 原始编号：L-032

- 状态：encoded（已修 + 回归测试 + 合并 main）
- 适用：方向池分配、已否证因子归档
- 证据：`_list_unscanned_factor_directions` 的 falsified 判断用 `"factor" in d or "因子" in d` 子串匹配 archived，但 archived 条目是 `new_strategy:capital_flow_momentum:做多资金流入被否证(787笔已结算)...`（含 momentum 不含 factor/因子），不被匹配 → 已否证的资金流因子被方向池反复分配（L-031 后 claim 又拿到它）。与 L-028 同类（参数名/记录键命名不一致导致去重失效）。
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

## L-034 gap_fill（跳空缺口回补）方向已否证，工具已合并

- 原始编号：L-034

- 状态：active
- 适用：缺口/跳空形态相关的新策略选题
- 证据：gap_fill 假设（信号日向上跳空未回补缺口→上方套牢盘压力→后续弱）：path A 2025_2026（n=3553）加 `max_signal_gap_pct=3` 剔除跳空高开≥3% 信号：avg +0.007pp、cum +3.90pp、WR +0.11pp、PF +0.003 改善但幅度在容差边缘，砍 37 笔（-1.0% 覆盖率），MDD -0.16pp 微退化。regime 指纹：弱势+0.010/震荡+0.043 改善，**强势 -0.044 退化**（砍 34 笔强势——强势周期跳空高开是追高动能的正常特征，剔除误伤）。bootstrap CI[-0.010,+0.051] 含 0。判定 FINDING 不发布。
- 永久规则：gap_fill 方向已归档（改善微弱+强势退化+CI含0）。与 entry_gap_filter（入场跳空 T+1）不同机制但结论一致——跳空相关过滤不产生可发布改善。**工具已合并**：path A 支持 `SignalRecord.signal_gap_pct` + `max_signal_gap_pct` 过滤（62e06b6），后续无需重复造。
- 与 L-024 交叉：改善微弱+单 regime 退化=判 FINDING。
- 吸收位置：主提示词「新策略研究」。

## L-035 sector_rotation_momentum（板块轮动动能）已被强力否证

- 原始编号：L-035

- 状态：active
- 适用：板块/行业轮动类新策略选题
- 证据：板块动能假设（强势板块中个股更易延续上涨）：path A 2025_2026（n=3553）加 `min_industry_momentum=0.45` 剔除所属板块当日上涨比例<0.45 的信号：**全面退化** avg -0.139pp、cum -56.76pp、WR -1.55pp、PF -0.061、MDD -35.25pp，砍 950 笔（-26.7% 覆盖率），bootstrap CI[-0.666, -0.001] **显著为负**。被砍的弱板块推荐含大量正收益低吸机会——弱势板块反而可能是低吸/超跌机会（与「追高动能策略在弱势周期恶化」教训一致）。
- 永久规则：**板块动能/追高类过滤在 path A 上被强力否证**（CI 显著为负），不要再测板块轮动动能过滤。弱板块≠该回避——它可能是低吸机会。**工具已合并**：path A 支持 `industry_momentum` 特征 + `min_industry_momentum` 过滤（1b308eb），后续无需重复造。
- 吸收位置：主提示词「新策略研究」。

## L-036 volume_price_divergence（量价背离）已否证

- 原始编号：L-036

- 状态：active
- 适用：量价关系类新策略选题
- 证据：量价背离假设（价涨量缩→动能衰竭→后续弱）：path A 2025_2026（n=3553）加 `exclude_volume_price_divergence` 剔除「当日上涨且 vol5_20<0.8」信号：退化 avg -0.001pp、cum -12.43pp、WR -0.04pp、MDD -5.82pp，砍 33 笔（-0.9%）。bootstrap CI[-0.120,+0.104] 含 0。被砍的价涨量缩交易含正收益——**A股短线量缩上涨常是主力控盘/惜售特征，非动能衰竭**。
- 永久规则：**量价背离（价涨量缩）过滤方向已否证**，不要再测。价涨量缩在 A 股短线是强势控盘信号而非衰竭信号（与教科书背离理论相反）。**工具已合并**：path A 支持 `vol5_20`/`day_pct_chg` 特征 + `exclude_volume_price_divergence` 过滤（d34e0d1）。
- 吸收位置：主提示词「新策略研究」。

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
- 与 L-021/L-036 交叉：单日极端值放大 avg 是常见陷阱；本课是 champion 层面（非候选）的同类风险。
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
- 证据：clean>=72 20cm failed n=30 归因——stop_loss 家族(hit+gap+both) 12 笔 -61.5% 占总亏损 78%；最大簇=弱势·活跃 stop_loss_hit 4 笔 -22.8%；次之=震荡·高潮 stop_loss -26.5%；跳空(stop_loss_gap avg -6.59%)主导深亏。时间上 07-07~09 三天集中亏损 -90.7%（占 78%）。与 L-011 一致（跳空主导损失、止损距离无法修复）；止损宽度已 FALSIFIED（L-026）。
- 永久规则：**止损侧已穷举（L-020/L-026）无可解**；亏损归因指向入场侧——弱势·活跃 + 震荡·高潮的止损重灾区是追高结构（但追高/regime 闸门已 archived）。当前最活跃风险信号是弱势近期衰减（L-040），不是止损参数。**改止损/出场前先确认归因**（本课是归因证据）。
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

## L-055 margin_detail 数据质量无护栏，07-16 起污染 2 周未发现

- 原始编号：L-050

- 状态：encoded（已修 + 合并 main 1bd2293）
- 适用：辅助因子数据质量、self_check 护栏
- 证据：margin_detail 本地表 07-16 起 99.5% 股票 rzye 从 6.4亿 骤降到 <100（7 个量级单位错），仅 0.5% 股票保留正常量级；生产推荐 margin 因子 07-16 后 92% 变 0。Tushare API 返回正常（rzye 15亿级）、state_adapters /1e8 路径正确，问题在 07-17 17:20 一次异常写入（来源不明）。capital_flow 已有 max_abs 质量护栏（L-014 后加），margin_detail 没有，污染持续 2 周未被 self_check 检测。
- 永久规则：**辅助因子表必须加数据质量护栏**（类似 capital_flow 的 max_abs / margin 的近14天异常占比）。用「近窗口异常占比」而非全表 max——少数正常值（0.5%）会掩盖大面积污染（99.5%）。margin 因子当前无策略使用（未进 evaluator 白名单），但污染会影响因子研究；新因子研究前先查数据质量。
- 与 L-014 交叉：capital_flow 曾因单位错漏检，本课是 margin 同类问题——同一类"单位/缩放错误"漏检模式。
- 吸收位置：主提示词「工程稳定性」+ self_check 注释。

## L-056 margin_detail 单位不一致 + 护栏方向误判（L-055 修正）

- 原始编号：L-051

- 状态：encoded（已修 + 合并 main 887d701）
- 适用：辅助因子单位、self_check 护栏方向
- 证据：margin_detail 07-16 前 17.9 万行存「元」（rzye avg 6.4e8 应为 6.4 亿元），07-16 起存「亿元」（正确）。源头是 backfill_margin(backfill_stale_domains.py) 写原始值未 /1e8，与 fetch_margin_detail_batch/state_adapters 的亿元契约不符。**上轮 L-055 护栏方向判断反了**：把 07-16 后正常「亿元」数据(rzye<100)当异常告警，实际 07-16 前「元」数据才是真异常。
- 永久规则：**数据单位修复前必须先确认契约方向**（feature_builder 注释「融资余额(亿)」即亿元契约），不能凭「数值小就是异常」武断判断——`rzye<100` 可能是正常亿元量级（37.8 亿对应值 37.8），`rzye>1e8` 才是元单位异常。加护栏前用对照日期验证方向（正常日 vs 污染日）。
- 与 L-055 交叉：L-055 是「无护栏漏检」，本课是「有护栏但方向错」——同 bug 的两面。
- 吸收位置：主提示词「工程稳定性」+ self_check 注释。

## L-057 后端已算的 quality_scorecard 必须前端可见（驾驶舱系统质量徽章）

- 原始编号：L-050

- 状态：active（FINDING，已合并 main f30fb68）
- 适用：前端驾驶舱、后端已计算但前端未展示的数据字段、系统质量可观测性
- 证据：overview 已返回 quality_scorecard（数据/验证/执行/研究四维加权 + overall 评分 + highlights）但前端全站未使用（仅 types 定义），用户看不到「最近推荐准确率仅 43.8%」「严格准确率闸门 29 条信号失效」「最近最大回撤 -48.91%」等验证维度信息，无法判断系统推荐可信度。同批未用字段：validation（quality_gate 详情）、task_health（任务健康）。
- 永久规则：**后端已计算好的系统质量评分必须暴露给用户**——quality_scorecard 的 overall_score/status/highlights 是「系统是否可信」的一眼判断，前端不能只定义 types 不用。驾驶舱 StatusBar 已加「系统质量」徽章（复用 quality_scorecard，点击进 DataHealthPage）。发现「后端字段已返回但前端全站未用」时，先评估该字段是否有用户可观测价值，有则接线。
- 与 L-047/L-048/L-051/L-053/L-054 交叉：驾驶舱 run 展示层六个 UX 修复（L-047/L-048/L-051/L-053/L-054 run 状态）+ 本课系统质量可观测性（L-057）——驾驶舱「查看/解释」缺口第七个闭环。
- 回归验证：TSC exit 0、36 前端测试全过、徽章逻辑 4 种状态全正确。
- 吸收位置：主提示词「前端体验」+ OPERATIONS.md。
