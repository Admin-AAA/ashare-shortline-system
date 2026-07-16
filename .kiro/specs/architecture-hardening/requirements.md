# Architecture Hardening（二期）需求文档

## Introduction

本文档定义 Architecture Hardening（二期）的架构加固需求：以稳定的 Agent 研究入口为 P0 核心交付，在不改变既有业务语义的前提下，建立可审计迁移、可重放回测、可信数据边界和可验证的生产编排能力。

## Background and Goals

一期 `architecture-deepening` 已完成交易日历、板规则、Signal、Factor、Strategy Registry、Market Source、Market Pool、Simulation、Backtest Result 与推荐运行子模块的初步拆分，并通过真实 MySQL 和目标回归测试验证了当前行为基本稳定。

二期目标不是继续机械拆文件，而是让关键架构边界真正闭合。**二期的 P0 核心交付是面向后续自我优化 Agent 的研究入口**：Agent 能以稳定 API 指定单一策略和单日/日期区间，完成防前瞻、可重放、可比较、可归因的回测实验，并以样本外证据验证胜率、盈亏比和回撤改进。Migration、Strategy/Factor 指纹、Simulation、数据快照、Repository 和 Coordinator 重构均服务于这一核心闭环，而不是与其并列的机械重构目标。

本期允许修改 MySQL 表结构，但必须采用可审计、可重复、非破坏性的迁移方式。禁止对业务数据库执行清库、重建、`DROP TABLE` 或 `TRUNCATE`。

## Scope

### In Scope

按业务优先级排序；实现仍须遵守依赖关系和迁移安全门禁：

1. **P0：建立自我优化 Agent 的统一研究入口**，使 Agent 能创建、重放、查询和比较“单一策略 + 单个交易日或日期区间”的回测与实验，并取得候选、交易、失败归因和样本外证据。
2. 建立统一、可复现的 Backtest 领域模型、Registry、Runner、Data Snapshot 和研究实验记录，为 P0 入口提供唯一执行内核。
3. 建立版本化 MySQL schema migration 机制和独立研究持久化，确保 P0 证据不会污染或覆盖 LIVE/formal 数据。
4. 建立唯一、类型化、可版本化的 `RunRequest` 契约。
5. 修复 `run_mode` 在入队、持久化、子进程和恢复过程中的信息丢失。
6. 消除命令式策略身份双源，并冻结 Factor 执行计划，使研究结果绑定真实策略/因子版本。
7. 将 outcome simulation 与 ORM/私有服务常量解耦，形成统一交易成本和结果计算。
8. 使 `MarketSourcePort` 与生产调用完全一致。
9. 将辅助数据描述符扩展为 refresh/persist/load/coverage 全链路扩展点，并使用 MySQL 批量原子 upsert。
10. 将推荐运行拆为薄 `RunCoordinator` 与可注入阶段服务。
11. 增加真实顶层编排、Agent 研究入口、迁移、架构门禁和 CI 验收。

### Non-Goals

1. 不重写前端交互或改变现有公开 API 的业务语义。
2. 不改变选股阈值、策略评分公式、推荐排序和风控口径，除非为修复已确认缺陷且有 golden-master 证据。
3. 不引入 Kafka、Celery、分布式事件总线或微服务拆分。
4. 不在本期替换 SQLAlchemy、FastAPI、MySQL 或现有子进程运行模型。
5. 不为旧的 Python 私有函数、下划线导出或内部 import 路径保留兼容层；仓内调用点必须同步迁移。
6. 不对业务数据库做破坏性回滚；迁移失败使用向前修复。

## Glossary

- **RunRequest**：推荐运行唯一输入契约，覆盖 API、调度器、队列、子进程和断点恢复。
- **RunCoordinator**：只负责运行阶段顺序、状态转换和错误策略的应用层编排器。
- **Port**：生产调用者唯一允许依赖的公开 Protocol。
- **Descriptor**：描述一个辅助数据集完整生命周期策略的不可变声明。
- **Execution Plan**：Factor Registry 完成依赖校验后冻结的确定性拓扑执行计划。
- **Migration Ledger**：记录已应用 schema migration 版本、checksum 和执行结果的数据库表。
- **BacktestRequest**：独立研究回测的唯一、不可变输入契约；必须精确绑定一项策略与一个日期选择器。
- **交易日（signal date）**：策略可见数据的截止交易日；单日回测只在该日生成信号，收益标签只能读取该日之后由执行/持有 policy 允许的行情。
- **数据截面（Data Snapshot）**：某次回测读取的表覆盖、watermark、行数、内容摘要、数据质量和缺口的不可变证据，不包含凭据。
- **研究实验（Experiment）**：假设、候选策略版本、基线、时间切分、准入标准和回测证据的持久化记录；实验结果不能自动修改 formal 策略、权重或预设。
- **失败归因（Failure Attribution）**：仅面向已成交且负收益/失败标签回测交易的类型化原因、证据引用、关联规则/因子、置信度和未知状态；无信号、数据缺口或未完成标签不属于失败归因。
- **Agent 研究入口（Agent Research Gateway）**：后续自我优化 Agent 创建预检、单策略回测、重放、实验对照和失败归因查询的 P0 应用层 API；它不授予 formal 发布权限。
- **业务库**：当前配置指向的 `ashare_workbench` MySQL 数据库。

## Traceability

### 一期残余问题追踪

| 来源问题 | 二期需求 | 设计落点 | 任务落点 |
|---|---|---|---|
| 命令式策略 manifest/实现身份双源 | 4 | §8 | S2 |
| MarketSourcePort 与生产隐藏方法不一致 | 5、11 | §10 | S3、S7 |
| AuxiliaryDataset 未覆盖完整生命周期、逐行非原子 upsert | 6、12 | §11 | S4 |
| `workbench_service` 仍是 God module、run_mode 跨异步边界丢失 | 2、3、10、12 | §6–§7、§13 | S1、S5 |
| Factor 图只在首次运行时校验 | 7 | §9 | S2 |
| Simulation 依赖 ORM/私有常量 | 8 | §12.1 | S6 |
| Backtest 家族未统一、指定 strategy_id 未真正限制执行范围、回测结果相互覆盖 | 9 | §12.2–§12.5 | S6 |
| golden-master 未覆盖真实编排接线 | 10 | §13 | S5、S7 |
| market_pool facade 暴露私有实现、CI 被删除 | 11 | §3、§14 | S7 |
| 交易日历/指标/板规则等重复定义 | 已由一期关闭，本期仅回归 | §13 | S7.5 |

版本化 migration、在线 DDL 安全闸门和运行可观测性属于二期新增治理能力，不是一期候选的简单延续。

## Requirements

### Requirement 1: 版本化数据库迁移

**User Story:** 作为维护者，我希望数据库结构能够随代码安全演进，从而避免启动时静默修改失败或依赖人工重建数据库。

#### Acceptance Criteria

1. WHEN 应用初始化数据库 THEN 系统 SHALL 在同一个专用、不归还连接池的 MySQL connection 上获取 migration lock、执行 ledger 检查与全部 migration，并在 finally 中释放 lock；`GET_LOCK` 返回 0 或 NULL 时必须失败。
2. WHEN migration 成功应用 THEN 系统 SHALL 在 `schema_migrations` 记录 version、description、checksum、applied_at 和 execution_ms。
3. IF 已记录 version 的 checksum 与代码不一致 THEN 系统 SHALL 停止后续 migration，并将数据库健康状态标记为 migration error。
4. WHEN 同一 migration 重复执行、在任一 DDL 步骤后故障重入或应用有多个实例并发启动 THEN 系统 SHALL 保持幂等，不重复修改 schema 或数据。
5. WHEN migration 失败 THEN 系统 SHALL 不记录成功 ledger，不吞掉异常，不继续开放依赖新 schema 的写路径。
6. THE migration framework SHALL 只通过受限 `MigrationContext` 暴露允许的 schema/data 操作，禁止 migration 获取 raw connection，并拒绝业务库 `DROP TABLE`、`TRUNCATE` 或无条件批量删除。
7. THE migration framework SHALL 支持新增列、调整兼容类型、创建表、创建索引和分批回填现有数据；每个操作必须声明可检查的 precondition/postcondition，不能安全重入的多步变更必须拆成独立版本。
8. WHEN 在真实 MySQL 上连续运行两次迁移 THEN 第二次 SHALL 为 no-op，且业务表行数与关键字段值保持不变。
9. THE existing `init_db` ad-hoc `_ensure_column`/`_ensure_index` 逻辑 SHALL 迁入有版本和测试的 migration，不再以 catch-and-ignore 作为成功语义；生产 schema 只由 migration runner 管理，`create_all` 仅用于不进入生产迁移流程的单元测试临时库。
10. BEFORE 业务库执行索引、UNIQUE 或类型变更 THEN 系统 SHALL 只读预检表规模、自然键重复、磁盘余量和 metadata-lock blocker，并设置有限 lock wait/执行时限；预检失败必须停止且不得自动删除冲突数据。

### Requirement 2: 唯一且可版本化的运行请求契约

**User Story:** 作为运行系统维护者，我希望所有入口共享同一个请求模型，从而避免字段在异步边界或恢复过程中丢失。

#### Acceptance Criteria

1. THE system SHALL 定义不可变、类型化的 `RunRequest`，覆盖 run_mode、data_source、preset、skills、score_line、risk、review、refresh 和 trade_date override 等运行输入。
2. WHEN API、调度器或内部任务创建运行 THEN 系统 SHALL 只通过同一个 parser/validator 构造 `RunRequest`。
3. WHEN 运行入队 THEN 系统 SHALL 在启动 worker 前持久化 canonical request JSON 和 request_schema_version。
4. WHEN 子进程或断点恢复运行 THEN 系统 SHALL 从持久化的 `RunRequest` 恢复，不从分散列和默认值重新拼装业务身份。
5. IF `run_mode=experiment` 或 `run_mode=backtest` THEN 任意后续阶段 SHALL NOT 将其覆盖为 `formal`。
6. WHEN 请求版本升级 THEN 系统 SHALL 通过显式 upgrader 将旧 request schema 转换到当前版本，并测试转换结果。
7. THE `recommendation_runs` table SHALL 先以 nullable expand migration 新增 `request_schema_version`、`request_json` 和 `execution_version`；只有成功回填且校验一致的行才能设置 schema version，旧 writer 切换前不得声明全局 DEFAULT 1。
8. THE frequently queried columns（run_mode、status、trade_date、started_at）SHALL 继续使用独立列和索引，不要求查询 JSON。

### Requirement 3: 显式运行状态机与薄编排器

**User Story:** 作为功能开发者，我希望新增运行阶段时只扩展一个阶段服务和状态定义，而不是修改数千行中央服务。

#### Acceptance Criteria

1. THE system SHALL 定义有限的 RunPhase 和 RunStatus 转换表，逐条声明触发者、前置状态、目标状态、持久化原子性和恢复条件；非法转换必须立即失败。
2. THE `RunCoordinator` SHALL 只负责阶段顺序、checkpoint、heartbeat、状态转换、错误分类和补偿调用。
3. THE market preparation、auxiliary refresh、feature pipeline、strategy evaluation、review、recommendation assembly 和 persistence SHALL 通过显式依赖注入的服务/Port 提供。
4. WHEN 任一阶段失败或 worker spawn 失败 THEN Coordinator SHALL 按转换表持久化失败/中断阶段、稳定错误码和可读消息，并释放可计数的 Agent/DB/临时资源 lease。
5. WHEN 从 checkpoint 恢复 THEN Coordinator SHALL 基于原子持久化的 last_committed_phase 与 side_effect_key 从下一合法阶段继续，且不得重复已确认持久化的副作用。
6. THE coordinator module SHALL NOT 直接依赖 Tushare/OpenAI/Agno 实现或具体 ORM model；这些依赖由 adapter/repository 隔离。
7. THE existing `workbench_service` SHALL 收缩为公开应用服务 facade 和查询入口，不再拥有完整流水线实现。
8. WHEN 新增一个不改变既有数据结构的运行阶段 THEN 修改范围 SHALL 限于 phase 定义、阶段实现、装配和测试，不需要修改无关阶段。

### Requirement 4: 策略身份单一来源

**User Story:** 作为策略作者，我希望策略 ID、名称和版本只有一个权威来源，从而保证启用状态、结果归因和绩效统计一致。

#### Acceptance Criteria

1. THE `StrategySkill`/manifest SHALL 是 strategy_id、name、mode 和声明版本的唯一业务身份来源。
2. THE imperative Python implementation SHALL 只提供策略计算行为，不重复声明可漂移的业务 ID 和名称。
3. WHEN Registry 装配实现 THEN 系统 SHALL 注入不可变 `StrategyDescriptor`，并拒绝不满足 Protocol 的实现。
4. THE effective `version_id` SHALL 包含 manifest/contract fingerprint 与 implementation fingerprint，使行为变化可追踪。
5. IF manifest 指向不存在或不兼容的 impl_class THEN 应用 SHALL 在策略 bootstrap 时失败，而不是运行到候选计算时才失败。
6. WHEN 策略返回 Signal THEN Signal.strategy_id SHALL 始终等于 Registry key。
7. THE tests SHALL 覆盖身份 mismatch、实现不存在、版本变化和声明式/命令式统一分发。

### Requirement 5: 完整且可信的 MarketSourcePort

**User Story:** 作为数据源开发者，我希望只实现公开 Port 就能接入生产路径，而不需要猜测隐藏方法。

#### Acceptance Criteria

1. THE `MarketSourcePort` SHALL 声明生产路径实际需要的全部 quote 和 daily-bar 能力。
2. THE daily-bar 请求 SHALL 使用类型化 request/quote reference 表达 code、exchange、date range 和 fallback 所需信息。
3. THE production caller SHALL NOT 调用 Protocol 外的 `fetch_bars_for_quote`、`getattr` 能力探测或 adapter 私有方法。
4. WHEN 新 adapter 完整实现 Port THEN 它 SHALL 可直接通过 market pool 和 coordinator contract tests。
5. THE multi-source 与 in-memory adapter SHALL 具有一致的错误、空结果和部分失败语义。
6. WHEN 单只股票拉取失败 THEN 是否降级、跳过或中止 SHALL 由显式 policy 决定并写入数据审计。

### Requirement 6: 辅助数据全生命周期扩展点

**User Story:** 作为数据工程开发者，我希望增加普通辅助数据集时只注册描述符和数据模型，而不修改中央运行编排。

#### Acceptance Criteria

1. THE `AuxiliaryDatasetDescriptor` SHALL 声明 dataset key、model/repository、fetcher、normalizer、loader、coverage policy、refresh policy、natural key 和 `freshness_field`/`freshness_extractor`。
2. WHEN 注册普通辅助数据集 THEN 通用驱动 SHALL 能完成 refresh → normalize → persist → load → coverage audit 全链路。
3. THE `RunCoordinator` SHALL 只调用 `AuxiliaryDataOrchestrator`，不得逐个硬编码 concept、dragon-tiger、capital-flow、news 等数据集。
4. THE Registry SHALL 在 bootstrap 时校验 key 唯一、自然键非空、loader 输出 key 和依赖完整性并冻结。
5. THE MySQL repository SHALL 使用批量原子 upsert，禁止逐行 SELECT 后 INSERT/UPDATE。
6. WHEN 同一批次重复导入或两个 worker 并发导入相同自然键 THEN 最终 SHALL 只有一条逻辑记录且字段符合确定性更新策略。
7. WHEN 数据集具有特殊拉取或加载逻辑 THEN 它 SHALL 通过 strategy/handler 注入，不得绕过统一审计和持久化入口。
8. THE persistence layer SHALL 对 batch size、attempted/affected/failed rows、execute count 和 duration_ms 输出结构化指标；除非数据集使用显式 staging/查询方案，否则不得把 MySQL rowcount 推断成精确 inserted/updated 数。
9. `institutional_survey` SHALL 永久作为审计专用、不可发布的辅助工件：因供应商未提供可核实的完整性合同，它不属于必须发布的生产 inventory，不得驱动 feature、预过滤、策略、推荐或任何 feature view。系统只可经显式类型化审计边界，在调用方拥有的事务内捕获全字段原始响应并持久化 immutable success/failure snapshot；该边界不得发布 active pointer、读取 pointer 或 legacy 数据，也不得推断 provider cap、分页、`has_more` 或 `count` 语义。

### Requirement 7: Factor 启动期执行计划

**User Story:** 作为因子开发者，我希望依赖错误在应用启动时暴露，而不是等到第一轮推荐才失败。

#### Acceptance Criteria

1. WHEN Factor 注册完成 THEN bootstrap SHALL 立即校验重复 key、重复 output owner、悬空依赖和循环依赖。
2. WHEN 校验成功 THEN Registry SHALL 生成并冻结确定性 ExecutionPlan。
3. AFTER Registry frozen THEN 生产代码 SHALL NOT 动态注册或覆盖 Factor。
4. THE factor registration SHALL 使用显式 bootstrap，不依赖不可见的 import side effect。
5. THE tests SHALL 使用独立可变 Registry 构造 fixture，不共享生产全局状态。

### Requirement 8: 纯 Simulation 领域边界

**User Story:** 作为回测维护者，我希望收益模拟可以脱离数据库独立测试和复用。

#### Acceptance Criteria

1. THE outcome simulation SHALL 接收不可变 RecommendationInput、BarInput 和 SimulationPolicy，不接收 ORM entity 或 Session。
2. THE SimulationPolicy SHALL 显式包含手续费、滑点、止损止盈、持有期和价格约束。
3. THE simulation module SHALL NOT 反向导入 outcome_service 私有常量。
4. THE repository/application layer SHALL 负责 ORM ↔ DTO 映射与结果持久化。
5. FOR ALL 固定输入序列，重复模拟 SHALL 返回完全一致的结果。
6. THE existing outcome golden cases SHALL 在迁移后保持等价。

### Requirement 9: 统一 Backtest 领域模型与分发

**User Story:** 作为策略研究开发者或后续自我优化 Agent，我希望通过二期 P0 研究入口指定一个策略和单日/日期区间，复用统一 Runner、Trade、Result、重放和实验比较能力，从而持续提高胜率、盈亏比并降低回撤，而不是复制 service/API/统计逻辑或直接改写 formal 配置。

#### Acceptance Criteria

1. THE system SHALL 定义统一的 `BacktestRequest`、`Trade`、`EquityPoint`、`BacktestMetrics` 和 `BacktestResult`；独立策略研究 Backtest 不等同于 recommendation `RunRequest.run_mode=backtest`，后者仅表示推荐管线回放并通过 adapter 委托 Backtest Runner。
2. THE Backtest Registry SHALL 以 strategy_type/version 注册实现并在 bootstrap 时校验重复和缺失实现。
3. THE Backtest Runner SHALL 负责数据准备、策略执行、成本 policy、指标计算和结果投影的稳定骨架。
4. THE overnight、stock skill 和 time-series backtest SHALL 迁移到统一领域结果，允许各自保留专用输入 adapter。
5. WHEN 新增回测实现 THEN 不得复制通用收益率、回撤、胜率、交易次数和展示投影计算。
6. THE public API response SHALL 保持现有字段语义；内部 legacy result adapter 在调用点全部迁移后删除。
7. THE `BacktestRequest` SHALL 要求精确的 `strategy_id`，并且日期选择器必须二选一：`signal_date`（单个交易日）或 `[start_date, end_date]`（含端点区间）；不得将空 strategy、多个 strategy 或模糊的 `skill_id` 回退为“全部 enabled 策略”。
8. WHEN 执行单策略单日回测 THEN Runner SHALL 只装配该 `StrategyDescriptor`、其声明依赖的 Factor/数据集和显式全局执行 policy；输出该日的 universe、每个候选的 signal/no-signal 原因、过滤轨迹、生成交易和不可交易原因。
9. WHEN 执行单策略日期区间回测 THEN Runner SHALL 对每个有效 signal date 独立执行同一策略版本和同一 execution policy，并聚合交易、日度权益曲线、胜率、扣费后收益、盈亏比、最大回撤、样本数和覆盖率；无信号/数据不足日期必须单列，不得计为亏损或成功交易。
10. BEFORE 回测执行 THEN `DataReadinessPort` SHALL 按策略声明的 lookback、daily/minute bars、辅助数据、基准和未来 label horizon 生成覆盖报告；缺失关键输入时 SHALL 返回 `insufficient_data`/`incomplete_evaluation` 和可定位缺口，不得外网补数、静默降级或输出可用于晋级的绩效结论。
11. THE Runner SHALL 在创建回测前持久化 canonical request、StrategyDescriptor/effective version、Factor ExecutionPlan fingerprint、execution/cost policy fingerprint、代码 revision 和 Data Snapshot；相同完整指纹的 completed run 默认复用，`force_rerun=true` 必须创建具有 parent reference 的新 run。
12. THE system SHALL 将回测交易和候选审计持久化到独立 `backtest_trades`/`backtest_candidates`（或等价专属表），以 `backtest_id` 隔离；不得删除或复用其他回测的 `btrec_*`、Recommendation 或正式 Outcome 记录。
13. THE API SHALL 提供类型化的创建、预检、详情、交易明细、候选审计和基线比较接口；保留 `/api/runs/backtest` 作为兼容 adapter，但其 strategy_id/日期语义必须映射到 `BacktestRequest`，不再使用裸 `dict` 作为领域输入。
14. WHEN Agent 创建策略/因子改动的研究实验 THEN 请求 SHALL 记录 hypothesis、baseline_backtest_id、candidate strategy/version、训练/验证/样本外时间切分、预先声明的比较指标和最小样本数；没有独立样本外证据的结果 SHALL 标记为 exploratory，不能作为晋级结论。
15. THE Experiment service SHALL 比较候选与基线在相同 Data Snapshot、执行成本和日期切分下的胜率、盈亏比、平均净收益、最大回撤、交易数与覆盖率，并输出显著性/置信区间或“样本不足”的明确状态；不得只按胜率排序或以零交易宣称改善。
16. THE Agent-facing experiment API SHALL 只创建/读取 experiment 和 candidate proposal；任何启用、降权、修改 formal 策略、预设或生产参数的动作 SHALL 要求独立的人工/显式发布工作流，且不得由回测完成自动触发。
17. THE tests SHALL 覆盖单策略单日、单策略区间、多策略拒绝、未知/禁用策略、无信号、日线缺口、分钟线缺口、未来标签不足、重复请求复用、force rerun、失败重试、并发请求、回测隔离和基线比较。
18. THE Runner SHALL 通过类型隔离的 `SignalDateDataReader` 与 `OutcomeDataReader` 读取数据：策略/Factor 只能访问 `trade_date <= signal_date` 的输入，未来行情只能由 Simulation/Outcome 层读取；任何策略侧未来日期访问必须以稳定错误码失败。
19. THE Data Snapshot SHALL 具有稳定 `snapshot_id`，记录实际查询分区的版本/行数/内容摘要，并持久化每个 signal date 的可重放输入工件；在源表后续回填或修正后，系统 SHALL 能使用原工件重放旧 snapshot 或明确返回 artifact 不可用，不得悄然使用新数据冒充旧结果。
20. THE ExperimentPlan SHALL 冻结 baseline 的 snapshot_id、execution policy fingerprint 和 canonical split（实际交易日集合及 hash）；candidate request 必须继承它们，repository SHALL 拒绝 snapshot/policy/split mismatch、时间切分重叠、训练/验证后修改 candidate 参数及在样本外结果可见后重写切分。
21. THE system SHALL 以 request fingerprint + attempt model 管理幂等：仅成功 completed attempt 可被默认复用；force rerun、failed/incomplete 后重试和并发相同请求必须生成或原子认领独立 attempt，保留 parent/attempt 关系且不覆盖任何历史证据。
22. THE system SHALL 为已成交且负收益/失败标签的 backtest trade 保存类型化 `FailureAttribution`（reason_code、evidence references、rule/factor reference、confidence、unknown state）；无信号、数据缺口和不可完成标签不得计为失败归因，且 API SHALL 提供按 strategy/version/split 聚合的归因结果。
23. THE Agent Research Gateway SHALL 是二期 P0 应用入口；在广泛迁移全部历史回测家族前，系统 SHALL 先交付至少一个真实声明式策略的端到端垂直切片，覆盖 preflight → snapshot → 单日/区间执行 → candidate/trade → attribution → experiment comparison，并以同一稳定契约供后续策略接入。
24. WHEN 二期进入最终验收 THEN `self-improve` 的 T1/T2/T3 研究流程 SHALL 只通过 Agent Research Gateway 创建新回测和实验，不再直接拼接旧 `/api/runs/backtest` payload、查询 `btrec_*` 作为新证据或修改数据库研究记录；旧入口仅承担外部兼容。

### Requirement 10: 真实顶层编排与 MySQL 验收

**User Story:** 作为维护者，我希望测试覆盖真实架构接线，而不仅是手工组合内部纯函数。

#### Acceptance Criteria

1. THE test suite SHALL 至少有一条直接执行 `RunCoordinator` 的确定性测试，覆盖 prepare → evaluate → review → persist 完整路径。
2. THE coordinator test SHALL 使用 in-memory market source、fake review executor 和可回滚 repository，不访问外网。
3. THE tests SHALL 覆盖 formal/experiment/backtest 的入队、子进程恢复、checkpoint 恢复和最终 run_mode 不变。
4. THE migration tests SHALL 在真实 MySQL 专用测试 schema 或 CI 临时 MySQL 中覆盖旧结构 → 新结构、重复执行、checksum mismatch 和并发锁。
5. THE atomic upsert tests SHALL 在真实 MySQL 覆盖重复批次和并发自然键写入。
6. WHEN 运行二期目标回归 THEN 一期 golden-master、现有 API 投影和推荐结果 SHALL 保持等价。
7. THE test harness SHALL NOT 对业务库执行 drop/truncate；CI 可使用随 Job 销毁的临时 MySQL 实例。

### Requirement 11: 架构依赖与兼容面收口

**User Story:** 作为代码审查者，我希望依赖规则能够自动检查，防止重构后再次出现跨层私有调用。

#### Acceptance Criteria

1. THE project SHALL 定义并自动验证依赖方向：API/scheduler 可依赖 application；application 可依赖 domain/engine 与 application-owned Ports；adapters/repositories 实现 Ports；domain/engine 不得依赖 application、ORM、adapter 或供应商。
2. THE market_pool facade SHALL 不再 re-export `_persist_*`、`_load_*`、`_fetch_*`、`_upsert_by_key` 等私有实现。
3. THE tests SHALL 不再 monkeypatch 已删除的 service 私有函数，而应 patch 注入的 Port 或 fake dependency。
4. THE changed Python code SHALL 通过 compile/import、目标 pytest、`git diff --check` 和架构依赖检查。
5. THE external HTTP API SHALL 保持兼容；内部 Python import 路径不要求兼容。
6. THE CI SHALL 恢复前端 build，并增加纯 Python 测试、架构检查和 MySQL integration job。

### Requirement 12: 可观测性、性能与交付质量

**User Story:** 作为运维和维护人员，我希望能快速判断运行卡在哪个阶段、迁移是否成功以及重构是否引入性能回退。

#### Acceptance Criteria

1. THE coordinator SHALL 为每个 phase 输出 run_id、phase、status、duration_ms 和 stable_error_code 的结构化日志。
2. THE health endpoint SHALL 区分 database unavailable、migration error 和 application degraded。
3. THE migration runner SHALL 输出 version、checksum、execution_ms 和 applied/no-op 状态，但不得输出凭据或完整敏感 payload。
4. FOR N rows and batch_size B，THE auxiliary batch persistence SHALL 满足数据库 batch execute 次数 `≤ ceil(N/B)`；事务、预检和审计语句另以固定常数 K 单独计数并在测试中声明。
5. WHEN 在固定环境完成预热后对同一 fixture 各执行至少 5 次推荐 coordinator THEN `median_v2 SHALL <= 1.20 * median_baseline`；超过阈值即验收失败，原因只能记录风险而不能替代通过。
6. THE final delivery SHALL 包含需求追踪矩阵、真实 MySQL 验证记录、测试结果和未解决风险。

## Definition of Done

本 Spec 只有同时满足以下条件才可标记完成：

1. Agent Research Gateway 的真实策略垂直切片首先完成，并能由后续 self-improve T1/T2/T3 稳定调用。
2. 需求 1–12 的其余验收标准均有代码或测试证据。
3. `run_mode` 跨 API、队列、子进程、checkpoint 和持久化保持一致。
4. `workbench_service` 不再承载完整推荐流水线。
5. Strategy 和 MarketSource 不存在双重或隐藏契约。
6. 新增普通辅助数据集不需要修改 Coordinator。
7. MySQL migration、原子 upsert 和单策略回测专属表在真实 MySQL 验证通过。
8. 真实 Coordinator 与单策略回测测试能够发现接线错误。
9. CI 恢复并覆盖前端、纯 Python、架构规则和 MySQL integration。
10. 回测实验可重放、防前瞻、隔离且不会自动改变 formal 生产策略。
11. 不对业务数据库执行 destructive migration，不提交凭据。
