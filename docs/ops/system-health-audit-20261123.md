# 系统数据健康体检（2026-11-23）

> 目的：回答「为什么数据健康问题几乎每天都在出」，区分**真问题**与**时序/口径造成的必然误报**，给出结构性修复清单。
> 数据来源：`backend/logs/app.log`(11-12~11-23) + `app.log.1`(10-20~11-12)、`scheduled_tasks`/`scheduled_task_runs`、`recommendation_runs.filter_audit_json`、`index_daily`/`limit_up_board` 的 `created_at`。
> 窗口：2026-10-24 ~ 2026-11-23，共 **21 个交易日**。

---

## 一、一句话结论

**约 2/3 的健康告警是「流水线/巡检跑在数据到齐之前」这类时序倒挂产生的必然误报，不是数据坏了。**
`formal:daily_output` 在 21 个交易日里 **19 天报红（90%）**——它每天都在重复同一个结构性事实：17:35 的出票流水线依赖 21:00 以后才入库的数据。

---

## 二、30 天告警画像（量化）

| 指标 | 数值 |
|---|---|
| 每日黄项数 | 26 ~ 93 条（11-03 单日 92 条） |
| 每日红项数 | 0 ~ 16 条 |
| `formal:daily_output` 红 | **19 天 / 21 交易日（37 次告警）** |
| `file:research_cache/kpl_list` 黄 | **21 天 / 54 次（约 2.6 次/天）** |
| `job:postclose_late_data_repair` 黄 | 20 天 / 43 次 |
| `file:runtime/datasource_capability_matrix.json` 黄 | 19 天 / 53 次（已于 11-21 修复周期阈值，今天不再黄） |
| `job:data_freshness_check_daily` 黄 | 16 天 / 37 次 |
| `file:research_cache/limit_list_d` / `file:xiaoshi_min_all` 黄 | 17 天 / 16 天 |

单项一天报 2~3 次 => 同一问题每天被 4 个巡检作业各评估一遍。

今日（11-23）真实非告警 `[ERROR]` 只有 **8 条**，全部是 `post_sync` 失败引发的 Prefect 记录。日志层面系统并不"乱"，乱的是健康面板。

---

## 三、根因 A：三处时间轴倒挂（必然每天发生）

### A1. 出票流水线 17:35 跑在数据到齐前 → 必然失败

实测上游每日入库时间（`created_at` 转 CST）：

| 交易日 | index_daily 入库 | limit_up_board 入库 |
|---|---|---|
| 11-23 | **21:02** | 18:24 |
| 11-20 | **21:04** | 18:28 |
| 11-19 | **21:03** | 18:41 |
| 11-18 | 22:39 | 22:39 |
| 11-17 | 22:28 | 22:28 |
| 11-16 | 22:48 | 20:19 |
| 11-10 | 次日 00:40 | 次日 00:40 |

`post_sync_formal_recommendation` cron = `35 17`（17:35 CST），而它的就绪闸门强制要求 `index_daily` + `limit_up_board` 覆盖当日。**17:35 时这两张表 100% 还没有当天数据。**

今日实测：17:35 起共 6 次尝试（3 次 schedule + 3 次 manual），全部 `failed`，19:08 有界重试耗尽后放弃。

### A2. `data_freshness_check_daily` 18:00 跑在数据到齐前 → 每天固定报 10 张表滞后

18:00 执行，此时 `index_daily / limit_up_board / capital_flow / dragon_tiger / hsgt_flows / hsgt_top10 ...` 全部未入库。
每天稳定输出「数据新鲜度异常 10 张表」，然后因为"有异常"把任务自己记成 `partial_failed` → 又生成一条 `job:data_freshness_check_daily` 黄项。**一条时序错误变成两条常态黄项，16 天 37 次。**

11-23 实例：
```
daily_kbars: 2026-11-23 关键列全 0/NULL: chip_cost_50,chip_winner_rate
capital_flow/index_daily/limit_up_board/dragon_tiger/hsgt_flows/hsgt_top10: 滞后 1 个交易日
```

### A3. 晚检 21:40 跑在自愈补跑 22:43 之前 → 每天记一次红

今日 21:40 `data_quality_check_evening` 记录红 2（含出票）；22:43 自愈完成补跑后我才重算出红 1。
**巡检永远比修复早 1 小时**，于是红项每天被"如实"记录一次，次日早上看板仍显示昨天的红。

---

## 四、根因 B：本该兜住 A 的补跑链自己失效了（真 BUG）

设计意图是好的：17:35 失败 → 21:00 晚补 → `_late_repair_repick` 补跑出票。今天它没生效。

`postclose_late_data_repair` 今日步骤与耗时：

```
quotes(16.2s) → daily_kbar_batch(2.4s) → auxiliary(151.0s)
→ health_remediation(3.6s，补 index_daily/limit_up_board)
→ late_repick  结果: status=skipped, reason=gate_blocked
   blockers: ["指数日线未覆盖 2026-11-23（最新 2026-11-20）", "指数日线近期仍有缺口"]
```

但 `index_daily` 的 11-23 行 `created_at = 21:02:54`，就在同一次任务内、`health_remediation` 阶段写进去的。
**late_repick 看不见自己刚补的数据**，判定读到的是陈旧事务快照（MySQL REPEATABLE-READ + 连接池复用）。

同类坑在 `scheduler_service._wait_for_recommendation_run` 的注释里已被记录并修复过（改用 `engine.connect()` 每次建物理新连接），但 `_late_repair_repick → _diagnose_postclose_readiness` 这条路径没有同样处理——它虽然传了 `force_refresh=True`，force 只跳过缓存，跳不过事务快照。

最终靠 22:43 自愈 daemon（新进程新连接）才补成，出票晚了 5 小时。

> 影响：`postclose_late_data_repair` 20 天 43 次黄，其中相当一部分是"补完数却不出票"。

---

## 五、根因 C：巡检口径自激（把常态算成异常，再自我放大）

1. **74 项检查里 19 项是 research/回测用的 parquet**（`midterm/basic_*`、`daily_basic`、`stk_limits`、`index_daily.parquet`、`cache/daily_kbar`、`research_cache/kpl_list`、`limit_list_d`、`xiaoshi_min_all`、`events/*`）。这些文件**没有日更调度任务**，靠手动/研究脚本产出，长期必然滞后。
   它们和实盘库表共用同一把健康分尺子 → 健康分理论上限就被压掉 5~10 分，永远到不了绿。
2. **4 个巡检作业跑同一批 item**：`data_quality_check_morning 08:50`、`data_health_snapshot_recheck 08:55`、`data_health_snapshot_daily 18:40`、`data_quality_check_evening 21:40`。
   告警有 24h 日级冷却，但 `sync_alerts` 的规则是「severity 变化立即发」。项状态在 green↔yellow 之间抖动（早上数据未到=黄、晚上补齐=绿、次日又黄）时，**每次都绕过冷却** → kpl 21 天 54 次。
3. **巡检任务把"发现问题"记成自己的失败**：`data_quality_check_morning/evening` 状态长期 `partial_failed`，于是每个真问题额外生成一条 job 级黄项。

今天最新重算：红 1 / 黄 5 / 绿 67，健康分 73~82。**在没有真故障的日子里，这套口径下也拿不到 90+。**

---

## 六、真问题 vs 假问题

### 真问题（会影响实盘或数据正确性）

| 问题 | 证据 | 影响 |
|---|---|---|
| **QVIX 缺 8 个交易日** | `QVIX50.SH/QVIX300.SH` 最新 11-13，最后入库 11-15 05:33 | `rsi6-capitulation` 的 QVIX 尖峰分支长期降级为 0（代码内静默降级不阻断），策略少一半触发条件；且 `INDEX_CODES` 不含 QVIX，**完全无监控、无调度** |
| **补跑链快照失效** | 见第四节 | 出票晚 5 小时，且每天制造一条红项 |
| **news_announcement 缺数** | 11-23 仅 15 条（11-17 125、11-20 65）；批次连续失败 `event_provider_request_failed` | 新闻/公告类策略与研究输入不足；但 15 条也判"完成"，无行数下限门禁 |
| **连续 2 个交易日 0 票** | 11-20、11-23 各 5 个 formal run 全 `recommended_count=0`；latest recommendation 停在 11-19（lag 4 交易日） | 政策闸门 `strong_climax_top_n`(20 只)/`strong_high_ivol_veto`(4)/`max_dd60_veto`/`high_turnover_veto` 全砍。**不是数据缺，是过滤过紧**，需人工判定行情无票还是阈值该调 |
| `margin_detail` 11-23 缺失 | 主备源都空（`primary missing {'SZ','SH'}, backup returned nothing`） | 融资类过滤降级 |

### 假问题（时序/口径产生，不是数据坏了）

| 告警 | 为什么必然出现 |
|---|---|
| `formal:daily_output` 每天红（19/21 天） | A1：17:35 < 21:00 数据到齐 |
| `job:post_sync_formal_recommendation` 每天红 | 同上，且「连续 8 次非成功」是这个结构的自然结果 |
| `data_freshness_check_daily` 每天 10 张表滞后 | A2：18:00 检查跑在 21:00 补数前 |
| `formal:daily_output` 在晚检时红、次日早晨绿 | A3：巡检早于自愈 |
| 19 项 research parquet 长期黄 | 没有日更调度，属设计常态 |
| `kpl` 每天黄 | 22:20 抓取时开盘啦尚未发布（`20261123 empty`），任务不重试也不改期 |
| `job:postclose_late_data_repair` 每天黄 | 第四节补跑失效 + news 单数据集失败拖累整体状态 |

---

## 七、修复清单（按性价比排序）

### P0（今天做，能直接消掉每天的红项）

1. **修 `_late_repair_repick` 的快照问题**
   `backend/app/services/scheduler_service.py:221` 的 `_diagnose_postclose_readiness(db, trade_date)`
   → readiness 判定改用独立物理连接（照抄 `_wait_for_recommendation_run` 里 `engine.connect()` 的做法），或在 `health_remediation` 后 `db.commit()` + 新 `SessionLocal()` 重评。
   验收：21:00 这次任务里 `late_repick.status == completed`，而不是 `gate_blocked`。

2. **让晚检跑在自愈之后**
   `data_quality_check_evening` cron `40 21` → `50 22`（或 23:00）。
   验收：晚检不再记录"出票 0/5"红项，第二天早上看板无隔夜红。

3. **修 `data_freshness_check_daily` 的时间或口径**（二选一）
   - cron `0 18` → `30 21`（晚补后）；
   - 或给 `lag=1` 的表在 21:00 前豁免"滞后 1 日"判定。
   验收：该任务不再每天 `partial_failed`，`job:data_freshness_check_daily` 黄项消失。

### P1（本周做，消掉大半黄项噪音）

4. **QVIX 纳入巡检 + 加日更**：`INDEX_CODES` 增加 `QVIX50.SH`/`QVIX300.SH`；`scripts/backfill_qvix.py` 挂进盘后任务（21:05 后）。
5. **kpl 抓取改期**：`research_kpl_list_daily` cron `20 22` → `30 23`（或次日 07:00 再补一次）；空结果时记 `skipped_empty` 而不是 `completed`，避免"成功但没数据"。
6. **巡检不再把"发现红黄"记成任务失败**：`data_quality_check_*` 用独立状态（如 `reported`），红黄只体现在报告里。
7. **补跑链加"数据到齐事件"触发**：`health_remediation` 完成后立即重评 `_diagnose_postclose_readiness`，通过则同一任务内继续出票，不等自愈。

### P2（结构治理，决定健康分是否可信）

8. **健康分分层**：把 19 项 research/回测 parquet 拆成独立「研究产物」分组，不计入实盘健康分（或权重 0.2），只在面板展示。
9. **news_announcement 加行数下限门禁**：当日行数 < 近 20 日中位数 50% 判失败，避免 15 条也算成功。
10. **`basic_<year>.parquet` 加周度调度**（或明确标注为手动产物、不进巡检）。
11. **`pool_bar_source_stats.json` 要么接调度、要么退役该项**（现已 6 天未更新，9 天 22 次黄）。
12. **巡检性能**：`check_chip_history` 单项 106s，整轮 2m33s；`/api/admin/data-quality?refresh=true` 会 HTTP 超时。给该检查加索引或改成增量（只看近 20 日）。

### 需要你决策的非技术项

- **出票时点**：把 `post_sync` 从 17:35 挪到 21:10（晚补后）能一次性消掉 A1，但会改变 `live_forward` 的 `actual_generated_at` 口径与"按时出票"定义。要么改时点 + 改口径，要么保留 17:35 但接受它每天失败一次、由 21:xx 补跑兜底（此时 `formal:daily_output` 必须改成"以当日补跑为准"而不是"17:35 按时"）。
- **0 票**：`strong_climax_top_n` 连续两天砍掉全部候选，是行情无票还是阈值过紧，需要你看一眼那 20 只被砍的票。

---

## 八、附：今日（11-23）事件链

| 时间(CST) | 事件 |
|---|---|
| 17:35~19:08 | `post_sync` 6 次尝试全 failed（指数日线/涨停板未到）；有界重试耗尽 |
| 18:00 | `data_freshness_check_daily` 报 10 张表滞后 → 自身 partial_failed |
| 18:24 | `limit_up_board` 11-23 入库（173 条） |
| 18:40 | `data_health_snapshot_daily` 拍快照（此时数据仍不全） |
| 21:00~21:04 | 晚补：quotes→daily_kbar→auxiliary(151s)→health_remediation→`late_repick=skipped(gate_blocked)` |
| 21:02:54 | `index_daily` 11-23 入库（本次任务自己写的，但 late_repick 没看见） |
| 21:40 | 晚检记红 2 / 黄 5 |
| 22:20 | kpl 抓取：`20261123 empty（尚未发布）`，任务仍 completed |
| 22:43 | 自愈 daemon 补跑出票完成（5 个 run，0 票） |
| 23:34 | 手动重算：红 1 / 黄 5 / 绿 67 |

---

## 九、修复记录（2026-11-24 凌晨落地）

### 已改代码

| # | 问题 | 改动 | 文件 |
|---|---|---|---|
| P0-1 | 晚补补跑看不见自己刚补的数据 | `_diagnose_postclose_readiness(..., fresh_session=True)`：先 `commit` 再在独立物理连接上重跑诊断；晚补补跑改用它 | `scheduler_service.py` |
| P0-2 | 晚检跑在自愈补跑之前 | `data_quality_check_evening` 21:40 → **22:50** | `scheduler_pure.py` |
| P0-3 | 新鲜度检查跑在数据到齐前 | `data_freshness_check_daily` 18:00 → **21:30**（晚补后） | `scheduler_pure.py` |
| P0-4 | 健康快照 18:40 取样时数据未到、出票未补跑 | `data_health_snapshot_daily` 18:40 → **22:55** | `scheduler_pure.py` |
| P0-5 | 改 cron 常量对线上无效（注册函数「已存在就不动」） | 新增 `_converge_task_cron`，cron/name 以代码为准幂等收敛并记 INFO | `scheduler_service.py` |
| P1-1 | QVIX 缺 8 个交易日且无监控 | `INDEX_CODES` 增加 `QVIX50.SH/QVIX300.SH`；新增 `qvix_daily_refresh` 任务（21:10，`backfill_qvix.py --since`） | `data_quality_monitor.py`、`scheduler_pure.py`、`scheduler_service.py` |
| P1-2 | kpl 22:20 抓取时上游未发布，任务仍记 completed | cron 22:20 → **23:30**；空结果改记 `skipped_empty`（不再算完成、不告警） | `scheduler_pure.py`、`scheduler_service.py` |
| P1-3 | 巡检把「发现红黄」记成自身 partial_failed，重复计分 | 新鲜度/质量巡检任务不再因红黄改状态，红黄只写摘要 + 各自告警 | `scheduler_service.py` |
| P2-1 | 19 项 research parquet 长期黄，压死健康分上限 | 新增 `RESEARCH_ONLY_FILE_KEYS` + `_item` 自动打 `scope`；`quality_score_penalty` 只计 `trading`，research 项仍展示不扣分 | `data_quality_monitor.py` |
| P2-2 | `pool_bar_source_stats.json` 是按需缓存，被当日更数据检查 | 从 `AGE_FILES` 移除（9 天 22 次黄，无一对应真实问题） | `data_quality_monitor.py` |
| P2-3 | `basic_<年>.parquet` 无调度（滞后 8 天） | 新增 `midterm_basic_snapshot_weekly`（周六 10:00，跑 `backfill_longterm.py basic_full`，带断点只补缺失日） | `scheduler_pure.py`、`scheduler_service.py` |
| P2-4 | 公告可能只有 15 行也判成功 | `news_announcement` 加 `minimum_record_count=40` | `auxiliary/production.py` |
| P2-5 | `check_chip_history` 单项 106s，整轮 2m33s | 窗口 120 → 30 个交易日，先走索引取日期列表再聚合 | `data_quality_monitor.py` |

### 验证

- 单元测试：`tests/test_data_quality_monitor.py`、`test_late_repick_after_repair.py`、`test_self_heal_repick_and_alert_cooldown.py`、`test_qvix_datasource.py`、`tests/quality`、`tests/scheduler`、`test_data_health_service.py`、`test_check_data_health_script.py` —— **102 passed**。
- 两个契约测试按新语义更新：晚补桩接受 `fresh_session` 关键字；`test_freshness_task_quality_red_does_not_fail_the_job` 断言红项不再把作业标成 partial_failed。
- 巡检整轮耗时 **2m33s → 42.5s**（`chip_history` 不再进慢检查榜）。
- 检查项 **74 → 78**（新增 2 个 QVIX 指数项 + 2 个新调度任务项）。
- 调度 cron 已在 DB 收敛（`ensure_scheduled_task_registrations()` 实测写回成功）。

### 改期当天的一次性现象（次日自愈，不是新故障）

改 cron 时当天旧时点已经跑过，`last_expected_run` 按新时点算出「应有但没跑」，于是
`data_freshness_check_daily` / `data_quality_check_evening` / `data_health_snapshot_daily` /
`research_kpl_list_daily` 各报一次「漏跑」。明天按新时点正常执行后自动消失。
若这类误报反复出现，再给 `scheduled_tasks` 加 `cron_changed_at` 列做改期豁免（本次未做，避免为一次性现象改 schema）。

### 仍未闭环

| 项 | 状态 |
|---|---|
| QVIX 历史缺口 | 已加任务并手工回补；`backfill_qvix.py` 走 akshare 较慢，需确认补齐到 11-23 |
| 连续 2 个交易日 0 票 | **未动**，属策略闸门（`strong_climax_top_n` 等）取舍，需要人判断行情无票 vs 阈值过紧 |
| 出票时点 17:35 | **未改**。P0-1 修好后 21:00 晚补即可补出票，不再依赖 22:43 自愈；若仍要挪时点，需同步改 `live_forward` 的 `actual_generated_at` 口径 |
| `formal:rolling_r12` | 未动（mid-incentive-option/-vol 各 271 天回放取数失败，属研究侧回放数据） |
| `alerts:stale_active` | 旧 `task_status|data_quality_check_morning` 告警按 24h TTL 自然过期 |
