# 仓库调研采纳项：技术设计

## 1. 设计原则

本设计面向尚未发布生产的测试版本：不保留旧内部契约兼容层，直接建立长期权威模型；但所有运行态能力必须具备生产级持久化、审计、幂等、迁移、故障隔离和真实环境验收。

核心原则：

1. **唯一权威源**：因子结果、执行二义性、fallback trace、影子权重和决策记录均只有一个 domain contract。
2. **纯计算与 I/O 分离**：统计、裁剪、校验等纯函数不依赖 ORM；数据库、治理对象库和 CLI 通过 adapter 接入。
3. **追加而非覆盖**：分析/建议/决策/outcome 结果均以 calculation identity 或 `supersedes_id` 追加。
4. **影子隔离**：贝叶斯建议不进入 `Signal.weight` 或 aggregation。
5. **复用现有基础设施**：canonical hashing、governance store、execution core、Tushare router、outcome version、data health 均复用，不创建平行实现。
6. **失败关闭**：scope、schema、hash、注册关系、数据契约不完整时，不产出权威成功结果。

## 2. 总体结构

```text
                         ┌─────────────────────────────┐
                         │ immutable governance store  │
                         │ envelope / hash / supersede │
                         └──────────────┬──────────────┘
                                        │
       ┌────────────────────────────────┼─────────────────────────────┐
       │                                │                             │
┌──────▼────────┐              ┌────────▼────────┐           ┌────────▼────────┐
│ factor analysis│              │ shadow weights  │           │ decision records│
│ pure evaluator │              │ pure estimator  │           │ typed service   │
└──────┬────────┘              └────────┬────────┘           └─────────────────┘
       │                                │
┌──────▼──────────────┐        ┌────────▼──────────────┐
│ MySQL selected/outcome│       │ MySQL formal outcomes │
│ read-only repository │       │ read-only repository  │
└─────────────────────┘        └───────────────────────┘

canonical execution core ── typed ambiguity ── adapters ── outcome versions ── stats

TushareProviderRouter ─┐
                      ├─ DataSourceRequestTrace ─ MySQL ─ data_source_health/API
Baostock minute path ──┘

ResearchContext ─ PromptContextPolicy ─ deterministic projector ─ single-call LLM

skills + preset groups ─ StrategyRegistrationValidator ─ CLI/self-check/formal preflight
```

## 3. 包与文件布局

具体实现应遵循现有目录风格，目标布局如下；若现有同名模块已覆盖职责，直接合并而不是新增平行文件。

```text
backend/app/domain/analysis/
  factor_analysis.py                 # 纯输入/输出契约与统计
  bayesian_weight.py                 # 纯 Beta 后验与 shadow suggestion

backend/app/services/analysis/
  factor_analysis_service.py         # orchestration + immutable artifact
  factor_observation_repository.py   # selected/outcome 只读查询
  strategy_weight_shadow_service.py  # scope 加载 + immutable artifact

backend/app/services/llm/
  prompt_context.py                  # PromptContextPolicy + deterministic projector

backend/app/services/registry/
  strategy_registration.py           # 全局投影与 validator

backend/app/services/data_source_trace.py
backend/app/services/decision_records.py

backend/app/models/                   # 沿用项目现有 ORM 布局
backend/migrations/                   # 沿用项目现有迁移机制

backend/scripts/analyze_factor_cross_section.py
backend/scripts/diagnose_strategy_registration.py
backend/scripts/compute_shadow_strategy_weights.py
backend/scripts/decision_record.py

backend/tests/analysis/
backend/tests/execution/
backend/tests/governance/
backend/tests/data_sources/
```

不为保持上述路径而机械新建空层；文件名可按仓库现有命名风格收口，职责边界不可改变。

## 4. 通用身份与持久化设计

### 4.1 Calculation identity

业务计算 identity 不包含 `created_at`、actor 或输出路径：

```python
@dataclass(frozen=True, slots=True)
class CalculationIdentity(CanonicalValue):
    calculation_kind: str
    schema_version: int
    request_fingerprint: str
    input_artifact_fingerprints: tuple[str, ...]
    code_revision: str
```

`calculation_fingerprint = canonical_fingerprint(identity)`。

规则：

- 相同 identity + 相同 result hash：幂等复用。
- 相同 identity + 不同 result hash：冲突并失败。
- 计算结果 payload 引用 input artifact/object IDs；envelope header 另记录 actor/time。

### 4.2 治理对象种类

新增三种 kind：

- `factor_analysis`
- `strategy_weight_shadow`
- `architecture_decision`

统一通过 `GovernanceObjectStore.put()` 写入，通过 `GovernanceEventLog.append()` 登记：

- `factor_analysis_completed`
- `strategy_weight_shadow_computed`
- `architecture_decision_created`
- `architecture_decision_superseded`

治理 store 是内容寻址事实库；MySQL 保存需要查询、聚合和运行追踪的索引/事件，不复制完整治理 payload。

### 4.3 MySQL migration

预计新增以下运行态表；最终字段名应遵循现有 ORM 命名规范：

#### `data_source_request_traces`

- `trace_id` PK
- `correlation_id`
- `operation_name`
- `request_identity_hash`
- `contract_id`
- `contract_version`
- `selected_provider`
- `final_outcome`
- `attempt_count`
- `fallback_count`
- `duration_ms`
- `telemetry_status`
- `started_at` / `completed_at`
- unique `(correlation_id, request_identity_hash)` 或等价稳定 idempotency key

#### `data_source_attempts`

- `attempt_id` PK
- `trace_id` FK
- `ordinal`
- `provider`
- `outcome`
- `failure_category`
- `retryable`
- `fallback_allowed`
- `fallback_from` / `fallback_to`
- `duration_ms`
- `field_fingerprint`
- `contract_validation_json`
- unique `(trace_id, ordinal)`

若现有数据源事件表已具备等价字段，应做直接迁移扩展并删除旧平行计数写路径，而不是保留双写。

## 5. 横截面因子评估器

### 5.1 Domain contract

```python
@dataclass(frozen=True, slots=True)
class CrossSectionalObservation(CanonicalValue):
    trade_date: str
    security_code: str
    factor_name: str
    factor_value: float | None
    future_return_pct: float | None
    universe_scope: str
    evaluation_scope: str
    recommendation_id: str | None
    outcome_identity: str | None
    source_artifact_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class FactorAnalysisRequest(CanonicalValue):
    factor_name: str
    start_date: str
    end_date: str
    horizon_days: int
    quantiles: int
    min_cross_section_size: int
    universe_scope: str = "selected"
    evaluation_scope: str = "live_forward"

@dataclass(frozen=True, slots=True)
class FactorAnalysisResult(CanonicalValue):
    request_fingerprint: str
    observation_fingerprint: str
    overall: FactorPeriodMetrics
    monthly: tuple[FactorPeriodMetrics, ...]
    equal_weighted_monthly: FactorEqualWeightedMetrics
    diagnostics: FactorEvidenceDiagnostics
```

### 5.2 算法

按 `trade_date` 分组：

1. coverage 分母为该日请求 universe 中观察数；分子为 factor 与 future return 都有效的观察数。
2. 有效样本少于 `min_cross_section_size`：跳过 IC/quantile，记录 `insufficient_cross_section`。
3. factor 或 return 无方差：跳过对应相关，记录 `zero_variance`。
4. Pearson 使用中心化协方差；Spearman 使用 average-rank 处理 ties 后计算 Pearson。
5. quantile 用确定性 rank-first 分桶：先按 factor value、security code 稳定排序，再按序号等频分桶，避免 `qcut` 对重复值随机/失败。
6. 分位收益先算日内各桶均值，再跨日等权，避免大截面日支配。
7. long-short 为每日最高桶减最低桶，再跨日等权。
8. 单调性是“桶编号 vs 各桶跨日等权收益”的 Spearman。
9. 自相关在相邻有效交易日相同证券交集上计算，再对日期对等权。
10. 月度先按交易月切片完整重算；全期额外给出逐月指标的等权汇总。

### 5.3 Repository

repository 只负责将 DB 事实映射成 observation：

- 默认从 final selected recommendations 加载 factor snapshot。
- outcome 必须通过 current valid projection 或固定 version identity 连接。
- `future_return_pct` 直接采用 canonical net/gross contract 中明确选择的一项；默认使用扣费后 `net_return_pct`，并在 request 中固化 return field。
- 对 JSON factor 字段的读取必须有字段白名单和数值类型校验。
- 不允许同一 recommendation 匹配多个 current outcome；发现时失败。

### 5.4 服务与 artifact

service 流程：

```text
validate request
→ load observations
→ canonical observation fingerprint
→ evaluate pure function
→ put factor_analysis object
→ append governance event
→ return object id + result
```

治理事件写入失败时，本次命令返回失败；已写入的 immutable object可由后续幂等重试复用。

## 6. LLM prompt context

### 6.1 不改变调用模型

`run_skill_agent()` 继续只执行一次 `agent.arun(input=user_prompt)`。修改点位于 `_build_user_prompt()` 之前：把 `ResearchContext` 或现有 snapshot 映射为版本化、确定性的 `PromptContextProjection`。

### 6.2 Policy

```python
@dataclass(frozen=True, slots=True)
class PromptContextPolicy(CanonicalValue):
    policy_id: str
    schema_version: int
    section_order: tuple[str, ...]
    field_rules: tuple[PromptFieldRule, ...]
    max_estimated_tokens: int
    required_paths: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class PromptContextProjection(CanonicalValue):
    policy_id: str
    original_fingerprint: str
    projected_context: Mapping[str, Any]
    projected_fingerprint: str
    audit: PromptTruncationAudit
```

token 估算只用于比较裁剪前后和触发字段淘汰，可使用项目现有 tokenizer（若无稳定依赖，采用明示版本的确定性估算器）；不得宣称是 provider 精确账单 token。

### 6.3 裁剪顺序

1. canonicalize + section/field whitelist。
2. 列表按稳定业务键排序；无业务键时保持输入顺序并记录。
3. 对文本按段落/句子边界裁剪。
4. 超限时按 field priority 从低到高移除完整字段/列表尾项。
5. required path 不可删除；仍超限则抛 `PromptContextTooLarge`，不发送残缺 prompt。
6. audit 进入现有 LLM review metadata/cost telemetry；不新增独立日志真相源。

### 6.4 Cache 稳定性

prompt 结构：

```text
[稳定系统规则/输出 schema/上下文 schema]
[版本化 policy identity]
[排序后的 projected context]
[rule signal 与本次股票动态尾部]
```

保持已有 cache key 的业务语义；若 projected context 改变，cache key 必须随 fingerprint 改变。

## 7. 策略注册诊断

### 7.1 投影模型

```python
@dataclass(frozen=True, slots=True)
class StrategyRegistrationIssue(CanonicalValue):
    code: str
    severity: str
    strategy_id: str | None
    preset_id: str | None
    group_id: str | None
    message: str
    available_values: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class StrategyRegistrationReport(CanonicalValue):
    active_strategy_ids: tuple[str, ...]
    presets: tuple[PresetRegistrationView, ...]
    strategy_to_presets: Mapping[str, tuple[str, ...]]
    issues: tuple[StrategyRegistrationIssue, ...]
```

### 7.2 Validator

validator 接受已经由 `load_skills()` 和 preset registry 加载的值，不再次读取 YAML，从而避免两套解析语义。

fatal issue：

- duplicate strategy/preset ID
- formal preset unknown group
- active strategy unknown declared group
- active strategy 未进入任何 executable preset
- preset 投影引用不存在 strategy（若投影为显式列表）

informational：inactive strategy 未进入 preset、空的非执行实验 group。

formal preflight 调用 `require_valid_registration()`；CLI/self-check 调用 `build_registration_report()` 并输出全部问题。

未知 ID 异常统一带 `available_values`，API 可安全序列化。

## 8. canonical exit ambiguity

### 8.1 Contract change

直接修改 canonical domain，不保留 reason-string 推断兼容层：

```python
class IntradayFirstHit(StrEnum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    AMBIGUOUS = "ambiguous"
    NOT_APPLICABLE = "not_applicable"

@dataclass(frozen=True, slots=True)
class IntradayExitEvidence(CanonicalValue):
    first_hit: IntradayFirstHit
    same_bar_stop_take_profit_hit: bool
    applied_priority: IntradayExitPriority | None

@dataclass(frozen=True, slots=True)
class CausalExitResult(CanonicalValue):
    ...
    intraday_evidence: IntradayExitEvidence

@dataclass(frozen=True, slots=True)
class ExitDecision(CanonicalValue):
    ...
    intraday_evidence: IntradayExitEvidence
```

所有 return 分支显式构造 evidence。both-hit 分支永远 `AMBIGUOUS`，但 reason/price 仍按 policy。gap/horizon 使用 `NOT_APPLICABLE`；单阈值使用对应 first hit。

### 8.2 Adapter 与版本

- research `SimulationResult` 直接投影 `ExecutionResult.exit.intraday_evidence`。
- formal `outcome_simulation` 直接持久化相同 payload。
- outcome calculation schema version 提升，不读取旧 result 补猜字段。
- current projection只把具备新 schema 的记录计入 ambiguity metric。

### 8.3 Statistics

增加纯统计函数：

```text
eligible = 新 schema 且 closed 且 exit evidence 存在
ambiguous = eligible 中 first_hit == ambiguous
rate = ambiguous / eligible
```

API 按 strategy/month/evaluation_scope group；无 eligible 时返回 `rate=null` 而非 0。

## 9. 贝叶斯影子权重

### 9.1 Pure estimator

```python
@dataclass(frozen=True, slots=True)
class BayesianWeightConfig(CanonicalValue):
    alpha: float = 15.0
    beta: float = 15.0
    min_samples: int = 30
    multiplier_min: float = 0.83
    multiplier_max: float = 1.20
    credible_level: float = 0.95

@dataclass(frozen=True, slots=True)
class StrategyOutcomeEvidence(CanonicalValue):
    strategy_id: str
    evaluated: int
    successes: int
    failures: int
    distinct_days: int
    distinct_months: int
    exposure_count: int
    opportunity_count: int | None
    base_weight: float

@dataclass(frozen=True, slots=True)
class ShadowWeightSuggestion(CanonicalValue):
    strategy_id: str
    status: str
    posterior_mean: float
    credible_interval: tuple[float, float]
    evidence_strength: float
    raw_multiplier: float
    clamped_multiplier: float
    effective_multiplier: float
    base_weight: float
    suggested_weight: float
```

可信区间使用 Beta 分布分位数；优先复用已安装且锁定版本的 SciPy。若项目不依赖 SciPy，采用经过数值测试的内部 beta quantile 实现或明确新增锁定依赖，不用正态近似冒充精确区间。

`evidence_strength` 定义为 `evaluated / (evaluated + alpha + beta)`，仅作为展示，不能单独激活权重。

### 9.2 样本 repository

- 从 valid formal/LIVE_FORWARD current outcome 读取。
- recommendation 的 strategy attribution 必须来自当时已存策略证据，不用当前 YAML 反推历史。
- 多策略共同命中的 recommendation 对每个已归因策略各产生一个 evidence unit，但必须保存 attribution contract version。
- `success`/`failed` 标签的权威定义沿用 outcome；`provisional` 排除。
- exposure/opportunity 口径和时间窗进入 request fingerprint。

### 9.3 Shadow service

```text
load immutable sample set
→ validate provenance/scope
→ group per strategy
→ estimate suggestions
→ include exclusions and exposure diagnostics
→ put strategy_weight_shadow object
→ governance event
```

不得导入 aggregation module 或修改 skill/YAML。用依赖方向测试/源码边界测试确保 shadow package 不依赖生产权重写路径。

## 10. 数据源 fallback trace

### 10.1 Provider-agnostic contract

```python
class DataSourceAttemptOutcome(StrEnum):
    SUCCESS = "success"
    VALID_EMPTY = "valid_empty"
    FAILURE = "failure"
    SKIPPED_BREAKER = "skipped_breaker"

@dataclass(frozen=True, slots=True)
class DataSourceAttempt(CanonicalValue):
    ordinal: int
    provider: str
    outcome: DataSourceAttemptOutcome
    duration_ms: int
    failure_category: str | None
    retryable: bool | None
    fallback_allowed: bool | None
    fallback_from: str | None
    fallback_to: str | None
    field_fingerprint: str | None
    contract_validation: DataContractValidation

@dataclass(frozen=True, slots=True)
class DataSourceRequestTrace(CanonicalValue):
    trace_id: str
    correlation_id: str
    operation_name: str
    request_identity_hash: str
    contract_id: str
    contract_version: int
    attempts: tuple[DataSourceAttempt, ...]
    selected_provider: str | None
    final_outcome: str
    telemetry_status: str
```

时间戳作为持久化事件字段；业务 identity 不含 secret 或随机重试细节。

### 10.2 Tushare instrumentation

在 `TushareProviderRouter` 选路循环处发出 attempt，不在各业务调用方重复包裹。已有 `ProviderFailure` 直接映射 category/retryable/fallback_allowed。合法空 envelope 映射 `VALID_EMPTY` 并结束链。

为避免 trace persistence 把 transport 锁死，router 只依赖轻量 sink protocol：

```python
class DataSourceTraceSink(Protocol):
    def persist(self, trace: DataSourceRequestTrace) -> TracePersistResult: ...
```

生产 sink 写 MySQL；测试 sink 为内存 collector。formal remediation 可要求 persistence success；普通交互查询可返回数据并暴露 `telemetry_status=failed`，同时报警。

### 10.3 Baostock instrumentation

不把 Baostock 强行包装成 Tushare provider。分钟 K fallback orchestration 在其现有主备边界构造同一 trace：Tushare attempt 后 Baostock attempt。字段、频率、复权、时区和单位通过 operation contract validator 后才能标 success。

### 10.4 Health projection

`data_source_health` 从 trace repository 聚合窗口指标。现有零散 runtime health 仍用于 breaker 即时状态，但历史成功率/fallback rate 以 trace 为准；若存在旧 DB 计数表，迁移后停止写入旧路径。

## 11. 版本化决策记录

### 11.1 Payload

```python
@dataclass(frozen=True, slots=True)
class ArchitectureDecisionPayload(CanonicalValue):
    decision_key: str
    title: str
    status: DecisionStatus
    context: str
    source_references: tuple[DecisionReference, ...]
    options: tuple[DecisionOption, ...]
    adopted_scope: tuple[str, ...]
    rejected_scope: tuple[str, ...]
    rationale: tuple[str, ...]
    tradeoffs: tuple[str, ...]
    evidence_references: tuple[DecisionReference, ...]
    validations: tuple[DecisionValidation, ...]
    risks: tuple[DecisionRisk, ...]
    next_state: str
    related_requirement_ids: tuple[str, ...]
    behavior_activation_state: str
```

字段做长度、枚举、唯一性和互相一致性校验。accepted 决策必须至少有 rationale、validation 和 evidence；superseded 状态必须通过新对象表示，而不是改旧 payload。

### 11.2 Service

`DecisionRecordService`：

- `create(payload, metadata)`：同 key 无现存 active 决策才创建。
- `supersede(previous_id, payload, metadata)`：验证 previous kind/key，写 `supersedes_id`。
- `get/verify/list/render`：每次读取重算 hash。

为按 key 查询，可维护 append-only `decision_index.jsonl` 或 GovernanceEventLog 事件投影；index 只存 object ID/key/status/time，读取时仍以 object store 验证后的 envelope 为准。并发 append 使用文件锁或原子 append 机制；若现有 event log 不保证跨进程原子性，应在本任务中补齐后复用，不能假设单进程。

### 11.3 本 Spec 首个决策

实现完成时创建本 Spec 的 accepted 决策，明确：

- 项目是 pre-production test，不是 Demo。
- 不保留向前兼容。
- 基础设施按生产级。
- 不接券商。
- P1-1 shadow-only。
- P1-2/P1-3 排除。

## 12. 关键失败场景

| 场景 | 行为 |
|---|---|
| 因子 observation identity 冲突 | 中止，不写成功 artifact |
| selected 与 signal universe 混入 | scope validator 拒绝 |
| LLM required context 裁剪后仍超限 | 不发请求，typed failure |
| active 策略未进入任何 executable preset | formal preflight 失败 |
| both-hit 日 K | 标 ambiguous，仍按 policy 成交 |
| outcome 旧 schema 无 ambiguity | 不进入新 ambiguity rate 分母 |
| shadow 样本不足 30 | multiplier=1.0，状态 insufficient |
| shadow 写入后 aggregation 尝试读取 | 架构测试禁止依赖 |
| provider 返回合法空结果 | valid_empty，停止 fallback |
| fallback 数据字段/单位不合约 | 失败关闭，不入池 |
| trace DB 写失败 | 显式 telemetry failure；formal remediation 可失败 |
| 决策 supersedes 不同 key/kind | 拒绝 |
| immutable identity 同 hash 不同 bytes | 拒绝覆盖 |

## 13. 验证策略

### 13.1 单元测试

- 手工小矩阵验证 IC、rank ties、quantile、long-short、单调性、自相关。
- Prompt policy 稳定性、required fields、中文/段落边界、超限失败。
- 全策略注册投影与 fatal/info 分类。
- canonical exit 五类 first-hit 与 T+1。
- Beta 后验、clamp、min samples、credible interval。
- trace outcome/fallback chain/valid empty/contract failure。
- decision create/supersede/hash/invalid references。

### 13.2 集成测试

- ORM migration 与 trace repository 幂等冲突。
- factor repository 仅加载 selected + valid outcome。
- outcome adapter 与 research adapter ambiguity parity。
- Tushare router/Baostock trace sink。
- governance store/event/index 并发与崩溃恢复。
- formal run registry preflight。

### 13.3 回归测试

- 默认 execution policy 的收益、reason 和 position size 在新增 evidence 后不变。
- 默认推荐集合、排序和 `Signal.weight` 不变。
- 现有 LLM structured output 和 cache 行为不退化。
- 现有 Provider Router route policy、合法空结果和 breaker 语义不变。

### 13.4 真实环境

- 只读/append-only MySQL smoke，不清库、不删表、不重建。
- 使用真实已结算精选推荐跑因子分析。
- 使用真实 provider 正常请求并通过受控测试替身验证 fallback；若当时自然发生 fallback，保存真实 trace，不人为破坏服务。
- governance object create/read/verify/supersede。
- formal preflight 与 canonical both-hit fixture。

## 14. 部署与回滚

1. 先迁移 DB，再部署写 trace 的代码；迁移必须向前可重复执行。
2. ambiguity 是 outcome calculation schema 新版本；旧版本保留，不做原地补字段。
3. LLM policy 通过 policy ID 版本切换；出错可切回上一 policy，但不保留双格式 adapter。
4. fallback trace sink 可以通过配置切换 fail-closed 范围，但不能关闭 transport 本身的现有保护。
5. shadow weight 无行为激活，无需生产推荐回滚；删除错误建议禁止，使用新对象 supersede/retire。
6. decision record 只追加，不提供 delete/update API。
