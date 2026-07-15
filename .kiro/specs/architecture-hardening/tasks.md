# Architecture Hardening（二期）实施任务

> 任务按依赖顺序执行，但业务优先级以 **Agent Research Gateway（P0）** 为最高：完成 0.2–0.5（migration）、1.1/1.6（request 与 execution version 契约）、2.1–2.3（StrategyDescriptor 与 Factor 指纹）后，立即顺序启动 S6.1–S6.6 的真实策略垂直切片，不等待 S1 其余状态机/推荐回填任务或 S3–S5 全部重构结束。每个阶段完成后必须先通过该阶段验证，再进入依赖它的任务。
>
> 数据库约束：允许新增/调整 MySQL 表、列、索引和约束；禁止在业务库执行 `DROP TABLE`、`TRUNCATE`、清库或重建。迁移采用 forward-only 修复。

## S0 — 基线与数据库迁移地基

> **当前进度（2026-07-14）**：0.1–0.3 已完成；0.4 的预检、结构签名、受限 DDL 和 V003–V005 版本实现已提交准备，但真实 MySQL 安全证据仍被权限阻断。SQLite 针对性验证为 `14 passed, 21 warnings`，未执行 V003–V005 DDL，也未写入其 ledger。
>
> **真实 MySQL 阻断**：`metadata_lock_waits` 因应用账户缺少 `performance_schema.metadata_locks` 的 `SELECT` 权限而为 `UNKNOWN`；`long_transactions` 因缺少全局 `PROCESS` 权限而为 `UNKNOWN`。严格 preflight 要求所有检查为 `pass`/`not_applicable`，不得将 `UNKNOWN` 降级为通过。下一会话必须先取得正向安全证据，再运行 `run_migrations(engine)`、二次 no-op 和兼容脚本。

- [x] 0.1 固化二期行为和性能基线
  - 记录当前分支、目标测试集合、真实 MySQL 版本和 `/api/health` 结果。
  - 保存一期 coordinator 相关 golden fixture、formal/experiment/backtest 运行计数和关键 API 投影样本。
  - 固定机器/依赖/fixture，预热后至少执行 5 次并记录中位耗时。
  - 不把数据库 URL、凭据、token 或真实敏感 payload 写入文件。
  - _需求：10.6, 12.5, 12.6_

- [x] 0.2 建立受限 migration contract 与发现机制
  - 新建 `app/db/migrations`，定义 `Migration`、可重入 `MigrationOperation` 和不暴露 raw connection 的 `MigrationContext`。
  - migration 按稳定 version 排序；重复/非法 version 或 checksum 冲突在启动期失败。
  - operation 声明 precondition/postcondition；不能安全重入的多步 DDL 拆成独立版本。
  - 只允许 context 白名单操作，拒绝业务库 DROP/TRUNCATE/无条件批量删除。
  - _需求：1.3–1.7_

- [x] 0.3 实现 MySQL migration runner、V000 baseline 和 ledger
  - V000 负责新库基础结构与已有库结构识别；生产 schema 只由 runner 管理。
  - runner 用同一专用 connection 完成 GET_LOCK、ledger、全部 migration、RELEASE_LOCK；0/NULL lock 结果立即失败。
  - 创建 `schema_migrations`，实现 applied/no-op/checksum-mismatch/failure 结果和结构化日志。
  - migration 失败不得写成功 ledger，且必须传播到应用健康状态。
  - `create_all` 仅保留给不进入生产 migration 流程的单元测试临时库。
  - _需求：1.1–1.5, 1.9, 12.2, 12.3_

- [x] 0.4 建立 schema inventory 与在线 DDL 安全闸门（代码完成；真实 MySQL 验收阻断）
  - 枚举当前 `_ensure_column`、`_ensure_column_type`、`_ensure_index`、`_migrate_minute_kbars` 涉及的表/列/索引及其目标版本，不使用“本期涉及”模糊范围。
  - 实现表规模、磁盘余量、metadata-lock blocker、长事务和 natural-key 重复的只读预检。
  - 设置有限 lock wait/执行时限；UNIQUE/索引/类型调整每项一个 migration。
  - 发现重复键只输出冲突报告并停止，不自动删除；不复制 `_migrate_v2` destructive drop。
  - _需求：1.6, 1.9, 1.10_

- [x] 0.5 验证 migration 地基和故障重入（SQLite 已通过；真实 MySQL 验收待权限补齐）
  - 单测排序、checksum、失败 ledger、白名单操作和禁用 SQL。
  - 真实 MySQL 专用 schema/CI 临时 MySQL 覆盖 baseline、连续执行两次、并发 lock、checksum mismatch。
  - 对多 operation fixture 在第 k 步注入故障，重跑后只产生一条成功 ledger 且 schema/data 正确。
  - 验证 migration error 可从 health 区分于 database unavailable。
  - _需求：1.4, 1.5, 1.8, 10.4, 10.7, 12.2_

## S1 — RunRequest、持久化和状态机

- [x] 1.1 定义类型化 `RunRequest` 与版本升级器
  - 定义 RunMode、DataSourceId、SkillSelection 和不可变 RunRequest。
  - 实现统一 parser、validation、canonical JSON、V1 schema 和旧版本 upgrader。
  - 明确默认值和字段边界，禁止凭据进入 request JSON。
  - API、Scheduler、CLI/internal task 共用 parser。
  - _需求：2.1, 2.2, 2.6_

- [x] 1.2 执行 request schema expand、双写、回填与 cutover
  - 先以 nullable migration 新增 `request_schema_version`、`request_json`、`execution_version`，不设置 DEFAULT 1。
  - 新 writer 双写独立列和 canonical JSON；分批回填旧行并追赶验证。
  - 只有 JSON 可解析且 run_mode/关键列一致的行才设置 schema version。
  - 保留旧 `params_json` 供历史读取，本期不删除列；worker 切换前验证行数和 mode 分组计数不变。
  - _需求：1.7, 2.3, 2.7, 2.8_

- [x] 1.3 建立显式 RunStateMachine 与 RunRepository
  - 逐条定义 transition actor、前置/目标状态、终态、恢复条件和稳定错误码。
  - 覆盖 spawn failure、heartbeat timeout、cancel、interrupted resume 和 completed/failed/cancelled 终态。
  - Repository 使用 compare-and-set，并原子写 `(last_committed_phase, side_effect_key, heartbeat_at)`。
  - 失败测试用可计数 fake lease 断言 Agent/DB/临时资源全部释放。
  - _需求：3.1, 3.4, 3.5, 12.1_

- [x] 1.4 重构入队与子进程边界
  - 顺序固定为 validate → persist queued run/request → commit → spawn worker(run_id)。
  - spawn 失败按状态机落为稳定失败状态。
  - queue/subprocess 只传 run_id；worker 从 Repository 读取并升级 canonical RunRequest。
  - 删除子进程从 `params_json` 重新拼 payload 的路径。
  - _需求：2.3, 2.4, 3.1, 3.5_

- [x] 1.5 修复 `run_mode` 全生命周期
  - 删除 `_persist_run_start`、`run_workbench` 等阶段中的 `run_mode="formal"` 硬编码覆盖。
  - 保证 formal、experiment、backtest 从创建、入队、执行、恢复到完成始终一致。
  - 对数据库列与 request.run_mode 做写入前一致性断言。
  - _需求：2.4, 2.5, 2.8_

- [x] 1.6 定义 `ExecutionVersionBuilder` 契约
  - 本阶段只定义 builder 输入/输出和 nullable 持久化，不提前依赖尚未产生的 Strategy/Factor fingerprint。
  - execution version 只用于归因和审计，不改变筛选逻辑。
  - _需求：2.7, 4.4, 7.2_

- [x] 1.7 验证 RunRequest、状态机与持久化切换
  - 测试三个 run_mode 的 API/调度器创建、入队、子进程加载和 checkpoint 恢复。
  - 测试 V1 升级、非法请求、列/JSON mismatch、非法转换、spawn failure 和资源释放。
  - 真实 MySQL 验证 queued row 在 worker 启动前已持久化，回填重复执行为 no-op。
  - _需求：2.2–2.8, 3.1, 3.4, 3.5, 10.3_

## S2 — Strategy 身份与 Factor 启动计划

- [x] 2.1 建立唯一 `StrategyDescriptor`
  - manifest/StrategySkill 成为 strategy_id、name、mode、declared version 的唯一来源。
  - Descriptor 包含 manifest hash、implementation hash 和 effective version。
  - Registry key、Signal.strategy_id、PresetSkill 和绩效归因统一使用 descriptor.strategy_id。
  - _需求：4.1, 4.3, 4.4, 4.6_

- [x] 2.2 简化命令式策略实现协议
  - imperative implementation 只提供 evaluate/可选 universe hook，不重复声明业务 ID/name。
  - 装配器注入 descriptor 并将 StrategyDecision 转为 Signal。
  - bootstrap 时验证 impl_class 存在、可构造且满足 Protocol。
  - 同步迁移现有样例策略和 fixture，删除双身份兼容代码。
  - _需求：4.2, 4.3, 4.5_

- [x] 2.3 显式 bootstrap 并冻结 Factor ExecutionPlan
  - 去掉依赖 import side effect 的生产注册。
  - 注册结束后校验重复 key、output owner、悬空依赖和循环。
  - 生成确定性拓扑 plan、fingerprint 并冻结 Registry。
  - 生产冻结后禁止动态注册；测试使用独立 Registry。
  - _需求：7.1–7.5_

- [x] 2.4 装配并持久化 `execution_version`
  - 将应用版本、Strategy descriptor fingerprints、冻结 Factor plan fingerprint 和关键 policy fingerprint 注入 `ExecutionVersionBuilder`。
  - 对新运行持久化非空稳定版本；历史未运行记录允许保持 NULL。
  - 测试任一策略实现、manifest、Factor plan 或关键 policy 变化都会改变版本。
  - _需求：2.7, 4.4, 7.2_

- [x] 2.5 验证 Strategy/Factor 契约
  - 覆盖身份 mismatch、实现不存在、实现 hash 变化、声明式与命令式统一分发。
  - 覆盖 Factor bootstrap 失败、冻结后注册失败、顺序稳定和 fixture 隔离。
  - 保持一期 Signal/Factor golden-master 等价。
  - _需求：4.5–4.7, 7.1–7.5, 10.6_

## S3 — MarketSourcePort 闭合

- [x] 3.1 定义类型化行情请求与批次结果
  - 定义 QuoteRef、QuoteRequest、DailyBarRequest、failure policy 和 per-code audit/error。
  - 明确空结果、单只失败和整体失败语义。
  - _需求：5.1, 5.2, 5.5, 5.6_

- [x] 3.2 统一生产 Port 和 adapters
  - `MarketSourcePort` 声明生产实际需要的全部能力。
  - multi-source、Tushare、in-memory adapter 实现同一 Port。
  - 将 `fetch_bars_for_quote` 降为 adapter 私有 helper 或删除。
  - _需求：5.1–5.5_

- [x] 3.3 迁移所有调用点
  - market pool、data remediation、feature preparation 和 coordinator 只依赖 Port。
  - 删除 Protocol 外调用、`getattr` 能力探测和私有 adapter import。
  - _需求：5.3, 5.4, 11.1_

- [x] 3.4 验证 MarketSource contract
  - 用只实现公开 Port 的 fake 运行 market preparation contract test；真实 Coordinator 接线统一在 5.6 验收。
  - 参数化覆盖 success、empty、partial、fatal 四类状态、异常与 audit 字段。
  - 确认没有调用方引用 `fetch_bars_for_quote`。
  - _需求：5.4–5.6, 10.2_

## S4 — 辅助数据全链路 Registry 与 MySQL 原子写入

> **当前状态（2026-07-15，阻断已修复）**：S4 的 4.1–4.5 已完成。13 个 state natural-key UNIQUE migration（V006–V021）已进入正常生产 discovery 并保持受限 preflight/no-delete/no-drop 契约；`institutional_survey` 的 active-pointer publish/read 在 core repository 与 reader 边界均被拒绝，且 audit capture 仅接受调用方已开启的事务。默认生产 Registry/composition/application/workbench/script inventory 精确为 13 个 state atomic-upsert 数据集加 4 个可发布 immutable event 数据集：`news_announcement`、`block_trade`、`share_float`、`holder_trade`（共 17 项）。默认执行绝不请求 `stk_surv`，也不会产生 institutional-survey active-pointer 错误。
>
> **已批准的审计边界**：`institutional_survey` 永久 audit-only，因缺少权威 provider 完整性合同而不属于必须发布的生产 inventory，不能驱动 feature、预过滤、策略或推荐。它只能通过显式类型化审计入口、调用方拥有的事务和注入的 raw provider 捕获全字段响应，持久化 immutable success/failure snapshot；该入口没有 active-pointer 发布/读取、feature view 或 legacy fallback 能力，也不推断 cap、分页、`has_more` 或 `count` 语义。空 envelope 仅在 `items` 为空时安全；非空 envelope 仍要求完整声明 schema，重复 event 行保持其 multiplicity。
>
> **S4 验证证据**：无网络回归覆盖 17 项默认 inventory、冻结 registry、无 `stk_surv` 调用、无 survey feature/pointer error，以及显式 audit-only 成功/失败快照、无 pointer/feature/legacy fallback 和 rollback 无残留。真实 MySQL rollback-only 验证 V024 结构、4 个默认 event snapshot 与 13 个 state 的既有保障，并在同一调用方事务内验证 survey 审计成功/失败快照不发布 pointer、回滚后无残留。

- [x] 4.1 定义 `AuxiliaryDatasetDescriptor` 与 Registry
  - descriptor 声明 key、natural key、fetcher、normalizer、repository、loader、coverage、refresh policy、freshness_field/freshness_extractor。
  - bootstrap 校验 key 唯一、自然键、handler、依赖、输出 key 和 freshness 配置后冻结。
  - _需求：6.1, 6.4_

- [x] 4.2 建立 `AuxiliaryDataOrchestrator`
  - 通用驱动执行 refresh → normalize → persist → load → coverage audit。
  - 支持普通 descriptor 和显式 custom handler，但两者共享审计和错误模型。
  - 返回类型化结果，不把数据集特有分支泄漏给 Coordinator。
  - _需求：6.2, 6.3, 6.7_

- [x] 4.3 建立 MySQL batch atomic upsert repository
  - 使用 MySQL dialect `on_duplicate_key_update` 和可配置 batch size。
  - 每个数据集先执行重复键预检，再用独立版本 migration 新增/核验 natural-key UNIQUE constraint；发现冲突停止且不删除。
  - 每数据集声明逐字段 overwrite/keep/latest policy。
  - 输出 batch size、attempted/affected/failed rows、execute count 和 duration_ms；不得从普通 rowcount 推断精确 insert/update。
  - _需求：1.10, 6.5, 6.6, 6.8, 12.4_

- [x] 4.4 按显式 inventory 迁移现有辅助数据集
  - 生产 Registry/ORM inventory 已评审并冻结为 13 个 state atomic-upsert 数据集与 4 个可发布 immutable event snapshot 数据集；`institutional_survey` 已按用户批准决定独立为永久 audit-only artifact，不计入生产发布 inventory。
  - 每个生产数据集已完成适用的重复键预检、UNIQUE migration 或 snapshot 发布保障、字段 update policy、descriptor 接线和集成测试；审计 artifact 仅验证 raw success/failure snapshot 持久化、无 pointer、无 view/fallback 与 rollback。
  - 已删除手写 persist wrapper、中央 loader 枚举和重复 coverage 分支；默认路径不请求 `stk_surv`。
  - Coordinator 最终只按请求/策略调用 orchestrator，不硬编码数据集，该接线在 S5 验收。
  - _需求：6.2, 6.3, 6.7_

- [x] 4.5 验证扩展成本与并发正确性
  - 新增无 custom handler 的 fixture dataset，仅注册 descriptor 即可通过 orchestrator 全链路 contract；Coordinator 接线留到 5.6。
  - 真实 MySQL 验证重复批次和两个连接并发写同一 natural key，最终一条逻辑记录。
  - 对 N rows、batch_size B 断言 batch execute 次数 `≤ ceil(N/B)`，并显式记录固定额外语句 K。
  - _需求：6.2, 6.5, 6.6, 10.5, 12.4_

## S5 — RunCoordinator 分阶段迁移

- [x] 5.1 定义 RunContext、Stage Protocol 和依赖装配
  - RunContext 使用类型化字段，不以共享 `dict[str, Any]` 作为阶段隐式协议。
  - Stage 通过构造注入 Port/Repository；生产与测试使用同一装配入口。
  - _需求：3.2, 3.3, 3.6_

- [x] 5.2 迁移 prepare、market 和 auxiliary 阶段
  - prepare 只解析持久化 RunRequest 和 Strategy/Factor plan。
  - market 阶段只依赖 MarketSource/MarketPreparation Port。
  - auxiliary 阶段只依赖 AuxiliaryDataOrchestrator。
  - 保持阶段审计和 checkpoint 顺序等价。
  - _需求：3.2–3.5, 5.3, 6.3_

- [x] 5.3 迁移 feature 阶段
  - FeaturePipeline 使用类型化输入/输出并通过构造注入。
  - 保持现有特征字段、缺失值和审计语义；阶段测试不访问外部供应商。
  - _需求：3.2, 3.3, 10.6_

- [x] 5.4 迁移 strategy evaluation 阶段
  - StrategyEvaluationService 只依赖冻结 Registry/ExecutionPlan 和类型化 stock context。
  - 保持 candidate、Signal、veto、score cap 和 evidence golden 等价。
  - _需求：3.2, 3.3, 4.6, 7.2, 10.6_

- [x] 5.5 迁移 review 阶段并验证资源策略
  - ReviewExecutor Port 封装 Agno/OpenAI，Coordinator 不直接导入供应商实现。
  - 明确并测试并发、超时、partial failure、降级和 cancel；失败后可计数 Agent/DB/temp lease 均为 0。
  - _需求：3.3, 3.4, 3.6, 12.1_

- [x] 5.6 迁移 aggregation、persistence 和 completion 阶段
  - recommendation assembly 保持一期评分、过滤和审计行为。
  - Repository 负责幂等推荐写入、run summary、checkpoint 和完成状态。
  - persistence 失败时不得留下 completed run 或重复推荐。
  - _需求：3.2–3.5, 10.6_

- [~] 5.7 收缩 `workbench_service`
  - 先将 run facade 切换到 RunCoordinator，再迁移查询/projection/人工标注/历史分析，最后删除完整旧流水线。
  - 删除模块级隐藏依赖；用调用点 inventory 验证旧入口引用为 0。
  - 新增运行阶段只允许修改 phase 定义、该阶段实现、装配和对应测试。
  - _需求：3.6–3.8, 11.1_

- [~] 5.8 建立真实 Coordinator golden-master 与结构化日志断言
  - 直接执行 RunCoordinator，使用 in-memory market source、fake review 和可回滚 repository。
  - 分别覆盖 happy path、失败资源释放、checkpoint/side-effect 幂等和 projection golden。
  - 断言每个 phase 日志含 run_id/phase/status/duration_ms/stable_error_code；成功为 NULL，失败属于固定枚举。
  - 用只实现公开 MarketSourcePort 的 fake、fixture Auxiliary Descriptor 验证真实接线且不修改 Coordinator。
  - 保留纯计算 golden-master，但不再把它当作顶层接线证据。
  - _需求：10.1–10.3, 10.6, 12.1_

## S6 — P0 Agent Research Gateway：可重放回测与研究实验

> S6 是二期核心业务里程碑，不是末尾附加能力。完成 0.2–0.5、1.1/1.6、2.1–2.3 后，即顺序启动 6.1–6.6；只有 6.7 的全家族迁移需要等待 S5 完成。

- [x] 6.1 建立纯 Simulation 与统一 Backtest contracts
  - 定义不可变 RecommendationInput、BarInput、SimulationPolicy、SimulationResult、BacktestRequest、SingleDate/DateRange、Trade、EquityPoint、BacktestMetrics、BacktestResult 和 FailureAttribution。
  - simulation 不导入 ORM、Session 或 outcome_service 私有常量；Runner 统一费用、滑点、止损止盈、权益曲线、胜率、盈亏比和回撤计算。
  - `strategy_id` 必填且只能解析到一个冻结 StrategyDescriptor；拒绝空 ID、多策略、未知/禁用策略和把 skill_id 回退成全策略。
  - Registry 在 bootstrap 校验重复和缺失实现后冻结；固定输入重复执行完全一致。
  - _需求：8.1–8.6, 9.1–9.9_

- [x] 6.2 建立单策略日期选择、DataRequirement 和 preflight
  - 实现互斥 `signal_date` 与 `[start_date, end_date]`：单日只运行一个 signal date，区间按每个有效交易日独立执行。
  - 用 `DataReadinessPort` 按策略 DataRequirement 预检 lookback、daily/minute bars、辅助数据发布时点、基准和未来 label horizon。
  - `target_codes` 只允许 diagnostic scope，禁止作为全市场晋级结论；默认 `local_only`，回测时不得外网补数或写行情。
  - 将 `insufficient_data`/`incomplete_evaluation` 作为可观测非成功状态，不得形成可晋级绩效。
  - _需求：9.7–9.10_

- [x] 6.3 建立不可变 Data Snapshot、重放工件和 as-of readers
  - Data Snapshot 记录稳定 snapshot_id、实际查询分区/版本/日期范围/row_count/content hash、coverage 和缺口。
  - 按 signal date 持久化带 schema/hash 的 SIGNAL_INPUT 与 OUTCOME_INPUT 不可变工件；重放只能读取原工件，禁止在缺失时回退当前源表。
  - Strategy/Factor/FeaturePipeline 只获得 `SignalDateDataReader`，强制 `visible_at <= signal_date`；只有 Simulation 获得 `OutcomeDataReader`。
  - 越界未来读取以 `future_data_access_denied` 失败；工件缺失/损坏以稳定 snapshot 错误失败。
  - _需求：9.18, 9.19_

- [x] 6.4 迁移专属 schema 并实现 request fingerprint + attempt claim
  - 通过版本 migration 创建 `backtest_data_snapshots`、`backtest_input_artifacts`、`backtest_request_claims`、`backtest_runs_v2`、`backtest_candidates`、`backtest_trades`、`backtest_failure_attributions`、`backtest_experiments` 及必要索引/唯一约束。
  - `backtest_runs_v2` 持久化 request_fingerprint、attempt_no、snapshot_id、strategy/execution/policy version、parent relation 和结果摘要；唯一键为 `(request_fingerprint, attempt_no)`，禁止全局唯一 idempotency key。
  - Repository 原子认领 active/completed/next attempt：completed 默认复用；failed/incomplete 后重试和每次 force rerun 分配新 attempt；并发普通请求不得重复执行。
  - candidate/trade 以 backtest_id 隔离；删除旧 `DELETE btrec_*` 与复用 Recommendation/Outcome 的写入路径，不改删历史 LIVE/旧 backtest 数据。
  - _需求：1.7, 1.10, 9.11, 9.12, 9.21_

- [x] 6.5 交付 P0 Agent Research Gateway 真实策略垂直切片
  - 新增 preflight、create/detail、replay、candidate audit、trade detail、failure attribution、experiment create/detail API，并定义 Pydantic schema、稳定错误码和 research-only 权限。
  - 选择至少一个真实声明式策略接入统一 Runner，完成 preflight → snapshot → 单日/区间 → replay → candidate/trade → attribution → experiment comparison；不得用纯 fake 代替垂直切片验收。
  - `FailureAttribution` 至少支持 chase_high、stop_loss、sector_decay、black_swan、execution_failure、unknown，保存证据、规则/因子引用和 confidence，并按 strategy/version/split 聚合。
  - ExperimentPlan 冻结 baseline snapshot_id、policy fingerprint、实际交易日集合/split_hash、candidate fingerprint 和预声明准入指标；拒绝 mismatch、重叠切分、冻结后变更及 OOS 泄漏。
  - Agent 仅能创建/读取研究和 candidate proposal；`promotion_candidate` 不得自动启用、降权或改写 formal 策略、Preset、权重和生产参数。
  - _需求：9.13–9.16, 9.20, 9.22–9.24_

- [~] 6.6 优先验证 Gateway 可重放、防前瞻、幂等和归因闭环
  - 源表回填/修正后按旧 snapshot 重放结果和 input hash 不变；artifact 缺失/损坏不得静默读新数据。
  - 策略未来读取失败；Simulation 仅能读取 execution policy 允许的未来窗口。
  - 真实 MySQL 覆盖两个连续 force rerun、failed/incomplete 重试、相同请求并发 claim、attempt 递增及所有历史证据保留。
  - 覆盖 snapshot/policy/split mismatch、split 重叠、candidate 冻结后变更和 OOS 泄漏拒绝。
  - 覆盖 chase_high、stop_loss、sector_decay、unknown；no-signal、数据缺口、不可成交和 incomplete 不产生失败归因。
  - 运行一条机器可消费的 Agent contract smoke，证明 self-improve 可完成研究闭环且无法调用 formal 发布写路径。
  - _需求：9.17–9.24, 10.4–10.6_

- [~] 6.7 逐家族迁移到已验收的 Gateway/Runner
  - overnight、stock skill、time-series 和现有推荐流水线回放改为统一结果与 Runner。
  - 专用数据输入通过 adapter 提供，不复制公共指标计算；隔夜策略保留分钟线覆盖门禁、成本与 walk-forward 语义。
  - 将 `/api/runs/backtest` 改为兼容 adapter，拒绝原有裸 dict 的全策略默认行为；新 Agent 只使用 `/api/backtests`。
  - 保持现有公开 API 字段语义和 golden result；迁移完成后删除 legacy result adapter、全 enabled skill 默认执行和重复计算。
  - _需求：9.3–9.6, 9.8–9.13, 9.24, 10.6_

- [~] 6.8 完成全量回测/实验回归和 self-improve 切换
  - 覆盖单策略单日/区间、未知/禁用策略、多策略拒绝、无信号、日线/分钟线缺口、未来标签不足、成本和隔离。
  - 在真实 MySQL 验证全部专属表 migration、约束、索引、查询和第二次 migration no-op。
  - 回归胜率、平均净收益、盈亏比、最大回撤、止损率、出场分布和按 split 失败归因输出。
  - 将 self-improve T1/T2/T3 的新研究调用切换到 Agent Research Gateway；禁止继续以 `btrec_*` 或旧裸 payload 产生新证据。
  - _需求：9.7–9.24, 10.4–10.6_

## S7 — 依赖收口、CI 与最终验收

- [~] 7.1 删除 market_pool 私有兼容面并验证外部 API
  - 将仓内调用点迁移到明确 core/auxiliary Port 或 application service。
  - 删除 facade 对 `_persist_*`、`_load_*`、`_fetch_*`、`_upsert_by_key` 的 re-export。
  - 测试改 patch 注入依赖，不 patch 已删除的 service 私有函数。
  - 对公开 HTTP endpoints 运行 schema/status/关键字段 contract snapshot；内部 Python import 不保兼容。
  - _需求：11.2, 11.3, 11.5_

- [~] 7.2 建立架构依赖自动检查
  - 声明 engine/domain、application、adapter/repository 的允许依赖方向。
  - 检查 Protocol 外调用、私有 facade import、生产 Registry 动态注册和 Coordinator 供应商 import。
  - 将检查纳入本地验证和 CI。
  - _需求：11.1–11.4_

- [~] 7.3 恢复并扩展 CI
  - 恢复 frontend build。
  - 增加 Python compile/import、纯单测、架构检查和 `git diff --check`。
  - 增加临时 MySQL service 的 migration、atomic upsert、snapshot replay、attempt concurrency、Agent Gateway contract 和 coordinator integration job。
  - 将 P0 Gateway smoke 设为二期必过门禁，不允许用“其余重构已完成”豁免。
  - 固定依赖安装方式，不在日志输出凭据。
  - _需求：9.17–9.24, 10.4, 10.5, 11.4, 11.6_

- [~] 7.4 按安全闸门执行真实业务 MySQL 验收
  - 先执行只读预检：表规模、磁盘、重复自然键、metadata-lock blocker、长事务和目标 DDL online 能力。
  - 保存可恢复备份证据并做 go/no-go；未通过不得执行 migration。
  - migration 逐版本执行，失败立即停止；随后只读验证 request 回填、mode 分组计数、全部 backtest/snapshot/artifact/claim/attribution/experiment 表、索引、唯一约束和 ledger，再执行第二次确认 no-op。
  - 启动应用确认 health=ok，执行 enqueue/run detail，并通过真实策略完成 Agent Gateway 的 preflight、单日、区间、replay、attribution 和 experiment smoke。
  - 验证 Gateway smoke 不新增/删除 Recommendation、RecommendationOutcome 或 formal 统计记录，且 research 凭据无法调用发布写路径。
  - 检查日志无凭据、migration error 或隐藏 Protocol 调用。
  - _需求：1.8, 1.10, 2.3–2.5, 9.17–9.24, 10.3–10.7, 12.2, 12.3_

- [~] 7.5 全量回归与性能验证
  - 运行一期全部相关测试、二期 domain/contract/coordinator/MySQL 测试和前端 build。
  - 运行 compile/import、架构检查、`git diff --check`。
  - 在 0.1 固定环境预热后对同一 fixture 至少执行 5 次；断言 `median_v2 <= 1.20 * median_baseline`，超限即失败。
  - _需求：10.6, 11.4, 12.5_

- [~] 7.6 最终需求追踪与清理
  - 建立需求 1–12 到代码、migration、测试和验证命令的追踪矩阵。
  - 确认 Agent Research Gateway 已成为 self-improve 新研究证据的唯一入口，且旧编排、旧 request 重建、身份双源、隐藏 Port、逐行 upsert 和私有 facade 已删除。
  - 确认未提交凭据、临时报告、测试数据库数据或与本 Spec 无关的文件。
  - 记录剩余风险和后续非阻塞优化。
  - _需求：9.23, 9.24, 12.6；完成定义 1–11_

## 任务依赖与优先级摘要

```text
P0 business path:
0.2–0.5 Migration Contract/Runner/Verification
 └─ 1.1 + 1.6 Request/ExecutionVersion Contracts
     └─ 2.1–2.3 StrategyDescriptor + Factor Fingerprints
         └─ 6.1 → 6.2 → 6.3 → 6.4 → 6.5 → 6.6 Gateway Vertical Slice  ← 二期核心

Parallel architecture path after S1:
S3 MarketSourcePort ─┐
S4 Auxiliary Data  ──┼─> S5 RunCoordinator ─> S6.7 Family Migration
S2 Strategy/Factor ──┘

Both paths complete:
S6.8 Self-improve Cutover ─> S7 CI + Real MySQL + Final Acceptance
```

S2、S3、S4 在 S1 相关依赖完成后可并行。P0 的精确启动门槛为 0.2–0.5、1.1/1.6、2.1–2.3；此后 6.1–6.6 顺序实施，不等待 1.2–1.5/1.7、2.4–2.5 或完整 S3–S5，因此 Agent 入口可以尽早产生真实研究价值并反向验证架构。S6.7 的历史回测家族/推荐回放迁移必须等待 S5 生产契约稳定。S7 只有在 Gateway 与架构两条路径均完成后才能通过。
