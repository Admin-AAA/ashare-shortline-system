# Requirements Document

## Introduction

本文档定义 `production-evidence-risk-hardening` 的需求边界、验收状态、发布范围和完成条件。本文档协调现有未提交实现与既有审计事实；当前阶段只更新需求与工作流配置，不实现产品代码，不执行环境写入，不修改设计或任务文档。

## Glossary

### Workflow and governance terms

- **Spec**：本文件定义的 `production-evidence-risk-hardening` 工作项。
- **Fast_Task_Phase_2**：仅允许生成或更新 `requirements.md` 与 `.config.kiro` 的静默需求阶段。
- **S0–S9**：从基线审计到最终验收的十个工作流阶段；S0 为基线、审计、开关、预检和测试基座，S1–S8 为功能加固，S9 为分层切换和最终验收。
- **Required_Work**：Spec 所需的 S0–S9 能力、验证和环境验收工作；Approval_Gated_Operation 的实际执行是否进入完成条件由 ReleaseScopeManifest 决定。
- **Completion_Required_Work_Set**：全部非 Approval_Gated_Operation 的 Required_Work，加上 ReleaseScopeManifest 中 `INCLUDED` 且分类为 `COMPLETION_CRITICAL` 的 Approval_Gated_Operation。
- **Behavior_Changing_Work**：改变 evidence、execution、release、outcome、risk、scheduler、Dashboard 行为或持久化 schema 的工作。
- **Approval_Gated_Operation**：只有获得专属书面授权后才允许执行的环境变更操作。
- **Approval_Gated_Operation_Registry**：集中记录当前 Spec 每个 Approval_Gated_Operation 稳定身份的完整注册表。
- **Separate_Write_Approval**：绑定单一操作、目标环境、计划哈希、制品校验和、操作者和有效期的明确写入授权；一个操作的授权不授权其他操作。
- **Completion_Impact**：ReleaseScopeManifest 中的枚举 `COMPLETION_CRITICAL` 或 `ACTIVATION_ONLY`。
- **Release_Inclusion**：ReleaseScopeManifest 中的枚举 `INCLUDED` 或 `EXCLUDED`。
- **Completion_Critical_Operation**：Release_Inclusion 为 `INCLUDED` 且 Completion_Impact 为 `COMPLETION_CRITICAL` 的 Approval_Gated_Operation。
- **Activation_Only_Operation**：Completion_Impact 为 `ACTIVATION_ONLY` 的 Approval_Gated_Operation；Activation_Only_Operation 不参与 Spec 完成判定。
- **ReleaseScopeManifest**：在 Behavior_Changing_Work 前预登记并冻结的不可变清单；清单逐项列出 Approval_Gated_Operation_ID、Release_Inclusion、Completion_Impact、目标环境身份、计划身份、所需 Layer_2_Evidence_Set_ID 和依赖关系。
- **Operation_Execution_State**：操作执行状态枚举 `PLANNED`、`NOT_AUTHORIZED`、`NOT_AUTHORIZED_NOT_EXECUTED`、`AUTHORIZED_NOT_EXECUTED`、`EXECUTED`、`ACCEPTED` 或 `FAILED`。
- **Production_Write**：改变目标业务数据库、生产投影、生产策略状态、生产调度状态或生产执行行为的写操作。
- **Dependency_Safe_Work**：不依赖未验收环境结果、不执行 Production_Write、失败后不改变生产状态的工作。
- **Non_Production_Write_Work**：仅修改代码、测试、文档、隔离测试数据或影子产物且不改变生产状态的工作。
- **Audit_Inventory**：按文件路径、符号、版本、工作区状态和关联测试记录 Existing_Artifact 的清单。
- **Existing_Artifact**：在 Spec 实施前已经存在的已跟踪或未提交代码、迁移、测试、配置、基线或预检产物。
- **Reuse_Decision**：`REUSE`、`RECONCILE` 或 `GAP` 三值审计结论；`GAP` 必须附可复现缺口证据。
- **Gap_Record**：把未满足验收标准映射到 Existing_Artifact、复现证据和最小补充范围的记录。
- **Traceability_Matrix**：把需求、代码、迁移、测试、验证命令、环境证据、清单身份和状态连接起来的可审计映射。
- **Baseline_Artifact**：在首个 Behavior_Changing_Work 前一次性冻结的计数、身份、配置、样例和输出摘要；Baseline_Artifact 具有不可变身份、内容哈希、采集时间和采集代码版本。
- **Baseline_Verification_Evidence**：后续环境任务对既有 Baseline_Artifact 的身份、内容哈希、引用和环境对应关系进行验证后追加的验收证据。
- **Feature_Flag_ID**：Feature_Flag 的稳定身份；本 Spec 的权威集合为 Feature Flag Registry 中的六个值。
- **Feature_Flag**：使新路径可在 `OFF`、`SHADOW`、`AUDIT` 或 `ENFORCE` 状态运行的版本化开关。
- **Required_Production_Enforcement_Set**：Feature Flag Registry 中全部六个 Feature_Flag_ID 的集合。
- **Golden_Harness**：用固定输入逐字段比较不同执行路径结果的离线测试基座。
- **Code_State**：代码交付状态枚举 `NOT_STARTED`、`IN_PROGRESS`、`CODE_COMPLETE`；`CODE_COMPLETE` 只表示指定 Revision_Binding 的代码与 Layer_1_Acceptance 证据齐备。
- **Revision_Binding**：把 Code_Revision、依赖文件或符号的内容哈希、测试身份和验收结果绑定为一个不可变身份的记录。
- **Task_1_3_Revision_Binding**：至少绑定 replay、evidence identity、Historical_Query、LIVE selector、迁移契约和关联测试依赖的 Revision_Binding。
- **Environment_Acceptance_State**：环境验收状态枚举 `NOT_RUN`、`BLOCKED`、`FAILED`、`ACCEPTED`；只有 Layer_2_Acceptance 成功才允许设为 `ACCEPTED`。
- **Production_Activation_State**：生产行为状态枚举 `NOT_AUTHORIZED`、`AUTHORIZED_NOT_ACTIVE`、`ACTIVE`、`ROLLED_BACK`。
- **Layer_1_Acceptance**：由单元测试、mock 测试、静态检查、离线 Golden_Harness、属性测试和代码审计构成的非生产验收层。
- **Layer_2_Acceptance**：在 Real_Environment_Identity_Manifest 指定的真实 MySQL 和真实 Prefect 控制面中验证迁移、回放、持久化查询、排除规则、并发、失败和恢复语义的环境验收层。
- **Layer_2_Evidence_Set_ID**：Feature Flag Registry 中绑定到单一 Feature_Flag_ID 的稳定 Layer 2 证据集合身份。
- **Real_MySQL_Acceptance**：在指定 MySQL 验收环境或经授权的目标环境中执行真实数据库引擎路径并保存可核验结果。
- **Real_Prefect_Acceptance**：在真实 Prefect server、worker、work pool 和 metadata database 上执行 flow 并保存可核验结果。
- **Task_1_3**：`tasks.md` 中“将授权历史回放改为显式历史重建写入”的任务。
- **Spec_Completion_State**：Spec 的 `IN_PROGRESS`、`BLOCKED`、`FAILED` 或 `COMPLETE` 状态。

### Preregistered manifest and correctness terms

- **Pre_Registered_Manifest**：在相关样本、预检或验收执行开始前保存的内容寻址不可变清单；清单至少包含稳定 ID、schema version、内容哈希、创建时间、适用 Code_Revision 和引用关系。
- **Shadow_Threshold_Manifest**：在首个 shadow 样本产生前冻结指标、比较方向、阈值、样本窗口、最小样本量、身份维度和失败动作的 Pre_Registered_Manifest。
- **Restore_Capacity_Criteria_Manifest**：在迁移预检前冻结 Verified_Restore_Point 的覆盖范围与验证条件，以及磁盘、临时空间、表和索引规模、预计增长、锁影响和安全余量阈值的 Pre_Registered_Manifest。
- **Layer_2_Evidence_Manifest**：在 Layer_2_Acceptance 前冻结每个 Layer_2_Evidence_Set_ID 的 schema、必需字段、证据 URI、内容哈希规则、断言集合和通过规则的 Pre_Registered_Manifest。
- **Real_Environment_Identity_Manifest**：在 Layer_2_Acceptance 前冻结 MySQL 实例与 database identity、Prefect server 与 metadata database identity、work pool、queue、worker、deployment 和去敏网络目标身份的 Pre_Registered_Manifest。
- **Acceptance_Command_Manifest**：在 Layer_2_Acceptance 前冻结命令 ID、可执行入口、参数、目标环境引用、预期退出状态、断言、超时和代码校验和的 Pre_Registered_Manifest；清单只引用 secret handle，不保存 secret value。
- **Evidence_URI**：Layer_2_Evidence_Manifest 中可解析到不可变证据内容并可核对内容哈希的规范化地址。
- **Correctness_Obligation**：设计正确性属性或验收标准要求证明的不变量，并具有稳定 ID、需求引用、输入域和判定 oracle。
- **Optional_Property_Test_Task**：任务计划中带可选标记、用于生成 Correctness_Obligation 证据的属性测试编写任务。
- **Equivalent_Correctness_Evidence**：可替代 Optional_Property_Test_Task 的既有不可变证据制品；制品必须绑定相同 Correctness_Obligation、相同或更强输入域、相同 oracle、当前 Revision_Binding、可解析 Evidence_URI 和成功结果。
- **Equivalence_Assessment**：审计 Equivalent_Correctness_Evidence 的不可变记录；记录包含制品哈希、Correctness_Obligation_ID、输入域映射、oracle 映射、Revision_Binding、审计者身份和 `VALID` 或 `INVALID` 结论。

### Evidence and execution terms

- **Evidence_Class**：证据身份枚举 `LIVE_FORWARD`、`HISTORICAL_RECONSTRUCTION`、`RESEARCH_BACKTEST`、`EXPERIMENT` 和 `UNVERIFIED`。
- **LIVE_FORWARD**：在目标交易日真实到达时生成，且 Complete_Provenance 可验证的前瞻证据。
- **HISTORICAL_RECONSTRUCTION**：在目标交易日之后按历史可见数据重建的推荐或结果证据。
- **RESEARCH_BACKTEST**：由研究回测路径生成的证据。
- **EXPERIMENT**：绑定冻结假设、候选集合、数据范围和执行身份的实验性证据。
- **UNVERIFIED**：来源、生成时点或 Complete_Provenance 无法验证的证据。
- **Complete_Provenance**：至少包含 Evidence_Class、Actual_Generated_At、Signal_Date、Trade_Date、Data_As_Of、Source_Snapshot_ID、Policy_Fingerprint、Execution_Policy_Fingerprint、Code_Revision、Run_Source 和适用的 Reconstruction_Batch_Identity 的身份集合。
- **Actual_Generated_At**：记录实际创建推荐或运行的不可伪造时间。
- **Signal_Date**：策略生成信号所针对的交易日。
- **Trade_Date**：推荐或模拟交易所属的业务交易日。
- **Data_As_Of**：策略输入允许观察到数据的最晚业务时点。
- **Source_Snapshot_ID**：固定输入数据快照的身份。
- **Policy_Fingerprint**：策略政策内容的规范化哈希。
- **Execution_Policy_Fingerprint**：ExecutionPolicy 内容的规范化哈希。
- **Code_Revision**：生成代码或证据所使用代码版本的可审计标识。
- **Run_Source**：区分实时运行、历史回放、研究回测和实验的类型化来源。
- **Reconstruction_Batch_Identity**：一次历史重建批次的唯一身份。
- **Historical_Record**：既有或新增的 replay run、recommendation、candidate audit、outcome、outcome version、experiment 或关联审计记录。
- **LIVE_KPI**：只由符合 LIVE_FORWARD 的证据计算的指标。
- **As_Of_Boundary**：策略在 Signal_Date 只能读取 `visible_at <= Signal_Date` 数据的时间边界。
- **Historical_Query**：按 Signal_Date、Trade_Date 和 Evidence_Class 查询 Historical_Record 的接口行为。
- **ExecutionPolicy**：不可变且版本化的执行契约，覆盖入场窗口、T+1、费用、滑点、仓位、涨跌停、流动性、止损、止盈、trailing、horizon 和 gap 处理。
- **Canonical_Execution_Core**：Backtest、historical reconstruction outcome 和 LIVE formal outcome 共用的唯一执行语义。
- **Execution_Parity**：相同输入、快照和 ExecutionPolicy 下，entry、exit、cost、position size、exit reason 和 net return 逐笔一致。
- **T_Plus_One**：禁止在入场交易日退出的执行规则。
- **Position_Cap**：由 score、market regime、risk template、single-name risk 或 portfolio risk 限定的仓位上限。

### Release, quality, risk, scheduling, and observability terms

- **Active_Strategy**：当前策略注册表中启用的版本化策略契约。
- **Canonical_Profile**：绑定策略版本、数据快照、ExecutionPolicy、日期窗口和 Code_Revision 的策略画像。
- **True_Forward_Ledger**：只接受 Complete_Provenance 的 LIVE_FORWARD recommendation 与 outcome 的 append-only 账本。
- **Champion_State**：`UNVERIFIED`、`HISTORICALLY_QUALIFIED`、`FORWARD_CANDIDATE`、`PRODUCTION_CHAMPION` 或 `STALE_UNVERIFIED` 的发布状态。
- **Evidence_Package**：包含唯一身份、证据范围、样本量、交易日数、覆盖率、分层指标、置信状态和失败门槛的发布证据包。
- **Holdout_Population_Identity**：由数据快照、实际日期集合、universe、label policy 和 ExecutionPolicy 规范化生成的 holdout 身份。
- **Research_Family**：在 holdout 解封前一次性冻结的 baseline、候选集合、指标、阈值和允许 Evidence_Scope 集合。
- **Evidence_Scope**：权威证据阶段枚举，且仅允许 `TRAIN`、`VALIDATION`、`PURGED_HISTORICAL_OOS`、`UNTOUCHED_HOLDOUT`、`SHADOW_FORWARD` 或 `LIVE_CANARY`。
- **Research_Path_Classification**：与 Evidence_Scope 分离的研究路径枚举 `GOVERNED_RELEASE`、`EXPLORATORY` 或 `PATH_A`。
- **Production_Eligibility**：布尔发布资格字段；`true` 仅表示证据可进入预登记发布门判定，不表示发布门已经通过。
- **Release_Eligibility_Decision**：对 Evidence_Scope、Research_Path_Classification、Production_Eligibility、Complete_Provenance、Evidence_Package identity、current policy/state/deployment identity 和预登记门槛执行确定性布尔判定的结果。
- **DataQualityDecision**：在 formal 执行前按必需数据、可选数据、覆盖率、freshness 和 synthetic 字段产生的 `READY`、`DEGRADED` 或 `BLOCKED` 结果。
- **Outcome_Calculation_Fingerprint**：由 recommendation identity、行情快照、ExecutionPolicy、计算代码版本和 label policy 组成的结果计算身份。
- **Current_Projection**：原子指向最新有效 append-only 版本的读模型。
- **PortfolioRiskPolicy**：版本化的单票、gross/net exposure、行业或题材集中度、单日亏损、回撤减仓、回撤停止和恢复规则。
- **PortfolioRiskState**：资本、开放和待执行敞口、集中度、已实现和未实现损益、峰值权益、回撤及不可卖仓位的版本化状态。
- **Risk_Gate**：在仓位持久化前原子评估 PortfolioRiskState 并产生 `ALLOW`、`REDUCE`、`REJECT` 或 `HALT` 的判定。
- **Prefect_Control_Plane**：共享一个 Prefect metadata database 的 server、work pool、queue 和 worker 集合。
- **Scheduled_Occurrence**：由 task type、business date、scope 和 scheduled time 唯一确定的一次调度实例。
- **Occurrence_Claim**：使用数据库唯一约束、compare-and-set 和 lease 取得 Scheduled_Occurrence 执行权的记录。
- **Misfire_Policy**：`SKIP`、`RUN_ONCE_WITHIN_GRACE` 或 `BOUNDED_CATCH_UP` 的错过调度处理规则。
- **Dashboard_Projection**：供页面展示 evidence、quality、risk、scheduler 和 freshness 状态的读模型。
- **Correlation_ID**：贯穿 API、scheduler、Prefect、run、provider 和 database 日志的请求身份。
- **Stale_Threshold**：判断 Dashboard_Projection 陈旧状态的版本化时间阈值。
- **Application_Lifespan**：应用启动、资源初始化、资源释放和关闭的统一生命周期契约。

### Migration, approval, and deferred-scope terms

- **Forward_Only_Migration**：只增加兼容字段、表或索引并通过后续向前修复恢复的迁移。
- **Read_Only_Migration_Plan**：不写业务数据并包含目标对象、操作、预计行数、锁影响、容量影响、前置条件和计划哈希的迁移计划。
- **Additive_Allowlist**：仅包含兼容 `CREATE TABLE`、`ADD COLUMN` 和 `CREATE INDEX` 的操作集合。
- **Verified_Restore_Point**：具有唯一标识、创建时间、覆盖范围和恢复验证证据的备份或快照。
- **Capacity_Check**：验证磁盘余量、临时空间、表规模、索引规模和预计增长满足 Restore_Capacity_Criteria_Manifest 的检查。
- **Migration_Checksum**：迁移内容的不可变哈希。
- **Precondition**：迁移执行前必须成立的可验证条件。
- **Postcondition**：迁移执行后必须成立的可验证条件。
- **No_Op_Verification**：同一迁移连续第二次执行时结构、数据计数和迁移状态不再变化的验证。
- **Classification_Backfill**：只向新增 provenance 字段写入分类结果、原因和 legacy audit 的操作。
- **Projection_Cutover**：把正式读取路径从旧投影切换到已验收新投影的操作。
- **Projection_Cutover_Dependency**：Feature Flag Registry 中取值为稳定 Projection_Cutover 操作 ID 或 `NONE` 的依赖字段。
- **Production_Enforcement_Switch**：把六个 Feature_Flag_ID 中一个且仅一个 Feature_Flag 切换到 `ENFORCE` 的 Approval_Gated_Operation。
- **Shadow_Mode**：并行计算并记录差异但不改变生产决策或正式读取结果的模式。
- **Read_Path_Rollback**：只恢复旧读取开关并保留新增 append-only 数据的回退方式。
- **D1_Full_Historical_Backfill**：补齐 2021–2024 全市场历史截面的独立倡议。
- **D2_New_Alpha**：新增 alpha、选股逻辑或策略阈值优化的独立研究倡议。
- **D3_Brokerage_Live_Trading**：接入券商凭据、账户、持仓、资金或订单的独立安全倡议。
- **Brokerage_Access**：读取、写入、传输或调用券商 credential、account、position、balance 或 order 的行为。

### Defined systems

- **Governance_System**：负责审计优先、复用决策、状态、追踪、授权、清单和范围控制的系统边界。
- **Evidence_System**：负责证据身份、历史重建、查询和 LIVE_KPI 隔离的系统边界。
- **Execution_System**：负责 ExecutionPolicy、Canonical_Execution_Core 和 Execution_Parity 的系统边界。
- **Release_Governance_System**：负责策略画像、holdout、Evidence_Package、Champion_State 和发布判定的系统边界。
- **Data_Quality_System**：负责 DataQualityDecision 和输入质量证据的系统边界。
- **Outcome_System**：负责 outcome 身份、版本、幂等和 Current_Projection 的系统边界。
- **Risk_System**：负责 PortfolioRiskPolicy、PortfolioRiskState 和 Risk_Gate 的系统边界。
- **Scheduler_System**：负责 Prefect_Control_Plane、Scheduled_Occurrence、重试和 Misfire_Policy 的系统边界。
- **Observability_System**：负责 Dashboard_Projection、Correlation_ID、指标、告警和 Application_Lifespan 的系统边界。
- **Migration_System**：负责迁移计划、迁移执行、回填、切换和回退的系统边界。
- **Acceptance_System**：负责 Layer_1_Acceptance、Layer_2_Acceptance、证据有效性和完成判定的系统边界。
- **Scope_Guard_System**：负责 D1、D2、D3 和 Brokerage_Access 边界的系统边界。

## Normative Registries

### Approval-Gated Operation Registry

以下稳定 ID 是当前已知 Approval_Gated_Operation 的最小完整集合。任何新增 Forward_Only_Migration apply 实例必须在 ReleaseScopeManifest 冻结前取得新的稳定 ID 并加入本注册表。

| Approval_Gated_Operation_ID | Operation | Required manifest rule |
|---|---|---|
| `migration_apply.task_1_3_schema_v1` | Task_1_3 所需真实 MySQL additive migration apply 与 No_Op_Verification | `INCLUDED` + `COMPLETION_CRITICAL` |
| `classification_backfill.provenance_v1` | Classification_Backfill | 必须显式选择 `INCLUDED` 或 `EXCLUDED`；只有 `INCLUDED` + `COMPLETION_CRITICAL` 阻塞完成 |
| `projection_cutover.production_read_v1` | Projection_Cutover | 必须显式选择 `INCLUDED` 或 `EXCLUDED`；只有 `INCLUDED` + `COMPLETION_CRITICAL` 阻塞完成 |
| `enforcement.evidence_projection` | `evidence_projection` Production_Enforcement_Switch | 必须显式选择 `INCLUDED` 或 `EXCLUDED`；只有 `INCLUDED` + `COMPLETION_CRITICAL` 阻塞完成 |
| `enforcement.canonical_execution` | `canonical_execution` Production_Enforcement_Switch | 必须显式选择 `INCLUDED` 或 `EXCLUDED`；只有 `INCLUDED` + `COMPLETION_CRITICAL` 阻塞完成 |
| `enforcement.release_gate` | `release_gate` Production_Enforcement_Switch | 必须显式选择 `INCLUDED` 或 `EXCLUDED`；只有 `INCLUDED` + `COMPLETION_CRITICAL` 阻塞完成 |
| `enforcement.portfolio_risk` | `portfolio_risk` Production_Enforcement_Switch | 必须显式选择 `INCLUDED` 或 `EXCLUDED`；只有 `INCLUDED` + `COMPLETION_CRITICAL` 阻塞完成 |
| `enforcement.scheduler_claim` | `scheduler_claim` Production_Enforcement_Switch | 必须显式选择 `INCLUDED` 或 `EXCLUDED`；只有 `INCLUDED` + `COMPLETION_CRITICAL` 阻塞完成 |
| `enforcement.dashboard_refresh` | Dashboard/observability 的 `dashboard_refresh` Production_Enforcement_Switch | 必须显式选择 `INCLUDED` 或 `EXCLUDED`；只有 `INCLUDED` + `COMPLETION_CRITICAL` 阻塞完成 |

### Feature Flag and Layer 2 Evidence Registry

以下六行是 Feature_Flag_ID 的权威完整集合。Layer_2_Evidence_Set_ID 和 Projection_Cutover_Dependency 不允许由部署者临时替换。

| Feature_Flag_ID | Feature boundary | Required Layer_2_Evidence_Set_ID | Required evidence content | Projection_Cutover_Dependency |
|---|---|---|---|---|
| `evidence_projection` | evidence projection | `L2_FLAG_EVIDENCE_PROJECTION_V1` | migration apply/no-op、Real_Prefect_Acceptance replay、持久化 Historical_Query、持久化 LIVE exclusion | `projection_cutover.production_read_v1` |
| `canonical_execution` | canonical execution | `L2_FLAG_CANONICAL_EXECUTION_V1` | 真实 MySQL 三路径 outcome、Execution_Parity、T_Plus_One、append-only 和旧版本可读 | `NONE` |
| `release_gate` | release governance | `L2_FLAG_RELEASE_GOVERNANCE_V1` | 真实持久化 profile/ledger/package、holdout single-use/freeze、身份和权限拒绝 | `NONE` |
| `portfolio_risk` | portfolio risk | `L2_FLAG_PORTFOLIO_RISK_V1` | 真实 MySQL reservation、rollback、HALT、locked exposure 和并发不超配 | `NONE` |
| `scheduler_claim` | scheduler claim | `L2_FLAG_SCHEDULER_CLAIM_V1` | 真实 Prefect + MySQL owner、concurrency、claim、lease、retry、misfire 和 reconciliation | `NONE` |
| `dashboard_refresh` | Dashboard/observability | `L2_FLAG_DASHBOARD_OBSERVABILITY_V1` | 真实 projection 的 current/stale/label、lifecycle、Correlation_ID 和 alert input | `projection_cutover.production_read_v1` |

## Current Audited State

以下快照记录当前工作区事实，不把现有未提交实现等同于环境验收：

| Scope | Code_State | Environment_Acceptance_State | Audited basis | Remaining gate |
|---|---|---|---|---|
| `Task_1_3` | `CODE_COMPLETE` 仅对已审计 Revision_Binding 有效 | `BLOCKED` | `backend/scripts/replay_all_days.py` 已显式写入 `HISTORICAL_RECONSTRUCTION`、真实生成时间、批次身份和 fingerprints；`backend/tests/test_historical_reconstruction_replay.py` 已覆盖 as-of、历史查询和 LIVE 排除的 unit/mock/static artifacts | 必须记录 Task_1_3_Revision_Binding；真实 MySQL migration、真实 Prefect replay、持久化 Historical_Query 与 LIVE_KPI 排除尚未完成 Layer_2_Acceptance |
| `Classification_Backfill` | `IN_PROGRESS` | `BLOCKED` | 只读分类计划存在；计划哈希为 `1e2255b4daf2ed18559f7911ff2aabeb849471ed6ea0b46153c723cc111e38e8`；`write_authorized=false` | ReleaseScopeManifest 冻结前不执行；若清单标记 `EXCLUDED`，最终状态为 `NOT_AUTHORIZED_NOT_EXECUTED` 且不阻塞 Spec 完成 |
| `Projection_Cutover` | `NOT_STARTED` | `BLOCKED` | 当前没有切换授权 | ReleaseScopeManifest 冻结前不执行；若清单标记 `EXCLUDED`，最终状态为 `NOT_AUTHORIZED_NOT_EXECUTED` 且不阻塞 Spec 完成 |
| Full `Spec` | `IN_PROGRESS` | `BLOCKED` | S0–S2 存在部分未提交实现与 Layer_1_Acceptance artifacts | S0–S9 Completion_Required_Work_Set、Task_1_3 completion-critical migration 和强制 Layer_2_Acceptance 尚未全部通过 |

当前诊断基线包括：provenance-clean true-forward `n=0`；既有 canonical backtest 存在 `14.98%` 同日退出；当前 champion identity 为 stale；Prefect 并发上限、Occurrence_Claim 和真实失败语义尚未完成环境验收。基线只描述当前状态，不构成策略有效性声明。后续环境任务只能验证并追加 Baseline_Verification_Evidence，不能重新采集或重写 Baseline_Artifact。

## Scope and Approval Classification

| Workstream | Classification | Requirement coverage | Execution boundary |
|---|---|---|---|
| S0 baseline, audit, flags, preflight, Golden_Harness | Required_Work | Requirements 1, 13 | 默认只读或非生产写入；Baseline_Artifact 在 Behavior_Changing_Work 前冻结 |
| S1 evidence and historical reconstruction | Required_Work | Requirement 2 | Task_1_3 migration apply 为 Completion_Critical_Operation；Classification_Backfill 由 ReleaseScopeManifest 决定 |
| S2 canonical execution | Required_Work | Requirement 3 | 先离线与 Shadow_Mode |
| S3 profiles, ledger, champion | Required_Work | Requirement 4 | 正式发布受独立开关约束 |
| S4 holdout and release governance | Required_Work | Requirement 5 | Production_Enforcement_Switch 为 Approval_Gated_Operation |
| S5 data quality and outcome | Required_Work | Requirement 6 | append-only；不自动删除 |
| S6 portfolio risk | Required_Work | Requirement 7 | Production_Enforcement_Switch 为 Approval_Gated_Operation |
| S7 Prefect and scheduler | Required_Work | Requirement 8 | Production_Enforcement_Switch 为 Approval_Gated_Operation |
| S8 Dashboard and observability | Required_Work | Requirement 9 | 正式读模型切换受 Projection_Cutover_Dependency 约束 |
| S9 migration, cutover, and full acceptance | Required_Work | Requirements 10–11, 13 | 每种环境变更分别授权；完成影响由 ReleaseScopeManifest 决定 |
| D1, D2, D3 | Separate initiative | Requirement 12 | 不属于 Spec 完成范围 |

Approval_Gated_Operation 包括每个 Forward_Only_Migration apply 实例、Classification_Backfill、Projection_Cutover 和六个 Production_Enforcement_Switch。任何授权均不隐含其他授权。ReleaseScopeManifest 只声明范围和完成影响，不构成 Separate_Write_Approval。

## Requirements

### Requirement 1: S0 Audit-First Baseline and Safe Unlocking

**User Story:** 作为实施负责人，我希望先冻结基线并审计复用现有实现，再补充已证明的缺口，从而避免基线漂移、重复实现和无证据替换。

#### Acceptance Criteria

1. THE Governance_System SHALL 为所有 Existing_Artifact 建立包含路径、版本、工作区状态和关联测试的 Audit_Inventory。
2. WHEN S0 在首个 Behavior_Changing_Work 前开始, THE Governance_System SHALL 一次性采集 git revision、策略身份、执行身份、Active_Strategy 清单、champion identity 和关键投影计数并创建 Baseline_Artifact。
3. WHEN Baseline_Artifact 创建完成, THE Governance_System SHALL 冻结 Baseline_Artifact 的身份、内容哈希、采集时间和采集代码版本。
4. WHEN 后续 S0 环境验收任务运行, THE Acceptance_System SHALL 验证既有 Baseline_Artifact 的身份、哈希、引用和环境对应关系并追加 Baseline_Verification_Evidence。
5. IF Baseline_Artifact 无法证明采集时间早于首个 Behavior_Changing_Work, THEN THE Governance_System SHALL 将 S0 的 Environment_Acceptance_State 设置为 `BLOCKED` 并追加原因码 `BASELINE_FREEZE_ORDER_UNPROVEN`。
6. IF 后续任务请求重新采集、覆盖或重写 Baseline_Artifact, THEN THE Governance_System SHALL 拒绝基线变更并追加不可变审计事件。
7. WHEN Existing_Artifact 映射到验收标准, THE Governance_System SHALL 为 Existing_Artifact 记录 `REUSE`、`RECONCILE` 或 `GAP` 的 Reuse_Decision。
8. IF 新实现提议缺少 Gap_Record, THEN THE Governance_System SHALL 拒绝新实现提议进入实施范围。
9. THE Governance_System SHALL 维护覆盖 Requirements 1–13 和 S0–S9 的 Traceability_Matrix。
10. THE Acceptance_System SHALL 使用固定输入建立跨 evidence、execution、outcome 和 risk 的 Golden_Harness。
11. THE Governance_System SHALL 将新 Feature_Flag 的初始状态设置为 `OFF`、`SHADOW` 或 `AUDIT`。
12. WHEN Layer_1_Acceptance 通过, THE Governance_System SHALL 仅解锁 Dependency_Safe_Work 和 Non_Production_Write_Work。
13. WHILE Environment_Acceptance_State 为 `BLOCKED`, THE Governance_System SHALL 保持相关 Completion_Required_Work_Set 项的完成状态为未完成。

### Requirement 2: S1 Evidence Identity and Historical Preservation

**User Story:** 作为系统用户，我希望历史推荐继续可重建和查询，同时历史重建不进入真实前瞻指标，从而保留历史价值并保持证据可信度。

#### Acceptance Criteria

1. THE Evidence_System SHALL 为每个 run、recommendation 和 outcome 持久化 Evidence_Class。
2. WHEN 历史日期在目标交易日之后重建, THE Evidence_System SHALL 将重建证据分类为 `HISTORICAL_RECONSTRUCTION`。
3. WHEN Evidence_System 创建 HISTORICAL_RECONSTRUCTION, THE Evidence_System SHALL 持久化 Complete_Provenance 和 Reconstruction_Batch_Identity。
4. THE Evidence_System SHALL 以零删除和零覆盖方式保留所有 Historical_Record 与原始时间字段。
5. IF run name、evaluation scope 或改写时间字段被用于声明 `LIVE_FORWARD`, THEN THE Evidence_System SHALL 拒绝 `LIVE_FORWARD` 声明。
6. WHEN LIVE_KPI、forward gate 或 champion evidence 被计算, THE Evidence_System SHALL 只选择 Complete_Provenance 可验证的 `LIVE_FORWARD`。
7. WHEN Historical_Query 被调用, THE Evidence_System SHALL 返回按 Evidence_Class 标记且按 Signal_Date 或 Trade_Date 排序的 Historical_Record。
8. WHILE 策略读取历史输入, THE Evidence_System SHALL 强制执行 As_Of_Boundary。
9. IF Existing_Artifact 无法证明真实生成时点或来源, THEN THE Evidence_System SHALL 将 Existing_Artifact 分类为 `UNVERIFIED`。
10. WHERE Classification_Backfill 获得专属 Separate_Write_Approval, THE Migration_System SHALL 只写新增 provenance 与 legacy audit 字段。
11. WHEN Classification_Backfill 完成, THE Migration_System SHALL 证明 Historical_Record 总数不变且第二次执行满足 No_Op_Verification。

### Requirement 3: S2 Canonical and Versioned Execution

**User Story:** 作为策略研究和生产维护者，我希望所有结果共享同一执行契约，从而使收益、回撤和发布比较具有一致语义。

#### Acceptance Criteria

1. THE Execution_System SHALL 为 ExecutionPolicy 提供不可变版本、规范化内容和 Execution_Policy_Fingerprint。
2. THE Execution_System SHALL 让 research backtest、historical reconstruction outcome 和 LIVE formal outcome 使用 Canonical_Execution_Core。
3. WHEN T_Plus_One 启用, THE Execution_System SHALL 使固定回归样本中的 `entry_date == exit_date` 数量等于零。
4. WHEN 相同输入、Source_Snapshot_ID 和 ExecutionPolicy 进入不同执行路径, THE Execution_System SHALL 产生 Execution_Parity。
5. WHEN ExecutionPolicy 内容变化, THE Execution_System SHALL 创建新版本并保持旧结果可查询。
6. THE Execution_System SHALL 将最终仓位限制为所有适用 Position_Cap 的最小值。
7. IF 运行路径尝试绕过 Position_Cap, THEN THE Execution_System SHALL 拒绝仓位计算结果。
8. THE Acceptance_System SHALL 用 Golden_Harness 验证 T_Plus_One、费用、滑点、涨跌停、gap、同日 high-low 冲突和 trailing 语义。

### Requirement 4: S3 Profiles, True-Forward Ledger, and Champion State

**User Story:** 作为策略负责人，我希望每个启用策略具有可复现画像，并且 champion 只由当前身份的真实前瞻证据支持。

#### Acceptance Criteria

1. THE Release_Governance_System SHALL 为每个 Active_Strategy 生成独立 Canonical_Profile。
2. WHEN Canonical_Profile 的交易数为零, THE Release_Governance_System SHALL 将 Canonical_Profile 标记为 `insufficient_sample`。
3. THE True_Forward_Ledger SHALL 只接受 Complete_Provenance 可验证的 `LIVE_FORWARD` recommendation 和 outcome。
4. WHEN provenance-clean true-forward `n=0`, THE Release_Governance_System SHALL 返回 `UNVERIFIED_CURRENT_POLICY`。
5. WHEN strategy、policy、ExecutionPolicy、snapshot schema 或 Code_Revision 与 Champion_State 身份不一致, THE Release_Governance_System SHALL 将 Champion_State 转换为 `STALE_UNVERIFIED`。
6. THE Release_Governance_System SHALL 在 Evidence_Package 中报告样本数、distinct signal days、覆盖率、置信状态和失败门槛。
7. THE Release_Governance_System SHALL 按 board、market regime、strategy、score bucket 和月份分层报告 Evidence_Package。
8. IF 发布请求只引用 HISTORICAL_RECONSTRUCTION、RESEARCH_BACKTEST 或单一正向切片, THEN THE Release_Governance_System SHALL 拒绝 `PRODUCTION_CHAMPION` 状态。

### Requirement 5: S4 Holdout Integrity and Mechanically Decidable Release Governance

**User Story:** 作为研究治理负责人，我希望同一底层 holdout 无法通过更换 token 重复窥探，并且证据阶段与研究路径分开记录，从而让发布资格可机械判定。

#### Acceptance Criteria

1. THE Release_Governance_System SHALL 根据数据快照、实际日期集合、universe、label policy 和 ExecutionPolicy 生成 Holdout_Population_Identity。
2. IF 调用方提供的 token 被用作 Holdout_Population_Identity, THEN THE Release_Governance_System SHALL 拒绝 holdout claim。
3. WHEN Holdout_Population_Identity 已消费, THE Release_Governance_System SHALL 拒绝由不同 experiment、hypothesis 或 token 发起的再次解封。
4. WHEN Research_Family 首次解封 holdout, THE Release_Governance_System SHALL 冻结 baseline、候选集合、指标、阈值和允许 Evidence_Scope 集合。
5. THE Release_Governance_System SHALL 将每条证据的 Evidence_Scope 持久化为 `TRAIN`、`VALIDATION`、`PURGED_HISTORICAL_OOS`、`UNTOUCHED_HOLDOUT`、`SHADOW_FORWARD` 或 `LIVE_CANARY` 中的一个值。
6. THE Release_Governance_System SHALL 将每条证据的 Research_Path_Classification 与 Evidence_Scope 分开持久化。
7. WHEN Research_Path_Classification 为 `EXPLORATORY` 或 `PATH_A`, THE Release_Governance_System SHALL 将 Production_Eligibility 设置为 `false`。
8. IF Evidence_Scope 或 Research_Path_Classification 缺失或不属于权威枚举, THEN THE Release_Governance_System SHALL 返回 `RELEASE_CLASSIFICATION_INVALID` 并拒绝发布判定。
9. THE Release_Governance_System SHALL 使用 Evidence_Scope 属于 Research_Family 允许集合、Research_Path_Classification 等于 `GOVERNED_RELEASE`、Production_Eligibility 等于 `true`、Complete_Provenance 有效和身份一致的布尔合取计算 Release_Eligibility_Decision。
10. THE Release_Governance_System SHALL 使用最小交易数、distinct signal days、覆盖率、bootstrap confidence interval、MDD regression 和 multiple-testing correction 判定 challenger。
11. WHEN current policy、state、deployment 或 Evidence_Package identity 不一致, THE Release_Governance_System SHALL 拒绝 publish。
12. WHEN Release_Eligibility_Decision 生成, THE Release_Governance_System SHALL 持久化全部输入枚举、布尔结果和稳定 reason code。
13. THE Release_Governance_System SHALL 将 research proposal 权限与 formal publish 权限分离。

### Requirement 6: S5 Data Quality and Outcome Provenance

**User Story:** 作为数据和绩效维护者，我希望 formal 证据与 outcome 可追溯且幂等，从而识别缺失数据、重复结果和陈旧投影。

#### Acceptance Criteria

1. THE Data_Quality_System SHALL 在 formal 执行前生成 DataQualityDecision。
2. IF Active_Strategy 声明的必需数据缺失或超过 freshness 限制, THEN THE Data_Quality_System SHALL 返回 `BLOCKED`。
3. WHEN 可选数据缺失或使用 synthetic 字段, THE Data_Quality_System SHALL 在 recommendation evidence 中记录 `DEGRADED` 和原因。
4. THE Outcome_System SHALL 为每个 outcome 生成 Outcome_Calculation_Fingerprint。
5. WHEN 相同 Outcome_Calculation_Fingerprint 和结果哈希已经存在, THE Outcome_System SHALL 复用现有 outcome version。
6. WHEN Outcome_Calculation_Fingerprint 的任一组成部分变化, THE Outcome_System SHALL 追加新 outcome version。
7. THE Outcome_System SHALL 通过原子更新维护 Current_Projection。
8. WHEN stale recompute 执行, THE Outcome_System SHALL 只选择 fingerprint 缺失或不匹配的 recommendation。
9. WHEN duplicate、orphan、missing-current 或 fingerprint-missing audit 执行, THE Outcome_System SHALL 保留全部 Historical_Record 并从 current KPI 排除无效记录。
10. WHILE 辅助数据反事实证据不足, THE Data_Quality_System SHALL 将辅助数据影响报告为 `UNKNOWN`。

### Requirement 7: S6 Portfolio Risk Controls

**User Story:** 作为风险负责人，我希望推荐和模拟组合受组合总敞口、集中度、单日亏损和回撤控制，从而限制相关损失放大。

#### Acceptance Criteria

1. THE Risk_System SHALL 提供版本化 PortfolioRiskPolicy。
2. THE Risk_System SHALL 维护版本化 PortfolioRiskState。
3. WHEN 新仓位请求进入持久化边界, THE Risk_System SHALL 原子执行 Risk_Gate 和 exposure reservation。
4. THE Risk_System SHALL 将批准仓位限制为所有适用 Position_Cap 的最小值。
5. WHEN 单日亏损或回撤达到 PortfolioRiskPolicy 阈值, THE Risk_System SHALL 产生 `REDUCE` 或 `HALT` 判定。
6. WHILE 仓位因涨跌停或流动性限制不可卖出, THE Risk_System SHALL 保持仓位占用风险预算。
7. THE Risk_System SHALL append-only 保存 state hash、policy fingerprint、requested size、approved size、reason code 和判定时间。
8. THE Acceptance_System SHALL 用多票同跌、同题材集中、连续跌停、gap-down、弱势 regime、stale quote 和并发 reservation 验证 Risk_Gate。
9. WHERE risk Production_Enforcement_Switch 获得专属 Separate_Write_Approval, THE Risk_System SHALL 在 Feature Flag Registry 绑定的 Layer_2_Evidence_Set_ID 通过后启用 `ENFORCE`。

### Requirement 8: S7 Prefect and Scheduler Reliability

**User Story:** 作为运维人员，我希望每个调度实例只产生一个副作用执行，并且业务失败、重试和补跑状态可核验。

#### Acceptance Criteria

1. THE Scheduler_System SHALL 为每个 Prefect metadata database 维持一个有效 Prefect_Control_Plane owner。
2. IF 第二个 owner 在有效 lease 期间启动, THEN THE Scheduler_System SHALL 拒绝第二个 owner 取得控制权。
3. THE Scheduler_System SHALL 为每个 work pool 和 queue 配置大于零的显式并发上限。
4. THE Scheduler_System SHALL 使用数据库唯一约束、compare-and-set 和 lease 实现 Occurrence_Claim。
5. WHEN 两个进程并发 claim 同一 Scheduled_Occurrence, THE Scheduler_System SHALL 只允许一个 Occurrence_Claim 获得执行权。
6. WHEN 业务处理失败, THE Scheduler_System SHALL 向 Prefect 传播类型化失败以触发配置的 retry 或 failed 状态。
7. THE Scheduler_System SHALL 为每类 Scheduled_Occurrence 配置 Misfire_Policy、grace window 和最大补跑次数。
8. WHEN scheduler polling 读取状态, THE Scheduler_System SHALL 使用新事务边界读取 MySQL 最新已提交状态。
9. THE Acceptance_System SHALL 在 Real_Prefect_Acceptance 中验证 owner、并发、claim、lease 接管、retry、misfire 和 false-green reconciliation。

### Requirement 9: S8 Dashboard and Operational Observability

**User Story:** 作为用户和运维人员，我希望页面分开展示证据类型并暴露陈旧、质量、调度和风险状态，从而定位运行异常。

#### Acceptance Criteria

1. THE Observability_System SHALL 在 Dashboard_Projection 中分开呈现 `LIVE_FORWARD`、`HISTORICAL_RECONSTRUCTION` 和 research evidence。
2. THE Observability_System SHALL 根据 Evidence_Class、Actual_Generated_At 和业务日期选择 current run。
3. WHEN Dashboard_Projection 刷新, THE Observability_System SHALL 使用有限轮询或可取消的 server-sent events 生命周期。
4. THE Observability_System SHALL 展示 last-updated time、Evidence_Class、reconstruction label、DataQualityDecision 和 Risk_Gate 状态。
5. WHEN Dashboard_Projection 年龄超过 Stale_Threshold, THE Observability_System SHALL 标记 Dashboard_Projection 为 stale。
6. THE Observability_System SHALL 让 Correlation_ID 贯穿 API、scheduler、Prefect、run、provider 和 database 日志。
7. THE Observability_System SHALL 为 latency、heartbeat stale、queue delay、flow failure、quality blocker、identity mismatch 和 risk halt 提供可配置告警条件。
8. THE Observability_System SHALL 通过 Application_Lifespan 管理启动、资源初始化、资源释放和关闭。
9. THE Acceptance_System SHALL 验证 refresh cleanup、stale display、evidence labels、current-run selection、quality scorecard 和 risk status。

### Requirement 10: S9 Immutable Release Scope, Safe Migration, and Approval-Gated Activation

**User Story:** 作为发布负责人，我希望每个环境操作在不可变发布范围中具有明确完成影响、独立授权和证据依赖，从而安全完成 Spec 且不误报生产就绪。

#### Acceptance Criteria

1. THE Governance_System SHALL 要求 ReleaseScopeManifest 对 Approval_Gated_Operation_Registry 中每个操作记录一个且仅一个条目。
2. THE Governance_System SHALL 要求 ReleaseScopeManifest 将 `migration_apply.task_1_3_schema_v1` 记录为 `INCLUDED` 和 `COMPLETION_CRITICAL`。
3. THE Governance_System SHALL 要求 ReleaseScopeManifest 为 Classification_Backfill、Projection_Cutover 和六个 Production_Enforcement_Switch 分别显式记录 Release_Inclusion 与 Completion_Impact。
4. IF ReleaseScopeManifest 缺少、重复或包含无法解析的 Approval_Gated_Operation_ID, THEN THE Governance_System SHALL 拒绝 ReleaseScopeManifest 并阻止 Approval_Gated_Operation 执行。
5. IF ReleaseScopeManifest 将 `EXCLUDED` 与 `COMPLETION_CRITICAL` 组合, THEN THE Governance_System SHALL 拒绝 ReleaseScopeManifest。
6. WHEN Approval_Gated_Operation 被记录为 `EXCLUDED`, THE Governance_System SHALL 将 Operation_Execution_State 设置为 `NOT_AUTHORIZED_NOT_EXECUTED` 并从 Completion_Required_Work_Set 排除 Approval_Gated_Operation。
7. WHEN Classification_Backfill、Projection_Cutover 或 Production_Enforcement_Switch 被记录为 `INCLUDED` 和 `COMPLETION_CRITICAL`, THE Acceptance_System SHALL 将对应操作加入 Completion_Required_Work_Set。
8. WHILE Approval_Gated_Operation 的 Completion_Impact 为 `ACTIVATION_ONLY`, THE Acceptance_System SHALL 从 Spec 完成阻塞条件中排除 Approval_Gated_Operation。
9. WHEN ReleaseScopeManifest 冻结后发生范围变更请求, THE Governance_System SHALL 创建新 manifest identity、保留旧 manifest、使旧完成判定失效并在新操作执行前完成重新预登记。
10. THE Governance_System SHALL 维护 Feature Flag Registry 中恰好六个相互独立的 Feature_Flag_ID。
11. THE Governance_System SHALL 将每个 Feature_Flag_ID 绑定到 Feature Flag Registry 指定的 Layer_2_Evidence_Set_ID 和 Projection_Cutover_Dependency。
12. IF Production_Enforcement_Switch 的 Feature_Flag_ID、Layer_2_Evidence_Set_ID 或 Projection_Cutover_Dependency 与 Feature Flag Registry 不匹配, THEN THE Governance_System SHALL 拒绝 Production_Enforcement_Switch。
13. THE Migration_System SHALL 在任何 migration apply 之前生成 Read_Only_Migration_Plan。
14. IF Read_Only_Migration_Plan 包含 Additive_Allowlist 之外的操作, THEN THE Migration_System SHALL 拒绝 migration apply。
15. WHEN migration apply 进入授权检查, THE Migration_System SHALL 验证 Verified_Restore_Point、Capacity_Check 和 Restore_Capacity_Criteria_Manifest。
16. WHERE Forward_Only_Migration apply 获得专属 Separate_Write_Approval, THE Migration_System SHALL 在目标环境、Migration_Checksum 和计划哈希匹配时允许 apply。
17. WHEN Forward_Only_Migration apply 开始, THE Migration_System SHALL 验证全部 Precondition。
18. WHEN Forward_Only_Migration apply 完成, THE Migration_System SHALL 验证全部 Postcondition。
19. WHEN 同一 Forward_Only_Migration 连续第二次执行, THE Migration_System SHALL 证明 No_Op_Verification 成立。
20. IF migration 请求包含 drop、truncate、历史记录删除、破坏性重建或 destructive downgrade, THEN THE Migration_System SHALL 拒绝 migration 请求。
21. WHERE Classification_Backfill 获得独立 Separate_Write_Approval, THE Migration_System SHALL 验证分类计划哈希后执行 Classification_Backfill。
22. WHERE Projection_Cutover 获得独立 Separate_Write_Approval, THE Migration_System SHALL 在 Shadow_Threshold_Manifest 的全部门槛通过后执行 Projection_Cutover。
23. IF Shadow_Mode 差异超过 Shadow_Threshold_Manifest 的任一门槛, THEN THE Migration_System SHALL 停止 Projection_Cutover 并保持旧读取路径。
24. WHEN Read_Path_Rollback 被触发, THE Migration_System SHALL 恢复旧读取开关并保留新增 append-only 数据。
25. WHERE Production_Enforcement_Switch 获得专属 Separate_Write_Approval, THE Governance_System SHALL 仅在关联 Layer_2_Evidence_Set_ID 通过且 Projection_Cutover_Dependency 满足后把获批 Feature_Flag 设置为 `ENFORCE`。
26. THE Migration_System SHALL 从迁移、回填和切换日志中排除 credential、secret 和完整敏感 payload。
27. WHEN 首个 Behavior_Changing_Work 被请求, THE Governance_System SHALL 验证 ReleaseScopeManifest 已冻结、内容哈希可核对且创建时间早于 Behavior_Changing_Work 开始时间。

### Requirement 11: Layered, Revision-Bound Acceptance and Completion Semantics

**User Story:** 作为验收负责人，我希望代码完成、环境验收、范围完成和生产激活分开记录，从而防止旧 revision、mock 结果或未授权操作被误报为完成。

#### Acceptance Criteria

1. THE Governance_System SHALL 为每个任务分别记录 Code_State、Environment_Acceptance_State 和适用的 Production_Activation_State。
2. THE Governance_System SHALL 将 Task_1_3 的 `CODE_COMPLETE` 绑定到 Task_1_3_Revision_Binding。
3. THE Governance_System SHALL 将当前 Task_1_3 的 Environment_Acceptance_State 记录为 `BLOCKED`。
4. WHEN Task_1_3 的 replay、evidence identity、Historical_Query、LIVE selector、迁移契约或关联测试依赖哈希变化, THE Governance_System SHALL 使原 `CODE_COMPLETE` 与原 Layer_1_Acceptance 失效并将 Code_State 设置为 `IN_PROGRESS`。
5. WHEN Task_1_3 在新 Task_1_3_Revision_Binding 上通过 Layer_1_Acceptance, THE Governance_System SHALL 将 Task_1_3 的 Code_State 重新设置为 `CODE_COMPLETE`。
6. WHILE Task_1_3 缺少真实 MySQL migration、Real_Prefect_Acceptance replay、持久化 Historical_Query 或 LIVE_KPI 排除证据, THE Acceptance_System SHALL 保持 Task_1_3 为未完成。
7. WHEN Layer_1_Acceptance 通过, THE Acceptance_System SHALL 保持 Environment_Acceptance_State 不变。
8. WHEN Real_MySQL_Acceptance 和 Real_Prefect_Acceptance 全部通过, THE Acceptance_System SHALL 根据保存且 revision-matched 的环境证据把关联 Environment_Acceptance_State 设置为 `ACCEPTED`。
9. THE Acceptance_System SHALL 根据全部非 Approval_Gated_Operation 的 Required_Work 和 ReleaseScopeManifest 中 `INCLUDED` + `COMPLETION_CRITICAL` 的操作构造 Completion_Required_Work_Set。
10. WHILE Completion_Required_Work_Set 中任一项的 Environment_Acceptance_State 或 Operation_Execution_State 未达到适用的 `ACCEPTED`, THE Acceptance_System SHALL 保持 Spec_Completion_State 为 `BLOCKED`。
11. IF Completion_Critical_Operation 未获得 Separate_Write_Approval, THEN THE Governance_System SHALL 将 Operation_Execution_State 记录为 `NOT_AUTHORIZED` 并保持 Spec_Completion_State 为 `BLOCKED`。
12. WHEN Completion_Required_Work_Set 的全部项目通过验收, THE Acceptance_System SHALL 将 Spec_Completion_State 设置为 `COMPLETE`。
13. IF Required_Production_Enforcement_Set 中任一 Feature_Flag_ID 的 Production_Activation_State 不为 `ACTIVE`, THEN THE Governance_System SHALL 拒绝“production ready”声明。
14. IF Required_Production_Enforcement_Set 中任一 Feature_Flag 未处于 `ENFORCE`, THEN THE Governance_System SHALL 拒绝“production ready”声明。
15. THE Acceptance_System SHALL 在最终证据中报告 ReleaseScopeManifest identity、provenance-clean true-forward 样本状态、已知未知项、全部 `NOT_AUTHORIZED_NOT_EXECUTED` 操作和未激活 Feature_Flag_ID。

### Requirement 12: Deferred Initiatives and Security Boundary

**User Story:** 作为项目所有者，我希望长期数据补齐、新策略研究和券商交易保持独立立项，从而避免本次加固扩大到未授权数据或资金操作。

#### Acceptance Criteria

1. THE Scope_Guard_System SHALL 将 D1_Full_Historical_Backfill 排除在 Spec 完成范围之外。
2. THE Scope_Guard_System SHALL 将 D2_New_Alpha 排除在 Spec 完成范围之外。
3. THE Scope_Guard_System SHALL 将 D3_Brokerage_Live_Trading 排除在 Spec 完成范围之外。
4. IF Spec 工作请求 Brokerage_Access, THEN THE Scope_Guard_System SHALL 拒绝 Spec 工作请求。
5. IF Spec 工作请求读取或写入券商 credential、account、position、balance 或 order, THEN THE Scope_Guard_System SHALL 拒绝访问请求并记录不含敏感值的审计事件。
6. THE Scope_Guard_System SHALL 将 Risk_System 的本 Spec 范围限制为 recommendation、simulation、paper 和 shadow portfolio。
7. WHEN D1_Full_Historical_Backfill、D2_New_Alpha 或 D3_Brokerage_Live_Trading 被提出, THE Scope_Guard_System SHALL 要求独立需求、设计、任务、权限和验收流程。

### Requirement 13: Preregistered Acceptance and Mandatory Correctness Evidence

**User Story:** 作为审计和验收负责人，我希望阈值、环境、命令、证据位置和正确性证明在观察结果前固定，从而防止移动门槛或把可选任务误解为可选证据。

#### Acceptance Criteria

1. WHEN 首个 Shadow_Mode 样本生成前, THE Governance_System SHALL 冻结适用的 Shadow_Threshold_Manifest。
2. WHEN migration preflight 开始前, THE Governance_System SHALL 冻结适用的 Restore_Capacity_Criteria_Manifest。
3. WHEN Layer_2_Acceptance 开始前, THE Governance_System SHALL 冻结适用的 Layer_2_Evidence_Manifest。
4. WHEN Layer_2_Acceptance 开始前, THE Governance_System SHALL 冻结适用的 Real_Environment_Identity_Manifest。
5. WHEN Layer_2_Acceptance 开始前, THE Governance_System SHALL 冻结适用的 Acceptance_Command_Manifest。
6. WHEN Layer_2_Acceptance 开始, THE Acceptance_System SHALL 验证 Layer_2_Evidence_Manifest、Real_Environment_Identity_Manifest 和 Acceptance_Command_Manifest 的创建时间早于验收开始时间。
7. WHEN Layer_2_Acceptance 生成证据, THE Acceptance_System SHALL 按 Layer_2_Evidence_Manifest 验证 schema、Evidence_URI、内容哈希、环境身份、Code_Revision、命令身份、断言和 attempt identity。
8. IF Layer_2_Acceptance 的环境身份、命令身份、schema、Evidence_URI 或内容哈希与 Pre_Registered_Manifest 不匹配, THEN THE Acceptance_System SHALL 将关联 Environment_Acceptance_State 保持为 `BLOCKED` 或设置为 `FAILED`。
9. WHEN Pre_Registered_Manifest 被替代, THE Governance_System SHALL 创建新 manifest identity 并保留旧 manifest 与全部既有 attempt。
10. THE Acceptance_System SHALL 为每个 Correctness_Obligation 保存成功的属性测试证据或成功的 Equivalent_Correctness_Evidence。
11. WHEN Optional_Property_Test_Task 被评估为可跳过, THE Acceptance_System SHALL 验证 Equivalent_Correctness_Evidence 的不可变内容哈希、可解析 Evidence_URI、Correctness_Obligation 引用、需求引用、输入域、oracle、当前 Revision_Binding 和成功结果。
12. WHEN Optional_Property_Test_Task 被评估为可跳过, THE Acceptance_System SHALL 验证关联 Equivalence_Assessment 的结论为 `VALID`。
13. IF Optional_Property_Test_Task 缺少有效 Equivalent_Correctness_Evidence 或有效 Equivalence_Assessment, THEN THE Acceptance_System SHALL 将 Optional_Property_Test_Task 分类为必需并阻止关联 Code_State 进入 `CODE_COMPLETE`。
14. WHEN Equivalent_Correctness_Evidence 的代码、依赖、输入域、oracle 或 Revision_Binding 变化, THE Acceptance_System SHALL 使旧 Equivalence_Assessment 失效并要求重新审计或执行属性测试。
15. THE Governance_System SHALL 让 Acceptance_Command_Manifest 和全部验收日志只引用 secret handle 并排除 credential、secret 和完整敏感 payload。

## Fast Task Phase 2 Execution Boundary

| Item | Phase 2 disposition |
|---|---|
| `requirements.md` | Updated by this phase |
| `.config.kiro` | Verified and written by this phase |
| `design.md` | Unchanged |
| `tasks.md` | Unchanged |
| Product code, migrations, tests, MySQL, Prefect | No implementation or execution in this phase |

Phase 2 在上述两个文档写入后停止；后续设计、任务协调、代码实施、Production_Write 和环境验收由独立阶段执行。
