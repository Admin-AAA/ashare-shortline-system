# 每策略每日新推荐上限（2026-10-26，回放口径 2026-11-03 更新）

用户决策：每个策略每天最多推荐 5 只。2026-11-03 起，中线/长线的历史回放重建与实盘使用同一套上限。已经写到磁盘上的旧 replay 文件不会自动变少。

- 配置：`settings.max_daily_picks_per_strategy`（`backend/app/core/config.py`，默认 5，环境变量 `ASHARE_MAX_DAILY_PICKS_PER_STRATEGY`，0 = 不限制）。
- 实现：`backend/app/services/daily_pick_cap.py`。只切片、不重排；取前 N 的依据永远是策略自身已有排序。
- 同分顺延（用户 2026-10-27）：先取前 N 只，再带上与第 N 名同分的票，更低分的不要。
  例：100,100,90,90,90,80 → 5 只；10 只全满分 → 10 只；分数各不相同 → 前 N 只。同日之前的 run 已满额时不再顺延。
  分数字段：短线 `portfolio_rank_score`（缺则 selection_score / final_score）；中长线 picks 的 `score`。没有分数的票每只各算一档（等同取前 N 只）。
  审计里 `cap_unit="top_n_with_ties"`。
- 短线（`ProductionRecommendationAssembly.aggregate`，政策之后、冷却之前）：仅 run 行存在、run_mode=formal、非历史重建的实盘 run 才生效；
  回测回放（`backtest-rule-replay-*` 无 run 行）、单股分析不受影响。排序 = 政策输出顺序（portfolio_rank_score 降序）。
  单策略 run 把同日同策略已落库推荐数计入（同日多次 run 合计 <= 上限）。
  审计：被截断的卡 `policy_drop_owner="daily_strategy_cap"` + `policy_verdict_steps`，run 的 `pipeline_audit.daily_strategy_cap`
  含 cap / 截断数 / 截断代码；filter_audit 逐票原因为 `production_policy: daily_strategy_cap`。
  短线历史重建（`scripts/replay_all_days.py`）仍然不套这个上限。
- 中长线（`midterm_service._save_picks`）：`generate_picks(..., apply_daily_cap=True)`、`generate_block_trade_picks(..., apply_daily_cap=True)`。
  实盘由 17:35 定时任务、晚间大宗任务、手动补跑 API 传 True。
  历史回放/重建由 `scripts/rebuild_replay_picks.py` 的 `replay_midlong` 默认传 True（`--execute`、`--dry-run`、`--onboard`、滚动 R12 都走这条）。中线和长线共用这一路径。
  `generate_picks` 本身的默认值仍是 False，避免不带开关的直接调用和旧单测被截断。
  回放落盘包在 `replay_pick_writes()` 里：历史日已经过 D+1 09:15，仍允许覆盖 staging 文件。生产实盘批次过了截止时间照旧不覆盖。
  先剔除已持有再取前 N；排序即各策略 candidates 函数返回顺序（小市值/低价小市值=市值升序；小盘高股息=股息率降序；
  小盘低市销率=PS 升序；小盘反转=20 日跌幅最大优先；回购预案/大宗=金额占比降序；业绩加速=EA_combo score 降序（当日候选内信号日涨跌幅百分位+小市值百分位，平手按代码）；预告/fina 事件=score 降序；
  股权激励=公告前 20 日涨幅升序；低换手低波/红利低波=综合排名分升序）。
  审计：picks 文件 `daily_cap` 字段（cap / 候选数 / 截断数 / 截断代码 / `cap_unit=top_n_with_ties`）。没超过上限的批次没有这个字段。
- 与持仓名额无关：中长线 20 仓位（回测口径 k_slots=20）、持有 20/60 日不受影响；这里限制的只是「当天新推荐数」。

## 已落盘的旧 replay 不会自动变少

代码改完之后，生产目录里已经写好的 `origin=replay` 批次只数不变。复盘列表里某一天 43 只，要变成加帽后的结果，必须在生产机上对历史窗口重跑 rebuild，用新文件换掉旧文件。本次改动不执行这步。

在生产机 `backend` 目录执行，避开 16:35–18:35（17:35 日更窗口）：

```bash
cd backend
# 先看计划。dry-run 只写临时目录，不删生产 picks。
.venv/bin/python scripts/rebuild_replay_picks.py --dry-run --skip-short --midlong-range 2025-10-20:<最新交易日>

# 确认后再换盘。--keep-live-forward 保留没有 origin=replay 的实盘批次；--skip-short 不动短线表。
.venv/bin/python scripts/rebuild_replay_picks.py --execute --confirm DELETE-AND-REBUILD \
  --keep-live-forward --skip-short --midlong-range 2025-10-20:<最新交易日>
```

`--execute` 会先删除所选策略的全部 `origin=replay` 文件（不限于 `--midlong-range` 里的日期），再只把本次 staging 覆盖到的交易日拷回去。因此区间必须盖住想保留的全部回放交易日，否则区间外的旧 replay 会被删掉且不会写回。不传 `--strategies` 时，中线和长线一起换。

`--onboard <strategy_id> --execute` 只在生产目录缺同名文件时复制，不会覆盖已经落盘的无帽批次。改旧文件必须走上面的 `--execute`，不能只靠 onboard。

换盘完成后，被截断的批次 JSON 里应有 `daily_cap.truncated_count`。策略画像条的「回放已平仓·近12个月」读的是结算统计，不是批次只数；只数变化要等换盘之后才会出现在复盘列表里。

## 画像条「回放已平仓」窗口

`GET /api/horizons/{mid|long}/metrics` 的 `portrait_realized_12m`：信号日 >= 请求当天往前 365 个自然日（含当天）。与 `rolling_r12` 的 `window_start = cutoff - 365 天` 同一长度，按信号日，不按平仓日。诊断页自己的 `window_days` 不改变这一块。研究回测 `quality.R12` 是上线快照里的固定阶段，数字不必相等。前端只展示后端返回的起止日。

- `by_strategy`：窗口内全部来源的已平仓。真实前瞻已平仓为 0 时，画像条「回放已平仓·近12个月」用这份。
- `by_strategy_live`：同一窗口里只要 `origin=live` 的已平仓。窗口内已有实盘平仓时，画像条改显示「前瞻已结算·近12个月」并改读这份，不把回放算进去。
- 短线画像的「前瞻已结算」仍走 `/api/metrics/by-strategy` 的 live_forward，不读这个块。
