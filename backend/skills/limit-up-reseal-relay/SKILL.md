# 涨停回封接力

这是一个 A 股短线涨停接力策略，只关注已经触发涨停且盘中有换手机会的标的。它不追求“所有涨停都买”，而是把开板回封质量、封单承接、板块梯队和情绪热度作为核心过滤条件。

## 策略依据

- A 股涨跌停制度会造成价格发现延迟，涨停后的次日溢价与后续延续性常常取决于封板质量、成交充分度和市场情绪。
- 系统已接入本地涨停池字段，包括 `is_limit_up`、`limit_up_consecutive_days`、`limit_up_open_times`、`limit_up_fd_amount_yi`、`limit_up_turnover_ratio`，可直接规则化回封接力。
- 为避免不可成交的一字板和情绪末端，策略要求开板次数适中、换手不失控、封单不太弱，并过滤涨停情绪过热和 20cm 阻断。

## 入场条件

1. 股票未 ST，无负面公告，成交额不低于 3 亿元，换手率在 3%-12%。
2. 当日涨停，且近 20 日至少有 1 次涨停记录。
3. 优先首板或二板回封，板块梯队分不低于 50，资金共振分不低于 50。
4. 封单金额不低于 0.15 亿元，开板次数 1-8 次，代表有分歧但未明显失控。
5. 若属于主线板块、封单较强、龙虎榜或主力资金配合，则排序优先。

## 出场条件

1. 次日竞价或早盘承接弱，不能高开或快速修复分时均线。
2. 炸板后无法回封，或回封后反复开板且封单快速衰减。
3. 板块梯队断层、高标负反馈扩散，退出接力观察。

## 风控条件

1. 连板数过高、涨停情绪过热、20cm 风险闸门阻断时不参与。
2. 一字板无换手、开板次数过多、封单过弱时降权或过滤。
3. 高换手但资金共振不足，视为分歧过大。
4. 有新闻或公告风险时排除。

## 仓位/排序规则

- 只做小样本高强度观察，按最终分数排序输出。
- 首板/二板、主线、梯队完整、封单强、资金共振者优先。
- 三板以上或情绪高潮只保留观察，不做默认追高。

## 所需数据字段

`amount_yi`、`turnover_rate`、`pct`、`is_limit_up`、`limit_up_days20`、`limit_up_consecutive_days`、`limit_up_open_times`、`limit_up_fd_amount_yi`、`limit_up_turnover_ratio`、`limit_up_overheat_score`、`sector_ladder_completeness_score`、`sector_ladder_height`、`capital_resonance_score`、`twenty_cm_risk_gate`、`announcement_negative_count`、`news_risk_count`、`is_st`。

## 不适用场景

- 普跌、跌停扩散、连板亏钱效应明显的退潮阶段。
- 连续一字板、封单虚弱、反复炸板回封失败。
- 极端情绪高潮或 20cm 高波动标的。
- 本地涨停池缺失导致封单和开板字段不可用的样本。

本 Skill 仅用于研究和复盘，不构成投资建议。

---

## Agent 审查输出要求

你是涨停回封接力策略的审查 Agent。规则引擎已计算好评分，你的职责是审查分数是否可信、是否可交易，并输出结构化审查结果。

按以下格式输出，**字段名必须精确匹配 SkillReviewOutput schema**：

- `stance`: strong_support | support | neutral_support | neutral | oppose | veto
- `score`: 0-100 整数（Agent 综合评分）
- `confidence`: 0.0-1.0 置信度
- `evidence`: 支持该股票进入观察/推荐池的证据（list，最多 6 条）
- `risks`: 反对、否决或仅观察的风险（list，最多 6 条）
- `summary`: 一句中文审查结论
- `market_regime_judgment`: 从涨停接力视角对当前市场状态的判断（如 情绪退潮/分歧转一致/主升加速）
- `action`: 观察 | 轻仓试错 | 确认后加仓 | 不做
- `position_range`: 0% | 0-5% | 5-15% | 15-30%
- `entry_style`: 建议入场方式（如 回封确认 / 分歧低吸 / 竞价跟随）
- `invalidation_conditions`: 使判断失效的条件（list，优先写市场、主线、承接、量能风险）
- `next_day_plan`: 次日跟踪或执行计划（list，强调竞价、承接、量能、主线反馈）

**stance 必须与 score 区间匹配**：涨停接力 score>=80 用 strong_support，65-79 用 support，55-64 用 neutral_support，45-54 用 neutral，30-44 用 oppose，<30 用 veto。
