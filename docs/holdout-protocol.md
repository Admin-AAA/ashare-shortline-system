# Hold-out 评估协议（2026-10-13 起生效）

## 切分
- **训练段**：2025-01-15 ~ 2026-06-30。所有调参、选阈值、选策略组合、网格搜索只能在这一段做。
- **Hold-out 段**：2026-07-01 ~ 最新已结算交易日（本次截至 2026-10-09）。**只评估一次**。
  一次评估之后，这段数据就算「用过了」；后续新提案要以 2026-10-10 之后新积累的前瞻数据作为下一段 hold-out。
- 两段之间不设禁区：持有期上限 5 天，训练段最后几天的出场 bar 会落在 hold-out 内，只影响训练段末尾收益，
  不会把 hold-out 信息带进参数选择。

## 流程
1. 用 `scripts/research_strategy_ablation_fast.py --window-range <段> --panel-dir <目录>` 分别导出训练段和 hold-out 段面板
   （研究原型策略用环境变量 `ASHARE_RESEARCH_EXTRA_SKILLS=<id,...>` 临时纳入回放，不改注册表）。
2. 在训练段上决定改动（`scripts/research_holdout_protocol.py`）。
3. 在 hold-out 段上跑**一次**，用 `evaluate_challenger` 判定。
4. **训练段与 hold-out 段都判 PRODUCTION 才可以发布。** 只在训练段过关的改动，记为 FALSIFIED 并写入 LESSONS。

## 禁止
- 看过 hold-out 结果后回头改参数再测（这就变成在 hold-out 上调参了）。
- 用全窗口（训练段 + hold-out 段）的结果做发布依据。

见 L-165、L-162。
