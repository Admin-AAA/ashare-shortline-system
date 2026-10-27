# 每策略每日新推荐上限（2026-10-26）

用户决策：每个策略每天最多推荐 5 只，历史数据不动。

- 配置：`settings.max_daily_picks_per_strategy`（`backend/app/core/config.py`，默认 5，环境变量 `ASHARE_MAX_DAILY_PICKS_PER_STRATEGY`，0 = 不限制）。
- 实现：`backend/app/services/daily_pick_cap.py`。只切片、不重排；取前 N 的依据永远是策略自身已有排序。
- 同分顺延（用户 2026-10-27）：上限按「前 N 个不同分数」计。同分的票一起保留，全部同分时可超过 N 只；分数各不相同时就是前 N 只。
  分数字段：短线 `portfolio_rank_score`（缺则 selection_score / final_score）；中长线 picks 的 `score`。没有分数的票每只各算一档（等同取前 N 只）。
  审计里 `cap_unit="score_levels"`。
- 短线（`ProductionRecommendationAssembly.aggregate`，政策之后、冷却之前）：仅 run 行存在、run_mode=formal、非历史重建的实盘 run 才生效；
  回测回放（`backtest-rule-replay-*` 无 run 行）、单股分析不受影响。排序 = 政策输出顺序（portfolio_rank_score 降序）。
  单策略 run 把同日同策略已落库推荐数计入（同日多次 run 合计 <= 上限）。
  审计：被截断的卡 `policy_drop_owner="daily_strategy_cap"` + `policy_verdict_steps`，run 的 `pipeline_audit.daily_strategy_cap`
  含 cap / 截断数 / 截断代码；filter_audit 逐票原因为 `production_policy: daily_strategy_cap`。
- 中长线（`midterm_service._save_picks`）：`generate_picks(..., apply_daily_cap=True)`、`generate_block_trade_picks(..., apply_daily_cap=True)`
  仅由 17:35 定时任务、晚间大宗任务、手动补跑 API 传 True；默认 False，`scripts/rebuild_replay_picks.py`（历史重建）与测试不受影响。
  先剔除已持有再取前 N；排序即各策略 candidates 函数返回顺序（小市值/低价小市值=市值升序；小盘高股息=股息率降序；
  小盘低市销率=PS 升序；小盘反转=20 日跌幅最大优先；回购预案/大宗=金额占比降序；业绩加速/预告/fina 事件=score 降序；
  股权激励=公告前 20 日涨幅升序；低换手低波/红利低波=综合排名分升序）。
  审计：picks 文件 `daily_cap` 字段（cap / 候选数 / 截断数 / 截断代码）。
- 与持仓名额无关：中长线 20 仓位（回测口径 k_slots=20）、持有 20/60 日不受影响；这里限制的只是「当天新推荐数」。
