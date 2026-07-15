# Architecture Hardening（二期）设计文档

## 1. 设计目标

一期建立了多个局部抽象，但部分抽象仍存在“公开接口与生产真实调用不一致”“只拆文件但中央编排仍拥有全部业务”的问题。二期以 **Agent Research Gateway** 为 P0 核心产品能力：先让后续自我优化 Agent 能安全执行单策略回测、失败归因和样本外对照，再用契约闭合、应用层编排和版本化 schema 演进支撑该闭环长期扩展。架构重构是这一入口的基础设施和安全边界，不是独立于业务结果的终点。

核心设计原则：

1. **研究入口优先**：优先交付 preflight → snapshot → backtest → attribution → experiment 的真实策略垂直切片；历史回测家族和外围模块随后按同一契约迁移。
2. **单一事实来源**：运行请求、策略身份、数据源能力和成本 policy 只能有一个权威来源。
3. **依赖倒置**：Coordinator 依赖 Port，不依赖供应商、ORM model 或模块级全局状态。
4. **显式状态机**：运行阶段、状态转换、恢复和副作用边界可枚举、可测试。
5. **扩展由注册驱动**：新增 Factor、Strategy、Market Source、Auxiliary Dataset、Backtest Strategy 不修改中央分发器。
6. **数据库向前演进**：所有 schema 变化有版本、checksum、锁、验证和真实 MySQL 证据。
7. **外部兼容、内部收口**：保持 HTTP API 业务语义，内部私有兼容层直接迁移后删除。
8. **研究与发布分权**：Agent 可以生成研究证据和候选提案，但不能通过研究入口修改 formal 策略或生产参数。

## 2. 方案比较

### 方案 A：继续局部抽函数

- 优点：短期改动小。
- 缺点：无法解决 `workbench_service` 的共享可变 dict、隐藏调用和运行请求丢失；新增功能仍修改中央模块。
- 结论：不采用。

### 方案 B：增量式契约加固与绞杀迁移（采用）

- 先建立 migration、RunRequest、Port 和 Repository 契约。
- 再把现有行为逐阶段迁到 Coordinator。
- 每迁一段就用 golden-master 与真实 MySQL 验证。
- 最后删除私有兼容出口和旧入口。

优点是风险可分段控制，旧系统始终可运行；缺点是迁移期会短暂存在旧 facade 与新实现并存，需要严格限定时间和删除任务。

### 方案 C：事件驱动/分布式任务系统重写

- 优点：理论上扩展性强。
- 缺点：当前单机/单库规模不需要，引入消息一致性、部署和运维复杂度，无法证明收益。
- 结论：本期明确不采用。

## 3. 目标分层

```text
HTTP API / Scheduler / CLI
            │
            ▼
     RunRequest Parser
            │
            ▼
      Run Application API
            │
            ▼
       RunCoordinator
       ├─ RunRepository (Port)
       ├─ MarketPreparation (Port)
       ├─ AuxiliaryDataOrchestrator (Port)
       ├─ FeaturePipeline (Port)
       ├─ StrategyEvaluation (Port)
       ├─ ReviewExecutor (Port)
       ├─ RecommendationAssembler (Port)
       └─ RecommendationRepository (Port)
            │
            ▼
Adapters / SQLAlchemy Repositories / Tushare / Agno / MySQL
```

依赖规则：

```text
engine/domain 不能依赖 services、ORM 或供应商
application/coordinator 可以依赖 engine/domain 和 Port
adapters/repositories 实现 Port，可依赖 ORM 和供应商
API/scheduler 只调用 application API
```

## 4. 建议包结构

```text
backend/app/
├── db/
│   └── migrations/
│       ├── runner.py
│       ├── contract.py
│       └── versions/
├── engine/
│   ├── strategy.py
│   ├── signal.py
│   ├── simulation.py
│   └── backtest/
│       ├── contracts.py
│       ├── registry.py
│       ├── runner.py
│       ├── readiness.py
│       ├── repository.py
│       └── experiments.py
├── services/
│   ├── runs/
│   │   ├── contracts.py
│   │   ├── coordinator.py
│   │   ├── state_machine.py
│   │   ├── repository.py
│   │   ├── stages/
│   │   └── queue.py
│   ├── market_source/
│   ├── market_pool/
│   ├── auxiliary/
│   │   ├── contracts.py
│   │   ├── registry.py
│   │   ├── orchestrator.py
│   │   └── mysql_repository.py
│   └── backtest/
└── adapters/
    ├── market_source/
    ├── review/
    └── repositories/
```

不要求一次性移动所有文件；任务按依赖顺序逐步迁移。禁止只移动文件而不改变依赖方向。

## 5. 数据库迁移设计

### 5.1 Migration Contract

```python
@dataclass(frozen=True)
class MigrationOperation:
    precondition: Callable[[MigrationContext], OperationState]
    apply: Callable[[MigrationContext], None]
    postcondition: Callable[[MigrationContext], None]

@dataclass(frozen=True)
class Migration:
    version: str
    description: str
    checksum: str
    operations: tuple[MigrationOperation, ...]
    verify: Callable[[MigrationContext], None]
```

`MigrationContext` 只暴露白名单 schema/data 操作和受审计 SQL，不暴露 raw DBAPI/SQLAlchemy connection。每个 operation 必须能在其 apply 已完成但 ledger 尚未写入时安全重入；无法满足时拆成单操作 migration。

Migration 文件一旦合入并在环境中应用，不允许修改；需要修复时新增更高版本 migration。

### 5.2 Ledger 表

```sql
CREATE TABLE schema_migrations (
    version VARCHAR(64) PRIMARY KEY,
    description VARCHAR(255) NOT NULL,
    checksum CHAR(64) NOT NULL,
    applied_at DATETIME(6) NOT NULL,
    execution_ms INTEGER NOT NULL
);
```

### 5.3 执行流程

1. runner 独占一个专用 connection；该 connection 在 release lock 前不得归还连接池。
2. 在该 connection 上调用 MySQL `GET_LOCK('ashare_schema_migration', timeout)`；只有返回 1 才能继续，0/NULL 立即标记 migration error。
3. 通过 V000 baseline migration 创建/核验 ledger 与业务基础结构。生产 schema 的唯一 owner 是 migration runner；`Base.metadata.create_all()` 仅用于不进入生产迁移流程的单元测试临时库。
4. 按 version 排序加载 migration。
5. 已应用版本：校验 checksum；一致则跳过。
6. 未应用版本：逐 operation 执行 precondition → apply（若未满足）→ postcondition，随后执行 migration verify 并写 ledger。
7. 失败时记录结构化错误，不写成功 ledger，不开放依赖新 schema 的写路径；再次启动必须能从任一已完成 operation 安全重入。
8. finally 在同一 connection 上调用 `RELEASE_LOCK`，再关闭/归还 connection。

MySQL DDL 可能隐式提交，因此不提供 destructive down migration。回滚策略是停止新写路径、恢复应用旧版本的只读能力（若兼容）、修正代码并追加 forward migration。

### 5.4 二期 schema 变化

`recommendation_runs` 新增：

```sql
request_schema_version SMALLINT NULL,
request_json LONGTEXT NULL,
execution_version VARCHAR(128) NULL
```

采用 expand → backfill → cutover：先只新增 nullable 列，不设置 DEFAULT 1；待 V1 RunRequest encoder/parser 完成后切换新 writer 双写 canonical request，再分批回填并追赶校验，最后切换 worker 读取。只有 request JSON 可解析且与独立列一致的行才设置 `request_schema_version=1`。

保留 `run_mode/status/trade_date/started_at` 独立列和现有索引。`request_json` 保存 canonical JSON，不保存凭据、token 或数据库连接信息。

迁移按批次读取旧 `params_json` 和现有列，构造 V1 `RunRequest` 后回填。回填完成后 verify：

- 所有可恢复运行都有合法 request JSON；
- request.run_mode 与列值一致；
- 行数不变；
- formal/experiment/backtest 分组计数不变。

旧 `params_json` 本期不物理删除，仅停止作为运行恢复的权威来源，避免破坏历史查询。

单策略研究回测新增专属表，全部通过 forward-only migration 创建：

```sql
CREATE TABLE backtest_data_snapshots (
    snapshot_id VARCHAR(80) PRIMARY KEY,
    snapshot_version SMALLINT NOT NULL,
    manifest_hash CHAR(64) NOT NULL,
    partition_manifest_json LONGTEXT NOT NULL,
    coverage_json LONGTEXT NOT NULL,
    missing_requirements_json LONGTEXT NOT NULL,
    created_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_backtest_snapshot_manifest (manifest_hash)
);

CREATE TABLE backtest_input_artifacts (
    artifact_id VARCHAR(100) PRIMARY KEY,
    snapshot_id VARCHAR(80) NOT NULL,
    signal_date VARCHAR(10) NOT NULL,
    artifact_scope VARCHAR(24) NOT NULL,
    artifact_type VARCHAR(48) NOT NULL,
    schema_version SMALLINT NOT NULL,
    row_count INTEGER NOT NULL,
    content_hash CHAR(64) NOT NULL,
    content_encoding VARCHAR(24) NOT NULL,
    payload LONGBLOB NOT NULL,
    created_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_backtest_artifact (snapshot_id, signal_date, artifact_scope, artifact_type)
);

CREATE TABLE backtest_request_claims (
    request_fingerprint CHAR(64) PRIMARY KEY,
    active_backtest_id VARCHAR(80) NULL,
    completed_backtest_id VARCHAR(80) NULL,
    next_attempt_no INTEGER NOT NULL,
    updated_at DATETIME(6) NOT NULL
);

CREATE TABLE backtest_runs_v2 (
    backtest_id VARCHAR(80) PRIMARY KEY,
    experiment_id VARCHAR(80) NULL,
    parent_backtest_id VARCHAR(80) NULL,
    request_fingerprint CHAR(64) NOT NULL,
    attempt_no INTEGER NOT NULL,
    snapshot_id VARCHAR(80) NOT NULL,
    strategy_id VARCHAR(80) NOT NULL,
    strategy_version VARCHAR(160) NOT NULL,
    request_schema_version SMALLINT NOT NULL,
    request_json LONGTEXT NOT NULL,
    execution_version VARCHAR(128) NOT NULL,
    execution_policy_fingerprint CHAR(64) NOT NULL,
    signal_date VARCHAR(10) NULL,
    date_from VARCHAR(10) NULL,
    date_to VARCHAR(10) NULL,
    status VARCHAR(40) NOT NULL,
    result_json LONGTEXT NOT NULL,
    started_at DATETIME(6) NULL,
    finished_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_backtest_request_attempt (request_fingerprint, attempt_no)
);

CREATE TABLE backtest_candidates (
    candidate_id VARCHAR(100) PRIMARY KEY,
    backtest_id VARCHAR(80) NOT NULL,
    signal_date VARCHAR(10) NOT NULL,
    code VARCHAR(6) NOT NULL,
    decision_status VARCHAR(32) NOT NULL,
    signal_json LONGTEXT NULL,
    audit_json LONGTEXT NOT NULL,
    input_artifact_id VARCHAR(100) NOT NULL,
    UNIQUE KEY uq_backtest_candidate (backtest_id, signal_date, code)
);

CREATE TABLE backtest_trades (
    trade_id VARCHAR(100) PRIMARY KEY,
    backtest_id VARCHAR(80) NOT NULL,
    signal_date VARCHAR(10) NOT NULL,
    code VARCHAR(6) NOT NULL,
    entry_date VARCHAR(10) NULL,
    exit_date VARCHAR(10) NULL,
    outcome_label VARCHAR(32) NOT NULL,
    trade_json LONGTEXT NOT NULL,
    UNIQUE KEY uq_backtest_trade (backtest_id, signal_date, code)
);

CREATE TABLE backtest_failure_attributions (
    attribution_id VARCHAR(100) PRIMARY KEY,
    backtest_id VARCHAR(80) NOT NULL,
    trade_id VARCHAR(100) NOT NULL,
    reason_code VARCHAR(48) NOT NULL,
    rule_or_factor_reference VARCHAR(160) NULL,
    evidence_references_json LONGTEXT NOT NULL,
    confidence FLOAT NOT NULL,
    is_unknown BOOLEAN NOT NULL,
    created_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_backtest_trade_attribution (trade_id, reason_code)
);

CREATE TABLE backtest_experiments (
    experiment_id VARCHAR(80) PRIMARY KEY,
    hypothesis TEXT NOT NULL,
    baseline_backtest_id VARCHAR(80) NOT NULL,
    snapshot_id VARCHAR(80) NOT NULL,
    execution_policy_fingerprint CHAR(64) NOT NULL,
    split_hash CHAR(64) NOT NULL,
    split_json LONGTEXT NOT NULL,
    candidate_strategy_id VARCHAR(80) NOT NULL,
    candidate_strategy_version VARCHAR(160) NOT NULL,
    frozen_candidate_fingerprint CHAR(64) NOT NULL,
    acceptance_json LONGTEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    conclusion_json LONGTEXT NULL,
    created_at DATETIME(6) NOT NULL,
    frozen_at DATETIME(6) NULL,
    finished_at DATETIME(6) NULL
);
```

实际字段长度、压缩编码和外键策略以现有 MySQL 表约束及在线 DDL 预检为准，但上述职责不可合并回 `recommendations`/`recommendation_outcomes`：研究回测必须与 LIVE、formal、experiment 推荐统计隔离。输入工件必须是可校验内容摘要的不可变 payload，而不能只有 watermark；若未来迁移到内容寻址对象存储，MySQL 仍须保留 hash、schema、row_count 和不可变定位引用。`backtest_runs` 旧表只作为历史兼容读模型；调用点全部切换后再决定迁移/归档策略，不在本期删除历史数据。

### 5.5 迁移现有 init_db

`_ensure_column`、`_ensure_column_type`、`_ensure_index` 和特定表迁移先形成 inventory，再按功能转成版本文件。旧 `_migrate_v2` 的 destructive drop 不得被二期 migration runner 复制；历史遗留表清理由独立人工运维流程处理。完成后生产 `init_db` 只调用 migration runner 和 schema verification。

### 5.6 业务库在线 DDL 安全闸门

每个索引、UNIQUE 约束或类型修改使用独立 migration。执行前必须只读检查：

- 表行数、数据大小和可用磁盘；
- natural key 重复记录（发现即停止并输出冲突报告，不自动删除）；
- metadata lock blocker 和活跃长事务；
- 当前 MySQL 对目标 DDL 的 online/in-place 支持。

runner 设置有限的 `lock_wait_timeout` 和执行时限。业务库账号应移除 DROP/TRUNCATE 权限。真实业务库执行遵循：预检 → 可恢复备份证据 → go/no-go → migration → 只读结构/计数校验 → smoke；任一步失败立即停止。

## 6. RunRequest 与执行版本

```python
@dataclass(frozen=True)
class RunRequest:
    schema_version: int
    run_mode: RunMode
    run_name: str
    data_source: DataSourceId
    preset_id: str | None
    skills: tuple[SkillSelection, ...]
    score_line: float
    risk_template: str
    output_count: int
    skip_aux_refresh: bool
    disable_agent_review: bool
    trade_date_override: str | None
```

要求：

- API、Scheduler、CLI 共用一个 parser。
- `to_canonical_json()` 使用稳定字段排序和明确默认值。
- `from_json()` 先按 schema version upgrade，再验证。
- `execution_version` 由应用版本、策略指纹、Factor plan 指纹和关键 policy 指纹组成，用于结果归因，不参与业务筛选。

入队顺序固定为：

```text
validate request
→ persist queued run + canonical request
→ commit
→ spawn/enqueue worker(run_id)
→ worker by run_id loads request
```

worker 不再携带一份可漂移的 payload 副本。

## 7. 运行状态机与 Coordinator

### 7.1 状态

```text
queued --spawn success--> running --all stages committed--> completed
   |                       |--stage failure-------------> failed
   |                       |--heartbeat timeout---------> interrupted
   |                       |--cancel request------------> cancelled
   |--spawn failure-------> failed
   |--cancel request------> cancelled
interrupted --explicit resume + valid checkpoint-------> running
```

`completed/failed/cancelled` 为终态，不允许恢复；`interrupted` 只有在 canonical request 合法、checkpoint 可验证且不存在活跃 worker lease 时才能恢复。每条转换定义 trigger actor、expected current status、target status、stable error code 和同一数据库事务内的字段更新；compare-and-set 防止两个 worker 同时推进。

非法转换由 `RunStateMachine.transition()` 拒绝。

### 7.2 阶段

```text
prepare
market_data
auxiliary_data
features
strategy_evaluation
review
aggregation
persistence
completed
```

每个 Stage 实现：

```python
class RunStage(Protocol):
    phase: RunPhase
    def execute(self, context: RunContext) -> RunContext: ...
```

`RunContext` 使用明确字段和不可变值对象；阶段需要更新时返回新 context 或受控 result，不共享任意可变 `dict[str, Any]` 作为隐式接口。

### 7.3 副作用与 checkpoint

- 纯计算阶段可安全重放。
- 外部拉取阶段通过 audit key 去重。
- persistence 阶段使用 run_id/natural key 保证幂等。
- Repository 在同一事务中原子写入 `(last_committed_phase, side_effect_key, heartbeat_at)`；只有副作用提交成功后才能推进 checkpoint。
- heartbeat 超时只产生 `interrupted`，不会自动重复执行；显式 resume 先验证 side-effect key。

## 8. Strategy 身份设计

```python
@dataclass(frozen=True)
class StrategyDescriptor:
    strategy_id: str
    name: str
    mode: StrategyMode
    declared_version: str
    manifest_hash: str
    implementation_hash: str
```

Registry key、Signal.strategy_id、PresetSkill.skill_id 和绩效归因都使用 `descriptor.strategy_id`。

命令式实现协议只定义行为：

```python
class ImperativeStrategy(Protocol):
    def evaluate(self, stock: StockInput, context: StrategyContext) -> StrategyDecision: ...
```

实现类不再维护独立 ID/name。装配器包装 Decision 为统一 Signal，并注入 descriptor 身份。

`effective_version = sha256(manifest_hash + implementation_hash + contract_version)`。

## 9. Factor ExecutionPlan

显式 bootstrap：

```text
register built-in factors
→ inject allowlist/policies
→ validate dependency graph
→ validate output ownership
→ resolve deterministic order
→ freeze registry
→ publish execution plan fingerprint
```

生产路径只执行冻结 plan；测试创建局部 Registry，不修改生产 singleton。

## 10. MarketSourcePort

```python
@dataclass(frozen=True)
class QuoteRef:
    code: str
    exchange: str | None

@dataclass(frozen=True)
class DailyBarRequest:
    instruments: tuple[QuoteRef, ...]
    start_date: str | None
    end_date: str | None
    failure_policy: FailurePolicy

class MarketSourcePort(Protocol):
    def fetch_quotes(self, request: QuoteRequest) -> QuoteBatch: ...
    def fetch_daily_bars(self, request: DailyBarRequest) -> DailyBarBatch: ...
```

`DailyBarBatch` 同时返回成功数据和 per-code error/audit。`fetch_bars_for_quote` 不再作为生产隐藏出口；adapter 内部可有私有 helper，但调用者只见 Port。

## 11. Auxiliary Dataset

### 11.1 Descriptor

```python
@dataclass(frozen=True)
class AuxiliaryDatasetDescriptor(Generic[RawT, NormalizedT, ViewT]):
    key: str
    natural_key: tuple[str, ...]
    fetcher: AuxiliaryFetcher[RawT]
    normalizer: AuxiliaryNormalizer[RawT, NormalizedT]
    repository: AuxiliaryRepository[NormalizedT]
    loader: AuxiliaryLoader[ViewT]
    coverage_policy: CoveragePolicy
    refresh_policy: RefreshPolicy
    freshness_field: str | None
    freshness_extractor: FreshnessExtractor[NormalizedT] | None
```

Registry bootstrap 校验 key、自然键、handler、依赖、loader 输出 key 和 freshness 配置，然后冻结。

### 11.2 Orchestrator

Coordinator 只调用：

```python
result = auxiliary_orchestrator.prepare(
    datasets=requested_dataset_keys,
    instruments=stocks,
    trade_date=trade_date,
    refresh=not request.skip_aux_refresh,
)
```

Orchestrator 统一产生 refresh、coverage、staleness、persist 和 failure audit。

### 11.3 MySQL atomic upsert

MySQL 实现使用 SQLAlchemy MySQL dialect：

```python
insert(table).values(batch).on_duplicate_key_update(...)
```

- 按可配置 batch size 写入。
- natural key 必须有数据库 UNIQUE 约束。
- 明确哪些字段覆盖、保留或取最新值。
- SQLite 仅用于纯 contract tests；并发和原子性必须在真实 MySQL 验证。

## 12. Simulation 与 Backtest

### 12.1 Simulation

`engine/simulation.py` 只依赖 DTO 与 Policy：

```python
SimulationResult simulate(
    recommendation: RecommendationInput,
    bars: tuple[BarInput, ...],
    policy: SimulationPolicy,
)
```

ORM 映射和持久化留在 application/repository。

### 12.2 可复现单策略 Backtest

回测的领域入口是不可变 `BacktestRequest`，不能复用裸 `dict`、`RunRequest` 或模糊 `skill_id`：

```python
@dataclass(frozen=True)
class SingleDate:
    signal_date: str

@dataclass(frozen=True)
class DateRange:
    start_date: str
    end_date: str

@dataclass(frozen=True)
class BacktestRequest:
    schema_version: int
    strategy_id: str
    strategy_version: str | None       # None = 当前冻结 descriptor，执行前解析并固化
    date_selector: SingleDate | DateRange
    universe: UniverseSelector
    execution_policy: ExecutionPolicy
    data_policy: BacktestDataPolicy
    parameter_set: ParameterSet | None
    experiment_id: str | None
    baseline_backtest_id: str | None
    force_rerun: bool
```

规则：

- `strategy_id` 必填且精确对应一个 enabled `StrategyDescriptor`；不支持多个 strategy_id、空 ID 或把未指定 ID 回退为所有 enabled strategies。
- `SingleDate` 只生成一个 signal date；`DateRange` 是含端点的交易日范围。两者互斥，且不得只提供自然日却未通过交易日历验证。
- `target_codes` 仅作为诊断 universe，结果必须标记为 `diagnostic_scope`，不能与全市场表现混作策略晋级证据。
- `parameter_set` 只能引用 strategy manifest 声明、带类型/范围/默认值的 BacktestParameterSpec；任意 JSON 覆盖被拒绝。策略/因子代码改动仍通过 worktree 的 descriptor/implementation fingerprint 识别。
- `ExecutionPolicy` 显式定义入场/出场、horizon、佣金、印花税、滑点、仓位、止损止盈和不可成交规则；不读取模块级可变 grid 参数。
- `BacktestDataPolicy` 默认 `local_only=True`，禁止回测期间网络补数或写入行情/辅助数据；可写数据只允许写入专属 backtest 表。

### 12.3 DataReadiness、可重放快照与防前瞻读取

每个 `BacktestStrategy` 声明 `DataRequirement`：lookback bars、未来 label horizon、所需 daily/minute frequency、基准、辅助数据集及每项最小 coverage。`DataReadinessPort.preflight(request)` 在 Runner 创建前生成：

```python
@dataclass(frozen=True)
class PartitionManifest:
    dataset: str
    query_range: DateRange
    source_version: str
    row_count: int
    content_hash: str

@dataclass(frozen=True)
class DataSnapshot:
    snapshot_id: str
    snapshot_version: int
    partitions: tuple[PartitionManifest, ...]
    artifacts_by_signal_date: Mapping[str, tuple[ArtifactRef, ...]]
    coverage_by_signal_date: Mapping[str, CoverageResult]
    input_manifest_hash: str
    missing_requirements: tuple[DataGap, ...]
```

单日回测需要同时满足：该日及 lookback 历史可用、策略所需辅助数据在其可见时间点已发布、执行 policy 所需的未来日线/分钟线存在。区间回测对每个 signal date 独立判定。`insufficient_data`（无法生成信号）与 `incomplete_evaluation`（能生成信号但缺未来标签）是正常可观测终态，不能伪装成 completed，也不能产出可晋级指标。

Snapshot 创建器按**实际查询分区**记录版本、日期范围、行数和 canonical content hash，并将每个 signal date 的策略输入及 outcome 输入分别序列化为不可变、带 schema version 的内容寻址工件。重放指定 `snapshot_id` 时只能读取原工件并逐项校验 hash；源表后续回填、修正或删除不会改变旧结果。工件缺失或 hash 不一致时返回 `snapshot_artifact_unavailable`/`snapshot_integrity_error`，不得回退到当前源表冒充原快照。

防前瞻不是只靠 preflight，而是由能力分离强制执行：

```python
class SignalDateDataReader(Protocol):
    def read(self, signal_date: str, requirement: SignalInputRequirement) -> SignalInput: ...

class OutcomeDataReader(Protocol):
    def read(self, signal_date: str, policy: ExecutionPolicy) -> OutcomeInput: ...
```

`BacktestDateContext` 只持有 `SignalDateDataReader`，它在 repository/query builder 与工件 reader 两层都强制 `visible_at <= signal_date`；Strategy、Factor 和 FeaturePipeline 不得获得 Session、generic repository 或 `OutcomeDataReader`。只有 Simulation/Outcome 层持有 `OutcomeDataReader` 并可按 execution policy 读取未来行情。越界读取返回稳定错误 `future_data_access_denied` 并使该 attempt 失败。

### 12.4 Backtest Runner、隔离持久化与 attempt 幂等

```python
class BacktestStrategy(Protocol):
    descriptor: StrategyDescriptor
    data_requirement: DataRequirement
    def evaluate_date(self, context: BacktestDateContext) -> DateDecision: ...

class BacktestRunner:
    def preflight(self, request: BacktestRequest) -> BacktestPreflight: ...
    def run(self, request: BacktestRequest) -> BacktestResult: ...
```

Runner 固定骨架：resolve descriptor → validate request → preflight/readiness → create/load immutable Data Snapshot → atomic claim attempt → evaluate each signal date with SignalDateDataReader → generate immutable candidates → simulate with OutcomeDataReader → build EquityPoint/Metrics/FailureAttribution → persist dedicated rows → project API response。

- 单日返回完整 candidate/no-signal trace 和交易细节；区间聚合日度权益曲线、交易、样本数、覆盖率、胜率、平均净收益、盈亏比、最大回撤及置信状态。
- `request_fingerprint = SHA-256(canonical semantic request + effective strategy/execution version + execution policy fingerprint + snapshot_id)`；`force_rerun` 不进入 fingerprint。completed attempt 可默认复用，failed/incomplete attempt 不可冒充完成结果。
- Repository 在事务中插入或 `SELECT ... FOR UPDATE` 锁定 `backtest_request_claims`：普通并发请求复用 active/completed run；failed/incomplete 后重试以及每次 `force_rerun` 原子领取新的 `attempt_no`。`backtest_runs_v2` 以 `(request_fingerprint, attempt_no)` 唯一，并通过 `parent_backtest_id` 保留尝试链。
- attempt 完成时 compare-and-set 更新 claim 的 completed reference；失败只释放 active reference并保留历史。进程崩溃由 lease/heartbeat 判定过期后领取下一 attempt，永不覆盖旧 run。
- `backtest_candidates` 与 `backtest_trades` 的 natural key 都包含 `backtest_id`，严禁 `DELETE btrec_*`、覆盖其他 backtest、写 `Recommendation` 或写 `RecommendationOutcome`。
- 旧 `/api/runs/backtest` 成为兼容 HTTP adapter，转换为 `BacktestRequest` 后调用 Runner；不保留 `run_backtest` 裸 dict/全策略默认行为。Agent Research Gateway 始终调用新的 application service，不经过兼容 adapter。

### 12.5 实验与自我优化 Agent 准备

`BacktestExperiment` 不等同于 BacktestRun。它把“为什么改、和谁比较、是否可信”持久化：

```python
@dataclass(frozen=True)
class ExperimentPlan:
    hypothesis: str
    baseline_backtest_id: str
    snapshot_id: str
    execution_policy_fingerprint: str
    splits: CanonicalTimeSplits        # 实际交易日集合 + split_hash
    candidate_request: BacktestRequest
    frozen_candidate_fingerprint: str
    acceptance: AcceptanceCriteria     # 预先声明的 metric/min samples/max drawdown
```

创建计划时从 baseline 冻结 `snapshot_id`、execution policy fingerprint 与 canonical split。split 必须满足 `train < validation < out_of_sample`、集合不重叠且日期全集与 baseline 一致。candidate 在 train/validation 完成后冻结 parameter/code/descriptor fingerprint；冻结后只能在原 out-of-sample 集合上评估。Repository 在写入和比较前同时校验 snapshot、policy、split hash 与 candidate fingerprint，任一 mismatch 或 OOS 已可见后的重新切分均拒绝。

Experiment service 执行 candidate 与 baseline 的同快照、同执行成本、同日期切分比较，输出每个 split 的 trade count、coverage、win rate、profit-loss ratio、average net return、max drawdown、bootstrap confidence interval 或 sample-insufficient 状态。

状态为 `draft → running → frozen → completed | failed | insufficient_data | exploratory | rejected | promotion_candidate`。`promotion_candidate` 仅表示证据已满足实验计划；它**不得**自动修改 `strategy.yaml`、SkillRegistry、权重、Preset 或 formal 运行。自我优化 Agent 只拥有创建实验、读取证据、创建 candidate proposal 的能力；发布动作必须走单独的显式人工/发布流程。

失败归因是独立领域结果，而不是任意 `audit_json`：

```python
@dataclass(frozen=True)
class FailureAttribution:
    reason_code: FailureReasonCode
    evidence_references: tuple[EvidenceRef, ...]
    rule_or_factor_reference: str | None
    confidence: float
    unknown: bool
```

只对已成交且净收益为负或业务标签为 failed 的 Trade 生成归因；`no_signal`、`insufficient_data`、`incomplete_evaluation` 和不可成交样本不得进入分母。首期稳定 reason codes 至少包括 `chase_high`、`stop_loss`、`sector_decay`、`black_swan`、`execution_failure` 和 `unknown`。聚合 API 按 strategy_id、strategy_version、experiment split、reason_code 返回数量、比例、净收益与证据引用，使 T3 能执行“失败 → 归因 → 规则/因子调整 → 同快照验证”。

这为 `self-improve` 的 T1/T2/T3 提供二期最高优先级的稳定入口：先针对某 strategy 和日期/区间诊断，再记录假设与 baseline，最后用独立样本外窗口确认胜率、盈亏比和回撤是否同时改善，避免只拟合历史胜率。

### 12.6 Agent Research Gateway API（P0）

这是二期首要 application API，也是改造完成后 `self-improve` T1/T2/T3 创建新研究证据的唯一入口。新 API 使用 Pydantic schema：

```text
POST /api/backtests/preflight             # BacktestRequest → DataReadiness/Date coverage
POST /api/backtests                       # 创建或复用单策略回测
POST /api/backtests/{id}/replay            # 使用原 snapshot_id/工件创建重放 attempt
GET  /api/backtests/{id}                  # 汇总、版本、数据截面、指标和状态
GET  /api/backtests/{id}/candidates       # 信号/无信号与过滤审计
GET  /api/backtests/{id}/trades           # 独立逐笔交易
GET  /api/backtests/{id}/attributions     # 失败归因明细与聚合
POST /api/backtest-experiments            # 创建带 hypothesis/baseline/frozen splits 的实验
GET  /api/backtest-experiments/{id}       # 对照、置信状态与晋级建议
```

所有 create 请求返回 `backtest_id`/`experiment_id`、attempt_no、是否复用、request/effective strategy version、snapshot_id、policy/split fingerprints 和状态；错误以稳定错误码返回。Gateway 的应用凭据只允许 research create/read/propose，不暴露 formal publish/update 路由。`/api/runs/backtest` 保持当前外部字段兼容，但只作为 adapter；新增 Agent 请求必须使用本节 API。

P0 垂直切片的完成标准不是“路由存在”，而是至少一个真实声明式策略能在真实 MySQL 上完成：预检 → 单日/区间 → 重放 → 候选/交易 → 失败归因 → baseline/candidate 样本外比较，并由一条 Agent contract smoke 以机器可消费响应完成调用。

## 13. 测试设计

### 13.1 测试金字塔

1. Domain unit：state machine、request upgrader、simulation、metrics。
2. Contract：Strategy、Factor、MarketSource、Auxiliary、Backtest Registry。
3. Coordinator integration：fake adapter/repository 下执行真实完整阶段。
4. MySQL integration：migration、atomic upsert、并发锁、真实 repository。
5. API smoke：真实应用启动、health、enqueue、run detail。

### 13.2 关键回归场景

- experiment 入队后由子进程执行，最终仍是 experiment。
- checkpoint 恢复不会重复推荐持久化。
- manifest identity 与 Signal identity 一致。
- 仅实现公开 MarketSourcePort 的 fake 能运行完整 market preparation。
- 新增 fixture Auxiliary Descriptor 不修改 Coordinator。
- migration 连续执行两次，第二次 no-op。
- checksum mismatch 阻止写路径并体现在 health。
- 两个连接并发 upsert 同一 natural key，最终一条记录。
- 单策略单日回测仅调用指定 descriptor，并保存 signal/no-signal trace，不执行其他 enabled 策略。
- 单策略区间回测按每个交易日独立检查 lookback、辅助数据和未来标签；日线/分钟线缺口必须返回可定位状态。
- Strategy/Factor 通过 `SignalDateDataReader` 请求未来日期立即失败；只有 Simulation 可通过 `OutcomeDataReader` 读取 policy 范围内未来行情。
- 修改/回填源表后，指定旧 snapshot_id 重放仍得到相同输入 hash 和结果；工件缺失/损坏必须失败，不能回退当前数据。
- 相同 request fingerprint 的普通并发请求只认领一个 active attempt；completed 可复用，failed/incomplete 后重试以及连续两次 force rerun 均创建递增 attempt 且不覆盖旧证据。
- 两个回测共享相同 signal date/code 时，候选和交易仍按 backtest_id 隔离；不得删除 `btrec_*` 或正式推荐。
- Experiment 创建后锁定 snapshot/policy/canonical split；拒绝 mismatch、重叠 split、candidate 冻结后变更和 OOS 泄漏。
- `chase_high`、`stop_loss`、`sector_decay`、`unknown` 可形成结构化归因；no-signal、数据缺口和 incomplete 不产生失败归因。
- 真实 Agent contract smoke 完成 preflight → run/replay → attribution → experiment comparison，且无法调用 formal 发布写路径。
- Experiment 比较必须有预先声明的 hypothesis/baseline/splits；只有独立样本外窗口满足样本数、胜率、盈亏比和最大回撤准入条件时才能标记 promotion_candidate。
- Coordinator golden-master 与一期推荐卡、审计和 projection 等价。

## 14. CI 设计

建议恢复 `.github/workflows/verify.yml` 并拆为：

1. `frontend-build`：安装锁定依赖并 build。
2. `python-unit`：compile/import + 不依赖 MySQL 的目标测试。
3. `architecture-check`：依赖规则、禁止私有 facade import、`git diff --check`。
4. `agent-gateway-contract`：真实策略 fixture 验证 preflight、snapshot replay、future-read rejection、attribution 和 frozen experiment contract。
5. `mysql-integration`：临时 MySQL service，执行 migration、artifact integrity、attempt concurrency、repository 和 coordinator integration。

`agent-gateway-contract` 与 `mysql-integration` 是 P0 必过门禁，不能因其他架构任务通过而豁免。本地真实 MySQL 验收使用现有连接，但不得输出连接 URL、用户名、密码和 token。

## 15. 迁移顺序与风险控制

1. 先固化行为基线并建立 migration runner/ledger，不包含 RunRequest 数据回填。
2. 定义 V1 RunRequest encoder/parser；以 nullable expand migration 新增 request 列，新 writer 双写后分批回填、追赶校验并切换读取。
3. 建立 StrategyDescriptor、Factor fingerprint 与纯 Simulation；一旦这些最小依赖稳定，立即开始 P0 Agent Research Gateway 垂直切片，不等待完整 Coordinator 拆分。
4. 先交付一个真实声明式策略的 snapshot/as-of reader/attempt repository/backtest/attribution/experiment 端到端路径，并通过真实 MySQL Agent contract smoke。
5. 建立 RunStateMachine/RunRepository，worker 切换为按 run_id 加载 request，随后删除 payload 重建逻辑。
6. 闭合 MarketSource 契约并建立 Auxiliary Orchestrator/原子 repository。
7. 抽出 Coordinator 阶段，逐阶段替换旧 `run_workbench`。
8. 将 overnight、stock skill、time-series 和旧 backtest API 迁移到已经验收的 P0 Runner/Gateway。
9. 删除 facade 私有出口和旧编排实现。
10. 恢复 CI，按业务库安全闸门完成真实 MySQL、Agent Gateway 和 API smoke。

主要风险及缓解：

| 风险 | 缓解措施 |
|---|---|
| request 回填错误导致历史运行不可读 | 分批回填、分组计数校验、保留旧 params_json |
| MySQL DDL 隐式提交 | 仅向前迁移、verify 后写 ledger、失败追加修复 migration |
| Coordinator 拆分改变业务顺序 | 阶段级 golden-master + 真实 coordinator 测试 |
| 原子 upsert 更新语义变化 | 每数据集显式 update policy + 重复/并发测试 |
| 策略版本变化影响统计 | execution_version 独立记录，不改历史结果 |
| 内部兼容层长期残留 | 每个迁移任务包含删除旧入口，最终架构检查禁止回归 |

## 16. 完成时目标状态

- Agent Research Gateway 是 self-improve T1/T2/T3 创建新研究证据的唯一 P0 入口，并已通过真实策略、真实 MySQL 垂直切片。
- Agent 可指定单一策略和单日/区间，重放不可变 snapshot，查询交易与失败归因，并执行锁定快照/成本/切分的样本外实验。
- 研究凭据不能修改 formal 策略或生产参数，回测数据不写入 Recommendation/Outcome。
- `workbench_service` 仅保留稳定 application facade/query，不含完整流水线。
- 任一 worker 仅凭 run_id 可恢复唯一 RunRequest。
- Strategy、Factor、MarketSource、Auxiliary 和 Backtest 都具有启动期可验证 Registry/Port。
- Domain simulation 不依赖 ORM。
- 所有 schema 变化可追踪且可在真实 MySQL 重复执行。
- CI 能阻止未来数据读取、snapshot 不可重放、attempt 覆盖、实验泄漏、隐藏接口、私有 facade 和顶层编排测试缺失重新出现。
