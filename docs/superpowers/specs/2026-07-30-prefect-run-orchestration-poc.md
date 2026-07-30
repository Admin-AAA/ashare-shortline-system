# Prefect 运行编排 PoC Spec

日期：2026-07-30
状态：待实现

## 1. 背景

当前系统同时维护两类状态：

- 推荐运行事实：`RecommendationRun`、canonical `RunRequest`、阶段 checkpoint、worker lease、CAS revision、推荐结果。
- 执行基础设施状态：定时触发、进程内排队、后台线程、子进程派发、重试、重启恢复、运行日志和任务观察。

第一类属于 A 股推荐系统的领域审计语义，现由 `RunStateMachine`、`RunCoordinator` 和 MySQL repository 管理。第二类主要分散在 `scheduler_service.py`、`runs/queue.py`、`workbench_service.py` 和 FastAPI startup hook 中，重复承担通用工作流编排职责。

本 PoC 引入开源 Prefect 3，只替代“策略曝光补跑”的执行基础设施，不迁移正式推荐，不改变策略计算、推荐结果或统计口径。

## 2. 目标

- 验证 Prefect 能否稳定承载本地单机上的定时触发、手动触发、串行执行、失败重试、重启恢复和日志观察。
- 将策略曝光补跑从 APScheduler + 后台线程 + 轮询等待收拢到一个 Prefect flow interface。
- 保持 MySQL 为推荐运行和业务任务的唯一事实源，Prefect 只保存执行编排事实。
- 证明相同输入下，PoC 路径与现有路径选择相同低曝光策略，并生成语义一致的 experiment runs。
- 建立可开关、可回退的 adapter seam，为后续是否迁移盘后流水线提供证据。

## 3. 非目标

- 不替换 `RunRequest`、`RunStateMachine`、`RunCoordinator`、checkpoint、worker lease 或 SQL CAS。
- 不迁移 formal 推荐、盘后数据同步、Outcome 结算、数据修复和回测任务。
- 不让 Prefect 数据库成为推荐记录、策略结果或前端统计的读取来源。
- 不改变 `strategy.yaml`、策略筛选、评分、聚合、LLM 审查或推荐持久化逻辑。
- 不在 PoC 中部署 Redis、RabbitMQ、Kubernetes 或云端 Prefect Cloud。
- 不为了 Prefect 改造现有领域 pipeline 的阶段 interface。

## 4. 核心原则

### 4.1 双状态模型

| 状态类别 | 唯一事实源 | 例子 |
|---|---|---|
| 业务运行状态 | MySQL | `queued/running/completed/failed`、checkpoint、lease、推荐数 |
| 编排执行状态 | Prefect | deployment、flow run、task attempt、retry、日志、调度时间 |

二者不能互相推断：

- Prefect flow `Completed` 只表示编排函数正常结束；关联的 `RecommendationRun` 必须在 MySQL 中独立达到合法终态。
- Prefect flow `Failed` 不得直接覆盖已完成或已取消的 `RecommendationRun`。
- 前端业务页面继续读取 MySQL。Prefect UI 仅作为运维观察入口。

### 4.2 领域状态不外包

Prefect 不拥有以下合法性判断：

- Run 状态迁移是否合法。
- checkpoint 是否按顺序提交。
- worker lease 是否仍有效。
- 推荐持久化是否完成。
- interrupted Run 应续跑、重放还是取消。

这些判断继续只通过现有领域 interface 完成。

### 4.3 至少一次派发，业务侧幂等

Prefect retry 和 worker 重启可能使 task 至少执行一次。所有可重试操作必须以稳定幂等键进入现有 MySQL seam：

```text
exposure_batch_key = trade_date + window_days + min_sample + enabled_skill_fingerprint
experiment_run_key = exposure_batch_key + skill_id
```

同一 `experiment_run_key` 已存在 queued/running/completed Run 时不得创建第二条；failed/cancelled 是否允许重试由明确 policy 决定，不能按“最近 60 分钟”模糊判断。

## 5. PoC 范围

### 5.1 选择策略曝光补跑的原因

策略曝光补跑具备足够的编排复杂度：

- 定时和手动两种入口。
- 先统计近 N 日样本，再确定动态任务集合。
- 多个策略需要控制并发，避免全市场扫描造成 CPU 峰值。
- 每个策略派发独立 experiment Run，并等待 MySQL 终态。
- 单个策略失败不应抹掉其他策略的结果。
- 结果只进入策略胜率样本，不进入记录中心和驾驶舱正式推荐。

同时它不影响 formal 推荐主链，适合作为低风险试点。

### 5.2 PoC 运行方式

- Prefect Server、worker 与 FastAPI 运行在同一台本地机器。
- 使用 Prefect 自带本地服务能力，不引入额外消息中间件。
- flow deployment 使用 Asia/Shanghai 时区，cron 延续现有配置。
- work pool 并发上限为 1；策略 experiment Run 逐个派发。
- Prefect 配置与运行元数据不得写入 Git 跟踪文件中的凭据。

## 6. 建议 module 与 interface

### 6.1 `ExposurePlanningModule`

职责：读取 MySQL 统计并生成不可变执行计划，不依赖 Prefect。

```python
build_exposure_plan(
    db,
    *,
    trade_date: str,
    window_days: int,
    min_sample: int,
) -> ExposurePlan
```

`ExposurePlan` 至少包含：

- `batch_key`
- `trade_date`
- `window_days`
- `min_sample`
- `enabled_skill_fingerprint`
- 按 `skill_id` 稳定排序的目标策略及当前样本数
- 生成时间只用于审计，不参与幂等键

该 module 应从现有 `_run_strategy_exposure()` 中提取纯计划逻辑，现有 APScheduler adapter 与 Prefect adapter 在迁移期共同调用。

### 6.2 `ExposureRunGateway`

职责：通过现有 canonical Run 入口派发并查询一个 experiment Run。

```python
enqueue_experiment_run(plan_item, *, experiment_run_key: str) -> str
wait_for_terminal(run_id: str, *, timeout_seconds: int) -> RunTerminalResult
```

实现必须复用 `parse_run_request()` 和持久化后的 canonical request，不得直接拼写 `params_json`，也不得绕过 `RunStateMachine`。

PoC 只有一个生产 adapter：MySQL + 现有推荐 pipeline。测试使用 in-memory fake，因此该 seam 有两个真实 adapter。

### 6.3 `ExposureResultRepository`

职责：记录 batch 与 Prefect flow 的关联，供幂等、回退和对账使用。

建议新增独立表 `strategy_exposure_batches`：

| 字段 | 语义 |
|---|---|
| `batch_key` | 主键，稳定幂等键 |
| `trade_date` | 目标交易日 |
| `plan_json` | canonical `ExposurePlan` 快照 |
| `orchestrator` | `legacy` 或 `prefect` |
| `orchestrator_run_id` | Prefect flow run id，可空 |
| `status` | `planned/running/completed/partial/failed/cancelled` |
| `started_at/completed_at` | UTC naive，与现有表约定一致 |
| `summary_json` | 每个 skill 对应的 recommendation run id 与终态 |
| `revision` | CAS revision |

唯一索引：`batch_key`。PoC 不复用 `scheduled_task_runs` 表冒充 Prefect flow 状态。

### 6.4 Prefect flow interface

```python
@flow(name="strategy-exposure-backfill")
def strategy_exposure_flow(request: ExposureFlowRequest) -> ExposureFlowResult:
    ...
```

Flow 内部只编排以下 task：

1. `plan_exposure_batch`
2. `claim_exposure_batch`
3. 对每个目标策略顺序执行 `dispatch_experiment_run`
4. `observe_experiment_terminal_state`
5. `finalize_exposure_batch`

业务计算仍在现有推荐子进程/pipeline 内完成，不能复制进 Prefect task。

## 7. 状态映射

| Prefect 状态 | Exposure batch 动作 | RecommendationRun 动作 |
|---|---|---|
| Scheduled/Pending | 无 | 无 |
| Running | CAS `planned -> running` | 通过现有入口创建 queued Run |
| task retry | 保持 running，记录 attempt | 查询幂等键后复用已有 Run |
| Completed | 根据子 Run 汇总为 completed/partial | 不额外改写 |
| Failed | batch 标记 failed 或 partial | 只由现有 lifecycle 处理自身失败 |
| Cancelled | batch 标记 cancelled | 已派发 Run 不自动取消；显式操作另行设计 |
| Crashed | 保持可恢复状态 | 已派发 Run 继续以 MySQL 事实为准 |

PoC 禁止将 Prefect cancellation 自动传播到正在运行的推荐 Run。取消语义涉及领域状态、子进程信号与副作用清理，需要独立 spec。

## 8. 重试与失败政策

### 8.1 可自动重试

- 短暂数据库连接错误。
- Prefect worker 与本地 server 的短暂通信错误。
- 查询 RecommendationRun 终态时的瞬时读取失败。
- 在任何业务 Run 尚未创建前发生的计划读取失败。

默认：最多 3 次，间隔使用有上限的指数退避；测试中注入零等待 policy。

### 8.2 不自动重试

- `RunContractError`。
- 找不到有效策略或 preset。
- canonical request 与索引列不一致。
- 业务 Run 明确到达 failed/cancelled。
- 数据质量前置条件失败。
- 幂等键对应不同 canonical 计划。

### 8.3 部分成功

若 N 个策略中部分 completed、部分 failed/cancelled/timeout：

- batch 终态为 `partial`。
- `summary_json` 保存每个策略的稳定结果。
- flow 返回结构化结果后正常完成，不通过抛异常丢失已完成信息。
- 告警依据 batch 的业务终态，而非只看 Prefect flow 是否 Completed。

## 9. 调度与并发

- PoC deployment cron 与当前 `strategy_exposure_run` 一致，时区固定 `Asia/Shanghai`。
- 同一 `batch_key` 只能有一个 owner；由 MySQL 唯一键和 CAS 保证，不依赖 Prefect concurrency 名称保证业务幂等。
- work pool 并发上限为 1，保持当前全市场扫描串行约束。
- 去掉固定 `EXPOSURE_ENQUEUE_STAGGER_SECONDS` 前必须用实测证明 worker 串行已消除 CPU 尖峰；PoC 初期可保留可观测的节流 task。
- FastAPI 重启不得启动第二套 Prefect server/worker；开发脚本负责显式管理进程。

## 10. 配置

新增配置项建议：

| 配置 | 默认值 | 说明 |
|---|---|---|
| `EXPOSURE_ORCHESTRATOR` | `legacy` | `legacy` / `shadow` / `prefect` |
| `PREFECT_API_URL` | 本地地址 | 不包含凭据 |
| `PREFECT_EXPOSURE_DEPLOYMENT` | 固定 deployment 名 | Prefect deployment |
| `PREFECT_EXPOSURE_TIMEOUT_SECONDS` | `3600` | 单策略观察上限 |

模式语义：

- `legacy`：只有现有 APScheduler 路径执行。
- `shadow`：现有路径执行；Prefect 只生成并记录计划，不派发 RecommendationRun。
- `prefect`：Prefect 执行；现有 exposure cron 必须禁用，手动入口改为创建 Prefect flow run。

未知值必须启动失败，不能静默回退。

## 11. 可观测性

每次 flow/task 日志必须带：

- `batch_key`
- `prefect_flow_run_id`
- `task_run_id`（如适用）
- `skill_id`（如适用）
- `recommendation_run_id`（派发后）
- `trade_date`
- `attempt`

禁止记录 canonical request 全文、数据库连接串、token、password 或 provider credential。

系统控制台在 PoC 阶段不嵌入 Prefect UI，只增加只读摘要：当前 orchestrator、最近 batch 终态、关联 flow run id、各策略关联 RecommendationRun。业务结果仍链接到现有 Run 详情页。

## 12. 测试策略

### 12.1 纯 module 测试

- 相同数据库事实生成相同 `ExposurePlan` 与 `batch_key`。
- 策略顺序变化不改变 fingerprint。
- 样本数、交易日或启用策略集合变化会改变正确的计划字段。
- 计划为空时返回 completed/skipped，不派发 Run。

### 12.2 Adapter 契约测试

同一套测试运行于 in-memory fake 与 MySQL adapter：

- 幂等 enqueue 返回同一 `recommendation_run_id`。
- canonical request 不一致时报稳定错误。
- terminal result 保留 completed/failed/cancelled/timeout 区别。
- 关闭或重建数据库 session 不改变结果。

### 12.3 Flow 测试

- 单策略成功。
- 多策略严格串行。
- 第二个策略失败后第三个仍按 policy 执行。
- 查询瞬时失败触发 retry，但不重复创建 Run。
- worker 在 enqueue 后崩溃，重启后复用已有 Run。
- 重复 schedule/manual trigger 只 claim 一个 batch。
- Prefect Completed 但子 Run failed 时 batch 为 partial/failed。
- cancellation 不会偷偷取消已派发 RecommendationRun。

### 12.4 影子对账

`shadow` 模式至少运行 10 个有效交易日，并比较：

- 目标策略集合完全一致。
- 每个目标策略的样本数一致。
- 计划 payload 经 canonical 化后一致。
- 空计划/跳过原因一致。
- 计划生成时间 P95 不比 legacy 增加 20% 以上。

影子模式不生成第二套 experiment Run，因此不会污染统计样本。

## 13. 验收标准

PoC 进入 `prefect` 模式前必须全部满足：

- 10 个有效交易日影子对账零策略集合差异。
- 故障注入证明 worker/FastAPI/Prefect server 分别重启后不会重复创建业务 Run。
- 相同 `experiment_run_key` 在并发触发下最多对应一个 RecommendationRun。
- 每个已派发策略都能由 `batch_key -> flow_run_id -> recommendation_run_id` 完整追踪。
- 业务终态只由现有 `RunStateMachine` 合法产生。
- partial 结果不会被误报为全量成功。
- `legacy` 回退开关经过演练，切换不需要 schema rollback。
- Prefect 停止时 formal 推荐、数据同步、Outcome 和回测行为不受影响。
- 新增测试通过，现有 run/scheduler/exposure 测试无回归。

## 14. 迁移阶段

### 阶段 A：提取领域计划 module

- 从 `scheduler_service.py` 提取 `ExposurePlanningModule`。
- legacy 路径改为调用新 interface，行为保持一致。
- 先通过现有测试和计划快照测试。

### 阶段 B：建立 Prefect adapter 与本地运行环境

- 增加显式依赖和本地开发启动命令。
- 建立 flow、task、work pool 和 deployment。
- 增加 `strategy_exposure_batches` 与幂等 repository。

### 阶段 C：Shadow

- 配置为 `shadow`。
- 连续 10 个有效交易日记录并自动对账计划。
- 不派发第二套 experiment Run。

### 阶段 D：单任务切换

- 配置为 `prefect`。
- 禁用 APScheduler 中的 exposure cron；其他任务不变。
- 手动触发入口创建 Prefect flow run。
- 观察至少 10 个有效交易日。

### 阶段 E：决策

满足验收后，单独评估盘后流水线是否迁移。不得因为 exposure PoC 成功就自动迁移 formal 推荐或删除 `RunStateMachine`。

## 15. 回退

回退只改配置和调度启用状态：

1. 将 `EXPOSURE_ORCHESTRATOR=legacy`。
2. 恢复 APScheduler exposure cron。
3. 停止新的 Prefect exposure deployment 调度。
4. 已派发 RecommendationRun 按 MySQL 状态继续收敛，不删除、不重建。
5. 保留 `strategy_exposure_batches` 作为审计记录。

回退不能清空 Prefect 数据库或业务表，也不能重写已完成 experiment Run。

## 16. 暂缓决策

以下问题不阻塞 PoC，但在扩大迁移前必须另行决策：

- Prefect server 数据库的备份和保留周期。
- 正式环境是否继续单机 worker，还是引入独立执行节点。
- Prefect flow cancellation 如何映射到领域 Run cancellation。
- 是否将现有 scheduler 管理页扩展为双 orchestrator 视图。
- 盘后多步骤数据同步是否需要 Prefect artifact/result persistence。

## 17. 删除条件

只有 exposure 在 `prefect` 模式稳定运行至少 10 个有效交易日且回退演练通过后，才允许删除该任务在 `scheduler_service.py` 中的以下实现：

- exposure cron 注册与 reload 分支。
- 手动后台线程入口。
- `_run_strategy_exposure()` 中已被新 module 完整覆盖的编排代码。
- 固定 stagger 和轮询逻辑中已由 Prefect 明确接管的部分。

不得顺带删除其他 ScheduledTask 类型、推荐领域 lifecycle 或共享 helper。删除前需用 deletion test 证明复杂度已经由 Prefect deep module 吸收，而不是扩散到调用方。
