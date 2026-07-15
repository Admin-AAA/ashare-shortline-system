# S0.4 Schema inventory 与 DDL 安全闸门

## 目标版本与 owner

`backend/app/db/migrations/schema_inventory.py` 是旧 `init_db` helper surface 的机器可读清单。除特别标记外，旧 helper 对象由 `V000` 作为 **strict adoption baseline** 核验：缺少对象或形状不一致时停止，不能以 catch-and-ignore 宣称成功。`V000` 不删除表、不删除索引、不写业务数据。

当前显式 forward-only DDL 版本为：

| 版本 | 独立变更 |
| --- | --- |
| `V002_workbench_run_lifecycle` | `recommendation_runs` 的 canonical request/CAS/checkpoint/lease 列与两个索引；每列/索引一个 operation |
| `V003_add_model_route_max_tokens` | `model_route_config.max_tokens`，nullable `INTEGER` |
| `V004_add_model_route_temperature` | `model_route_config.temperature`，nullable `FLOAT` |
| `V005_add_model_route_timeout_seconds` | `model_route_config.timeout_seconds`，nullable `FLOAT` |

每个新增索引、UNIQUE 或类型调整必须建立独立 migration/operation，并在 MySQL 执行前通过 `DdlPreflight`。不得通过一个 catch-and-ignore 的兼容脚本绕过 runner。

## 旧 `_ensure_column` / `_ensure_column_type` / `_ensure_column_default`

目标版本：`V000` baseline adoption；以下是精确的表/列/目标形状。`_ensure_column_type` 只允许兼容类型调整，`_ensure_column_default` 的实验预设回填不在 schema startup 自动执行。

- `_ensure_column`: `recommendation_runs.score_line FLOAT NOT NULL DEFAULT 72`; `heartbeat_at DATETIME`; `progress_phase VARCHAR(80)`; `progress_current INTEGER NOT NULL DEFAULT 0`; `progress_total INTEGER NOT NULL DEFAULT 0`; `progress_message TEXT`; `interrupted_at DATETIME`; `checkpoint_phase VARCHAR(80)`; `filter_audit_json LONGTEXT`。
- `_ensure_column`: `scheduled_task_runs.summary TEXT`; `payload_json TEXT`; `result_json TEXT`。
- `_ensure_column`: `presets.score_line FLOAT NOT NULL DEFAULT 72`; `data_source VARCHAR(20) NOT NULL DEFAULT 'local_pool_preferred'`; `skip_aux_refresh BOOLEAN NOT NULL DEFAULT 0`。
- `_ensure_column`: `api_call_logs.prompt_tokens INTEGER`; `completion_tokens INTEGER`; `cache_hit_tokens INTEGER`。
- `_ensure_column`: `recommendation_outcomes.exit_date VARCHAR(10)`; `exit_price FLOAT`; `executed_return FLOAT NOT NULL DEFAULT 0`; `weighted_return FLOAT NOT NULL DEFAULT 0`; `position_size FLOAT NOT NULL DEFAULT 1`; `holding_days INTEGER NOT NULL DEFAULT 0`; `entry_slippage_bps FLOAT NOT NULL DEFAULT 0`; `exit_slippage_bps FLOAT NOT NULL DEFAULT 0`; `stop_loss_price FLOAT`; `take_profit_price FLOAT`; `exit_reason VARCHAR(40)`; `achievable_return FLOAT NOT NULL DEFAULT 0`; `outcome_label_legacy VARCHAR(30) NOT NULL DEFAULT 'pending'`。
- `_ensure_column_type`: `recommendations.feature_snapshot_json LONGTEXT NOT NULL`。
- `_ensure_column_default`: `presets.data_source` 的目标默认值为 `local_pool_preferred`。
- 旧的一次性数据语句 `UPDATE presets SET skip_aux_refresh = 1 WHERE run_mode = 'experiment' AND skip_aux_refresh = 0` 仅作为 inventory 记录，未由启动迁移重放；若要执行必须另建有审计、计数和回填验证的 data migration。

## 旧 `_ensure_index`

目标版本：`V000` baseline adoption；列顺序不可变。缺失/同名但形状不一致时停止，不创建一个“看起来相同”的替代索引。

- `daily_kbars`: `idx_kbars_td(trade_date)`; `idx_kbars_code_td(code, trade_date)`; `idx_kbars_td_ma5(trade_date, ma5)`。
- `stock_quotes`: `idx_quotes_td(trade_date)`; `idx_quotes_status(freshness_status, snapped_at)`。
- `limit_up_board`: `idx_lub_td_limit(trade_date, limit_type)`; `idx_lub_td_ltimes(trade_date, limit_times)`。
- `concept_sector`: `idx_cs_td(trade_date)`。
- `concept_sector_stock`: `idx_css_code_sc(code, sector_code)`。
- `capital_flow`: `idx_cf_td_main(trade_date, main_net_inflow_yi)`。
- `dragon_tiger`: `idx_dt_td_net(trade_date, net_amount_yi)`。
- `news_announcement`: `idx_na_code_date(code, announce_date)`。
- `model_route_config`: `idx_mrc_enabled_priority(enabled, priority)`。
- `margin_data`: `idx_margin_td(trade_date)`。
- `hk_holdings`: `idx_hk_td(trade_date)`。
- `holder_numbers`: `idx_hn_code_ed(code, end_date)`。
- `stk_limits`: `idx_sl_code_td(code, trade_date)`。
- `minute_kbars`: `idx_mk_td(trade_date)`; `idx_mk_td_code(trade_date, code)`。
- `auction_data`: `idx_auction_td(trade_date)`。
- `broker_recommends`: `idx_br_month(month)`。
- `holder_trades`: `idx_ht_code_ann(code, ann_date)`。
- `institutional_surveys`: `idx_is_code_sd(code, surv_date)`。
- `share_floats`: `idx_sf_code_fd(code, float_date)`。
- `pledge_stats`: `idx_ps_code_ed(code, end_date)`。
- `repurchases`: `idx_rep_code_ann(code, ann_date)`。
- `recommendation_runs`: `idx_runs_mode_trade_date(run_mode, trade_date)`; `idx_runs_status_started(status, started_at)`。
- `recommendations`: `idx_recs_run_score(run_id, final_score)`; `idx_recs_trade_score(trade_date, final_score)`; `idx_recs_code_trade(code, trade_date)`。
- `recommendation_outcomes`: `idx_outcomes_label(outcome_label)`; `idx_outcomes_entry_label(entry_triggered, outcome_label)`。

## `_migrate_minute_kbars`

目标版本：`V000` baseline inventory。精确目标为新增 `minute_kbars.freq VARCHAR(5) DEFAULT '1min'`，并存在 `UNIQUE(code, freq, trade_time)`，名称 `uq_minute_kbar_code_freq_time`。自然键重复必须由预检报告并停止。

旧索引 `uq_minute_kbar_code_time(code, trade_time)` 被列为 `FORBIDDEN_NOT_MANAGED`：forward-only runner 不执行 `DROP INDEX`，也不复制旧逻辑的静默异常吞掉行为。它不会被自动删除；若其存在导致业务需要人工处理，必须走独立、已批准且可验证的运维变更。

## 明确禁止的旧 `_migrate_v2` 行为

以下对象只在 `FORBIDDEN_DESTRUCTIVE_TABLES` 中作为负向安全断言出现，任何 migration 不得执行 `DROP TABLE`：`raw_quotes`、`raw_daily_bars`、`raw_limit_up_board`、`raw_concept_sector`、`raw_capital_flow`、`raw_dragon_tiger`、`raw_news_announcement`、`unified_quotes`、`daily_bars`、`daily_indicators`。同样禁止 `TRUNCATE`、无条件批量删除和自动删除重复键数据。

## 在线 DDL 预检证据

对既有业务表的索引、UNIQUE 或类型/列调整，`DdlPreflight` 只读检查：

1. InnoDB、估算行数、数据/索引/总表大小，并可配置最大行数/总字节数；
2. MySQL datadir 所在文件系统可用空间；远程/不可观测时为 `UNKNOWN`，严格 gate 拒绝继续；
3. `performance_schema.metadata_locks` 中 pending wait 及其 granted blocker；缺少 `SELECT` 权限为 `UNKNOWN`，不能降级为通过；
4. `information_schema.innodb_trx` 中超过阈值的长事务；缺少 `PROCESS` 权限为 `UNKNOWN`，不能降级为通过；
5. natural-key 重复组及有限 JSON 冲突样本；重复只报告并停止，不删除数据；
6. 当前 MySQL 版本与 `ALGORITHM`/`LOCK` 能力矩阵。

runner 在同一专用 connection 上设置 `lock_wait_timeout`、`innodb_lock_wait_timeout`、只读查询 `max_execution_time` 和单 operation wall-clock execution limit。任一检查为 `BLOCK` 或 `UNKNOWN` 都不执行 DDL；DDL 失败不写成功 ledger。
