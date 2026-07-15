# RunRequest 契约

`backend/app/services/runs/contracts.py` 是推荐运行输入的唯一领域契约。API、Scheduler、CLI/internal task 和 worker 不应自行拼接 `params_json`；入口在持久化或执行前都必须经过 `parse_run_request()`，worker 只从已持久化的 canonical JSON 恢复。

## V1 字段

| 字段 | 默认值 | 边界/语义 |
| --- | --- | --- |
| `schema_version` | `1` | 当前唯一支持版本；未来版本拒绝 |
| `run_mode` | `formal` | `formal`、`experiment`、`backtest` |
| `run_name` | `临时运行` | 非空，最多 120 字符 |
| `data_source` | `auto` | `auto`、`local_pool`、`local_pool_preferred`、`local_pool_realtime`、`realtime` |
| `preset_id` | `null` | 可选，最多 80 字符 |
| `skills` | `[]` | `SkillSelection` 元组；`skill_id` 唯一，权重 `(0, 100]`，顺序 `0..100000` |
| `score_line` | `72.0` | `[0, 100]` |
| `risk_template` | `balanced` | 非空，最多 40 字符 |
| `output_count` | `6` | `1..1000` |
| `skip_aux_refresh` / `disable_agent_review` | `false` | 严格布尔值 |
| `trade_date_override` | `null` | `YYYY-MM-DD` |
| `market_scope` | `all` | 非空，最多 30 字符 |
| `agent_review_limit` | `0` | `0..100000`；并发数 `0..100` |
| `hide_recent_repeats` / `dedup_same_day` / `matrix_run` | `true` / `true` / `false` | 严格布尔值 |

`RunRequest` 和 `SkillSelection` 都是 frozen Pydantic models；`skills` 在校验时按 `(sort_order, skill_id)` 稳定排序。数字和布尔值不接受字符串或 `bool -> int` 的隐式转换。

## Canonical JSON 和安全边界

`to_canonical_json()` 始终输出所有字段（包括 `null` 默认值），使用 UTF-8、稳定 key 排序和紧凑分隔符。`request_schema_version` 必须等于 `RunRequest.schema_version`；`run_mode` 仍保存在独立索引列。凭据字段（token、password、secret、authorization、连接串等常见变体）在解析和序列化边界都会被拒绝，不能进入 `request_json`。

## 版本升级

没有 `schema_version` 的历史 payload 视为 V0，由 `upgrade_run_request_document()` 显式升级到 V1。已支持的映射包括：`mode/name/data_source_mode/risk/trade_date`、字符串或对象形式的 `preset`、正向语义的 `review/refresh`、`selected_skill_ids/selected_skills`、`single_skill_id/skill_id`，以及历史 `backtest: true` 标记。未声明的字段不会被静默丢弃，而是由 V1 的 `extra="forbid"` 拒绝。高于当前版本的请求直接返回 `unsupported_request_schema`。

所有入口对无效请求都收到 `RunContractError(code="invalid_request_schema", ...)`（凭据和未来 schema 使用各自稳定错误码），方便 API、调度器和内部任务统一处理。

## Expand → backfill → cutover

`V002_workbench_run_lifecycle` 以 nullable 方式新增 `request_schema_version`、`request_json` 和 `execution_version`；前三列没有 `DEFAULT 1`，`params_json` 保留不删除。新 writer 在同一事务中写入独立索引列、canonical `request_json`、`request_schema_version` 和 legacy `params_json` 投影。

历史数据使用受限批处理脚本回填：

```bash
cd backend
.venv/bin/python scripts/backfill_run_requests.py --batch-size 500 --verify-cutover
```

每批只处理 `request_schema_version` 或 `request_json` 缺失的行。脚本先解析旧 `params_json`，再校验 `run_mode`、`preset_id`、`run_name`、市场范围、风险模板、评分线和输出数量等独立列；任一 JSON/列不一致的行保持 nullable，不会被标记为 schema V1，并在报告中计为 invalid/mismatch。脚本不会删除或改写 `params_json`，可重复执行，已完成行会成为 no-op。

`--verify-cutover` 在 worker 切换前检查 queued/running/interrupted 行均能从 canonical pair 恢复，并验证 `recommendation_runs` 总行数及 formal/experiment/backtest（以及其他现存 mode）分组计数未变化。worker/subprocess 只接受通过同一校验的 canonical request；未回填或列/JSON 漂移的 run 会被拒绝，而不是回退到 `params_json`。
## ExecutionVersionBuilder（审计身份）

`CanonicalExecutionVersionBuilder` 将 `ExecutionVersionInput` 规范化为稳定 JSON，并持久化为 `execution-v1:<sha256>`。它仅用于结果归因和审计，**不会参与候选筛选、策略评分或排序**。输入固定包含：应用 `version`/`revision`、`request_schema_version`、已选执行 policy、Factor `ExecutionPlan` fingerprint，以及从 `StrategyDescriptor` 投影的 strategy/effective/manifest/implementation fingerprints。

执行 policy 的嵌套对象按 key 排序、禁止 NaN/infinity 和非 JSON 对象；策略贡献按 `strategy_id` 排序。任何凭据字段（包括嵌套 `api-token`）或连接 URL/credential-shaped application revision 都会在构建前拒绝，因此 canonical JSON、哈希、`recommendation_runs.execution_version` 和运行摘要不包含密钥。部署可显式设置 `ASHARE_APPLICATION_VERSION` 与 `ASHARE_APPLICATION_REVISION`；未设置时固定使用 `development` / `unknown`。

当前推荐入队和旧 recommendation-backtest writer 都在创建行时写入该 nullable 列。历史记录保持 `NULL`，不会被此任务回写。当前 builder 已接入冻结 Factor plan；`StrategyExecutionFingerprint.from_descriptor()` 是 StrategyDescriptor 接线点，待 S2 装配完成后可增加策略贡献而不更改版本格式。