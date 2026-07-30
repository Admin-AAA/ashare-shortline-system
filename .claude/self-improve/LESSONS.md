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
- 永久规则：照常报告跨交易日稳定性、特征覆盖率、基础相关绝对值和 regime 聚集；这些数字不得形成额外发布门槛。发布统一服从 L-010 的整体改善与串行 champion 规则。
- 自动护栏：`scripts/feature_signal_guard.py`、alpha decay 诊断及相关测试。
- 吸收位置：主提示词“选择唯一动作”。

## L-005 回测数据深度和幸存者偏差必须显式披露

- 状态：active
- 适用：历史回测晋级
- 证据：早期 `daily_kbars` 仅覆盖少量股票，且当前 universe 缺少部分历史退市或无数据标的。
- 永久规则：报告历史应有 universe 的缺口比例；该数字不得形成风险提示或生产发布门槛。
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

## L-009 验证窗口记录不构成发布门槛

- 状态：active
- 适用：所有 challenger 研究
- 证据：同一批 outcome 上连续更换阈值会把 holdout 变成训练集，并产生选择性汇报。
- 永久规则：主要变体和用过的窗口全部登记，避免重复做完全相同的实验；holdout 数量、窗口数量、单日聚集、样本量和显著性不构成额外门槛，发布统一服从 L-010 的整体改善规则。
- 吸收位置：主提示词“统计协议”“验证与决策”。

## L-010 整体优于 production 的候选允许直接发布

- 状态：active
- 适用：回测或 LIVE 验证通过后的发布
- 旧规则：回测或 LIVE 配对验证只能晋级 shadow，满足额外样本门槛后才能 production。
- 用户决策（2026-07-30）：取消 shadow、OBSERVE、`n>=100`、多窗口、CI 显著性和覆盖率等发布门槛；样本量、CI、覆盖率和窗口数只报告数字，不得附加劝退或风险提示。
- 整体改善定义：平均单笔净收益和同资金口径累计净收益必须同时提高；胜率、PF、最大回撤至少一项改善且其余不恶化。仅允许 `0.01` 的数值精度容差。
- 串行规则：每发布一个候选立即生成新 champion；其他相对旧 champion 的候选必须重新验证，禁止直接叠加。
- 永久保留：真实性硬门禁、可复现差分、回归测试、回滚点和发布后 LIVE 监控。
- 吸收位置：主提示词“生产发布条件”。

## L-011 板块拆分 divergence ≠ 板块级 exit 缺陷

- 状态：active
- 适用：看到 20cm vs 10cm（或任二元切分）D 项收益大幅分化时
- 证据：2026-07-30 official forward clean≥72（n=69）显示 10cm avg -2.85%/netPL 0.21 vs 20cm +1.98%/netPL 2.13。但反事实证伪两条 exit 侧解释：①只放宽 10cm 固定止损（-5.5%→-7~-12%）胜率升（31.6%→47.4%）但 avg 仍深负（最好 -2.05%、netPL≤0.36），因为止损簇亏损大头是隔夜跳空穿透（open<任意止损价，gap 成交），止损距离几乎无助；唯一 CI 显著的 -12% 档 max_loss -12.19% 突破 champion 风险边界且贴 -15% GAP_CAP。②regime 分解显示该 divergence 是 regime 成分效应：弱势 10cm -2.55% vs 20cm +3.67%（同一 regime 两板反向），结构性强势两板同亏（-4.51%/-5.75%，毒区）。
- 永久规则：看到板块/二元收益分化，先做 regime 分解 + 出场结构分解，确认是「同 regime 下的执行差异」再谈板块级 exit 改动；跳空主导的损失不能用止损距离修复。10cm 弱势的亏损是 regime-entry 问题（追高/regime 闸门已 archived），不是 exit 问题。
- 禁止做法：对 10cm 单独加风险闸门/降权/改止损宽度（反事实已证伪）；对 20cm 加闸门（self_check 护栏）。
- 复现脚本：`backend/scripts/si_board_stop_counterfactual.py`（official forward scope + 因果引擎重放 + 按 trade_date 的 paired cluster bootstrap）。
- 吸收位置：主提示词“选择唯一动作”。

## L-012 D 项 champion 查询必须套用 official sample scope

- 状态：active
- 适用：任何生成/引用 D 项 champion 基线（clean≥72 等）的查询或报告
- 证据：2026-07-30 发现 `scripts/self_check.py` 的 `_print_low_score_line_pollution` 查询只过滤 `run_mode='formal'` + `outcome_label`，漏掉 `OFFICIAL_SAMPLE_SQL` 全套口径（live_forward/completed/data_as_of<=td/source_snapshot_id/生成日==交易日），把 `historical_replay` 等非前瞻 formal outcome 计入 clean≥72 总体，使 champion 净胜率由真值 ~48.6% 虚高到 58.6%（193 笔非前瞻被排除后回到 48.6%）。整轮 Δ 比较此前建立在错误基线上。
- 永久规则：D 项 champion / clean 口径查询必须与 `_fetch_dedup_live_formal` 同口径——内联 `OFFICIAL_SAMPLE_SQL`，不得只按 `run_mode='formal'` 过滤；`historical_replay` scope 的 formal outcome 一律不计入 live-forward 基线。任何"champion 待重放"提示解除前，先确认查询已套 scope 且存储 outcome 与当前代码重放零翻转。
- 修复/护栏：`LIVE_FORMAL_CLEAN_POLLUTION_SQL`（f-string 内联 OFFICIAL_SAMPLE_SQL）+ `backend/tests/test_self_check_scope_guard_champion.py`（断言查询含全套口径子句，防回归）。
- 真实基线（修正后，北极星目标 WR62%/PF2.0）：clean≥72 scoped 净WR48.6%/PF1.04；rec_id 口径 WR49.2%/PF0.90；均低于目标。
- 吸收位置：主提示词“真实性硬门禁”“验证与决策”。

## L-013 regime 归因只能读取决策时持久化标签

- 状态：active
- 适用：任何依赖 regime 的复盘、硬否决重验、regime χ² 分桶、最差桶归因
- 证据：2026-07-30 发现 aggregator 硬门槛惩罚（结构性强势 +4 / 强势 +8）基于决策时 `_snapshot_regime`，但 `score_line_meta` 此前从不持久化该标签，也不持久化 `regime_strong_penalty`。下游若读 `feature_snapshot_json.market_regime`，拿到的是 `feature_builder` 按 trade_date **事后重算**的回溯标签——与决策时实际施加的门槛不一致。14:25 实例据此误判 3 笔为"realness bug"，实则为标签回溯；13:18 的 climax/osc 硬否决子集切分、self_check regime χ²/最差桶均被污染。
- 永久规则：优先读取 `score_line_meta.decision_regime`（=决策时实际使用的 `_snapshot_regime`）。旧记录可回退到每笔推荐的 `market_context_json`：持久化代码明确保存 `card["market_snapshot"]`，而 `card` 与 `apply_pool_rules` 收到同一个 snapshot。禁止读取 `feature_snapshot_json.market_regime`，因为 feature 重建可能事后改变它；也禁止按 trade_date 重新计算标签冒充决策时值。`regime_strong_penalty` 仍从 `score_line_meta` 读取。
- 修复/护栏：`aggregator.apply_pool_rules` 现持久化 `decision_regime` + `regime_strong_penalty` 进 `score_line_meta`；`backend/tests/test_aggregator_decision_regime.py`（4 例锁定持久化/自洽/不回溯）。
- 状态补充（2026-07-30）：新记录由 `score_line_meta.decision_regime` 直接锁定；历史记录虽缺该字段，但逐笔 `market_context_json` 可作为同一决策时 snapshot 的兼容证据，无需危险回填。
- 吸收位置：主提示词“选择证据路径”“验证与决策”。

## L-014 资金流因果回测必须用 strictly-prior-day 快照

- 状态：active
- 适用：任何用 `capital_flow` 表做 champion/challenger 配对回测、因子研究或 entry 过滤
- 证据：2026-07-30 诚实配对测试发现，系统自带 `capital_flow_factor_evidence.DEDUP_LIVE_FORMAL_CAPITAL_FLOW_SQL` 用 `cf.trade_date = r.trade_date`（T-day 快照）做 join。但 production formal run 有盘前 cron（08:30 `formal_run_safety_net`）也会生成 trade_date=T 的推荐，盘前决策时 T-day 资金流尚未发布 → 该 join 对盘前 run **含未来函数**。同一数据集上，T-day 快照口径给出反向（mean_diff −2.48%, CI 跨 0）结果，而 strictly-prior-day（trade_date 严格早于信号日 T 的最大可得日）快照口径方向与探索窗口一致且零未来函数。
- 永久规则：资金流因子因果回测一律取 **strictly-prior-day** 资金流快照（推荐信号日 T 之前最近已发布交易日），不得用 T-day 快照做主判定；T-day 快照仅作敏感性对照并明确标注未来函数风险。所有资金流 entry 过滤只用决策时可得的前置快照。
- 复现状态：一次性实验脚本已随基础设施切换归档；结构化运行记录保留 prior-day 主判定与 T-day 敏感性对照。
- 实证（2026-07-30 si20260730-1957，clean>=72 官方重放 n=78）：honest strictly-prior-day 口径下 capital_flow 逆向硬否决**不复制**——被否决子集 n=45 WR46.7%/PF0.66≈champion(WR46.2%/PF0.78)，非毒桶；challenger(剔该子集) n=33 WR45.5%(**反降**)/avg_net-0.103%/PF0.96/max_loss-7.41%，胜率不升、覆盖-57.7%(78→33)；paired CI[-2.769,1.468] lo 远<0。self_check 探索窗口「3日累计净流入 流入>0 WR44.3% vs ≤0 WR59.4% (Δ−15.1pp)」系 **T-day 快照(含未来函数)伪影**——T-day 敏感性对照 mean_diff=−1.788(负)、crosses 0。结论：clean>=72 总体中 prior-day 资金流逆向信号无干净 edge，该方向归档，勿据此加 entry 规则。
- 吸收位置：主提示词“真实性硬门禁”“选择证据路径”。

## L-015 主动弃权型组合级测量（仅旧三级门槛已废止）

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

- 状态：active
- 适用：任何退出、止损、止盈或 trailing 候选
- 证据：trailing 0.90 曾在全板旧 champion 上通过并被另一分支记为 production；但本地 `main` 已先发布 20cm-only，而退出候选仍把 10cm 历史交易算入。2026-07-30 按真实发布顺序重放 20cm population 后，0.50→0.90 的平均净收益 1.5130%→1.4458%、PF 1.7584→1.7246%，均退化；累计净收益 -2.4014%→-1.4112% 改善，胜率和 MDD 持平，故不满足 L-010，判定 FALSIFIED。
- 永久规则：退出候选先套用当前 production 的全部入场、过滤和仓位规则，再在同一最终 population 上比较；旧 champion 的实验结果不得跨生产版本复用。
- 当前生产：20cm-only + 结构性强势 hard veto + trailing profit_floor 0.50；0.90 不得视为已发布或待自动合并。
- 复现脚本：`backend/scripts/si_sequential_champion_replay.py`。

## L-017 发布状态必须由 Git 与生产配置共同证明

- 状态：active
- 适用：champion 发现、候选串行排序、状态恢复与发布
- 证据：20cm-only、结构性强势 veto、trailing 0.90 分散在互不包含的分支，但共享状态文件曾把三者同时登记为 published，形成不存在的组合 champion。
- 永久规则：`published_candidates` 只是缓存，不是事实来源。每轮必须校验 production commit 的祖先关系与实际配置值；不在同一 `main` 历史且未出现在生产配置中的候选必须标为 invalidated 或 falsified。
- 发布规则：一个候选合入后立即重放剩余候选；严禁根据多个候选分别优于旧 champion 推断组合后仍优。
- 自动护栏：隔离启动器、`challenger_decision.evaluate_challenger`、`si_sequential_champion_replay.py` 和原子状态写入器。
