# 系统体检与处置报告（2026-11-22 体检 / 2026-11-23 处置）

起因：`GET /api/admin/data-health` 冷路径读到 17s。修复后做了全系统体检，2026-11-23 把体检出的问题逐项处置完毕。

---

# 第一部分：data-health 17s

| 项 | 改动 | 文件 |
|---|---|---|
| 读路径禁网 | `open_dates_between / latest_trade_date / is_open` 增加 `allow_network`；`fetch_recent_trade_dates(allow_network=False)`；data-health 只读本地 `trade_calendar`，上游同步只留给盘后 `refresh_trade_calendar` | `services/trade_calendar.py`、`services/tushare_client.py`、`services/data_health_service.py` |
| stale-while-revalidate | 缓存过期先返旧值 + 守护线程重算（`allow_stale=True`，仅 HTTP 读接口）；数据管道/出票闸门/`force_refresh` 仍同步真算；后台用独立 Session，generation 变了不写回 | `services/data_health_service.py`、`services/data_health_snapshot.py`、`api/admin.py` |
| 冷算 SQL 收敛 | 大表走 INFORMATION_SCHEMA 估算（全库一次查询 + 60s 进程缓存）、同一张表一次诊断只数一遍、估算为 0 时退回精确计数（保住「空表」判定） | `services/data_health_service.py` |

实测：冷算 4.3~5.9s → **1.0~2.0s**；SQL 合计 4.5s → 1.7s；查询 137 → 119 条；缓存命中 7~10ms；TTL 过期后 HTTP 侧 **1ms** 返回旧值。得分语义逐字段一致（score 73 / score_db 100 / 参考日一致 / issues 0），只有 3 张表 `total_rows` 变估算值（`total_rows_estimated=true`）。

> 踩坑记录：改造中一度让 `force_refresh` 被新鲜缓存短路（0.06s 就返回），已加 `force=` 参数并补测试 `test_diagnose_all_force_refresh_bypasses_fresh_cache`。

---

# 第二部分：体检问题与处置

## 处置总表

| # | 问题 | 处置 | 验证 |
|---|---|---|---|
| P0-1 | 2026-11-20 / 11-23 五个短线策略 0 出票 | 定位为策略规则门 0 命中，非管道故障（证据见下） | 见「P0-1 结论」 |
| P0-2 | 健康快照「盘后出票成功率」恒 0 | 成功率改按「每个交易日最新一条记录（复核优先，其次原始）」判定，新增 `output_failed_at_original_dates` 保留 18:40 时点状态 | 真实数据 0% → **50%**（6/12 天最终失败） |
| P1-3 | `/api/strategies` 28ms → 651ms | `read_rolled` 按 target fingerprint 缓存 300s（`invalidate_profile_cache`，`refresh()` 落库后自动失效） | 651/954ms → **30/32ms** |
| P1-4 | 中线 trades 183/492ms、metrics 218/279ms | `horizon_analytics.trades()` 结果按 (horizon, strategy_id, OUT_DIR) 缓存 30s，`/trades` 与 `/metrics` 共用一次全量结算；新批次落盘即失效 | trades 183/492 → **111/140ms**；metrics 218/279 → **90/91ms** |
| P1-5 | `recommendation_runs` 6.1GB/631 行、单行 8MB | 写库前裁掉前端从不读的 `rows[].prefilter / trace_summary`（`compact_filter_audit_for_storage`）；新脚本 `scripts/compact_filter_audit.py` 处理历史 | 审计载荷 5.12GB → **2.96GB**（省 2.5GB / 48.8%，1103 个 run） |
| P1-6 | 过滤审计快照原始 JSON 存 3.71GB / 1133 行 | 迁移 `0033_filter_audit_compressed` 改为 zlib 压缩列 `payload_z`（读写走 `app/services/runs/audit_payload.py`），`OPTIMIZE TABLE` 回收空间 | 3.753GB → **0.27GB**；健康巡检 200 行读取 5.12s/626MB → **28ms/44MB** |
| P1-7 | `minute_kbars` 索引 9.96GB，`idx_mk_code_time` 与唯一键重复 | 迁移 `0034_drop_redundant_minute_index` 删除 `idx_mk_code_time`（所有查询都带 `freq`，唯一键已覆盖）；`OPTIMIZE TABLE` 回收 | 索引 9.96GB → **5.49GB**；单股查询 2ms、50 只批量 143ms |
| P1-8 | 出票后 LLM 风险备注自 10-14 静默 | 根因是 daemon 线程随 Prefect 子进程被杀（不是 LLM 不可用）；改为出票全部落库后 `asyncio.gather` 并发挂载 | 实测 run_20261124_130417_4c2efb updated=1，写入 stance + 4 条风险标签；review-health 由 disabled 变 `enabled=true/status=ok` |
| P2-1 | 重启端口风暴 + `dev.sh` 3s 误报失败 | `kick()` 改轮询到 90s 并验 HTTP 真实响应；失败提示区分「端口占用 / 已监听但无响应」 | 本次重启实测「等待 8s 就绪」 |
| P2-2 | Prefect 502 冒成 500 | `/tasks/{id}/run` 增加 Prefect 传输异常 → 503 可读提示，其它异常照常抛 | 单测覆盖 |
| P2-3 | readiness 因 `model_completion 0/0` 永久 degraded | 无调用证据判 `unknown`（info 级提示），不再算 degraded | `/api/admin/readiness` → **ready** |
| P2-4 | 夜间打开持仓页触发 `rt_k` 全上游失败告警，挂 24h | 实时类接口（`rt_k`/`rt_min`）非交易时段失败只记日志、不告警 | 单测覆盖 |
| P2-6 | 性能基准 `/api/presets` 已下线，bench 预热即 404 | 删除失效条目 | bench 可完整跑通 |
| P2-7 | **QVIX 停更 8 个交易日**（QVIX50/QVIX300 最新 11-13，主指数已到 11-23），而 `rsi6-capitulation` 的 QVIX 尖峰分支在字段缺失时**静默降级为仅价格门** | 补数到 11-18（上游本身有 2~3 天延迟）；巡检新增 `db:index_daily:QVIX*` 项，滞后 >5 个交易日判黄并提示跑 `scripts/backfill_qvix.py` | 巡检已显示 2 个 QVIX 项（green，lag 3） |

> P2-5（promax `rt_k` / teajoin 网络抖动）属上游侧，代码侧只做了告警降噪（P2-4），网络本身需继续观察。

## P0-1 结论：零出票是策略规则门 0 命中，不是数据/管道故障

2026-11-20 与 11-23 各 5 个 formal run 全部 `completed`、`recommended_count=0`，`recommendations` 表 11-20 起无行。逐层核对：

| 交易日 | run | 预过滤通过（进 features） | 进规则门 skill_input | 最终出票 |
|---|---|---|---|---|
| 11-19 | rsi6-capitulation | 5022 | **30** | **5** |
| 11-19 | chen-xiaoqun | 36 | 1 | 1 |
| 11-20 | rsi6-capitulation | 5021 | **0** | 0 |
| 11-23 | chao-gu-yang-jia | 28 | 0 | 0 |
| 11-23 | chen-xiaoqun | 36 | 11 | 0 |
| 11-23 | oversold-capital-reversal | 4 | 3 | 0 |
| 11-23 | rsi6-capitulation | 5016 | **0** | 0 |
| 11-23 | sector-relative-strength-leader | 12 | 12 | 0 |

- 数据侧齐备：11-23 `daily_kbars` 5570 行、chip_cost_50 100%、ma20/atr 99.9%、capital_flow/index_daily/limit_up_board/dragon_tiger/hsgt 均到 11-23。
- 11-23 市场判定 `强牛/高潮`（`effective_score_line=64`），而 `rsi6-capitulation`（恐慌出清）在全市场 5016 只里规则门 0 命中——与该策略语义一致。
- 11-20 是 `弱势/平淡`（有效线 60），rsi6 同样 0 命中，说明当天确实没有符合「RSI6 超卖 + QVIX」的票。
- 4 个带 `universe` 段的策略（炒股养家/陈小群/超跌反包/板块相对强度）预过滤后只剩 28/36/4/12 只候选，是被各自的 `universe.include`（成交额≥3亿、自由流通市值≥20亿、换手区间、`primary_excess20≥3`）收窄，属配置预期。

> 附带发现（未改，属口径问题）：`recommendation_runs.universe_count` 落的是 `len(research_contexts)`（预过滤后），而不是 `breadth.scoped_candidate_count`（市场池 5016）。运行记录页把 36 显示成「股票池」，容易被误读为系统只看了 36 只票。

---

# 第三部分：验证

## 性能（`scripts/perf/bench.py`，热测 5 次）

| 接口 | 10-26 基线 p50/p95 | 11-22（体检时） | 11-23（处置后） |
|---|---|---|---|
| `/api/strategies` | 28 / 45 | 651 / 954 | **30 / 32** |
| `/api/horizons/mid/trades` | 73 / 77 | 183 / 492 | **111 / 140** |
| `/api/horizons/mid/metrics?origin=all` | 55 / 56 | 218 / 279 | **90 / 91** |
| `/api/horizons/mid/strategies` | 15 / 30 | 60 / 61 | 56 / 57 |
| `/api/admin/data-health` | 5 / 6 | 4 / 19 | 7 / 10 |
| `/api/dashboard/overview` | 311 / 351 | 190 / 571 | 203 / 236 |

热 p95 ≤ 200ms：**27/28**（仅 `/api/dashboard/overview` 236ms 略超，仍好于基线 351ms）。中线复盘整页 p50 844 → **700~800ms**（冷启动首次会更久：策略画像/中线结算缓存都要建一次）。

## 测试

相关 24 个测试文件 **224 passed**（新增 10 条：读路径禁网 / SWR 返旧值 / 并发只起一次后台 / 默认仍同步 / force 不被短路 / 出票成功率取最新复核 / trades 缓存 / 画像缓存 / Prefect 无证据判 unknown / 审计瘦身纯函数）。

## 数据

- `filter_audit` 载荷 5.12GB → **2.96GB**；`OPTIMIZE TABLE recommendation_runs` 已跑，表物理占用 6.08GB → **3.53GB**（回收约 2.5GB）。
- 过滤审计快照改 zlib 压缩存储（`0033_filter_audit_compressed`）：内容 3.71GB → **0.239GB**，表物理占用 3.753GB → **0.27GB**。1134 行逐行解压校验全部通过。
- 库总量 49.62GB → **47.9GB** / 91 表（`minute_kbars` 14GB、`auxiliary_snapshot_rows` 11GB、`backtest_input_artifacts` 5.7GB 未动）。

---

# 第四部分：仍建议跟进（未做，需你决定）

1. **大表留存策略**：`minute_kbars` 48M 行 / 14GB、`auxiliary_snapshot_rows` 12.5M 行 / 11GB 都没有清理脚本。前者是日内回测/策略输入，后者是辅助数据快照，删哪一段属研究资产决策，因此没有动。可按 `scripts/prune_backtest_artifacts.py` 的思路加一个按日期窗口的清理脚本（给我天数即可）。
2. **`universe_count` 口径**：落库是 `len(research_contexts)`（预过滤后），运行记录页显示成「股票池」，容易被读成「系统只看了 36 只票」。改成 `scoped_candidate_count` 涉及运行记录页与若干聚合口径，建议单独确认。
3. **数据新鲜度黄项（上游/调度侧，非代码 bug）**：
   - `basic_<year>.parquet` 停在 11-11（周度快照没有常驻调度，线上读不到会走网关现拉）；
   - `postclose_late_data_repair` 连续 5 次 partial_failed，失败数据集是 `news_announcement`（tushare `anns_d` 失败后 akshare 兜底也失败）；
   - `research_cache/kpl_list` 滞后 1 天（22:20 抓取时上游未发布，任务记录不写空日）；
   - `runtime/pool_bar_source_stats.json` 6 天未更新；
   - `formal:rolling_r12` 回放取数失败（mid-incentive-option / -vol 各 271 天）。
4. **`/api/dashboard/overview` 203ms**：略超 200ms 预算但已好于基线 311ms，要稳进 200ms 需再拆一次聚合。
