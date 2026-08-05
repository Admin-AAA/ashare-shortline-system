# 生产证据、执行一致性与风险加固追踪矩阵

本矩阵由任务 0.5 重建，映射 Requirements 1–13 与 S0–S9。每一行连接：artifact/Gap_Record → 代码 → migration → Layer 1 测试 → Correctness_Obligation → Layer 2 证据 → manifest → approval → 状态。

**治理对象库**：`.kiro/specs/production-evidence-risk-hardening/governance/store/<kind>/<sha256>.json`，`object_id=<kind>:sha256:<hash>`，`Evidence_URI=evidence://sha256/<digest>`。冻结于 2026-08-04 的 S0 根对象：

| 对象 | object_id |
|---|---|
| Baseline_Artifact（任务 0.1 基线） | `baseline_artifact:sha256:4d3a30a5ba3d3da7601967377374d654081a0c0210dbe5afeea9530f2d03b050` |
| Baseline_Artifact（任务 7.1 控制面基线） | `baseline_artifact:sha256:161c6504d1f3ba6e71ebc8f7aa83edbc26b8f916f32853685c11b3baf2205fc4` |
| Audit_Inventory（23 条 REUSE/RECONCILE + 3 个 Gap_Record） | `audit_inventory:sha256:2b02c356eeaf78c738e91d29929aa77fc7247230eec3169e87a04be1431a7c58` |
| Registry Snapshot（9 operations + 6 flags） | `registry_snapshot:sha256:c5cf11f0e46330b6ac2ff457423c514b256b680254ff03b888095189c2b2bfee` |
| ReleaseScopeManifest | `release_scope_manifest:sha256:658bb22c5d8575ae2fd8bb68c9774b9d8f8eec8d6be21420e5d3396f0e100188` |
| Behavior_Change_Barrier 事件 | `governance_event:sha256:6730529e74d67906633cde15f1be6dffe099656950491eb571af89639c64ae33` |

**ReleaseScopeManifest 冻结决策（2026-08-04，用户"尽量全量"）**：`migration_apply.task_1_3_schema_v1`=INCLUDED+COMPLETION_CRITICAL（强制）；`classification_backfill.provenance_v1`=INCLUDED+COMPLETION_CRITICAL；`projection_cutover.production_read_v1`=INCLUDED+COMPLETION_CRITICAL；六个 `enforcement.*`=INCLUDED+ACTIVATION_ONLY（不阻塞 Spec COMPLETE；9.30 独立判定 production-ready）。

**Feature Flag Registry（恰好六行，逐 flag 独立）**：

| Flag_ID | Layer_2_Evidence_Set_ID | Cutover Dependency | 运行时开关（安全默认） |
|---|---|---|---|
| `evidence_projection` | `L2_FLAG_EVIDENCE_PROJECTION_V1` | `projection_cutover.production_read_v1` | `ASHARE_HARDENING_EVIDENCE_PROJECTION_MODE`（shadow） |
| `canonical_execution` | `L2_FLAG_CANONICAL_EXECUTION_V1` | NONE | `ASHARE_HARDENING_CANONICAL_EXECUTION_MODE`（shadow） |
| `release_gate` | `L2_FLAG_RELEASE_GOVERNANCE_V1` | NONE | `ASHARE_HARDENING_RELEASE_GATE_MODE`（audit） |
| `portfolio_risk` | `L2_FLAG_PORTFOLIO_RISK_V1` | NONE | `ASHARE_HARDENING_PORTFOLIO_RISK_MODE`（shadow） |
| `scheduler_claim` | `L2_FLAG_SCHEDULER_CLAIM_V1` | NONE | `ASHARE_HARDENING_SCHEDULER_CLAIM_MODE`（shadow） |
| `dashboard_refresh` | `L2_FLAG_DASHBOARD_OBSERVABILITY_V1` | `projection_cutover.production_read_v1` | `ASHARE_HARDENING_DASHBOARD_REFRESH_MODE`（shadow） |

**Task_1_3_Revision_Binding**：scope=`task_1_3_historical_reconstruction`，六类 digest——REPLAY（`backend/scripts/replay_all_days.py`）、EVIDENCE_IDENTITY（`backend/app/services/evidence/identity.py`）、HISTORICAL_QUERY（历史查询/as-of reader）、LIVE_SELECTOR（全部 LIVE_KPI/forward gate/ledger/champion predicate）、MIGRATION_CONTRACT（`backend/alembic/versions/0004_evidence_provenance.py`）、ASSOCIATED_TESTS（`backend/tests/test_historical_reconstruction_replay.py` 等）。任一 digest 变化 → `REVISION_BINDING_STALE`，Code_State 回 `IN_PROGRESS`，旧事实保留。机制：`backend/app/governance/binding.py`。

**Wave 1 当前冻结/验证对象（2026-08-04）**：Task 1.3 binding=`revision_binding:sha256:c709c9bd690a1f716e6a6b64e84c701689d12c4ccbbadfdb4d56c124ea45ac6f`（binding hash `74fb2143c9ce7c8b5d1301638b0e49a3f7bf40c1eb6dce1658474c351cc4320d`）；Layer 1 validation=`revision_binding_validation:sha256:bdb1505f9347bbdb2ccf3e48b05f7cfcc808eacc7cb813c9a1885528c18811c5`，仅支持 Code_State=`CODE_COMPLETE`，Environment=`BLOCKED`、task_complete=false。Canonical execution Shadow_Threshold_Manifest=`shadow_threshold_manifest:sha256:a5730bda3ff85a49c241be5a42a774e6e68f752af974ba89416d08a55b3805eb`；门槛为逐字段 mismatch count 与 T+1 same-day exit count 均 `==0`、minimum sample=100、失败 `BLOCK_CUTOVER`，旧 projection 保持权威。Property 6–14/45/46 的 11 条 passing evidence 已存入 `governance/store/property_test_evidence/`；均为 Layer 1，不产生环境 ACCEPTED。

**状态正交性**：Code_State / Environment_Acceptance_State / Operation_Execution_State / Production_Activation_State 相互独立（`app/governance/state.py`）。Layer 1 永不能产生环境 `ACCEPTED`（`apply_layer1_environment_result` 强制）。

## Requirement → 实施 → 验证

| 需求 | 组件与实施任务 | Migration | Layer 1 测试 | Correctness_Obligation | Layer 2 验收 | 状态轨道 |
|---|---|---|---|---|---|---|
| R1 S0 审计先行基线与安全解锁 | `app/governance/`（envelope/store/events/state/binding/registries/scope/freeze/manifests/obligations）；任务 0.1–0.10 | 无（治理对象库为文件 CAS） | `tests/governance/test_governance_core.py`、0.11–0.20（Property 1–5, 50–51, 53–54 + 示例） | CO-PR-001–005, 050, 051, 053, 054 | 0.21 只读基线对应验证 | Code: 见 tasks.md 勾选；Env: BLOCKED→0.21 |
| R2 S1 Evidence identity 与历史保留 | `app/services/evidence/identity.py`、`scripts/replay_all_days.py`、Historical_Query/as-of reader、统一 `LIVE_FORWARD` predicate；任务 1.1–1.12 | `0004_evidence_provenance`（additive） | `test_evidence_identity.py`、`test_historical_reconstruction_replay.py`、Property 6–10, 45, 46 | CO-PR-006–010, 045, 046 | 1.3 四类 revision-matched 证据（migration/no-op、真实 Prefect replay、持久化 Historical_Query、持久化 LIVE exclusion） | Code: revision-bound；Env: BLOCKED→1.3/9.3 |
| R3 S2 Canonical ExecutionPolicy 与逐笔一致 | `app/engine/execution_policy.py`、`app/engine/canonical.py`、canonical core 与三 adapter、cap 组合 `MIN_ALL_APPLICABLE`；任务 2.1–2.10 | 复用 0004；identity 追加见 R6 | `test_execution_policy.py`、golden harness、Property 11–14 | CO-PR-011–014 | 2.10 真实 MySQL parity/T+1/append-only | Code；Env: BLOCKED→2.10 |
| R4 S3 Profiles/true-forward/Champion | Canonical_Profile、True_Forward_Ledger、Champion_State、Evidence_Package；任务 3.1–3.8 | 复用 0004 | Property 15–17 + 示例 3.7 | CO-PR-015–017 | 3.8 真实 mixed-class identity 验收 | Code；Env: BLOCKED→3.8 |
| R5 S4 Holdout 与发布治理 | Holdout_Population_Identity、single-use claim、Research_Family freeze、Release_Eligibility_Decision、分权；任务 4.1–4.10 | `0006_holdout_identity`（planned，additive） | Property 18–22 + 示例 4.9 | CO-PR-018–022 | 4.10 真实 MySQL 并发 claim/发布拒绝 | Code；Env: BLOCKED→4.10 |
| R6 S5 Data quality 与 outcome provenance | DataQualityDecision、Outcome_Calculation_Fingerprint、Current_Projection CAS、stale recompute、保留审计；任务 5.1–5.9 | `0005_outcome_fingerprint`（planned，additive） | Property 23–26 + 示例 5.8 | CO-PR-023–026 | 5.9 真实 MySQL idempotence/CAS | Code；Env: BLOCKED→5.9 |
| R7 S6 Portfolio risk | PortfolioRiskPolicy/State、原子 Risk_Gate/reservation、kill switches（audit-only 接入）；任务 6.1–6.8 | `0007_portfolio_risk`（planned，additive） | Property 27–28 + stress/并发 6.6/6.7 | CO-PR-027–028 | 6.8 真实 MySQL reservation 并发 | Code；Env: BLOCKED→6.8；券商访问永远拒绝 |
| R8 S7 Prefect/Scheduler | 单 owner lease、显式并发、Occurrence_Claim、typed retry、Misfire_Policy、poll 事务；任务 7.1–7.9 | `0008_scheduler_claim`（planned，additive） | Property 29–31 + 7.7 | CO-PR-029–031 | 7.8 真实 Prefect+MySQL；7.9 Task 1.3 substrate 只读就绪 | Code；Env: BLOCKED→7.8/7.9 |
| R9 S8 Dashboard/可观测性 | Dashboard_Projection、有限轮询/SSE、Correlation_ID、告警、lifespan；任务 8.1–8.6 | 无（读既有 projection） | Property 32 + 示例 8.5 + 前端 test/build | CO-PR-032 | 8.6 真实 projection/observability | Code；Env: BLOCKED→8.6 |
| R10 S9 发布范围/迁移/激活 | `app/governance/`（scope/manifests/approval/executor 边界/rollback/completion/report）；任务 9.1–9.30 | 执行链 `0004→0005→0006→0007→0008`（均 additive，逐操作授权） | Property 33–44, 47–49, 52 + 示例 9.28 | CO-PR-033–044, 047–049, 052 | 9.3 migration apply+no-op【授权#1】、9.4 backfill【授权#2】、9.5 cutover【授权#3】、9.6–9.11 六开关【授权#4–9】 | Operation/Activation: 逐操作独立记录 |
| R11 分层验收与完成语义 | Revision_Binding（`binding.py`）、正交状态机（`state.py`）、完成/就绪判定（9.29/9.30） | 无 | Property 44–48 | CO-PR-044–048 | 9.29 完成合取 + 最终报告；9.30 production-ready | Spec_Completion_State 独立 |
| R12 延期范围与券商边界 | 统一 Scope_Guard（CLI/API/scheduler/config/service）；任务 0.10、6.3、9.9 | 无 | Property 50 + 0.20 示例 | CO-PR-050 | 拒绝事件审计（安全 identity/hash/reason） | D1/D2/D3 永不进入 completion set |
| R13 预登记验收与强制正确性证据 | 五类 manifest 契约 + admission validator（`manifests.py`）、Correctness_Obligation 注册与双证明路径（`obligations.py`） | 无 | Property 51–54 + manifest 示例 | CO-PR-051–054 | 全部 Layer 2 走 admission pipeline | 任一漂移保持 BLOCKED 或记 FAILED |

## 验证命令

**Wave 2 Layer 1（2026-08-04）**：S3 复用 S1 provenance/selector 与现有 strategy/snapshot/ExecutionPolicy identity，新增 `app/services/release_governance.py` 的 Canonical_Profile、append-only True_Forward_Ledger、Champion_State 与 Evidence_Package；S4 新增 `app/governance/holdout.py`，协调既有 `backtest_holdout_claims`/research repository，并新增 additive-only `0006_holdout_identity.py`（未 apply）；S5 新增 `app/services/quality_outcome.py` 并由既有 `outcome_service.py` 重导出纯契约，新增 additive-only `0005_outcome_fingerprint.py`（未 apply）。Property 15–26 共 12 条 passing evidence 已存入 `governance/store/property_test_evidence/`。Wave 2 回归：backend 2419 passed/5 skipped/0 failed，架构与前端 test/build 通过。3.8/4.10/5.9 均保持 Environment=`BLOCKED`；为兼容尚未迁移的真实 MySQL，0005/0006 新列 ORM 映射在 9.3 migration ACCEPTED 前不启用。

**Wave 3 Layer 1（2026-08-04）**：S6 新增 `app/engine/portfolio_risk.py` 及既有 run persistence 边界的 audit-only `services/runs/risk_audit.py`，复用 canonical cap contract，新增 additive-only `0007_portfolio_risk.py`（未 apply，零券商访问）；S7 新增 `app/orchestration/scheduler_contracts.py`，协调既有 Prefect/scheduler 与 migration plan，新增 additive-only `0008_scheduler_claim.py`（未 apply）；S8 新增 `services/dashboard_hardening.py`、`services/observability.py`，在旧读路径仍权威的前提下 additive shadow 接线 dashboard/correlation/seven alerts/Application_Lifespan，前端复用 QueryClient 实现有限轮询、隐藏降频、dedupe、AbortSignal cleanup。Property 27–32 共 6 条 passing evidence 已落 CAS。Wave 3 回归：backend 2443 passed/5 skipped/0 failed，architecture/diff 通过；frontend 39 passed/build 成功。6.8/7.8/7.9/8.6 均保持 Environment=`BLOCKED`，portfolio/scheduler/dashboard enforcement 未启用。

**Wave 4 Layer 1（2026-08-04）**：新增 `app/governance/approval.py`、`gated_executor.py`、`completion.py`，复用 frozen ReleaseScopeManifest/registries/manifests/state/migration gates，实现 scope admission、EXCLUDED 终态、精确不可传递 Separate_Write_Approval、forward-only safety gates、Read_Path_Rollback、Completion_Required_Work_Set、独立 production-ready 与 deterministic final-report schema。Property 33–44/47–49/52 共 16 条（每条 100 examples）及 9.28 示例通过并记录 evidence。Wave 4 回归：backend 2468 passed/5 skipped/0 failed，governance 170 passed，frontend 39 passed/build，architecture/diff 通过。9.3–9.11 均未执行/未授权；9.29/9.30 未作真实环境最终判定；S9 Environment 与 Spec completion 仍 `BLOCKED`。

**Wave 5 read-only preparation（2026-08-04）**：已预登记/freeze Restore_Capacity=`restore_capacity_criteria_manifest:sha256:7b33e7294644773afe5b09c019133608dd58c39125d7d718960ab568f64e597b`、Real_Environment=`real_environment_identity_manifest:sha256:81bcdeab872c7c43b00d8cf262c8a6b2978c4d586e518998ca04e91cf1f8487e`、Acceptance_Command=`acceptance_command_manifest:sha256:8c3db6d29074058263ffd531ebeb1e158cb2d17d32cb63dd3bf9cadfa4d3a797` 及 baseline/substrate Layer_2 manifests。真实 MySQL 只读事务成功进入验证链；Prefect `127.0.0.1:4200` 不可达，故 0.21 attempt=`layer_2_readonly_evidence:sha256:0af53fc177e6ea7e54eb5bb61297f07e4dba2aaced01fd8372ebcd1d4a189ab3`、7.9 attempt=`layer_2_readonly_evidence:sha256:fa61bde82fc3a63b8f2d80066cb05f50172fa3d0e55ddb5ab5f2ae5fd8f31912` 均如实 `BLOCKED:PREFECT_API_UNREACHABLE`。未创建/更新 Prefect 资源、未触发 flow、未执行 migration/backfill/cutover/activation；0.21/7.9 保持未勾，写授权 #1 尚未请求。

**Authorization #1 / task 9.3（2026-08-04）**：用户精确授权 `migration_apply.task_1_3_schema_v1` 后冻结 Separate_Write_Approval=`separate_write_approval:sha256:43ce9a4292d659c41d46e3210f9e105eb947caa6c6f65736181628c7414f217c`。前置只读检查发现真实 MySQL 已由既有应用启动迁移机制到 `0008_scheduler_claim`（记录 `detected_prior_apply=true`，不隐瞒自动 apply）；随后执行授权范围内 `alembic upgrade head` second-run no-op，schema/data count/migration state 与全部目标表 row counts/primary-key digests 前后不变。Acceptance evidence=`operation_acceptance_evidence:sha256:a31b251d1486ed9423b122c8bd1d0d81deb56740ea110cca19eb75e7b90f7ba1`，结果 `ACCEPTED`。该授权未覆盖/未执行 replay、classification backfill、projection cutover 或任何 enforcement。

**Wave 5 latest disposition（2026-08-04）**：Task 1.3 在 superseding binding `e830a306…` 下完成真实 replay（clean-forward/recommendation n=0，未伪造数据）并 `ACCEPTED`。2.10/3.8/4.10/5.9/6.8/7.8/8.6 均有真实环境 ACCEPTED evidence。9.4 首次因 canonical-owned namespaced/replay run 被误判 partial 而 rollback；修复为“任何已有 identity hash+policy fingerprints+code revision 的 canonical writer-owned run 均不由 legacy backfill 接管”后，9.4 在 plan hash `744b7494…` 下更新 752 个 legacy runs、历史守恒、二次 write operations=0，evidence=`operation_acceptance_evidence:sha256:2eb34680637a6cd46f40d554ddaa141bd87892e958e79ede0e6d557455c5d2e9`，状态 `ACCEPTED`。9.5 真实门槛检查发现 parity sample=3 < frozen minimum=100，evidence=`operation_acceptance_evidence:sha256:15739858279554c25edb5cac140e274c191720d423ec80d9f207e5ba29063144`，因此未请求 cutover approval、旧 read path 保持权威；9.6–9.11 未越过依赖且保持未激活。最终 decision=`wave5_final_decision:sha256:685ab22635ed593ac719411d95dbf96aa385b87b729363ad74488e628bd0d63d`：Spec `BLOCKED` 的唯一 completion blocker 是 projection cutover FAILED；production-ready=false；clean-forward n=0/positive expectation NOT_CLAIMED；history preserved=true。最终 backend 2468 passed/5 skipped/0 failed，architecture/diff PASS。


**Replay/parity follow-up（2026-08-04）**：只读诊断确认 replay 的 market universe/Kline/skill gate/LLM review 均有输出；零推荐发生在 aggregation 最后 production policy，2026-07-16/07-29/08-04 分别因 `production_policy_blocked`（board_universe/score_line）全部过滤。旧的 `REPLAY-全量重放` runs 使用旧/可变输入且数据库中被标为 `live_forward`，不能作为当前 historical replay oracle；当前 replay 使用 `historical_replay` 正确隔离但不是 immutable full-input replay，LLM/cache 与 market-pool feature 漂移导致分数不同。修复了 identity 对当前 strategy inventory entry（implementation/manifest/effective version 无 fingerprint key）的兼容，并保持 policy 不放宽、不伪造推荐。已从真实 MySQL immutable `backtest_input_artifacts` + `backtest_trades` 采集 100 个唯一 shadow parity groups，summary=`shadow_parity_collection:sha256:37b6d87de8c660f68553d192f491a879b9717f83291a7ae32e928c838f3130d7`；阈值失败：exit_date=47、exit_price=51、cost=100、reason=96、net_return=51 mismatch。Shadow manifest minimum sample=100 已满足但 parity 未通过；9.5 继续 BLOCKED，旧 read path 权威。

**Latest replay/parity outcome（2026-08-04）**：成功单日真实 replay smoke 为 `2026-07-22`，run=`run_20260804_155418_9d7ee3`，生成 4 条 provenance-complete `HISTORICAL_RECONSTRUCTION` recommendations；persisted Historical_Query=4、LIVE selector=0，证据=`task_1_3_replay_evidence:sha256:b877fbeb9c3dd703f5345dd3cc9abca82852048048b0add2110b85563b68c26a`。7/15、7/16、7/29、8/4 零推荐均在 aggregation 的当前 production policy 阶段因 board/scoreline 合法阻断；未放宽 policy。真实 immutable backtest corpus 已采集 100 unique shadow groups，summary=`shadow_parity_collection:sha256:37b6d87de8c660f68553d192f491a879b9717f83291a7ae32e928c838f3130d7`，但 mismatch counts 非零（exit_date47/exit_price51/cost100/reason96/net_return51），所以 9.5 继续 BLOCKED，旧 read path 保持权威。

**Parity normalization follow-up（2026-08-05）**：`execution_parity.py` 已加入 Decimal 8 位规范化与旧 reason alias（`stop_loss→stop_loss_hit`、`take_profit→take_profit_hit`、`horizon→horizon_close`），新增测试通过。重新评估同一批 100 个真实 immutable groups 后，collector误报已归零：`cost_mismatch=0`、`exit_reason_mismatch=0`；剩余真实语义差异为 `exit_date=47`、`exit_price=51`、`net_return=51`。最新 summary=`shadow_parity_collection:sha256:8e8891225fc50bcb0507775f36e2252f4f27d7bda94c4f1c5809aa40510b68e7`，仍 `BLOCKED`，旧 read path 权威，不能 cutover。

**Legacy/canonical parity repair（2026-08-05）**：新增版本化只读 `app/services/legacy_execution_shadow.py`，明确复用 HEAD `9d81483` 的旧 `engine/simulation.py` entry/exit 规则，绝不进入正式读路径；`collect_real_shadow_parity.py` 已改为同一 immutable backtest snapshot/artifact 输入分别运行 legacy shadow adapter 与 canonical core，不再把历史持久化 `trade_json` 当作当前旧 adapter 结果。新增 legacy/T+1 boundary tests，parity tests 8 passed。最新100组 summary=`shadow_parity_collection:sha256:3a6df76eb7bdf90d8c89d751896eadb7daad34d2fc3be2e5c0d8a09a3fa0dfff`；cost/reason兼容误报已归零，真实差异仍 exit_date47、exit_price51、exit_reason52、net_return51，确认legacy entry-bar/无canonical trailing与T+1因果语义缺口。9.5继续BLOCKED，不修改旧read path、不降低阈值。

**Semantic migration audit（2026-08-05）**：100 unique legacy-vs-canonical groups 已完成分类，审计=`execution_semantic_migration_audit:sha256:e2ea2d90ed42d7dc85f813ff101cc812d374874266d62a1335664055cf2d31f9`：45 组无实质差异/旧 reason alias，15 组为明确 T+1 entry-bar migration，40 组为明确 canonical causal trailing migration，`unexplained=0`。这证明没有未解释差异，但冻结 `Shadow_Threshold_Manifest` 仍要求所有字段 mismatch=0，因此不放宽、不切换；需要后续新的语义迁移决策/manifest 或让旧权威结果升级到 canonical 后再重做 cutover。

**Demo canonical-only cutover（2026-08-05）**：用户明确允许 demo 不保留历史兼容、清理旧推荐/outcome/运行审计与旧兼容代码；该决策记录为 `demo_canonical_only_decision:sha256:8abebde2ae37ae416c86b69eaf46a67f10ab4a3a1525721520374d4174636e57` 并写入 README。history/stats/dashboard/outcome-summary/realtime-PnL 均使用 canonical provenance selectors；正式 outcome 的 entry/exit/slippage 已收口到 Canonical Execution Core。初次 cleanup evidence=`demo_canonical_only_cleanup:sha256:113d7c42ccac03bd25d7571bafe57b6955fc3fd725f0312821fa3ae270bc81ab`；严格声明每个 mutation target、计数 scoped `api_call_logs` 删除并验证未关联日志保留的 audit evidence=`demo_canonical_only_cleanup:sha256:00357dfea4e50748e9a318d42b829947eedbf19f83682a06a37276fcae69d9f5`，最终正式 `supersedes_id` 指向初次对象并引用该 audit 的 cleanup evidence=`demo_canonical_only_cleanup:sha256:b5a5a53e96e663f456f266d233982efd88961e7912c8ef53c7f06e4142c8c933`。保留 daily_kbars、stock_quotes、market_snapshots、backtest immutable artifacts、alembic state 与治理 CAS。cleanup 后 canonical replay `run_20260805_085515_e498ef` completed，5 recommendations，Historical_Query=5，LIVE selector=0，provenance complete。六个 runtime flags 已为 `ENFORCED`，并以六个独立 `demo_flag_activation` objects 记录 `ACTIVE`；这些对象明确 `LOCAL_DEMO_ONLY`，不构成生产 Layer2 ACCEPTED 或 production-ready。
- 后端全量：`cd backend && .venv/bin/python -m pytest tests/ -q`（基线 2162 passed / 5 skipped）
- 治理层：`cd backend && .venv/bin/python -m pytest tests/governance/ -q`
- 架构检查：`cd backend && .venv/bin/python -m compileall -q app && python ../scripts/check_architecture.py`
- 前端：`cd frontend-v2 && npm test && npm run build`（非 watch）
- S0 冻结（幂等，已完成）：`cd backend && .venv/bin/python scripts/freeze_production_hardening_governance.py`

## 已知 unknowns（如实登记）

- 当前 champion identity 不校验当前 git/policy identity（继承 baseline-task-0.1 known_unknowns）
- 辅助数据缺失选择影响无成对反事实（继承 baseline-task-0.1 known_unknowns）
- clean-forward n=0 → `UNVERIFIED_CURRENT_POLICY`；本矩阵不宣称任何策略正期望

## Wave 5 串行真实验收最终状态（2026-08-04）

本节为当前 revision `9d81483b295532e12ed23b1a8e60fa5ffbab78f9` 的最新权威增量；上文 Wave 2–5 preparation 的 `BLOCKED` 记录保留为历史 attempt，不覆盖、不删除。

| Task | 最终状态 | Approval object(s) | Evidence object |
|---|---|---|---|
| 1.3 | ACCEPTED；clean-forward n=0，未声称正期望 | 见 9.3 migration approval 与受控 replay approval | `task_1_3_acceptance:sha256:022efb9b69792d4a58116cc59acfa73f0870c760446e00062eb1d3eb6ef2988d` |
| 2.10 | ACCEPTED | `separate_write_approval:sha256:986d428000f2202c0efe03a5bb9df6d9f7dc1fedd0be318e5dd790956038bbde` | `layer_2_acceptance_evidence:sha256:85959096ae4540429c786cdc6aa38275bab257d0870bba4e1f299f6dd0e10aa5` |
| 3.8 | ACCEPTED | `separate_write_approval:sha256:99b6414f9cb7a474ec8b92e6ef4cdfa9f11d482a04bec8cf8612f14126ceab60` | `layer_2_acceptance_evidence:sha256:c748bf9980f1f923475dce4790f9a3f76abf956afbf896cd79b09d53f7df84c1` |
| 4.10 | ACCEPTED | `b9c8fa…`, `6d39f7…`, `6345c1…`, `4e384a…`（四个 claim attempt 各自独立） | `layer_2_acceptance_evidence:sha256:acd21c08fe073e4317b78efd49f1749a775f1ea8e0c05da5a5c8ae162fca0007` |
| 5.9 | ACCEPTED（首次门槛 attempt 保留为 BLOCKED，第二次全新 approvals） | `d14a3f…`, `1fb381…`, `71a11c…`, `bf9a09…`, `50b930…`, `21575a…` | `layer_2_acceptance_evidence:sha256:622b1fd996b3c03f9aac2eedd3ef7bcb934bb751b541a6932bedc46a1cf2aba5` |
| 6.8 | ACCEPTED（首次 SQL 参数 attempt 回滚并保留失败证据） | `1bdb05…`, `c670c9…`, `3b47b4…`, `5f0aba…`, `b6293a…`, `d85c1a…` | `layer_2_acceptance_evidence:sha256:9e65d37414873c414c56525405cc39f8407286530ecbe91c9e9ac0706555c858` |
| 7.8 | ACCEPTED（初次因 concurrency=null BLOCKED；pool/queue 分别独立授权后复验） | config `2b0abf…`, `e963bc…`; acceptance `a2f996…`, `38beaa…` | `layer_2_acceptance_evidence:sha256:e4c13c7a8839aaabf97efe06a65101634a4f6069cd03a49c30f148b19418ffbd` |
| 8.6 | ACCEPTED | `separate_write_approval:sha256:da52d3df8bbbd3ff959b976504395a0b1011e62c40ea028928886c6a0221fbfe` | `layer_2_acceptance_evidence:sha256:722b5f92fec6513472850971a36250cb9cf9d5f8a9fca2b6536f9d2d630f9c15` |
| 9.4 | BLOCKED / fail closed | `separate_write_approval:sha256:0c743f02f603a74afa7a406987a021f3b1dd92dc14f7fec0e0078f834c01730e` | `operation_acceptance_evidence:sha256:ca0d796b37f96e25bcf67f350e5d77581d77bba52f290f02bc3e29467c7f3dba` |
| 9.5–9.11 | BLOCKED / 未执行 | 未创建、未复用任何 approval | 由 9.4 fail-closed dependency 与最终报告记录 |

9.4 的 preflight hash 在运行期间经历 `335caf…`（用户提供）→`5fe67e…`（只读检查）→`744b7494086983a06d169ffe5d79c7e6cd945f9f32f5ab82ff34abd179bfecf8`（S2–S8 append-only fixtures 后冻结）。系统未沿用旧 scope；已冻结 superseding scope `release_scope_manifest:sha256:1973b5b7c8617f98b4fe0cb68709bf48e86edaab441ff46c10728f5c3a8ac9e8` 和 plan binding `classification_plan_binding:sha256:960088804d57d7c1b5dbe1f166a983cd111ce4bda17d6c208cc2bd852b837071`。执行事务因既有 run 的 partial provenance 冲突而整体回滚，目标 plan audit rows committed=0；依 DAG 停止 Projection_Cutover 与六个 flags。

9.29/9.30 最终判定对象：`wave5_final_decision:sha256:ccb3e8a45ea473b416bd242e23e8a0a62e9f1f6add14d1ea8fa2630f44f4a985`。Spec=`BLOCKED`（classification FAILED、projection PLANNED）；production-ready=`false`（六 flags 均 NOT_AUTHORIZED，runtime 为 SHADOW/AUDIT）；clean-forward n=0，positive expectation=`NOT_CLAIMED`，append-only history preserved=true。报告镜像：`wave5-final-report.json`。

验证：backend `2468 passed, 5 skipped, 0 failed`；额外 final-contract `25 passed`；architecture/compileall PASS；frontend `39 passed, 0 failed` 且 Vite production build PASS。全程未执行 DROP/TRUNCATE/DELETE/destructive downgrade，未访问 brokerage credential/account/position/balance/order，未 commit/push。
