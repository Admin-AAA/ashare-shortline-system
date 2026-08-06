# 仓库调研采纳项：需求规格

> Spec ID：`repo-survey-adoption-2026-08`
>
> 来源：`.kiro/specs/repo-survey-2026-08/report.md`
>
> 项目阶段：尚未发布生产的测试版本。允许直接调整内部契约、数据库结构和模块边界，不提供历史兼容层；持久化、审计、幂等、可观测、故障隔离、配置校验、数据契约、迁移与真实环境验收必须按生产级标准实现。

## 1. 目标与范围

本 Spec 落地以下已确认采纳项：

1. **P0-1**：横截面因子评估器。
2. **P0-2**：单轮 LLM 审查的上下文与 token 纪律。
3. **P0-3**：策略与 preset 注册遗漏诊断。
4. **P0-4**：日线同根止盈/止损二义性证据。
5. **P1-1**：贝叶斯收缩策略权重建议，默认仅影子运行。
6. **P1-4**：复用现有 Provider Router，补齐跨数据源 fallback 追踪。
7. **P2-5**：版本化决策记录。

### 1.1 明确非目标

1. 不实施 P1-2：LLM token/墙钟预算闸门。
2. 不实施 P1-3：ReAct、多步自主 Agent 或 progression 状态机。
3. 不接券商，不读取或保存券商凭据、账户、持仓、余额、订单，不实现真实下单。
4. 不以本 Spec 为由修改默认推荐规则、策略声明权重、score line、preset 选择、候选排序或聚合公式。
5. 不用原始价格比值替代 `pct_chg`/canonical outcome 收益口径。
6. 不创建第二套 Tushare Provider Router、限流器、重试器或熔断器。
7. 不用无 schema 的 Markdown、内存字典或临时 JSON 代替权威持久化记录。

## 2. 现有资产与差距基线

实现前必须以本表为边界；若代码现状与表中判断不一致，先更新本 Spec，不得平行建设。

| 采纳项 | 现有资产 | 判定 | 本 Spec 允许的工作 |
|---|---|---|---|
| P0-1 因子评估 | `scripts/feature_signal_guard.py` 的 point-biserial 与日期集中度防护 | **GAP** | 新增横截面评估域服务、DB 只读适配器、可复现 CLI 与不可变产物 |
| P0-2 LLM 上下文 | `agno_skill_agent.run_skill_agent()` 已是单轮调用且不传 history；已有 prompt cache/token 遥测 | **RECONCILE** | 对 canonical `ResearchContext` 做确定性选择、排序、语义边界裁剪和审计；不引入循环/预算 |
| P0-3 注册诊断 | `load_skills()`、YAML `default_preset_groups`、`DEFAULT_PRESETS.skill_groups`；少数策略有单独回归测试 | **GAP / RECONCILE** | 建立全局 invariant、集中诊断和 fail-closed formal 校验；保留 preset 环境隔离 |
| P0-4 出场二义性 | canonical execution core 已支持 `IntradayExitPriority` 和 both-hit reason | **RECONCILE** | 在 canonical 结果中增加 typed ambiguity evidence，并贯穿 adapter/outcome/statistics |
| P1-1 影子调权 | `calibrator.py`/`calibrate.py` 已有一次性校准；`Signal.weight` 为现行权重 | **GAP** | 新增独立贝叶斯估计与影子产物；禁止覆盖 `Signal.weight` 或 YAML |
| P1-4 fallback | `TushareProviderRouter` 已有 API 路由、独立 provider 隔离、retry、breaker、合法空结果语义 | **REUSE + GAP** | 复用 router，仅补 typed attempt trace、Baostock 统一投影、持久化和健康查询 |
| P2-5 决策记录 | `ImmutableEnvelope`、`GovernanceObjectStore`、`GovernanceEventLog` 已有 hash/supersedes/evidence URI | **REUSE + GAP** | 新增 decision payload schema、服务、CLI、校验与只读投影 |

## 3. 全局生产级约束

### R-GLOBAL-1：单一权威契约

1. 同一概念只能有一个权威 domain contract。
2. research、formal outcome、API 与报告只能投影权威结果，不得分别推断同一事实。
3. 变更 canonical contract 时允许 breaking change；必须同步修改所有调用方、持久化 schema、测试和文档，不保留双轨兼容层。

### R-GLOBAL-2：持久化、不可变性与幂等

1. 分析产物、影子权重建议和决策记录必须有 schema version、canonical payload hash、code revision、创建者、创建时间和来源 identity。
2. 相同 calculation identity 与相同 payload 的重跑必须复用已有对象或产生等价 no-op。
3. 相同 identity 对应不同 payload 时必须 fail closed，不得覆盖旧记录。
4. 修订必须通过新版本或 `supersedes_id` 追加，禁止原地改写证据。
5. 事务型、可查询运行事件必须写 MySQL；内容寻址治理产物应复用 `GovernanceObjectStore`，不得新建第二套 hash 体系。

### R-GLOBAL-3：配置与迁移

1. 所有新增运行参数必须由类型化配置承载，并校验范围和跨字段约束。
2. 数据库变更必须提供可重复执行、非破坏性的迁移。
3. 不为旧内部表结构提供长期 dual-write 或兼容读取；迁移完成后只保留新契约。
4. 配置错误必须在启动、命令执行或 formal run 前暴露，不能在运行中静默降级。

### R-GLOBAL-4：可观测与故障隔离

1. 每个新运行必须有 correlation/calculation identity、状态、耗时、输入规模、输出规模、失败类别和安全错误摘要。
2. 单个因子、策略或 provider 的失败不得污染其他独立单元；但权威产物不完整时不得标记成功。
3. 日志和持久化事件不得包含 API key、数据库密码、完整 prompt 中的敏感原文或外部服务 credential。
4. 数据质量、scope、schema 或 hash 校验失败必须 fail closed。

### R-GLOBAL-5：证据范围与默认行为

1. LIVE_FORWARD、HISTORICAL_REPLAY、EXPERIMENT 等 evaluation scope 必须显式保存，禁止混算。
2. 精选推荐池是生产因子结论的默认权威口径；信号池只用于选择效应诊断，必须单独标记。
3. 生产推荐默认行为在本 Spec 下保持不变；任何可能改变推荐行为的结果只能以 shadow/experiment 形式生成。
4. backtest/replay 不得伪装成 formal/LIVE_FORWARD 证据。

## 4. P0-1 横截面因子评估器

### R-FACTOR-1：输入契约

系统必须提供不可变的 `CrossSectionalObservation` 契约，至少包含：

- `trade_date`
- `security_code`
- `factor_name`
- `factor_value`（允许缺失但必须计入 coverage 分母）
- `future_return_pct`
- `universe_scope`
- `evaluation_scope`
- `recommendation_id`/outcome calculation identity（适用时）
- `source_artifact_ids`

默认 DB 加载器必须：

1. 只加载精选推荐池。
2. 只使用有效 current outcome projection 或明确指定的不可变 outcome version。
3. 排除 `pending`、`provisional`、无效和 identity 不完整的 outcome。
4. 用 canonical outcome 或 `pct_chg` 链计算未来收益，不使用未复权价格比值。
5. 对同一 `(trade_date, security_code, factor_name, outcome_identity)` 去重；冲突值必须报错。

### R-FACTOR-2：指标

评估器必须按交易日横截面计算并输出：

1. Pearson IC。
2. Spearman RankIC。
3. IC/RankIC 的日期数、均值、样本标准差、未年化 ICIR、正值比例。
4. 可配置 Q2–Q10 分位组的平均未来收益、样本数与日期覆盖。
5. top-minus-bottom long-short return。
6. 分层单调性（分位序号与分层平均收益的 Spearman 相关）。
7. 因子 coverage。
8. 相邻交易日、同证券交集上的因子自相关。
9. 每个交易日有效样本数、被跳过原因和跳过日期数。

所有 tie、缺失值、无方差截面和最小样本行为必须确定性定义并由测试覆盖。

### R-FACTOR-3：时间分层与防误用

1. 必须输出全期汇总、逐月汇总和按月等权汇总。
2. 必须报告最大单日/单月样本占比。
3. 必须复用或等价实现 `feature_signal_guard` 的日期集中度判断，并在结果中给出 `evidence_status`。
4. 样本不足、日期过度集中、月份不足或月间方向不稳定时，只能标记为 `insufficient`/`unstable`，不得输出“可发布”结论。
5. 评估产物不得自动修改 strategy YAML、规则、score 或权重。

### R-FACTOR-4：可复现产物

1. 每次评估必须计算 request fingerprint、observation-set fingerprint、result hash 和 code revision。
2. 结果必须以 `factor_analysis` immutable governance object 保存，并在治理事件流中登记。
3. CLI 必须支持只读执行、JSON 输出路径和对象 ID 输出。
4. 相同输入和配置重跑必须得到相同业务结果 hash；时间/actor 等 envelope header 不得混入 calculation fingerprint。

## 5. P0-2 LLM 上下文与 token 纪律

### R-LLM-1：范围约束

1. 保持现有单轮 skill review，不添加 message history、工具循环、ReAct 或自由反思。
2. 不实现 token/成本/墙钟预算闸门。
3. 不改变结构化输出 schema、规则分或 `Signal.weight`。

### R-LLM-2：版本化上下文策略

系统必须定义版本化 `PromptContextPolicy`，至少包含：

- `policy_id` 与 schema version
- 允许的 `ResearchContext` section 和固定顺序
- 每个 section 的字段白名单与优先级
- 列表项上限和语义截断规则
- 单字段文本上限
- 估算 token 上限（仅用于裁剪，不作为批次预算闸门）
- 必须保留字段

配置必须校验：必须保留字段不能同时被排除；上限必须为正；未知 section/field 必须拒绝。

### R-LLM-3：确定性序列化与语义截断

1. 稳定系统前缀、输出 schema 和规则说明必须置于 prompt 的稳定前缀。
2. 股票、snapshot、research context 和 rule signal 必须按固定 section 顺序、排序键和 canonical JSON 规则序列化。
3. 裁剪必须发生在 section、字段、列表项或完整文本段落边界，禁止从 JSON token、UTF-8 字符或句子中间硬切。
4. 必须优先删除低优先级可选字段，不得删除股票身份、交易日期、核心报价、策略身份、规则结论和输出 schema。
5. 相同业务输入与相同 policy 必须生成相同 prompt body 和 fingerprint。

### R-LLM-4：审计

每次审查必须记录且可查询：

- context policy ID
- 原始/发出上下文 fingerprint
- 原始/发出估算 token 数
- 被删除的 section/field/list item 数量
- truncation reason
- prompt cache token、input/output token（沿用现有遥测）

审计只保存结构化摘要和 hash，不重复落完整敏感 prompt。

## 6. P0-3 策略注册遗漏诊断

### R-REGISTRY-1：全局投影

系统必须从同一份已加载事实构建 `StrategyRegistrationReport`，包含：

- active/enabled 策略清单
- 每个策略声明的 `default_preset_groups`
- 可用 preset 及其 `skill_groups`
- 每个策略实际进入的 preset
- 未进入任何可执行 preset 的 active 策略
- preset 引用但不存在的 group
- group 引用但无 active 策略的情况
- YAML/策略 ID 重复、缺失或冲突

### R-REGISTRY-2：失败语义

1. formal 可执行 preset 引用了不存在 group 时必须配置失败。
2. active 策略未进入任何可执行 preset 时必须配置失败，不能仅写 warning。
3. 查询不存在的策略/preset/group 时，错误必须列出对应类别的可用值。
4. inactive 策略未注册只记 informational，不得阻止启动。
5. 不得取消 preset 的环境隔离，也不得把所有 active 策略自动塞入 formal preset。

### R-REGISTRY-3：执行位置

1. 诊断必须可由独立 CLI/self-check 执行。
2. formal run 在策略求值前必须执行同一 validator，失败时不产生部分推荐。
3. 测试必须遍历全部 skill，而不是继续为每个策略手写注册断言。

## 7. P0-4 日线同根出场二义性

### R-EXIT-1：canonical evidence

canonical execution contract 必须增加 typed 日内命中证据，至少表达：

- `first_hit = stop_loss | take_profit | trailing_stop | ambiguous | not_applicable`
- `same_bar_stop_take_profit_hit`
- `applied_intraday_exit_priority`（仅 ambiguous 时非空）

该证据必须由 `resolve_causal_exit()`/`execute_trade()` 产生；adapter 不得根据 reason 字符串再次猜测。

### R-EXIT-2：执行行为

1. 同根 low 触止损且 high 触止盈时，`first_hit` 必须为 `ambiguous`。
2. 实际成交 reason/price 继续由 `ExecutionPolicy.intraday_exit_priority` 决定。
3. 默认 policy 保持当前保守的 stop-loss 优先，不改变现有推荐/outcome 数值。
4. 仅止损、仅止盈、gap、trailing、horizon close 必须得到非歧义 typed evidence。
5. T+1 开启时不得扫描 entry bar，entry bar 同时触碰两阈值不能标记为离场 ambiguity。

### R-EXIT-3：持久化和统计

1. research simulation、formal outcome adapter、`recommendation_outcome_versions.result_json` 和 current projection 必须保留相同 evidence。
2. calculation schema version 必须提升；旧版本不得原地修改。
3. 若需历史重算，必须追加新 outcome version，并明确 evaluation scope/code revision。
4. 统计必须输出 `ambiguous_count`、`eligible_count`、`ambiguous_rate`，并可按策略、月份和 evaluation scope 分层。
5. 未升级 schema 或缺失 ambiguity evidence 的旧结果不得混入新口径 rate 分母。

## 8. P1-1 贝叶斯影子策略调权

### R-WEIGHT-1：样本与口径

1. 统计单元为去重后的 `(recommendation_id, strategy_id, outcome_calculation_identity)`。
2. 默认只接受 provenance 完整、有效、已结算的 formal/LIVE_FORWARD 样本。
3. replay、backtest、experiment、pending、provisional、无效 outcome 必须排除或在显式研究 scope 中单列，禁止混合。
4. 必须报告 sample count、success/failure、distinct trade days、月份数、exposure count/rate 和排除原因。
5. 不得仅凭策略原始胜率忽略曝光差异；报告必须显式展示 exposure 信息和“不代表因果增益”警告。

### R-WEIGHT-2：后验与建议

默认配置必须为：

- Beta 先验 `alpha=15`、`beta=15`
- `min_samples=30`
- multiplier 下限 `0.83`
- multiplier 上限 `1.20`

计算必须遵循：

```text
posterior_hit_rate = (success + alpha) / (evaluated + alpha + beta)
raw_multiplier = posterior_hit_rate / 0.5
suggested_multiplier = clamp(raw_multiplier, 0.83, 1.20)
```

并输出：

- posterior mean
- 可信区间及其置信水平
- evidence strength
- raw/clamped multiplier
- base weight
- shadow suggested weight
- calculation identity/status

样本少于 `min_samples` 时，状态必须为 `insufficient_evidence`，effective shadow multiplier 必须为 `1.0`。

### R-WEIGHT-3：影子隔离

1. 影子建议不得写回 YAML、`Signal.weight`、preset、aggregation 或 calibrator active config。
2. 生产 pipeline 最多记录与推荐关联的 shadow calculation/object ID，不得读取它改变排序。
3. 影子产物必须以 `strategy_weight_shadow` immutable object 保存，并登记治理事件。
4. 相同样本快照、配置和代码 revision 重跑必须幂等。

### R-WEIGHT-4：激活门槛（本 Spec 不执行激活）

任何未来行为激活必须由独立决策和发布 Spec 完成，并至少要求：

1. walk-forward 样本外优于当前权重。
2. 按月等权后不由单月 beta 驱动。
3. 至少一个完整月 formal 前瞻 shadow。
4. identity/provenance 完整。
5. 明确回滚策略和新版本决策记录。

本 Spec 的完成不得被解释为已批准自动调权。

## 9. P1-4 数据源 fallback 追踪

### R-SOURCE-1：复用边界

1. Tushare API 路由必须继续使用现有 `TushareProviderRouter` 和各 provider 的 limiter/retry/breaker。
2. 合法空结果不得改成 fallback 故障。
3. 参数错误和明确不可重试业务错误不得盲目 fallback。
4. 本 Spec 不改变现有 provider 默认优先级。

### R-SOURCE-2：统一 trace 契约

系统必须定义 provider-agnostic `DataSourceRequestTrace` 和 `DataSourceAttempt`，至少包含：

- correlation ID
- operation/API name
- request identity hash（不含 credential）
- provider、attempt ordinal、started/completed/duration
- outcome：success / valid_empty / failure / skipped_breaker
- failure category、retryable、fallback allowed
- fallback from/to
- selected provider
- contract ID/version
- 字段 fingerprint
- 单位/复权/日期契约校验结果
- final outcome

Tushare router 与 Baostock 分钟 K 备用路径必须投影到同一 trace 契约，但可保留各自 transport 实现。

### R-SOURCE-3：持久化与查询

1. 每个最终请求必须将 summary 与 attempts 以 append-only 方式持久化到 MySQL；写入必须有唯一幂等键。
2. trace 写入失败不得让已成功获取的数据伪装成“完整可审计成功”；调用方必须收到 telemetry persistence 状态，formal 数据修复任务可配置为 fail closed。
3. 必须支持按时间、operation、provider、outcome、failure category 和 correlation ID 查询。
4. `data_source_health` 必须从统一 trace 聚合 success rate、fallback rate、valid-empty rate、p95 duration 和 breaker skips，不得维护第二套互相漂移的计数。
5. 数据健康 API/UI 至少暴露最近 fallback chain 与安全错误摘要。

### R-SOURCE-4：数据契约

1. 每种 operation 必须声明日期格式、字段集合、单位和复权语义。
2. provider 切换成功但契约校验失败时，最终结果必须失败关闭，不得入池。
3. 同证券同日期的 fallback 源交叉校验必须支持 OHLCV/`pct_chg` 容差报告。
4. credential、完整请求 token 和上游原始敏感错误正文不得进入 trace。

## 10. P2-5 版本化决策记录

### R-DECISION-1：权威 schema

系统必须定义 `architecture_decision` payload schema，至少包含：

- `decision_key`、title、status
- context/source
- considered options
- adopted/rejected scope
- rationale/trade-offs
- evidence references（immutable object ID / evidence URI / external reference）
- validation commands 与结构化结果摘要
- risks、mitigations、next state
- related requirement/task IDs
- behavior activation state

status 必须来自封闭枚举，例如 `proposed | accepted | rejected | superseded | retired`。

### R-DECISION-2：不可变修订

1. 权威存储必须复用 `GovernanceObjectStore`/`ImmutableEnvelope`。
2. 同一 `decision_key` 的修订必须写新对象并设置 `supersedes_id`。
3. supersedes 对象必须存在、kind 相同、decision key 相同；否则拒绝。
4. 旧对象永久可解析，hash 不一致必须拒绝。
5. 可读 Markdown/JSON 仅为投影，不是权威源，不允许通过编辑投影修改决策。

### R-DECISION-3：服务与 CLI

必须支持：

- create
- supersede
- show/verify
- list by key/status
- render Markdown

所有写操作必须要求 actor、code revision、timezone-aware timestamp；所有 evidence object reference 必须可解析，外部 reference 必须明确标记为非内容寻址来源。

## 11. 测试与验收

### R-ACCEPT-1：自动化测试

每个采纳项至少需要：

1. domain unit tests。
2. contract/serialization/hash tests。
3. repository 或 adapter integration tests。
4. failure-path tests。
5. 与现有 canonical 路径的 parity/non-regression tests。

### R-ACCEPT-2：真实环境验收

在不执行破坏性操作的前提下，必须完成：

1. 生产 MySQL 上迁移 dry-run/状态检查与 append/read smoke。
2. 真实精选池 + valid outcome 的因子分析 smoke。
3. 至少一个真实 Tushare 主源请求和一个受控 fallback/Baostock trace smoke；不得为了测试主动破坏外部服务。
4. governance store 的 create/get/hash/supersede/evidence URI smoke。
5. formal pipeline 配置诊断 smoke。
6. canonical execution both-hit ambiguity smoke。

### R-ACCEPT-3：发布判定

1. 所有测试、编译/静态检查和 `git diff --check` 必须通过。
2. Spec tasks、验证命令、结果和已知限制必须更新。
3. 任何未完成的真实环境验收必须明确标为 blocker，不得以 mock 通过替代。
4. 影子调权仍必须保持未激活状态。
5. 验收不得访问券商能力或执行破坏性数据库操作。
