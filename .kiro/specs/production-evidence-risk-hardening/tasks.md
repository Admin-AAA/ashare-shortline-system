# 生产证据、执行一致性与风险加固实施计划

## Overview

本计划把当前 `requirements.md` 与 `design.md` 转换为可由代码生成代理逐步执行的 S0–S9 任务。执行时必须先冻结不可变基线和发布范围，再审计并复用当前已跟踪和未提交的 Existing_Artifact；只有带可复现 `Gap_Record` 的 `GAP` 才允许新增实现，禁止另建第二套 migration、run coordination、snapshot、execution、risk、scheduler 或 projection 基础设施。

本计划仅定义任务，不实现产品代码。Layer 1 通过后只解锁 Dependency_Safe_Work 和 Non_Production_Write_Work；真实环境验收与 ReleaseScopeManifest 中允许的 Production_Write 继续按独立状态跟踪。

### 当前状态与复用约束

| Scope | Code_State | Environment_Acceptance_State | Production_Activation_State | 计划语义 |
|---|---|---|---|---|
| Task 1.3 | `CODE_COMPLETE` 仅对已记录且当前有效的 `Task_1_3_Revision_Binding` 成立；否则为 `IN_PROGRESS` | `BLOCKED` | `NOT_AUTHORIZED` | 复用现有 replay/identity/tests；四类 revision-matched Layer 2 证据齐备前保持未勾选 |
| Classification_Backfill | `IN_PROGRESS` | `BLOCKED` | `NOT_AUTHORIZED` | 是否执行及是否阻塞完成只由冻结的 ReleaseScopeManifest 决定 |
| Projection_Cutover | `NOT_STARTED` | `BLOCKED` | `NOT_AUTHORIZED` | 是否执行及是否阻塞完成只由冻结的 ReleaseScopeManifest 决定 |
| Full Spec | `IN_PROGRESS` | `BLOCKED` | 按六个开关分别记录 | 完成只等待非 gated Required_Work 与 included completion-critical operations；production-ready 另行判定 |

全局约束：

- `[ ]` 表示任务整体尚未完成，不等同于 `Code_State`。Task 1.3 的历史代码完成事实必须保留，但只有当前 revision binding、Layer 1 和正确性证明有效时才支持当前 `CODE_COMPLETE`。
- 首个 Behavior_Changing_Work 前必须按顺序冻结一次性 `Baseline_Artifact`、规范注册表快照和 `ReleaseScopeManifest`；后续只能追加验证证据，不能重采集、覆盖或重写基线。
- Approval-Gated Operation Registry 中每个操作必须在 ReleaseScopeManifest 恰好出现一次。`migration_apply.task_1_3_schema_v1` 固定为 `INCLUDED + COMPLETION_CRITICAL`；其他操作必须显式 included/excluded 和 completion impact。
- ReleaseScopeManifest 标记为 `EXCLUDED` 的操作不得请求授权或调用 executor，必须记录终态 `NOT_AUTHORIZED_NOT_EXECUTED`，且不进入 Completion_Required_Work_Set。`ACTIVATION_ONLY` 操作不阻塞 Spec 完成。
- Feature Flag Registry 必须恰好包含 `evidence_projection`、`canonical_execution`、`release_gate`、`portfolio_risk`、`scheduler_claim`、`dashboard_refresh` 六个稳定 ID，并逐项绑定设计指定的 Layer 2 evidence set 与 cutover dependency。
- `Shadow_Threshold_Manifest`、`Restore_Capacity_Criteria_Manifest`、`Layer_2_Evidence_Manifest`、`Real_Environment_Identity_Manifest`、`Acceptance_Command_Manifest` 必须在各自 sample/preflight/acceptance 触发点前预登记并冻结；替代时创建新 identity 并保留旧版本及 attempts。
- 带 `*` 的 property-test 子任务只表示“新测试代码的编写可被跳过”。每个 Correctness_Obligation 的成功当前证明不可跳过：没有 revision-matched 属性测试证据时，必须存在同域或更强输入域、等价 oracle、可解析 immutable Evidence_URI 且 `Equivalence_Assessment=VALID` 的审计等价证据，否则对应 `*` 任务自动变为必需并阻止 `CODE_COMPLETE`。
- Migration apply、Classification_Backfill、Projection_Cutover 和六个 Production_Enforcement_Switch 都是互不传递的独立授权操作；ReleaseScopeManifest 不是写授权。
- 所有数据库变更仅允许兼容的 `CREATE TABLE`、`ADD COLUMN`、`CREATE INDEX`；禁止 `DROP`、`TRUNCATE`、历史记录 `DELETE`、清库、破坏性重建和 destructive downgrade。
- 所有 Historical_Record、原始时间、业务 payload、outcome version 和既有审计事实必须 append-only 保留；恢复只允许 forward fix 或 Read_Path_Rollback。
- Spec `COMPLETE` 当且仅当全部非 gated Required_Work 与 scope 中 `INCLUDED + COMPLETION_CRITICAL` 的操作均 `ACCEPTED`。声明 production-ready 还必须要求六个 flag 的 runtime mode 全部为 `ENFORCE` 且 Production_Activation_State 全部为 `ACTIVE`。
- D1 全量历史回填、D2 新 alpha、D3 券商/live trading 不属于完成范围。任何 brokerage credential、account、position、balance 或 order 访问都必须在资源解析前拒绝。

## Tasks

- [x] 0. S0 — 不可变基线、审计、安全解锁与验收基座
  - [x] 0.1 在首个行为变化前一次性冻结不可变 Baseline_Artifact
    - 对当前 revision 和 dirty workspace 执行只读采集，记录 git/workspace identity、Active_Strategy、champion、policy/execution identity、关键投影计数、Prefect 控制面和 golden samples 的去敏摘要。
    - 为 artifact 保存不可变 ID、canonical content hash、`captured_at`、capture code revision 和首个 Behavior_Changing_Work barrier；先登记 capture/freeze 事件，再允许任何行为变化。
    - 现有 `baseline-task-0.1.json`、`baseline-task-7.1.json` 仅作为 Existing_Artifact 审计；只有冻结顺序、hash 和 revision 可证明时才能复用为规范基线。无法证明时记录 `BASELINE_FREEZE_ORDER_UNPROVEN` 并保持 S0 `BLOCKED`，不得通过“刷新”基线修复。
    - 后续请求只能追加 `Baseline_Verification_Evidence`；覆盖、重写或重新采集必须以 `BASELINE_IMMUTABLE` 拒绝。
    - _Requirements: 1.2–1.6_

  - [x] 0.2 建立 Audit_Inventory、复用决策与缺口准入校验
    - 枚举全部已跟踪和未提交 Existing_Artifact，记录路径、符号、版本、workspace status、内容哈希、关联测试和需求引用。
    - 为每个映射 artifact 生成唯一 `REUSE|RECONCILE|GAP` 决策；优先复用已有 baseline、flags、migration、identity、replay、ExecutionPolicy 和 Golden_Harness 资产。
    - 让实施入口拒绝没有 Existing_Artifact 映射、可复现失败、受影响验收标准和最小变更边界的新增实现；不得以重命名或复制规避审计。
    - _Requirements: 1.1, 1.7, 1.8_

  - [x] 0.3 建立稳定 Approval-Gated Operation Registry 与六开关注册表
    - 固定并校验设计中的九个 operation ID：Task 1.3 migration、classification backfill、projection cutover 和六个 enforcement；新增 migration apply 必须先取得新稳定 ID 和 registry revision，禁止临时复用 ID。
    - 固定六个 Feature_Flag_ID 及绑定：`evidence_projection→L2_FLAG_EVIDENCE_PROJECTION_V1→projection_cutover.production_read_v1`、`canonical_execution→L2_FLAG_CANONICAL_EXECUTION_V1→NONE`、`release_gate→L2_FLAG_RELEASE_GOVERNANCE_V1→NONE`、`portfolio_risk→L2_FLAG_PORTFOLIO_RISK_V1→NONE`、`scheduler_claim→L2_FLAG_SCHEDULER_CLAIM_V1→NONE`、`dashboard_refresh→L2_FLAG_DASHBOARD_OBSERVABILITY_V1→projection_cutover.production_read_v1`。
    - Registry snapshot 必须内容寻址、恰好六行、无重复、逐 flag 独立；更新一个 flag 不改变其他五个。
    - _Requirements: 10.1, 10.10–10.12_

  - [x] 0.4 在行为变化前创建、校验并冻结 ReleaseScopeManifest
    - 为注册表中每个 Approval_Gated_Operation 恰好登记一次 inclusion、completion impact、目标环境 identity、plan identity、Layer 2 evidence bindings 和依赖；拒绝缺失、重复、未知 ID、依赖环和不可解析 immutable reference。
    - 强制 `migration_apply.task_1_3_schema_v1=INCLUDED+COMPLETION_CRITICAL`；拒绝 `EXCLUDED+COMPLETION_CRITICAL`。`EXCLUDED` 项初始化为 `NOT_AUTHORIZED_NOT_EXECUTED`，不得进入 executor 或 completion set。
    - 冻结 manifest identity/hash/time 并证明早于首个 Behavior_Changing_Work。任何范围变化必须创建新 identity、保留旧 manifest、使旧 completion decision 失效，并在新操作前重新预登记。
    - _Requirements: 10.1–10.9, 10.27, 11.9_

  - [x] 0.5 协调追踪矩阵、正交状态与 Task 1.3 revision-binding 支撑
    - 将 `traceability.md` 协调为 Requirements 1–13/S0–S9 映射，连接 artifact/Gap_Record、代码、migration、Layer 1、Correctness_Obligation、Layer 2、manifest、approval、state 和 evidence hash。
    - 保持 Code_State、Environment_Acceptance_State、Operation_Execution_State、Production_Activation_State 与 runtime flag mode 相互独立；Layer 1 不得改变环境或激活状态。
    - 实现 immutable Revision_Binding 及 stale invalidation；Task 1.3 至少绑定 replay、evidence identity、Historical_Query、LIVE selectors、migration contract、associated tests 六类 dependency digest。任一 digest 变化时保留旧事实但将当前 Code_State 置为 `IN_PROGRESS`，新 binding 的 Layer 1 与正确性证明通过后才能恢复 `CODE_COMPLETE`。
    - _Requirements: 1.9, 11.1–11.5_

  - [x] 0.6 实现五类预登记验收 manifest 契约与 admission validator
    - 为 `Shadow_Threshold_Manifest`、`Restore_Capacity_Criteria_Manifest`、`Layer_2_Evidence_Manifest`、`Real_Environment_Identity_Manifest`、`Acceptance_Command_Manifest` 实现公共 immutable header、schema validation、canonical hash、supersedes 链和 secret-handle-only 序列化。
    - Validator 必须验证 manifest 冻结时间早于 sample/preflight/acceptance，环境/命令/revision/URI/schema/hash/assertion/attempt identity 完全匹配；任一漂移保持 `BLOCKED` 或记录 `FAILED`，不能产生 `ACCEPTED`。
    - Evidence_URI 必须解析到不可变 bytes 并重算 hash；禁止 `latest`、可覆盖路径、日志搜索条件或只保存网页链接。
    - _Requirements: 13.1–13.9, 13.15_

  - [x] 0.7 实现 Correctness_Obligation 注册、证明证据与等价审计
    - 为设计 Property 1–54 建立稳定 obligation ID、需求引用、输入域、oracle、最低 iterations 和 revision-binding scope。
    - 接受两条且仅两条成功路径：当前 binding 的 passing property evidence；或 immutable、同域/更强域、等价 oracle、passing 且具有当前 `VALID` Equivalence_Assessment 的 Equivalent_Correctness_Evidence。
    - code/dependency/domain/oracle/binding 任一变化必须使旧 assessment 失效。没有有效 proof route 时，将对应 optional authoring task 提升为必需并拒绝 `CODE_COMPLETE`。
    - _Requirements: 13.10–13.14_

  - [x] 0.8 协调只读 migration 计划与安全预检代码
    - 复用 `production_hardening_migration_plan.py`、`production_hardening_preflight.py`、现有 preflight artifact 和 runner；只补充经 Gap_Record 证明缺失的 restore-point、capacity、lock、duplicate、pre/post/no-op 校验。
    - 每次 preflight 前必须已有适用的 frozen Restore_Capacity_Criteria_Manifest；真实观测写入独立 evidence attempt，不回写阈值。
    - 为每个 additive migration 生成目标对象、预计行数、锁/容量影响、checksum、precondition、postcondition 和 plan hash；拒绝 allowlist 外操作和 destructive downgrade，日志使用字段 allowlist 去敏。
    - _Requirements: 10.13–10.20, 13.2_

  - [x] 0.9 扩展现有跨模块 Golden_Harness
    - 复用 `backend/testkit/golden_harness.py` 和固定 fixtures，补齐 evidence、execution、outcome、risk canonical projection adapters，禁止网络和可变数据库读取。
    - 固定输入逐字段输出 evidence eligibility、entry/exit/cost/position/reason/return、outcome identity/version/current pointer 和 risk decision/state hash。
    - Golden_Harness 只提供 Layer 1 证据，任何结果不得标记环境 `ACCEPTED`。
    - _Requirements: 1.10, 3.8, 7.8_

  - [x] 0.10 在所有入口实现延期范围与券商访问 fail-closed guard
    - 在 CLI、API、scheduler payload、configuration loader 和 service boundary 复用统一 Scope_Guard，区分允许的历史重建与延期的 D1 全量历史回填。
    - 拒绝 D1、D2、D3 进入本 Spec，且在 secret/resource resolution 前拒绝 brokerage credential/account/position/balance/order；Risk_System 只允许 recommendation、simulation、paper 和 shadow portfolio。
    - 拒绝事件只记录安全 identity/hash/reason，不记录敏感值，也不得为了测试探测真实券商资源。
    - _Requirements: 12.1–12.7_

  - [x]* 0.11 编写属性测试：审计、复用与缺口准入完备
    - **Property 1: Audit, reuse, and gap admission are total**
    - **Validates: Requirements 1.1, 1.7, 1.8**

  - [x]* 0.12 编写属性测试：基线冻结有序、不可变且只追加验证
    - **Property 2: Baseline freeze is ordered, immutable, and append-only verified**
    - **Validates: Requirements 1.2–1.6**

  - [x]* 0.13 编写属性测试：追踪矩阵完备且引用有效
    - **Property 3: Traceability is complete and referentially valid**
    - **Validates: Requirements 1.9**

  - [x]* 0.14 编写属性测试：Golden_Harness 确定性
    - **Property 4: Golden harness is deterministic**
    - **Validates: Requirements 1.10**

  - [x]* 0.15 编写属性测试：安全初始化与解锁不隐含完成
    - **Property 5: Safe initialization and unlock rules never imply completion**
    - **Validates: Requirements 1.11–1.13**

  - [x]* 0.16 编写属性测试：延期与券商范围 fail-closed
    - **Property 50: Deferred and brokerage scope is fail-closed**
    - **Validates: Requirements 12.1–12.7**

  - [x]* 0.17 编写属性测试：预登记 manifest 不可变且时间有效
    - **Property 51: Preregistered manifests are immutable and temporally valid**
    - **Validates: Requirements 13.1–13.6, 13.9**

  - [x]* 0.18 编写属性测试：每个正确性义务都有当前成功证明路径
    - **Property 53: Every correctness obligation has a successful current proof route**
    - **Validates: Requirements 13.10–13.13**

  - [x]* 0.19 编写属性测试：等价正确性证据随语义变化失效
    - **Property 54: Equivalent correctness evidence invalidates on semantic change**
    - **Validates: Requirements 13.14**

  - [x]* 0.20 编写 governance/schema/manifest 示例测试
    - 覆盖 baseline reversed/missing timestamps、scope missing/duplicate/unknown ID、`EXCLUDED+COMPLETION_CRITICAL`、registry 非六行、manifest supersedes、URI/hash/schema/command/environment mismatch 和 stale equivalence assessment。
    - 使用隔离 fixtures，禁止 Production_Write 或真实券商探测。
    - _Requirements: 1.2–1.9, 10.1–10.12, 13.1–13.15_

  - [x] 0.21 完成 S0 真实环境只读基线验证
    - 对冻结的 Baseline_Artifact 执行目标 workspace、真实 MySQL 和真实 Prefect 的去敏只读对应验证，只追加 Baseline_Verification_Evidence；不得重新采集或改变 baseline identity/hash。
    - 在执行前冻结适用的 Layer_2_Evidence_Manifest、Real_Environment_Identity_Manifest 和 Acceptance_Command_Manifest，保存 revision、attempt、命令/query identity、断言、Evidence_URI 和结果 hash。
    - 独立更新 S0 Environment_Acceptance_State；失败或顺序无法证明时保持 `BLOCKED`，但不得阻止已解锁的 Dependency_Safe_Work。
    - _Requirements: 1.2–1.6, 11.7–11.8, 13.3–13.8_

- [x] Checkpoint A — S0 Layer 1 安全解锁
  - Ensure all tests pass, ask the user if questions arise.
  - 只有 0.1–0.20 的实现、审计和必需 proof route 通过后，才开始后续 Non_Production_Write_Work；0.21 与其他 Layer 2 blocker 继续独立跟踪。

- [x] 1. S1 — Evidence identity、历史保留与 revision-bound 真实回放
  - [x] 1.1 协调 Complete_Provenance、identity builder 和 additive schema 代码
    - 审计并复用 `0004_evidence_provenance.py`、`tables.py`、`services/evidence/identity.py`；仅对 Gap_Record 指出的缺口补齐 Trade_Date、Run_Source、Evidence_Class、immutable identity hash 和 reconstruction batch identity。
    - run、recommendation、outcome 的新写入保存 Complete_Provenance；原 `evaluation_scope`、`recommended_at`、`started_at`、payload 和历史版本保持不变，无法证明来源的 legacy row 只能为 `UNVERIFIED`。
    - 本任务只修改/测试 additive migration 与代码资产，不执行 migration apply。
    - _Requirements: 2.1–2.5, 2.9, 10.13–10.14_

  - [x] 1.2 协调 Historical_Query、as-of reader 和所有 LIVE selectors
    - 复用现有 replay/query/projection 代码，让 Historical_Query 按 Signal_Date 或 Trade_Date 返回保留 Evidence_Class 的全部历史记录，并保持 candidate audit/outcome version 可查。
    - 历史输入强制 `visible_at <= Signal_Date`；LIVE_KPI、forward gate、True_Forward_Ledger 和 champion evidence 使用同一 provenance-complete `LIVE_FORWARD` predicate。
    - run name、scope、改写时间和 classification backfill 都不得把 reconstruction/legacy 变为 LIVE。
    - _Requirements: 2.5–2.9, 4.3, 4.8_

  - [x] 1.3 将授权历史回放改为显式历史重建写入（revision-bound；完成 Layer 2 验收）
    - 复用 `replay_all_days.py` 与 `test_historical_reconstruction_replay.py`，保留既有代码完成审计事实；先创建并记录当前 `Task_1_3_Revision_Binding`，覆盖 replay、evidence identity、Historical_Query、LIVE selector、migration contract、associated tests 六类 digest。
    - 若 binding 缺失、hash 不匹配或 proof route 失效，将当前 Code_State 置为 `IN_PROGRESS` 并保留旧 binding/证据；只有新 binding 的 Layer 1、所有适用 Correctness_Obligation 和关联测试通过后才恢复 `CODE_COMPLETE`。Layer 1 通过仍保持 Environment_Acceptance_State=`BLOCKED`。
    - 在 9.3 的真实 MySQL migration apply/second-run no-op 验收和可识别真实 Prefect substrate 可用后，运行受控 historical replay，保存 server/worker/pool/queue/metadata DB/flow run、Reconstruction_Batch_Identity 与 current revision。
    - 从真实持久化数据验证 Historical_Query 和 LIVE exclusion；四类证据——真实 migration/no-op、Real Prefect replay、persisted Historical_Query、persisted LIVE exclusion——必须全部 revision/environment/manifest matched，否则任务保持未完成和 `BLOCKED`。
    - Classification_Backfill 既不由本任务授权，也不能替代新 replay 路径或四类证据。
    - _Requirements: 2.2–2.8, 11.2–11.8, 13.10–13.14_

  - [x] 1.4 协调既有记录只读分类计划与非破坏回填实现
    - 复用 classification plan 和 hash `1e2255b4daf2ed18559f7911ff2aabeb849471ed6ea0b46153c723cc111e38e8`，输出分类原因、样例、UNVERIFIED 清单、主键集合和前后计数预期。
    - executor 只能写新增 provenance/classification/reason/legacy-audit 字段，禁止修改原始时间、scope、payload 或删除/覆盖 Historical_Record；支持第二次 no-op。
    - 本任务只实现和离线验证计划/executor；真实回填由 9.4 根据 frozen release scope 和专属授权决定。
    - _Requirements: 2.4, 2.9–2.11, 10.21_

  - [x]* 1.5 编写属性测试：证据分类只由 provenance 推导
    - **Property 6: Evidence classification is provenance-derived**
    - **Validates: Requirements 2.1–2.3, 2.5, 2.9**

  - [x]* 1.6 编写属性测试：历史保留与分类回填幂等
    - **Property 7: Historical preservation and classification backfill are idempotent**
    - **Validates: Requirements 2.4, 2.10, 2.11**

  - [x]* 1.7 编写属性测试：LIVE consumers 精确包含合格 forward evidence
    - **Property 8: LIVE consumers contain exactly eligible forward evidence**
    - **Validates: Requirements 2.6, 4.3**

  - [x]* 1.8 编写属性测试：Historical_Query 保留 class 与业务日期顺序
    - **Property 9: Historical queries preserve class and business-date order**
    - **Validates: Requirements 2.7**

  - [x]* 1.9 编写属性测试：历史读取遵守 as-of 因果边界
    - **Property 10: Historical reads obey as-of causality**
    - **Validates: Requirements 2.8**

  - [x]* 1.10 协调 provenance、replay、query 与 LIVE 排除示例测试
    - 优先扩展现有测试，覆盖 trusted/untrusted source、late reconstruction、same-day valid LIVE、UNVERIFIED、日期排序、未来数据拒绝和原始字段守恒。
    - 测试离线或使用隔离数据，不得把 mock/static 结果写成 Layer 2 evidence。
    - _Requirements: 2.1–2.9, 11.4–11.7_

  - [x]* 1.11 编写属性测试：Task 1.3 code completion 精确绑定当前 revision
    - **Property 45: Task 1.3 code completion is revision-bound and invalidated exactly**
    - **Validates: Requirements 11.2, 11.4, 11.5**

  - [x]* 1.12 编写属性测试：Task 1.3 缺任一 Layer 2 证据即保持未完成
    - **Property 46: Task 1.3 remains incomplete without all revision-matched Layer 2 evidence**
    - **Validates: Requirements 11.6, 11.8**

- [x] 2. S2 — Canonical ExecutionPolicy 与逐笔一致性
  - [x] 2.1 协调不可变 ExecutionPolicy Registry
    - 复用现有 Python dataclass、canonical JSON、registry 和 policy artifacts；补齐 entry window、T+1、horizon、fees、slippage、board limits、liquidity、position caps、stop/TP/trailing、gap 和 same-bar priority。
    - policy 内容变化创建新 version/fingerprint，旧 policy 与 outcome 保持可查；current policy cap composition 为 `MIN_ALL_APPLICABLE`。
    - _Requirements: 3.1, 3.5_

  - [x] 2.2 收口唯一 Canonical_Execution_Core 与 adapters
    - 审计 `canonical.py`、`engine/simulation.py`、`outcome_simulation.py` 等符号，只在 Gap_Record 内迁移重复规则；backtest、reconstruction、LIVE formal 仅保留 DTO/ORM adapter。
    - T+1 启用时 entry bar 不参与退出；按版本化规则处理费用、滑点、涨跌停、流动性、gap、both-hit、trailing 和不可卖仓位。
    - 结果只追加新 OutcomeVersion，禁止覆盖旧 outcome。
    - _Requirements: 3.2–3.5, 3.8_

  - [x] 2.3 统一所有适用 Position_Cap 的最小值校验
    - 将 score、market regime、risk template、strategy、single-name、gross/net、industry/theme cap 汇入同一 sizing contract。
    - 删除或拒绝 weak-regime early return、adapter override 等绕过最小 cap 的路径；旧行为只能作为版本化 shadow policy。
    - _Requirements: 3.6, 3.7, 7.4_

  - [x] 2.4 实现逐笔 parity 与预登记 shadow 差异记录
    - 用同一 recommendation、snapshot 和 ExecutionPolicy 比较三条 adapter 路径的 entry、exit、cost、position、reason、net return，并输出结构化 attribution。
    - 在首个 shadow 样本前冻结 Shadow_Threshold_Manifest；新旧结果并行追加但旧 projection 保持权威，差异超过任一门槛时产生 blocker，不在本任务切换读取或 policy。
    - _Requirements: 3.4, 3.8, 10.22–10.23, 13.1_

  - [x]* 2.5 编写属性测试：ExecutionPolicy identity 确定且版本化
    - **Property 11: ExecutionPolicy identity is deterministic and versioned**
    - **Validates: Requirements 3.1, 3.5**

  - [x]* 2.6 编写属性测试：Canonical adapters 保持 parity 与 T+1
    - **Property 12: Canonical adapters preserve parity and T+1**
    - **Validates: Requirements 3.2–3.4**

  - [x]* 2.7 编写属性测试：仓位不得绕过任一适用 cap
    - **Property 13: No position can bypass an applicable cap**
    - **Validates: Requirements 3.6, 3.7, 7.4**

  - [x]* 2.8 编写属性测试：canonical core 符合注册边界语义
    - **Property 14: Canonical execution matches registered edge semantics**
    - **Validates: Requirements 3.8**

  - [x]* 2.9 扩展执行语义 golden/example tests
    - 覆盖 T+1、fee/slippage、limit-up/down、gap、same-bar conflict、trailing、不可卖延迟退出、zero trade 和旧 policy 可读；固定样本 `entry_date == exit_date` 必须为 0。
    - _Requirements: 3.3, 3.5, 3.8_

  - [x] 2.10 完成 S2 真实 MySQL outcome/parity Layer 2 验收
    - 使用预登记 manifests 在真实 MySQL 持久化三条 adapter 路径的 outcome/version/projection，验证逐字段 parity、T+1、append-only 和旧版本可查询。
    - 保存环境、policy/snapshot/code、command、attempt 和 evidence hashes；不得执行 Projection_Cutover。
    - _Requirements: 3.2–3.5, 11.8, 13.3–13.8_

- [x] 3. S3 — Canonical profiles、true-forward ledger 与 champion state
  - [x] 3.1 为每个 Active_Strategy 生成独立 Canonical_Profile
    - 按 strategy version、snapshot、ExecutionPolicy、实际日期窗口和 Code_Revision 生成一对一 profile。
    - 输出 n、distinct signal days、coverage 和分层指标；零交易为 `insufficient_sample`，不得伪装为 0% 胜率。
    - _Requirements: 4.1, 4.2_

  - [x] 3.2 协调 append-only True_Forward_Ledger 与 Champion_State
    - ledger 只接收 Complete_Provenance 的 LIVE_FORWARD recommendation/outcome；其他 evidence class 留在各自 projection。
    - 实现 champion identity stale 转换；current clean-forward `n=0` 返回 `UNVERIFIED_CURRENT_POLICY`，不得声称正期望或 current champion。
    - _Requirements: 4.3–4.5, 4.8_

  - [x] 3.3 生成可审计 Evidence_Package 与分层报告
    - 绑定唯一 identity/scope，报告 n、days、coverage、confidence、failed gates，并按 board/regime/strategy/score bucket/month 分层。
    - historical reconstruction、research 和单一正向切片不得单独产生 `PRODUCTION_CHAMPION`。
    - _Requirements: 4.6–4.8_

  - [x]* 3.4 编写属性测试：Active_Strategy 一对一映射 truthful profile
    - **Property 15: Active strategies map one-to-one to truthful profiles**
    - **Validates: Requirements 4.1, 4.2**

  - [x]* 3.5 编写属性测试：Champion_State 对不足或 stale evidence fail closed
    - **Property 16: Champion state fails closed on insufficient or stale evidence**
    - **Validates: Requirements 4.4, 4.5, 4.8**

  - [x]* 3.6 编写属性测试：Evidence_Package population accounting 一致
    - **Property 17: Evidence packages preserve population accounting**
    - **Validates: Requirements 4.6, 4.7**

  - [x]* 3.7 编写 profile/ledger/champion 示例测试
    - 覆盖零交易、clean-forward n=0、identity stale、历史 OOS 通过但 LIVE 不足、segment 退化和单一正向切片拒绝。
    - _Requirements: 4.1–4.8_

  - [x] 3.8 完成 S3 真实持久化 identity Layer 2 验收
    - 从真实 MySQL mixed-class records 生成 profile、ledger 和 package，验证 reconstruction 不进入 ledger、当前 n=0 状态真实呈现。
    - 使用预登记 manifests 只追加证据，不改变 champion 或 production flag。
    - _Requirements: 4.1–4.8, 11.8, 13.3–13.8_

- [x] 4. S4 — Holdout integrity 与机械可判定 release governance
  - [x] 4.1 实现 canonical Holdout_Population_Identity 与 additive schema 代码
    - 服务端按 snapshot、实际日期集合、universe、label policy 和 ExecutionPolicy 生成 identity；调用方 token 仅作审计 metadata。
    - 复用 migration runner 添加兼容表/列/唯一键代码，本任务不得 apply。
    - _Requirements: 5.1, 5.2_

  - [x] 4.2 实现 single-use claim、Research_Family freeze 与正交分类
    - population identity 作为唯一消费键，使用数据库唯一约束/CAS 支持并发 claim；换 token、experiment、hypothesis 不能重复解封。
    - 首次 unseal 冻结 baseline、candidate、metrics、thresholds 和 allowed Evidence_Scope；Evidence_Scope 与 Research_Path_Classification 独立持久化，`EXPLORATORY|PATH_A` 强制 Production_Eligibility=false。
    - _Requirements: 5.3–5.8_

  - [x] 4.3 实现 deterministic Release_Eligibility_Decision 与发布分权
    - 用 scope allowed、governed path、Production_Eligibility、provenance、package/current identities、minimum n/days/coverage、confidence、MDD 和 multiple-testing gates 的显式布尔合取产生决定及稳定 reason codes。
    - invalid classification、identity mismatch、failed statistics 或 research-only principal 均 fail closed；proposal 权限不得执行 formal publish。
    - _Requirements: 5.8–5.13_

  - [x]* 4.4 编写属性测试：Holdout identity canonical 且 single-use
    - **Property 18: Holdout identity is canonical and single-use**
    - **Validates: Requirements 5.1–5.3**

  - [x]* 4.5 编写属性测试：Research_Family freeze 不可变
    - **Property 19: Research family freeze is immutable**
    - **Validates: Requirements 5.4**

  - [x]* 4.6 编写属性测试：Evidence scope 与 research path 正交且有效
    - **Property 20: Evidence scope and research path remain orthogonal and valid**
    - **Validates: Requirements 5.5–5.8**

  - [x]* 4.7 编写属性测试：Release eligibility 是确定性合取
    - **Property 21: Release eligibility is a deterministic conjunction**
    - **Validates: Requirements 5.9–5.12**

  - [x]* 4.8 编写属性测试：Research authority 不能 publish
    - **Property 22: Research authority cannot publish**
    - **Validates: Requirements 5.13**

  - [x]* 4.9 编写 holdout/release 边界示例测试
    - 覆盖换 token/experiment/hypothesis、并发 claim、unseal 后新增 candidate、invalid enum、CI 跨零、多重检验失败、PATH_A 和权限越权。
    - _Requirements: 5.1–5.13_

  - [x] 4.10 完成 S4 真实 MySQL single-use 与发布拒绝验收
    - 在真实 MySQL 并发 claim 同一 population，证明只有一个 winner；验证冻结内容、正交分类、deterministic decision、identity mismatch 和 research principal 拒绝均可审计。
    - 使用预登记 manifests，不执行 production publish 或 release enforcement。
    - _Requirements: 5.1–5.13, 11.8, 13.3–13.8_

- [x] 5. S5 — Data quality 与 outcome provenance
  - [x] 5.1 实现策略声明驱动的 DataQualityDecision
    - 声明 required/optional datasets、coverage、freshness 和 synthetic 标记；formal execution 前生成 `READY|DEGRADED|BLOCKED`。
    - required 缺失/过期必须 BLOCKED；optional 缺失/synthetic 必须 DEGRADED 并记录原因；反事实不足时辅助数据影响为 `UNKNOWN`。
    - _Requirements: 6.1–6.3, 6.10_

  - [x] 5.2 实现 Outcome_Calculation_Fingerprint 与 append-only version
    - fingerprint 绑定 recommendation identity、market snapshot、ExecutionPolicy、calculation Code_Revision 和 label policy。
    - 相同 fingerprint + result hash 复用逻辑 version，任一组成变化追加新 version；只添加兼容字段/索引代码，本任务不 apply。
    - _Requirements: 6.4–6.6_

  - [x] 5.3 实现 atomic Current_Projection、selective stale recompute 与保留审计
    - 用 revision/CAS 原子指向完整 valid version，stale writer 不得覆盖新 revision；只重算 fingerprint/projection 缺失或不匹配的 records。
    - duplicate、orphan、missing-current、fingerprint-missing 全部保留可查，只从 current KPI 排除，不删除或修剪历史。
    - _Requirements: 6.7–6.9_

  - [x]* 5.4 编写属性测试：Data quality decisions 遵守声明 contract
    - **Property 23: Data quality decisions follow declared contracts**
    - **Validates: Requirements 6.1–6.3, 6.10**

  - [x]* 5.5 编写属性测试：Outcome identity 幂等且 append-only
    - **Property 24: Outcome identity is idempotent and append-only**
    - **Validates: Requirements 6.4–6.6**

  - [x]* 5.6 编写属性测试：Current projection 原子且保留审计历史
    - **Property 25: Current projection is atomic and preserves audit history**
    - **Validates: Requirements 6.7, 6.9**

  - [x]* 5.7 编写属性测试：Stale recomputation 精确
    - **Property 26: Stale recomputation is exact**
    - **Validates: Requirements 6.8**

  - [x]* 5.8 编写 quality/outcome 示例与隔离并发测试
    - 覆盖 required blocker、optional degraded、synthetic label、counterfactual unknown、相同 fingerprint 并发、stale selection 和 duplicate/orphan 保留。
    - _Requirements: 6.1–6.10_

  - [x] 5.9 完成 S5 真实 MySQL idempotence/CAS Layer 2 验收
    - 在真实 MySQL 验证 outcome 唯一约束、并发 idempotence、CAS stale-writer rejection、selective recompute 和 audit preservation。
    - 使用预登记 manifests 保存前后主键/计数、projection revision 和 evidence hash；禁止清理 duplicate/orphan。
    - _Requirements: 6.4–6.9, 11.8, 13.3–13.8_

- [x] 6. S6 — Recommendation/paper/shadow portfolio risk
  - [x] 6.1 实现版本化 PortfolioRiskPolicy 与 PortfolioRiskState
    - 保存 single-name、gross/net、industry/theme、daily loss、drawdown reduce/halt/resume 规则，以及 capital、open/pending/locked exposure、PnL、peak、drawdown、version/state hash。
    - stale/missing quote 和不可卖仓位按 policy 估值并持续占用预算；不得接入券商账户、持仓或订单。
    - _Requirements: 7.1, 7.2, 7.6, 12.6_

  - [x] 6.2 实现原子 Risk_Gate 与 exposure reservation
    - 在 recommendation persistence 边界同一事务读取 state/version、计算 min(all caps)、append decision、reserve exposure 并提交；失败回滚 decision/reservation/recommendation。
    - 输出 `ALLOW|REDUCE|REJECT|HALT`、requested/approved size、state hash、policy fingerprint、reason codes 和 decision time；并发请求不得超配。
    - _Requirements: 7.3–7.7_

  - [x] 6.3 将 Risk_Gate 接入 paper/shadow 路径
    - 初始只记录 would-decision，不改变生产推荐；historical reconstruction 与 paper forward 可重放 risk decisions。
    - 普通 recommendation task 不得清除 halt；resume 遵守 policy 和独立审计入口。本任务不得切换 risk enforcement。
    - _Requirements: 7.5, 7.8, 7.9, 12.6_

  - [x]* 6.4 编写属性测试：Risk policy/state/decision 是版本化 append-only facts
    - **Property 27: Risk policy, state, and decisions are versioned append-only facts**
    - **Validates: Requirements 7.1, 7.2, 7.7**

  - [x]* 6.5 编写属性测试：Risk reservation 原子且遵守 loss 与 locked exposure
    - **Property 28: Risk reservation is atomic and respects loss and locked exposure**
    - **Validates: Requirements 7.3, 7.5, 7.6, 7.8**

  - [x]* 6.6 编写 risk stress/example tests
    - 覆盖多票同跌、同题材集中、连续跌停、gap-down、弱势 regime、stale quote、locked position、halt/resume 和 cap composition。
    - 测试仅使用 simulation/paper/shadow 数据，禁止 brokerage access。
    - _Requirements: 7.3–7.8, 12.4–12.6_

  - [x]* 6.7 编写隔离事务与并发 reservation tests
    - 覆盖成功原子提交、recommendation 写失败回滚、stale state retry 和多个请求不超配；测试数据不得进入生产投影。
    - _Requirements: 7.3, 7.8_

  - [x] 6.8 完成 S6 真实 MySQL reservation Layer 2 验收
    - 在真实 MySQL 并发请求，验证 state version、decision、reservation、recommendation 原子提交，失败回滚且 aggregate exposure 不超限。
    - 使用预登记 manifests 验证 locked exposure、daily-loss/drawdown HALT 和普通任务不能 resume；不启用 production enforcement。
    - _Requirements: 7.3–7.8, 11.8, 13.3–13.8_

- [x] 7. S7 — Prefect control plane 与 scheduler reliability
  - [x] 7.1 复用控制面基线并实现单 owner lease
    - 复用 `baseline-task-7.1.json` 和 capture script，不把 stale worker/缺失 concurrency 的旧快照误报为当前证据，也不得改写 S0 Baseline_Artifact。
    - 为共享 metadata DB 建立 owner identity、heartbeat、expiry、grace 和 CAS takeover；有效 lease 下第二 owner fail closed。
    - _Requirements: 8.1, 8.2_

  - [x] 7.2 配置显式 concurrency 并实现数据库级 Occurrence_Claim
    - 每个 work pool/queue 配置正数并发上限并使用单一配置来源；缺失配置作为 blocker。
    - ScheduledOccurrence identity 绑定 task type、business date、scope、scheduled time，使用 MySQL unique constraint、CAS、lease/heartbeat；进程内集合仅作优化。
    - _Requirements: 8.3–8.5_

  - [x] 7.3 协调 typed failure/retry、Misfire_Policy 与 polling transaction
    - 业务异常添加上下文后重新抛给 Prefect，区分 retryable/non-retryable；reconciliation 识别 false-green、stale running 和 orphan flow。
    - 为每类 occurrence 配置 `SKIP|RUN_ONCE_WITHIN_GRACE|BOUNDED_CATCH_UP`、grace 和 max catch-up；每轮 polling 使用新短事务读取最新 committed state。
    - _Requirements: 8.6–8.8_

  - [x]* 7.4 编写属性测试：Control-plane ownership 排他
    - **Property 29: Control-plane ownership is exclusive**
    - **Validates: Requirements 8.1, 8.2**

  - [x]* 7.5 编写属性测试：ScheduledOccurrence claim 至多一次
    - **Property 30: Scheduled occurrence claims are at-most-once**
    - **Validates: Requirements 8.4, 8.5**

  - [x]* 7.6 编写属性测试：Misfire planning 有界
    - **Property 31: Misfire planning is bounded**
    - **Validates: Requirements 8.7**

  - [x]* 7.7 编写 scheduler unit/concurrency model tests
    - 覆盖 owner conflict、lease expiry/takeover、两个 claimant、typed retry、bounded catch-up、fresh transaction visibility 和 false-green reconciliation。
    - mock/模型结果不得标记 Real_Prefect_Acceptance。
    - _Requirements: 8.1–8.8_

  - [x] 7.8 完成真实 Prefect + MySQL Layer 2 验收
    - 使用真实 Prefect server、online worker、work pool、queue、共享 metadata DB 和真实 MySQL claim store，验证 owner、正数 concurrency、claim winner、lease takeover、retry/failed、misfire、fresh transaction 和 reconciliation。
    - 使用预登记 manifests 保存 deployment/flow/worker/pool/queue/state/claim/environment identity 与 immutable evidence；mock、SQLite 或旧 baseline 不能替代。
    - _Requirements: 8.1–8.9, 11.8, 13.3–13.8_

  - [x] 7.9 完成 Task 1.3 真实 Prefect substrate 只读就绪验收
    - 使用已冻结的 `Layer_2_Evidence_Manifest`、`Real_Environment_Identity_Manifest` 和 `Acceptance_Command_Manifest` 执行有界只读探测，验证真实 Prefect server 可达、目标 worker online、work pool、queue、deployment 均存在且 identity 匹配，并解析 metadata DB identity；不得创建或更新 deployment、queue、schedule，也不得触发 flow。
    - 保存 current revision、environment/command/attempt identity、server/worker/pool/queue/deployment/metadata DB 去敏 identity 与 immutable Evidence_URI/hash；任一预登记、identity、revision 或 command mismatch 均保持 `BLOCKED`。
    - 本任务只证明 Task 1.3 replay 的 substrate readiness；不验证 owner exclusivity、concurrency、claim、lease takeover、retry、misfire、fresh transaction 或 reconciliation，不依赖也不能替代 7.8。
    - _Requirements: 11.6–11.8, 13.3–13.8_

- [x] 8. S8 — Dashboard、observability 与 Application_Lifespan
  - [x] 8.1 协调 Dashboard_Projection、API contract 与 current-run selector
    - 分开返回 LIVE_FORWARD、HISTORICAL_RECONSTRUCTION 和 research evidence，包含 actual_generated_at、signal/trade date、reconstruction label、DataQualityDecision、Risk_Gate、last_updated 和 stale。
    - current run 按 Evidence_Class、Actual_Generated_At 和业务日期选择；无 LIVE 时可显示最近 reconstruction，但不得显示“今日 LIVE 已运行”。
    - _Requirements: 9.1, 9.2, 9.4, 9.5_

  - [x] 8.2 实现有限轮询或可取消 SSE 生命周期
    - 复用现有前端数据层，统一 refresh policy、页面隐藏降频、请求去重、AbortController/unmount/disconnect cleanup 和 stale threshold。
    - 不引入无限 polling、不可取消 stream 或第二套状态缓存。
    - _Requirements: 9.3, 9.5_

  - [x] 8.3 贯穿 Correlation_ID、告警条件与 FastAPI lifespan
    - 让 correlation identity 从 API 经 scheduler、Prefect、run 到 provider/database audit；日志只保存 allowlisted identity/hash/reason。
    - 配置 latency、heartbeat stale、queue delay、flow failure、quality blocker、identity mismatch、risk halt 告警；用 Application_Lifespan 管理初始化、失败清理和幂等 shutdown。
    - _Requirements: 9.6–9.8_

  - [x]* 8.4 编写属性测试：Dashboard selection 与 staleness 真实
    - **Property 32: Dashboard selection and staleness are truthful**
    - **Validates: Requirements 9.2, 9.5**

  - [x]* 8.5 编写 API/frontend/lifespan 示例测试
    - 覆盖 evidence labels、LIVE/reconstruction 分栏、quality scorecard、risk halt、timer/SSE cleanup、stale display、current-run selection、correlation propagation、startup failure 和 shutdown idempotence。
    - 运行非 watch frontend test/build，不启动长期 dev server。
    - _Requirements: 9.1–9.9_

  - [x] 8.6 完成真实 projection 与 observability Layer 2 验收
    - 使用真实后端 projection 验证 lifecycle、current-run、stale、correlation chain 和可配置告警输入。
    - 使用预登记 manifests 保存 API/flow/run/database identity 的去敏 evidence；不执行 Projection_Cutover 或 dashboard enforcement。
    - _Requirements: 9.1–9.9, 11.8, 13.3–13.8_

- [ ] 9. S9 — Scope-conditional migration、切换、激活与最终判定
  - [x] 9.1 实现 release-scope admission、精确授权与 forward-only executor 边界
    - 每个 gated executor 先验证 frozen ReleaseScopeManifest identity/hash、registry binding 和 inclusion。`EXCLUDED` 直接记录 `NOT_AUTHORIZED_NOT_EXECUTED`，不得解析 secret、请求授权或调用 Production_Write。
    - `INCLUDED` 操作的 Separate_Write_Approval 必须精确匹配 operation instance、target environment、plan hash、artifact checksum、actor 和有效期；授权不得跨 migration/backfill/cutover/flag 传递。
    - migration executor 调用前强制只读 plan、additive allowlist、frozen Restore_Capacity_Criteria_Manifest、Verified_Restore_Point、Capacity_Check 和 preconditions；调用后强制 postconditions 与 second-run no-op。
    - _Requirements: 10.1–10.21, 11.11, 13.2_

  - [x] 9.2 实现 Read_Path_Rollback、scope-aware completion 与最终证据 schema
    - rollback 只恢复旧 read selector，保留新增表/列/evidence/outcome/audit；禁止 destructive downgrade。
    - Completion_Required_Work_Set 精确等于全部非 Approval_Gated_Operation Required_Work，加上 frozen scope 中 `INCLUDED+COMPLETION_CRITICAL` 的操作；excluded 与 activation-only 操作状态不得改变 Spec completion。
    - 最终报告包含 scope identity、clean-forward n、known unknowns、全部 `NOT_AUTHORIZED_NOT_EXECUTED`、未激活 flags 和历史守恒。production-ready evaluator 独立要求六个 `ACTIVE+ENFORCE`。
    - _Requirements: 10.24, 11.9–11.15_

  - [x] 9.3 按 frozen scope 和专属授权执行 Task 1.3 Forward_Only_Migration/no-op 验收
    - Operation ID 固定为 `migration_apply.task_1_3_schema_v1`，scope 必须为 `INCLUDED+COMPLETION_CRITICAL`；scope 无效或授权缺失时记录 `NOT_AUTHORIZED`、不执行并保持 Spec `BLOCKED`。
    - 精确校验目标环境、migration checksum、plan hash、actor/expiry、restore/capacity criteria 与全部 preconditions；仅 apply additive allowlist。
    - 验证 postconditions、历史计数/主键不减少，并立即第二次执行证明 schema、数据计数和 migration state 不变；保存当前 revision 的真实 MySQL Layer 2 evidence。
    - 该授权不允许 Classification_Backfill、Projection_Cutover 或任何 enforcement。
    - _Requirements: 10.2, 10.13–10.20, 11.6, 11.11_

  - [x] 9.4 根据 frozen scope 处置或执行 Classification_Backfill
    - Operation ID=`classification_backfill.provenance_v1`。若 `EXCLUDED`，只记录 `NOT_AUTHORIZED_NOT_EXECUTED` 并从 completion set 排除；不得将未授权误记为 blocker。
    - 若 `INCLUDED`，只在 migration schema ACCEPTED 且专属 approval 精确匹配 classification plan hash/环境/checksum/actor/expiry 时执行；只写新增 provenance/classification/reason/legacy-audit 字段。
    - 验证总数、主键、原始时间、scope、payload 不变且第二次 no-op。只有该 entry 同时为 `COMPLETION_CRITICAL` 时，未 ACCEPTED 才阻塞 Spec；`ACTIVATION_ONLY` 不阻塞。
    - _Requirements: 2.4, 2.10, 2.11, 10.3, 10.6–10.9, 10.21_

  - [ ] 9.5 根据 frozen scope 处置或执行 Projection_Cutover
    - Operation ID=`projection_cutover.production_read_v1`。若 `EXCLUDED`，记录 `NOT_AUTHORIZED_NOT_EXECUTED` 并保持旧 read path；不得执行或阻塞 Spec。
    - 若 `INCLUDED`，先验证 frozen Shadow_Threshold_Manifest、evidence/canonical/dashboard Layer 2、shadow identity 和全部门槛；任一失败保持旧路径。
    - 通过后仍需该操作专属 approval 才可原子切换；验证 Read_Path_Rollback 只恢复 selector 且 append-only 数据完整。仅 `INCLUDED+COMPLETION_CRITICAL` 时该操作阻塞完成。
    - _Requirements: 10.3, 10.6–10.9, 10.22–10.24_

  - [ ] 9.6 根据 frozen scope 处置或切换 `evidence_projection` enforcement
    - 对 `enforcement.evidence_projection`：`EXCLUDED` 时记录 `NOT_AUTHORIZED_NOT_EXECUTED`；`INCLUDED` 时仅在 `L2_FLAG_EVIDENCE_PROJECTION_V1` ACCEPTED、projection cutover dependency 满足且专属 approval 有效时把该 flag 切为 `ENFORCE/ACTIVE`。
    - reconstruction/research/UNVERIFIED 仍不得进入 LIVE selectors，其他五个 flags 保持原状态；只有 scope 标记 completion-critical 时该操作阻塞 Spec。
    - _Requirements: 2.6, 4.3, 10.10–10.12, 10.25, 11.13–11.14_

  - [ ] 9.7 根据 frozen scope 处置或切换 `canonical_execution` enforcement
    - 对 `enforcement.canonical_execution`：`EXCLUDED` 时记录终态；`INCLUDED` 时仅在 `L2_FLAG_CANONICAL_EXECUTION_V1` ACCEPTED 且专属 approval 有效时切为 `ENFORCE/ACTIVE`，其 cutover dependency 为 `NONE`。
    - 保留旧 policy/outcome/read rollback，其他 flags 不变；只有 included completion-critical 才阻塞 Spec。
    - _Requirements: 3.2–3.5, 10.10–10.12, 10.25, 11.13–11.14_

  - [ ] 9.8 根据 frozen scope 处置或切换 `release_gate` enforcement
    - 对 `enforcement.release_gate`：`EXCLUDED` 时记录终态；`INCLUDED` 时仅在 `L2_FLAG_RELEASE_GOVERNANCE_V1` ACCEPTED、当前 identity/package/authority 校验通过且专属 approval 有效时切为 `ENFORCE/ACTIVE`。
    - clean-forward n=0 继续 `UNVERIFIED_CURRENT_POLICY`，不得自动产生 champion 或修改策略；只有 included completion-critical 才阻塞 Spec。
    - _Requirements: 4.4–4.8, 5.7–5.13, 10.25, 11.13–11.14_

  - [ ] 9.9 根据 frozen scope 处置或切换 `portfolio_risk` enforcement
    - 对 `enforcement.portfolio_risk`：`EXCLUDED` 时记录终态；`INCLUDED` 时仅在 `L2_FLAG_PORTFOLIO_RISK_V1` ACCEPTED 且专属 approval 有效时切为 recommendation/simulation 范围的 `ENFORCE/ACTIVE`。
    - 不得访问 brokerage credential/account/position/balance/order，其他 flags 不变；只有 included completion-critical 才阻塞 Spec。
    - _Requirements: 7.9, 10.25, 11.13–11.14, 12.4–12.6_

  - [ ] 9.10 根据 frozen scope 处置或切换 `scheduler_claim` enforcement
    - 对 `enforcement.scheduler_claim`：`EXCLUDED` 时记录终态；`INCLUDED` 时仅在 `L2_FLAG_SCHEDULER_CLAIM_V1` ACCEPTED、owner/concurrency/claim/retry/misfire/reconciliation 全部通过且专属 approval 有效时切为 `ENFORCE/ACTIVE`。
    - 保留可审计 disable/forward-fix 路径，不改变其他 flags；只有 included completion-critical 才阻塞 Spec。
    - _Requirements: 8.1–8.9, 10.25, 11.13–11.14_

  - [ ] 9.11 根据 frozen scope 处置或切换 `dashboard_refresh` enforcement
    - 对 `enforcement.dashboard_refresh`：`EXCLUDED` 时记录终态；`INCLUDED` 时仅在 `L2_FLAG_DASHBOARD_OBSERVABILITY_V1` ACCEPTED、Projection_Cutover ACCEPTED 且专属 approval 有效时切为 `ENFORCE/ACTIVE`。
    - 验证 labels、stale、quality/risk 状态真实，其他 flags 不变；只有 included completion-critical 才阻塞 Spec。
    - _Requirements: 9.1–9.9, 10.25, 11.13–11.14_

  - [x]* 9.12 编写属性测试：ReleaseScopeManifest total 且 canonical
    - **Property 33: ReleaseScopeManifest is total and canonical**
    - **Validates: Requirements 10.1–10.5**

  - [x]* 9.13 编写属性测试：Release scope 确定性构造 completion work
    - **Property 34: Release scope deterministically constructs completion work**
    - **Validates: Requirements 10.6–10.8, 11.9**

  - [x]* 9.14 编写属性测试：Release scope revision 不可变且先于行为变化
    - **Property 35: Release scope revisions are immutable and precede behavior**
    - **Validates: Requirements 10.9, 10.27**

  - [x]* 9.15 编写属性测试：六开关 registry bindings 精确
    - **Property 36: Stable flag registry bindings are exact**
    - **Validates: Requirements 10.10–10.12**

  - [x]* 9.16 编写属性测试：Migration plan 是只读前置且 additive-only
    - **Property 37: Migration plans are read-only prerequisites and additive-only**
    - **Validates: Requirements 10.13, 10.14, 10.20**

  - [x]* 9.17 编写属性测试：Migration 执行要求全部 safety gates 且可重复
    - **Property 38: Migration execution requires every safety gate and is repeatable**
    - **Validates: Requirements 10.15–10.19**

  - [x]* 9.18 编写属性测试：Approval 精确且不可传递
    - **Property 39: Approvals are exact and non-transitive**
    - **Validates: Requirements 10.16, 10.21, 10.22, 11.11**

  - [x]* 9.19 编写属性测试：Shadow threshold failure 保持旧 read path
    - **Property 40: Shadow threshold failure preserves the old read path**
    - **Validates: Requirements 10.22, 10.23**

  - [x]* 9.20 编写属性测试：Read-path rollback 保留 append-only 数据
    - **Property 41: Read-path rollback preserves append-only data**
    - **Validates: Requirements 10.24**

  - [x]* 9.21 编写属性测试：Enforcement activation 要求精确 evidence 与 dependency
    - **Property 42: Enforcement activation requires its exact evidence and dependency**
    - **Validates: Requirements 7.9, 10.25**

  - [x]* 9.22 编写属性测试：持久化运维 artifacts 不含 secrets
    - **Property 43: Persisted operational artifacts never contain secrets**
    - **Validates: Requirements 10.26, 12.5, 13.15**

  - [x]* 9.23 编写属性测试：Code/environment/activation 状态相互独立
    - **Property 44: Code, environment, and activation states remain independent**
    - **Validates: Requirements 11.1, 11.7**

  - [x]* 9.24 编写属性测试：Spec completion 精确等于 acceptance conjunction
    - **Property 47: Spec completion is exactly the acceptance conjunction**
    - **Validates: Requirements 11.9–11.12**

  - [x]* 9.25 编写属性测试：Production-ready 要求六个 ACTIVE 与 ENFORCE
    - **Property 48: Production-ready requires all six ACTIVE and ENFORCE states**
    - **Validates: Requirements 11.13, 11.14**

  - [x]* 9.26 编写属性测试：Final evidence report 完备且确定
    - **Property 49: Final evidence report is complete and deterministic**
    - **Validates: Requirements 11.15**

  - [x]* 9.27 编写属性测试：Layer 2 evidence 匹配全部预登记 binding
    - **Property 52: Layer 2 evidence matches every preregistered binding**
    - **Validates: Requirements 13.7, 13.8**

  - [x]* 9.28 编写 approval/state/report 示例测试
    - 覆盖 scope excluded/included/activation-only/completion-critical、缺失或错误 approval、destructive request、pre/post failure、Task 1.3 stale binding、六 flag mismatch 和 final report schema。
    - 每个 property task 使用独立测试模块或不冲突目标文件；fixtures 不得执行真实 Production_Write。
    - _Requirements: 10.1–10.27, 11.1–11.15, 13.1–13.15_

  - [x] 9.29 汇总 Completion_Required_Work_Set 并计算最终 Spec 状态
    - 运行非 watch 的完整自动化回归、静态/类型/构建检查和已授权环境 evidence validation，逐项解析 current revision 的 immutable proof/evidence；不得启动长期 server/watcher。
    - 只等待全部非 gated Required_Work 和 frozen scope 中 `INCLUDED+COMPLETION_CRITICAL` 的 operation 达到 `ACCEPTED`。excluded 与 activation-only 操作即使未执行也不阻塞；excluded 必须出现在报告中并标记 `NOT_AUTHORIZED_NOT_EXECUTED`。
    - 最终证据报告 Task 1.3 四类证据、scope identity、clean-forward n、known unknowns、历史守恒、未激活 flags 和 D1/D2/D3 延期范围；不得宣称策略正期望。
    - _Requirements: 11.6, 11.8–11.12, 11.15, 12.1–12.7, 13.10–13.14_

  - [x] 9.30 独立计算 production-ready 状态
    - 对 Feature Flag Registry 的恰好六个 ID 做完整集合判定；只有每个 runtime mode=`ENFORCE` 且 Production_Activation_State=`ACTIVE` 时返回 production-ready。
    - Spec `COMPLETE`、scope exclusion 或 activation-only 配置都不得放宽该判定；列出每个未满足的 flag ID 和状态，不自动执行任何 activation。
    - _Requirements: 11.13–11.15_

- [ ] Checkpoint B — 最终验收
  - Ensure all tests pass, ask the user if questions arise.
  - 若 completion-critical operation 缺授权或真实环境不可用，保留独立 `BLOCKED/NOT_AUTHORIZED` evidence；excluded operation 记录 `NOT_AUTHORIZED_NOT_EXECUTED`。不得用 Layer 1、mock 或跳过 proof obligation 替代。

## Notes

- 带 `*` 的子任务是可选的“测试代码编写”任务，不是可选正确性证明。每个 Property 1–54 必须有当前 Revision_Binding 的 passing property evidence；只有经审计且 `Equivalence_Assessment=VALID` 的 Equivalent_Correctness_Evidence 才能替代。否则相应 `*` 任务自动成为必需。
- 0.21、1.3、2.10、3.8、4.10、5.9、6.8、7.8、8.6、9.3–9.11、9.29–9.30 是环境、scope resolution 或状态判定任务，不因包含验证而标记可选。
- 每个实现任务开始前读取 Audit_Inventory、Reuse_Decision、当前 registry snapshot、ReleaseScopeManifest 和适用 preregistered manifests；发现新 migration apply 或其他 gated operation 时，先修订 registry/scope 并重新冻结，禁止绕过旧 manifest。
- Dependency graph 中 gated leaf 的“完成”可以是：scope=`EXCLUDED` 后记录 `NOT_AUTHORIZED_NOT_EXECUTED`；included activation-only 操作记录当前非激活状态；或 included operation 实际执行并验收。只有 included completion-critical 未 `ACCEPTED` 才阻止 Spec `COMPLETE`。
- Layer 1 与 Layer 2 独立：Dependency_Safe_Work 可并行推进；mock、SQLite、文件名和人工说明不能替代真实 MySQL/Prefect evidence。
- 所有 property tasks 使用独立测试模块或预先分配的不冲突目标文件，以便同一 execution wave 并行。
- D1_Full_Historical_Backfill、D2_New_Alpha、D3_Brokerage_Live_Trading 没有本计划任务 ID，不纳入 completion set，也不属于 dependency graph；提出时启动独立 requirements/design/tasks/permissions/acceptance 工作流。
- 本计划绝不授权 brokerage access，也绝不授权 destructive database operation；所有历史、manifest、attempt 和 outcome version 保持 append-only。

## Task Dependency Graph

```json
{
  "schema": "explicit-task-dag/v1",
  "semantics": {
    "array_order_has_no_dependency_semantics": true,
    "depends_on": "Unconditional prerequisite task IDs only.",
    "when_included_requires": "For a scoped operation node, task-ID prerequisites apply only when its own scope_operation is INCLUDED. For an aggregate node, condition objects add the referenced task only when the named operation is INCLUDED and COMPLETION_CRITICAL in the frozen ReleaseScopeManifest.",
    "excluded_disposition": "An EXCLUDED scope_operation waits only for its depends_on admission prerequisites, never evaluates when_included_requires, and records NOT_AUTHORIZED_NOT_EXECUTED."
  },
  "nodes": [
    { "id": "0.1", "depends_on": [] },
    { "id": "0.2", "depends_on": ["0.1"] },
    { "id": "0.3", "depends_on": ["0.2"] },
    { "id": "0.4", "depends_on": ["0.3"] },
    { "id": "0.5", "depends_on": ["0.2", "0.4"] },
    { "id": "0.6", "depends_on": ["0.3", "0.4"] },
    { "id": "0.7", "depends_on": ["0.2", "0.5"] },
    { "id": "0.8", "depends_on": ["0.2", "0.6"] },
    { "id": "0.9", "depends_on": ["0.2", "0.4"] },
    { "id": "0.10", "depends_on": ["0.2", "0.4"] },
    { "id": "0.11", "depends_on": ["0.2"] },
    { "id": "0.12", "depends_on": ["0.1"] },
    { "id": "0.13", "depends_on": ["0.5"] },
    { "id": "0.14", "depends_on": ["0.9"] },
    { "id": "0.15", "depends_on": ["0.4", "0.5"] },
    { "id": "0.16", "depends_on": ["0.10"] },
    { "id": "0.17", "depends_on": ["0.6"] },
    { "id": "0.18", "depends_on": ["0.7"] },
    { "id": "0.19", "depends_on": ["0.7"] },
    { "id": "0.20", "depends_on": ["0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8"] },
    { "id": "0.21", "depends_on": ["0.1", "0.6"] },

    { "id": "1.1", "depends_on": ["0.2", "0.4", "0.5", "0.8"] },
    { "id": "1.2", "depends_on": ["0.5", "1.1"] },
    { "id": "1.3", "depends_on": ["0.5", "0.7", "1.1", "1.2", "7.9", "9.3"] },
    { "id": "1.4", "depends_on": ["1.1", "1.2"] },
    { "id": "1.5", "depends_on": ["1.1", "1.2"] },
    { "id": "1.6", "depends_on": ["1.4"] },
    { "id": "1.7", "depends_on": ["1.2"] },
    { "id": "1.8", "depends_on": ["1.2"] },
    { "id": "1.9", "depends_on": ["1.2"] },
    { "id": "1.10", "depends_on": ["1.1", "1.2", "1.4"] },
    { "id": "1.11", "depends_on": ["0.5", "1.1", "1.2"] },
    { "id": "1.12", "depends_on": ["0.5", "0.6", "1.1", "1.2"] },

    { "id": "2.1", "depends_on": ["0.2", "0.4"] },
    { "id": "2.2", "depends_on": ["0.9", "2.1"] },
    { "id": "2.3", "depends_on": ["2.1", "2.2"] },
    { "id": "2.4", "depends_on": ["0.6", "2.2", "2.3"] },
    { "id": "2.5", "depends_on": ["2.1"] },
    { "id": "2.6", "depends_on": ["2.2"] },
    { "id": "2.7", "depends_on": ["2.3"] },
    { "id": "2.8", "depends_on": ["2.2", "2.3"] },
    { "id": "2.9", "depends_on": ["2.2", "2.3"] },
    { "id": "2.10", "depends_on": ["0.6", "2.4"] },

    { "id": "3.1", "depends_on": ["1.2", "2.1"] },
    { "id": "3.2", "depends_on": ["1.2", "3.1"] },
    { "id": "3.3", "depends_on": ["3.1", "3.2"] },
    { "id": "3.4", "depends_on": ["3.1"] },
    { "id": "3.5", "depends_on": ["3.2"] },
    { "id": "3.6", "depends_on": ["3.3"] },
    { "id": "3.7", "depends_on": ["3.1", "3.2", "3.3"] },
    { "id": "3.8", "depends_on": ["0.6", "3.3"] },

    { "id": "4.1", "depends_on": ["0.2", "0.4", "0.8"] },
    { "id": "4.2", "depends_on": ["4.1"] },
    { "id": "4.3", "depends_on": ["3.3", "4.2"] },
    { "id": "4.4", "depends_on": ["4.1", "4.2"] },
    { "id": "4.5", "depends_on": ["4.2"] },
    { "id": "4.6", "depends_on": ["4.2"] },
    { "id": "4.7", "depends_on": ["4.3"] },
    { "id": "4.8", "depends_on": ["4.3"] },
    { "id": "4.9", "depends_on": ["4.2", "4.3"] },
    { "id": "4.10", "depends_on": ["0.6", "4.3"] },

    { "id": "5.1", "depends_on": ["0.2", "0.4"] },
    { "id": "5.2", "depends_on": ["1.1", "2.1"] },
    { "id": "5.3", "depends_on": ["5.2"] },
    { "id": "5.4", "depends_on": ["5.1"] },
    { "id": "5.5", "depends_on": ["5.2"] },
    { "id": "5.6", "depends_on": ["5.3"] },
    { "id": "5.7", "depends_on": ["5.3"] },
    { "id": "5.8", "depends_on": ["5.1", "5.3"] },
    { "id": "5.9", "depends_on": ["0.6", "5.3"] },

    { "id": "6.1", "depends_on": ["0.2", "0.4", "0.10"] },
    { "id": "6.2", "depends_on": ["2.3", "6.1"] },
    { "id": "6.3", "depends_on": ["0.10", "6.2"] },
    { "id": "6.4", "depends_on": ["6.1"] },
    { "id": "6.5", "depends_on": ["6.2"] },
    { "id": "6.6", "depends_on": ["6.1", "6.2", "6.3"] },
    { "id": "6.7", "depends_on": ["6.2"] },
    { "id": "6.8", "depends_on": ["0.6", "6.3"] },

    { "id": "7.1", "depends_on": ["0.1", "0.2", "0.4"] },
    { "id": "7.2", "depends_on": ["7.1"] },
    { "id": "7.3", "depends_on": ["7.2"] },
    { "id": "7.4", "depends_on": ["7.1"] },
    { "id": "7.5", "depends_on": ["7.2"] },
    { "id": "7.6", "depends_on": ["7.3"] },
    { "id": "7.7", "depends_on": ["7.1", "7.2", "7.3"] },
    { "id": "7.8", "depends_on": ["0.6", "7.3"] },
    { "id": "7.9", "depends_on": ["0.6"] },

    { "id": "8.1", "depends_on": ["1.2", "5.1", "6.1"] },
    { "id": "8.2", "depends_on": ["8.1"] },
    { "id": "8.3", "depends_on": ["7.3", "8.1"] },
    { "id": "8.4", "depends_on": ["8.1"] },
    { "id": "8.5", "depends_on": ["8.1", "8.2", "8.3"] },
    { "id": "8.6", "depends_on": ["0.6", "8.1", "8.2", "8.3"] },

    { "id": "9.1", "depends_on": ["0.3", "0.4", "0.6"] },
    { "id": "9.2", "depends_on": ["0.4", "0.5", "9.1"] },
    {
      "id": "9.3",
      "depends_on": ["0.4", "9.1"],
      "scope_operation": "migration_apply.task_1_3_schema_v1",
      "scope_policy": "FORCED_INCLUDED_COMPLETION_CRITICAL",
      "when_included_requires": ["0.8", "1.1"]
    },
    {
      "id": "9.4",
      "depends_on": ["0.4", "9.1"],
      "scope_operation": "classification_backfill.provenance_v1",
      "when_included_requires": ["1.4", "9.3"],
      "excluded_disposition": "NOT_AUTHORIZED_NOT_EXECUTED"
    },
    {
      "id": "9.5",
      "depends_on": ["0.4", "9.1"],
      "scope_operation": "projection_cutover.production_read_v1",
      "when_included_requires": ["0.6", "1.3", "2.4", "2.10", "5.9", "8.6"],
      "excluded_disposition": "NOT_AUTHORIZED_NOT_EXECUTED"
    },
    {
      "id": "9.6",
      "depends_on": ["0.4", "9.1"],
      "scope_operation": "enforcement.evidence_projection",
      "when_included_requires": ["1.3", "9.5"],
      "excluded_disposition": "NOT_AUTHORIZED_NOT_EXECUTED"
    },
    {
      "id": "9.7",
      "depends_on": ["0.4", "9.1"],
      "scope_operation": "enforcement.canonical_execution",
      "when_included_requires": ["2.10"],
      "excluded_disposition": "NOT_AUTHORIZED_NOT_EXECUTED"
    },
    {
      "id": "9.8",
      "depends_on": ["0.4", "9.1"],
      "scope_operation": "enforcement.release_gate",
      "when_included_requires": ["3.8", "4.10"],
      "excluded_disposition": "NOT_AUTHORIZED_NOT_EXECUTED"
    },
    {
      "id": "9.9",
      "depends_on": ["0.4", "9.1"],
      "scope_operation": "enforcement.portfolio_risk",
      "when_included_requires": ["6.8"],
      "excluded_disposition": "NOT_AUTHORIZED_NOT_EXECUTED"
    },
    {
      "id": "9.10",
      "depends_on": ["0.4", "9.1"],
      "scope_operation": "enforcement.scheduler_claim",
      "when_included_requires": ["7.8"],
      "excluded_disposition": "NOT_AUTHORIZED_NOT_EXECUTED"
    },
    {
      "id": "9.11",
      "depends_on": ["0.4", "9.1"],
      "scope_operation": "enforcement.dashboard_refresh",
      "when_included_requires": ["8.6", "9.5"],
      "excluded_disposition": "NOT_AUTHORIZED_NOT_EXECUTED"
    },
    { "id": "9.12", "depends_on": ["0.4"] },
    { "id": "9.13", "depends_on": ["0.4", "9.2"] },
    { "id": "9.14", "depends_on": ["0.4"] },
    { "id": "9.15", "depends_on": ["0.3"] },
    { "id": "9.16", "depends_on": ["0.8", "9.1"] },
    { "id": "9.17", "depends_on": ["0.8", "9.1"] },
    { "id": "9.18", "depends_on": ["9.1"] },
    { "id": "9.19", "depends_on": ["2.4", "9.1"] },
    { "id": "9.20", "depends_on": ["9.2"] },
    { "id": "9.21", "depends_on": ["0.3", "9.1"] },
    { "id": "9.22", "depends_on": ["0.6", "0.10", "9.1"] },
    { "id": "9.23", "depends_on": ["0.5", "9.2"] },
    { "id": "9.24", "depends_on": ["0.4", "9.2"] },
    { "id": "9.25", "depends_on": ["0.3", "9.2"] },
    { "id": "9.26", "depends_on": ["9.2"] },
    { "id": "9.27", "depends_on": ["0.6"] },
    { "id": "9.28", "depends_on": ["9.1", "9.2"] },
    {
      "id": "9.29",
      "depends_on": ["0.7", "1.3", "1.4", "2.10", "3.8", "4.10", "5.1", "5.9", "6.8", "7.8", "8.6", "9.2"],
      "release_scope_manifest": "0.4",
      "when_included_requires": [
        { "scope_operation": "migration_apply.task_1_3_schema_v1", "requires": "9.3", "when_completion_impact": "COMPLETION_CRITICAL" },
        { "scope_operation": "classification_backfill.provenance_v1", "requires": "9.4", "when_completion_impact": "COMPLETION_CRITICAL" },
        { "scope_operation": "projection_cutover.production_read_v1", "requires": "9.5", "when_completion_impact": "COMPLETION_CRITICAL" },
        { "scope_operation": "enforcement.evidence_projection", "requires": "9.6", "when_completion_impact": "COMPLETION_CRITICAL" },
        { "scope_operation": "enforcement.canonical_execution", "requires": "9.7", "when_completion_impact": "COMPLETION_CRITICAL" },
        { "scope_operation": "enforcement.release_gate", "requires": "9.8", "when_completion_impact": "COMPLETION_CRITICAL" },
        { "scope_operation": "enforcement.portfolio_risk", "requires": "9.9", "when_completion_impact": "COMPLETION_CRITICAL" },
        { "scope_operation": "enforcement.scheduler_claim", "requires": "9.10", "when_completion_impact": "COMPLETION_CRITICAL" },
        { "scope_operation": "enforcement.dashboard_refresh", "requires": "9.11", "when_completion_impact": "COMPLETION_CRITICAL" }
      ]
    },
    { "id": "9.30", "depends_on": ["0.3", "9.2"] }
  ]
}
```
