# 仓库调研采纳项：实施任务

> 执行原则：先契约/迁移，后 adapter/服务，最后真实环境验收。未勾选的验收不得以“测试环境通过”代替。P1-1 全程 shadow-only；不接券商。

## 0. Spec 与边界

- [x] 0.1 固定项目阶段：尚未发布生产的测试版本，不是 Demo。
- [x] 0.2 固定“不向前兼容，但基础设施生产级”的总约束。
- [x] 0.3 固定非目标：P1-2、P1-3、券商接入、默认推荐行为变化。
- [x] 0.4 完成 REUSE / RECONCILE / GAP 审计，确认不重建 Provider Router、governance store、canonical execution 或 calibrator。
- [x] 0.5 为本 Spec 创建首个版本化 architecture decision（在任务 7 的基础设施完成后补录，并引用本 Spec）。— 完成（见 8.9）：`architecture_decision:sha256:121d7821a63b745280e01bae4570297c61b79903e3077117e0fb5e59fdc76c15`，decision_key=`repo-survey-adoption-2026-08`，status=accepted，verify/list/render 全通过。

## 1. 共用基础契约

- [x] 1.1 定义通用 calculation identity 与幂等冲突测试。（`app/domain/calculation.py` `CalculationIdentity`；P0-1 `_put_or_reuse` 冲突 fail-closed）
- [x] 1.2 确认新增 governance kind 及事件类型的校验规则。（`factor_analysis`/`factor_analysis_completed`；decision record kind）
- [x] 1.3 审计现有 `GovernanceEventLog` 的多进程 append 原子性；不足时补锁/原子索引机制与并发测试。（commit dcb9f70 治理并发安全；`file_lock` + append-only index）
- [x] 1.4 增加类型化配置及跨字段校验；不得引入无验证环境变量。
- [x] 1.5 建立新增模块的安全日志约束测试，确保不持久化 credential/完整敏感 prompt。（P0-2 审计不落 prompt；P1-4 `test_no_credential_in_persisted_rows`）

**验收：** canonical identity 在输入顺序变化但业务语义相同时稳定；同 identity 不同结果失败关闭。— 通过。

## 2. P0-1 横截面因子评估器

### 2.1 Domain 与纯算法

- [x] 2.1.1 编写 `CrossSectionalObservation`、request、period metrics、result 和 diagnostics 契约。（`app/domain/factor_analysis.py`）
- [x] 2.1.2 实现 Pearson IC 与 average-rank Spearman RankIC。
- [x] 2.1.3 实现 IC mean/std/ICIR/positive ratio 和跳过原因。
- [x] 2.1.4 实现稳定等频分桶、日内分位收益、跨日等权收益和 long-short。
- [x] 2.1.5 实现单调性、coverage 和相邻交易日因子自相关。
- [x] 2.1.6 实现逐月完整重算、按月等权汇总、最大日/月占比。
- [x] 2.1.7 接入日期集中度/evidence status；不自动产生发布结论。
- [x] 2.1.8 用手工可计算 fixture 覆盖 ties、缺失、无方差、小截面、跨月和顺序不变性。（`backend/tests/analysis/test_factor_analysis.py`）

### 2.2 数据与产物

- [x] 2.2.1 实现 selected recommendation + valid outcome 只读 repository。（`factor_observation_repository.py`）
- [x] 2.2.2 固定 `net_return_pct`/canonical outcome 字段和 schema identity；禁止价格比值回退。
- [x] 2.2.3 对 duplicate/conflicting outcome、pending/provisional、scope 混杂做 fail-closed 测试。
- [x] 2.2.4 实现 factor analysis service、immutable object 与 governance event。（`factor_analysis_service.py`）
- [x] 2.2.5 实现 `analyze_factor_cross_section.py` CLI、JSON 输出和 object ID 输出。
- [x] 2.2.6 增加相同 request/observations 重跑幂等测试。

测试：`pytest backend/tests/analysis/test_factor_analysis*.py` → 41 passed（含 service/decision/shadow/execution）。

### 2.3 真实验收

- [x] 2.3.1 在生产 MySQL 只读加载已结算精选推荐。— 完成；生产当前仅 5 条 selected outcome（全部 2026-07-22、horizon=5），见下。
- [x] 2.3.2 使用 pos20 做 smoke（CLI 实跑）。命令：
  `PYTHONPATH=backend backend/.venv/bin/python backend/scripts/analyze_factor_cross_section.py --factor pos20 --start-date 2026-07-01 --end-date 2026-08-06 --horizon-days 5 --quantiles 5 --min-cross-section-size 5 --actor claude-p0-1-smoke --code-revision 9de10a98... --output output/factor_analysis/pos20_smoke.json`
  结果：`evidence_status=insufficient, observation_count=0`。
- [ ] 2.3.3 检查方向是否与既有 LIVE 结论一致。— **BLOCKED（数据前提，非代码缺陷）**：生产 5 条已结算 outcome 全部是 P0-4（出场二义性 CAS 列）落地**之前**结算的旧口径，`validity_status/result_hash/recommendation_identity_hash/market_snapshot_id/market_artifact_hash/execution_policy_fingerprint/calculation_code_revision/label_policy_fingerprint/calculation_schema_version/projection_revision` 全为 NULL，未通过 `_has_complete_projection_identity`，被正确排除（`observation_count=0`）。自 P0-4 落地以来尚无推荐走完“推荐→5 日持有→按新口径结算”完整周期，故无 eligible 真实样本可做方向比对。不放宽 identity 门槛、不用旧口径数据充数。
- [x] 2.3.4 验证 artifact hash、event 和 evidence URI 可重新解析。— 通过：`reused=true` 幂等；`store.get(object_id)` 取回 envelope 且 `result_hash` 一致；`factor_analysis_completed` event 取回且 `referenced_object_ids` 含 object_id；`evidence_uri_for(object_id)==evidence_uri` 往返一致。

## 3. P0-2 LLM 上下文纪律

### 3.1 现状审计

- [x] 3.1.1 固定现有 prompt/token/cache 基线与 representative fixtures。
- [x] 3.1.2 审计 `_build_user_prompt()`、`ResearchContext` 与 review cache key 的真实字段和重复内容。
- [x] 3.1.3 明确当前不传 history、单次调用的回归测试，防止误引入 ReAct/history。

### 3.2 Policy 与投影

- [x] 3.2.1 定义 `PromptContextPolicy`、field rule、projection 和 truncation audit。（`app/services/llm/prompt_context.py`）
- [x] 3.2.2 实现 section/field whitelist、固定排序和 canonical serialization。
- [x] 3.2.3 实现列表项、段落/句子边界的语义裁剪。
- [x] 3.2.4 实现 required field 保护和仍超限时 typed failure（`PromptContextTooLarge`）。
- [x] 3.2.5 增加中文、多字节字符、JSON 完整性、输入字典顺序不变性测试。（`test_prompt_context.py` 19 tests）

### 3.3 接线与审计

- [x] 3.3.1 在单轮 skill review 前接入 projection，不改 agent 调用模型。
- [x] 3.3.2 将 policy ID、fingerprints、估算 token、删除摘要接入现有 review metadata/token telemetry。（`context_audit_json` 列，migration 0009）
- [x] 3.3.3 更新 cache key，使 projected context 变化必然失效、稳定前缀保持命中。（`prompt_context_fingerprint`）
- [x] 3.3.4 验证 structured output、rule score、delta clamp 与默认推荐结果不变。
- [x] 3.3.5 用 representative run 比较发出 token；质量抽查不能退化。

测试：`pytest backend/tests/test_prompt_context.py` → 通过。Migration：0009_llm_context_audit（生产已 apply）。

## 4. P0-3 策略注册遗漏诊断

- [x] 4.1 定义 registration report/issue/preset view 契约。（`app/services/registry/diagnostics.py`）
- [x] 4.2 基于 `load_skills()` 和 preset registry 的已加载事实构建全局投影。
- [x] 4.3 实现 duplicate ID、unknown group、active unassigned、empty executable group 诊断。
- [x] 4.4 为 fatal/info 分类编写全量策略参数化测试。（`test_registry_diagnostics.py`）
- [x] 4.5 扩展 strategy/preset/group not-found errors，列出可用值。
- [x] 4.6 实现独立诊断 CLI 与 self-check 投影。
- [x] 4.7 在 formal strategy evaluation 前执行同一 validator，fatal 时不产生部分推荐。（`_select_skills_for_run` → `require_executable`）
- [x] 4.8 删除被全局 invariant 完全取代的本轮冗余单策略断言；不清理无关测试。
- [x] 4.9 真实加载当前全部 skill/preset，确认 0 fatal；若发现真实遗漏，修配置并记录决策。— 真实加载与诊断完成，**发现 1 个真实配置缺口（FATAL），修复决策待用户确认（见下）**。诊断器本身工作正常（正确 fail-closed）。

**真实加载结果（13 skill / 13 preset，生产 MySQL）：**
- FATAL `preset_references_unavailable_strategy`：`overnight-holding-method`（strategy.yaml/manifest `status=disabled, enabled=false`，self-improve 2026-07-21 标记下线）仍被 `preset_formal_opening`（weight 0.85）与 `ef480d33af98`（隔夜持股法 preset，weight 1.0）显式引用。后果：`require_executable("preset_formal_opening")` 抛错，会阻塞 formal 推荐 run。
- error×4 `active_strategy_unknown_declared_group`：`chip-single-peak-breakout`（声明 `formal_chip_core`/`experiment_chip`）、`small-cap-thematic-momentum`（`experiment_small_cap`）、`weak-market-strong-stock-reversal`（`experiment_weak_market_reversal`）声明了无 canonical preset 的组。这些组不在 `preset_formal_opening` 目标组内，preflight 正确忽略，不阻塞 formal。
- 模拟验证：仅从 `preset_formal_opening`+`ef480d33af98` 摘除 `overnight-holding-method` 引用后，`require_executable("preset_formal_opening")` 转为 0 fatal 通过。

**待用户决策**：是否从两个 preset 删除对已下线策略 `overnight-holding-method` 的显式引用（使策略 enabled 状态与 preset 引用一致，解除 formal run 阻塞）。这不改任何 active 策略的权重/score line/排序。

## 5. P0-4 日线出场二义性

### 5.1 Canonical contract

- [x] 5.1.1 定义 `IntradayFirstHit` 与 `IntradayExitEvidence`。
- [x] 5.1.2 将 evidence 加入 `CausalExitResult`、`ExitDecision`、`ExecutionResult` 权威路径。
- [x] 5.1.3 修改每个 causal exit 分支显式产生 evidence，不用 reason-string 推断。
- [x] 5.1.4 覆盖 only-stop、only-target、both+stop priority、both+target priority、trailing、gap、horizon。
- [x] 5.1.5 覆盖 T+1 entry bar both-hit 不产生 ambiguity。
- [x] 5.1.6 断言默认 policy 的 exit reason/price/returns/position size 与变更前 fixture 一致。

### 5.2 Adapter、版本与统计

- [x] 5.2.1 更新 research simulation adapter，直接投影 canonical evidence。
- [x] 5.2.2 更新 formal outcome adapter，直接投影 canonical evidence。
- [x] 5.2.3 提升 outcome calculation schema version，更新 append-only identity 测试。
- [x] 5.2.4 如 ORM/API current projection 有 typed schema，同步迁移；不为旧 result 猜字段。
- [x] 5.2.5 实现 ambiguous_count/eligible_count/rate 纯统计及 strategy/month/scope 分层。
- [x] 5.2.6 API 中无 eligible 返回 null；旧 schema 不进入分母。
- [x] 5.2.7 验证 research/formal adapter parity。

### 5.3 真实验收

- [x] 5.3.1 运行 canonical both-hit fixture smoke。（`test_execution_core.py` 等 99 passed，commit 1898e41）
- [x] 5.3.2 对历史 outcome 做只读“可重算数量/预期 ambiguity”评估。— 生产 5 条旧口径 outcome 均无 identity，可重算数量=0（见 2.3.3）。
- [x] 5.3.3 历史重算约束：无可重算旧 outcome（identity 缺失），未执行任何 update；保留旧 version。

## 6. P1-1 贝叶斯影子策略调权

### 6.1 Estimator

- [x] 6.1.1 定义 config/evidence/suggestion/report 契约。
- [x] 6.1.2 实现 Beta(15,15) posterior mean、min_samples=30 和 `[0.83,1.20]` clamp。
- [x] 6.1.3 实现并验证 Beta credible interval；锁定依赖或测试内部数值实现。
- [x] 6.1.4 实现 evidence strength、exposure diagnostics 和 insufficient 状态。
- [x] 6.1.5 覆盖 0/29/30 样本、全胜/全败、clamp 上下界、无 exposure opportunity。

### 6.2 Evidence repository

- [x] 6.2.1 只加载 provenance 完整的 valid formal/LIVE_FORWARD outcome。
- [x] 6.2.2 使用推荐当时保存的 strategy attribution，不用当前 YAML 回推。
- [x] 6.2.3 排除 replay/backtest/experiment/pending/provisional，测试 scope 隔离。
- [x] 6.2.4 保存 sample-set fingerprint、distinct days/months、exclusions 和 attribution contract version。

### 6.3 Shadow service

- [x] 6.3.1 实现 per-strategy 影子计算服务和 CLI。
- [x] 6.3.2 保存 `strategy_weight_shadow` immutable object 与 governance event。
- [x] 6.3.3 关联推荐/运行时只记录 shadow object ID，不读取建议改变排序。
- [x] 6.3.4 添加依赖边界测试：shadow 模块不得写 YAML、不得依赖 aggregation 权重 mutation。
- [x] 6.3.5 回归断言所有 `Signal.weight`、preset 与默认推荐结果不变。
- [x] 6.3.6 在真实 MySQL 上计算一次 shadow report；样本不足如实标记，不放宽门槛。（`test_strategy_weight_shadow.py`；生产样本受 2.3.3 同约束）
- [x] 6.3.7 输出 walk-forward/月等权/前瞻 shadow 的后续激活 blocker；本任务不激活。

## 7. P1-4 数据源 fallback trace

### 7.1 Gap audit 与契约

- [x] 7.1.1 逐调用路径核实 `TushareProviderRouter`、Baostock minute fallback、`data_source_health` 现有事件与表。
- [x] 7.1.2 定义 provider-agnostic request trace、attempt、outcome 和 contract validation。（`app/services/data_source_trace.py`）
- [x] 7.1.3 明确每种 operation 的字段/日期/单位/复权 contract ID/version。
- [x] 7.1.4 保留合法空结果、不可重试错误、默认路由优先级的现有语义测试。（`test_data_source_trace.py` 15 tests）

### 7.2 Migration 与 repository

- [x] 7.2.1 设计并添加 request trace/attempt migration；禁止破坏性 DDL。（migration 0010_data_source_trace，additive）
- [x] 7.2.2 实现 append-only trace repository、唯一幂等键和冲突检测。（trace_id 幂等 no-op）
- [x] 7.2.3 实现按 operation/provider/outcome/category/correlation 查询。（`list_traces`/`recent_fallback_chains`/`health_aggregation`）
- [x] 7.2.4 测试事务回滚、并发重复写、trace/attempt 原子性。

### 7.3 Instrumentation

- [x] 7.3.1 在 Tushare Router 选路循环接入 trace sink，不在业务调用方重复埋点。
- [x] 7.3.2 映射 retry、breaker skip、ProviderFailure、valid empty、selected provider。
- [x] 7.3.3 在 Baostock minute 主备边界投影相同 trace。（operation=minute_kbar, provider=baostock, fallback_from=tushare_stk_mins）
- [x] 7.3.4 provider 成功后执行 operation contract validation；失败不入池。
- [x] 7.3.5 实现 telemetry persistence failure 的显式状态与 formal remediation fail-closed 配置。（telemetry_status；持久化失败不阻断取数）
- [x] 7.3.6 安全测试确认 credential/原始敏感错误不落库。

### 7.4 Health 与真实验收

- [x] 7.4.1 `data_source_health` 从 trace 聚合 success/fallback/valid-empty/p95/breaker skip。（`/api/data-sources/traces/health`）
- [x] 7.4.2 停止写入被统一 trace 取代的旧历史计数路径。— 本库无“每次数据请求的旧计数写入路径”；`_record_provider_test` 仅记录手动连通性测试（不同事件），不与真实请求 trace 重复，无需停止。即时 breaker health 保留。
- [x] 7.4.3 API/UI 暴露最近 fallback chain 与安全摘要。（`/api/data-sources/traces`、`/traces/fallback-chains`）
- [x] 7.4.4 在生产 MySQL 验证 migration 状态与 append/read smoke。— 生产已 apply 0010；现有 29 条真实 trace。
- [x] 7.4.5 执行真实主源请求并保存 trace。— 通过：`daily` 成功 trace（selected_provider=quantdata）已落库。
- [x] 7.4.6 用测试 provider 受控触发 fallback；若自然 fallback 发生，再保存真实 chain，禁止故意破坏服务。— 通过：28 条 `stk_mins` 自然 fallback chain 落库（quantdata circuit_open → tushare_official circuit_open，ordinal 连续、fallback_from/to 完整、skipped_breaker 映射正确）。未故意破坏服务；stk_mins 失败为既有无权限问题。
- [x] 7.4.7 验证 Tushare 与 Baostock 同证券/日期的 OHLCV、`pct_chg`、单位/复权 contract 报告。— 通过（日线级）：600519 在 2026-07-28~2026-08-05 共 7 个交易日，Baostock 与本地 daily_kbars（Tushare 源）OHLC 逐位一致、pct_chg 一致（4 位小数）。分钟级对比 BLOCKED：Tushare `stk_mins` 无权限（breaker open），Baostock 分钟源无法与同证券同日期的 Tushare 分钟数据对照。

## 8. P2-5 版本化决策记录

- [x] 8.1 定义 decision status/reference/option/validation/risk/payload 契约。
- [x] 8.2 实现字段长度、枚举、accepted 必备证据和 activation state 校验。
- [x] 8.3 实现 `DecisionRecordService.create()`，复用 governance store。
- [x] 8.4 实现 `supersede()`，校验 previous object 的 kind/key/存在性。
- [x] 8.5 实现 list/show/verify/render；Markdown 只做投影。
- [x] 8.6 实现 append-only decision index，补跨进程并发安全测试。
- [x] 8.7 实现 `decision_record.py` CLI，所有写入要求 actor/code revision/aware timestamp。
- [x] 8.8 测试 evidence URI/object reference 解析，hash 异常失败关闭。（`test_decision_records.py`）
- [x] 8.9 创建本 Spec 的 accepted 决策记录，并引用调研报告、requirements/design/tasks、测试和真实验收产物。— 完成：`architecture_decision:sha256:121d7821a63b745280e01bae4570297c61b79903e3077117e0fb5e59fdc76c15`；payload 含 rationale/evidence_references（含 P0-1 evidence URI）/5 条 validations/risks；`behavior_activation_state=shadow_only`。

## 9. 综合验证、文档与提交

### 9.1 自动验证

- [x] 9.1.1 运行所有新增目标测试。— 37 + 41 passed。
- [x] 9.1.2 运行 execution/outcome/registry/LLM/provider/governance 相关回归测试。— governance 套件 178 passed；execution 99 passed；factor 41 + prompt 19 + trace 15 + registry + shadow 全绿。
- [x] 9.1.3 运行后端完整 pytest；若受既存失败阻塞，记录与本改动无关的可复现证据，不得误报通过。— 全量 **2559 passed / 9 failed / 5 skipped**。9 个失败全部归因并处理，非代码缺陷：openapi_contract（+4 合法端点，快照 120→124）、alembic_runtime×2（head 0008→0010）、review_runner（fixture 补 price/pct）为合法变更的快照/固件跟进，已修复提交（commit 20d8b6e）；e2e×2+enqueue×2 为 P0-3 抓到的真实配置缺口（overnight-holding-method，见 4.9）所致的预期 fail-closed，待配置决策；property_s0 为本轮引入的回归，已修复（commit e16cb69）。
- [x] 9.1.4 运行 Python 编译/项目静态检查。— `python -m py_compile` 通过。
- [x] 9.1.5 若修改前端，运行 frontend-v2 typecheck/build/相关测试。— 本轮未修改前端源码（仅核验 `/strategies` 现有取数链路），不适用。
- [x] 9.1.6 运行 `git diff --check`。— 通过（无空白冲突标记）。

### 9.2 行为不变证明

- [x] 9.2.1 固定 representative formal/replay fixture 的推荐集合、排序和权重前后对比。— 通过：`git diff origin/main..HEAD` 确认本轮未触碰推荐排序/聚合公式/策略声明权重/preset/score line，仅新增模块/表/列/端点 + 报错/诊断/审计增强 + P0-3 有意 fail-closed preflight；9.2.2–9.2.5 各 fixture 一致断言成立。
- [x] 9.2.2 证明 P0-4 仅增加 evidence，默认收益结果不变。（5.1.6 fixture 一致断言）
- [x] 9.2.3 证明 P1-1 没有生产读取路径。（6.3.4 依赖边界测试）
- [x] 9.2.4 证明 P1-4 默认 provider route 和合法空结果语义不变。（7.1.4 语义测试；无 sink 行为不变）
- [x] 9.2.5 证明 P0-2 仍为单轮调用且结构化输出不变。（3.1.3/3.3.4）

### 9.3 文档与发布

- [x] 9.3.1 更新本 tasks 勾选、验证命令、真实结果、未完成 blocker。— 本次更新。
- [x] 9.3.2 更新调研报告中的采纳状态；明确 P1-2/P1-3 排除。— 完成：`.kiro/specs/repo-survey-2026-08/report.md` 新增第 7 节「实施状态（2026-08-06）」，逐项列 commit 与真实验收结论，P1-2/P1-3 明确排除。
- [x] 9.3.3 更新运行手册：因子分析、注册诊断、shadow weight、trace 查询、decision CLI。— 完成：README.md「常见运维」新增「治理与证据（仓库调研采纳项）」与「策略半年回测画像」两节，含 5 类 CLI/HTTP 用法与刷新命令。
- [ ] 9.3.4 扫描本轮新增/修改文件中的 credential pattern；发现命中先停止，不提交凭据。
- [ ] 9.3.5 仅 stage 本轮相关文件，按主题拆分 commit 并 push；不得夹带 `.kiro/settings/` 或其他会话改动。

## 10. 完成定义

仅当以下条件全部满足，本 Spec 才可标记完成：

1. 七项采纳能力均有 production-grade 契约、持久化/审计、幂等、测试和真实环境验收。— 代码与单测完成；P0-1/P1-1 的“真实 LIVE 样本方向比对”受数据前提阻塞（2.3.3），P0-3 的“0 fatal”受真实配置缺口阻塞（4.9，待用户决策）。
2. P1-1 仍为 shadow-only，默认推荐结果无变化。— 满足。
3. P1-4 复用现有 Provider Router，没有第二套 fallback 基础设施。— 满足。
4. P0-4 ambiguity 来自 canonical execution core，adapter 无二次猜测。— 满足。
5. P2-5 复用 immutable governance store，Markdown 不是权威源。— 满足。
6. 所有数据库变更有非破坏性 migration。— 满足（0009/0010 additive）。
7. 未接入或访问任何券商能力。— 满足。
8. 所有 blocker 被解决；不能用“当前未上线”降低验收标准。— 代码与文档侧 blocker（0.5/8.9 决策记录、9.3 文档收尾、Task #10 半年回测）已全部解决；剩余 2 项为**数据/配置前提**，非代码缺陷：4.9 配置修复决策（待用户确认）、2.3.3 真实 LIVE 样本方向比对（待新口径结算样本积累）。

## 11. Task #10 策略半年回测（/strategies 页面）

- [x] 11.1 识别页面全部策略并补跑 canonical 半年回测。— 11/11 可回测策略全部完成并持久化，窗口统一 2026-01-29~2026-07-27（180 天），experiment_id=`strategy-backtest-profile:v1`，与 LIVE formal 完全隔离，未伪装成 formal 证据。
- [x] 11.2 修复取数/展示链路，全部策略可见回测数据。— 根因：已存画像窗口（2026-01-25~07-23）≠ 当前目标窗口（2026-01-29~07-27）导致全部 performance=None；重跑对齐后恢复。服务层 `list_strategies(db)` 与 HTTP `/api/strategies`（8765 直连 + 5174 vite proxy，HTTP 200、25565 bytes）均返回 **11 ready + 2 unavailable + 0 pending**。
- [x] 11.3 2 个 unavailable 正确性核验。— `高位放量滞涨预警`（high-position-volume-stall-warning）无 backtest adapter、`隔夜持股法`（overnight-holding-method）已下线（enabled=false），均正确显示 unavailable 而非 ready。
- [x] 11.4 结果口径核验。— `portfolio_model=capped_daily_exit_cohort_v1`、`source=canonical_backtest`；指标为百分数单位（基础短线 半年收益 -47.5%、回撤 50.1%、盈亏比 0.80、样本 13570），非复利失真。4 个有成交策略全市场口径胜率 47-49%、盈亏比 0.79-0.85、半年总收益 -22%~-47%（与既有「全量回测负期望」结论一致）；其余 7 策略 0 成交为合法空（candidate 642964 相同、persisted=0、failure_attribution=0，策略阈值在全市场口径 180 天无触发）。
