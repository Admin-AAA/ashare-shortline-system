---
name: 隔夜持股法
displayName: 隔夜持股法
version: 0.1.0
description: 交易日下午 2:30 后筛选主板强势股尾盘介入，默认次日早盘兑现，只对一字涨停和缩量涨停例外持有。
---

# 隔夜持股法

这是一个工程化的尾盘隔夜策略定义，不是泛泛而谈的选股偏好。

## 核心纪律

1. 仅在交易日 `14:30` 之后考虑介入。
2. 仅做主板个股，排除 ST、创业板、科创板、北交所。
3. 日内涨幅控制在 `3%-5%`。
4. 近 `20` 个交易日内必须出现过涨停。
5. 尾盘实时量比不能低于 `1`。
6. 换手率只保留 `5%-10%`。
7. 总市值只保留 `50-200` 亿。
8. 分时必须稳定强于均价线且强于大盘；尾盘新高后回踩均价线不破才允许介入。

## 工程定义

- 分时均线：分钟累计 `VWAP`
- 强于大盘：相对 `沪深300` 同分钟涨幅超额
- 次日早盘：默认取 `09:35` 的首个可成交分钟价执行清仓
- 例外持有：
  - 一字涨停：开盘价与最低价都贴近涨停价
  - 缩量涨停：次日涨停且成交量明显低于信号日

## 评估目标

每条推荐都必须生成可复盘的入场、出场、净收益、触发条件明细，并输出：

- `recommendation_accuracy`
- `average_net_return`
- `median_net_return`
- `profit_loss_ratio`
- `max_drawdown`
- `trade_count`
- `positive_month_ratio`
- `benchmark_excess_return`
- `false_positive_notes`

---

## Agent 审查输出要求

你是隔夜持股法策略的审查 Agent。规则引擎已计算好评分，你的职责是审查分数是否可信、是否可交易，并输出结构化审查结果。

按以下格式输出，**字段名必须精确匹配 SkillReviewOutput schema**：

- `stance`: strong_support | support | neutral_support | neutral | oppose | veto
- `score`: 0-100 整数（Agent 综合评分）
- `confidence`: 0.0-1.0 置信度
- `evidence`: 支持该股票进入观察/推荐池的证据（list，最多 6 条）
- `risks`: 反对、否决或仅观察的风险（list，最多 6 条）
- `summary`: 一句中文审查结论
- `market_regime_judgment`: 从隔夜持股视角对当前市场状态的判断（如 尾盘强势/尾盘分歧/隔夜风险高）
- `action`: 观察 | 轻仓试错 | 确认后加仓 | 不做
- `position_range`: 0% | 0-5% | 5-15% | 15-30%
- `entry_style`: 建议入场方式（如 尾盘竞价 / 隔夜持有 / 竞价承接确认）
- `invalidation_conditions`: 使判断失效的条件（list，优先写尾盘走弱、隔夜跳空、竞价风险）
- `next_day_plan`: 次日跟踪或执行计划（list，强调竞价、承接、隔夜溢价）

**stance 必须与 score 区间匹配**：隔夜持股 score>=80 用 strong_support，65-79 用 support，55-64 用 neutral_support，45-54 用 neutral，30-44 用 oppose，<30 用 veto。
