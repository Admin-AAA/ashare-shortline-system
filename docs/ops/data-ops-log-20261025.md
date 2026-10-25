# 2026-10-25 数据运维记录（P0-3 / P1 / P2）

只记录对生产库/文件的实际写入与范围；代码改动见 git log。所有回补只填 NULL（或明确的坏值），不覆盖已有非空值。

## 1. KDJ/BOLL/CCI/vol_ratio 本地复算回补（`kdj_boll_cci_fill.py`）
- 阶段 1（早）：2025-01-01 ~ 2026-05-31 的 kdj/boll/cci；vol_ratio 2026-05 起。
- 本轮（18:42–18:49 CST）：按季度分批 2024-01-01 ~ 2026-05-31 全部 9 列，写入行数
  2024Q1 309,950 / Q2 315,484 / Q3 341,662 / Q4 326,797；2025H1 629,440 / H2 682,430；2026-01~05 520,176。
- 回补后 NULL 残留（新股/停牌/不足 20 日窗口）：2024 boll 1,959 cci 1,319；2025 boll 1,387 cci 959；2026 boll 289 cci 157。
- 2024 之前（2022–2023）未回补：本表自 2021-05 起，2022–2023 的 kdj/boll/cci 仍为 NULL，可用同函数 `since=2022-01-01` 继续（owner 域，未动）。
- 补充（18:50–18:55 CST）：2022-03-01 ~ 2023-12-31 同法回补，写入行数
  2022-03~06 394,995 / 2022-07~12 617,344 / 2023H1 604,525 / 2023H2 654,209。2022-01~02 未回补（需要约 40 个交易日历史让 KDJ 收敛，库内 daily_kbars 自 2022-01 起）。

## 2. 缺口补数（18:55–19:05 CST）
- daily_basic 2025-01-02 ~ 2025-01-14（9 个交易日）：`scripts/backfill_daily_basic.py`（网关，覆盖写该日），每日 5369–5377 行、覆盖率 100%。
- margin_data 2026-05-20 ~ 2026-07-08：31 个整日缺失 + 4 个仅 259 行的样本日，用同库 `margin_detail`（完整源；2026-10-22 对比 4484 行 rzye/rzmre/rqye 100% 一致）INSERT IGNORE 补缺，共 130,192 行，只补缺失 (code, trade_date)。
- block_trades：2026-07-09 起停写是设计（线上读 auxiliary 快照，见 data_live_sources.py），不补。
- daily_kbars 2021-05-26~12-31 的 750 行（000001~000007）与 2022–2024-10 的 920xxx 12.6 万行 **不是垃圾**：与 Tushare 逐日核对一致（000001/000006 2021-05-26~06-01 开高低收量额全等），920xxx 是北交所换码后的同一公司历史，库里无旧码行 → 保留，不清理。

## 3. 运维清理（19:00 CST 前后）
- 冗余索引：`scripts/p2_drop_redundant_indexes.py --apply` 删除 14 个（daily_kbars 3、minute_kbars 2、capital_flow 1、daily_basic 1、margin_data 2、auction_data 2、limit_up_board 1、stock_quotes 2）；
  stk_limits 2 个（ix_stk_limits_code、idx_sl_code_td）因回填事务持元数据锁超时跳过，待回填结束后重跑脚本。
  过程中发现并清除一条已死客户端遗留 6.9 小时的 SELECT（daily_kbars 自连接，持元数据锁，会让 ALTER 堵住整表）：`KILL QUERY 6289`，只读查询，无数据影响。
- prefect.db：flow_run 仅保留自 2026-09-24（<30 天，无可删）；事件表已按 7 天自动修剪；真正的占用是空闲页 41%，
  停 worker/server（无 RUNNING/到期任务）后 `VACUUM`：825MB → 463MB，随后 server、worker 重新拉起。
- launchd：`ashare.lt12`、`ashare.ltbackfill{,2,3,4}` 5 个已完成的研究回补 plist 移至 `~/Library/LaunchAgents.disabled/`；
  新增 `com.wangao.ashare-log-rotate`（每周日 03:30 copy-truncate 轮转）已加载。
- Git：删除 41 个已合并分支、15 个 prunable worktree 记录及 wt-st-hist；保留他人 worktree 检出的分支与 `codex/*` 未合并分支。
- 备份表 `recommendation_outcomes_bak_20261019`（590 行）导出 csv.gz 至 backend/data/runtime/cleanup/ 后 DROP。
- MySQL：开启慢查询日志（>1s，my.cnf 已固化）；buffer pool 维持 2G（运行时=配置，修正原 my.cnf 的 512M 漂移），
  因 Mac 交换区已用 7.8/8GB，未提到 4G。

## 4. stk_limits 历史回填（18:40–19:20 CST）
- promax 通道限流/熔断严重（约 5 天/13 分钟）；改用 quantdata（0.5s/次，`--provider quantdata`，日额度预算 40%=368/920，用满自动停）。
- 结果：2022 年补齐 242 个交易日；2023 年补到 165 个交易日（至 2023-08-30），剩余 85 个交易日由 promax 慢速链路（/tmp/stk_q.sh 第三步）继续；
  2018–2021 共 973 个交易日未动（上游 2019-07-03 起有数据；需约 3 天的 quantdata 预算，建议次日 00:00 后每日 40% 预算分批，非必需）。
- `stk_limits.parquet` 已 `--full` 原子重导：6,830,690 行，2022-01-04 ~ 2026-10-23（峰值内存 3.9GB）；回填完成后需再 `--only stk_limits --full` 一次。
- 同时 `st_hist.parquet` 权限 600→644，供 owner 研究进程读取。
