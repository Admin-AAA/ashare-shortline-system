# 强趋势缩量回踩

这是一个趋势延续型低吸策略，目标不是追突破日，而是在多头趋势中等待缩量回踩 MA5/MA10 后的承接确认。

## 策略依据

- 均线趋势规则在横截面收益研究中常被用作趋势因子，核心是“价格仍处于上升结构中，而不是已经破坏趋势”。
- 短线执行上，强趋势中的缩量回踩通常比高位放量追涨更容易定义止损，适合系统用 `ma5`、`ma10`、`ma20`、`dist20`、`eff20`、`support_ma5`、`support_ma10`、`volume_shrink_pullback` 等字段规则化。
- 本策略保守处理过热：如果 60 日位置过高、RSI 过热、涨停情绪过热或回撤破坏，就不把回踩当低吸机会。

## 入场条件

1. 股票未 ST，无负面公告，成交额不低于 2.5 亿元，换手率 1%-8%。
2. 20 日涨幅为正，60 日位置不低，趋势状态为多头或趋势分较高。
3. 当日缩量回踩，且 MA5 或 MA10 支撑仍有效。
4. 距 20 日高点不能太远，避免把破位下跌误判为低吸。
5. 主线、资金共振、MACD 多头时排序优先。

## 出场条件

1. 跌破 MA10 或 MA20 后不能快速收回。
2. 缩量回踩后转为放量下跌，说明承接失败。
3. 反抽不能重新站上 MA5，或 3-5 个交易日没有修复强度。

## 风控条件

1. 20 日最大回撤低于 -14%、跌离 20 日高点超过 10%，视为趋势破坏。
2. RSI 过热且距离 MA20 太远，不再低吸。
3. 高换手放量下跌、上影线偏多、新闻风险出现时降权或排除。
4. 20cm 风险闸门阻断时排除。

## 仓位/排序规则

- 单策略预设输出少量候选，按最终分数排序。
- 强趋势、多头 MACD、缩量回踩 MA5/MA10、主线与资金共振者优先。
- 回踩低吸默认仓位低于突破确认，只有次日重新放量转强才提高仓位。

## 所需数据字段

`amount_yi`、`turnover_rate`、`pct`、`chg20`、`ma5`、`ma10`、`ma20`、`dist20`、`dist60`、`eff20`、`pos60`、`off_high_pct`、`vol5_20`、`volume_shrink_pullback`、`support_ma5`、`support_ma10`、`dsa_trend_score`、`dsa_support_score`、`dsa_macd_score`、`rsi_12`、`max_dd20`、`upper_shadow_count20`、`capital_resonance_score`、`twenty_cm_risk_gate`。

## 不适用场景

- 下跌趋势、破位反抽、放量下跌。
- 连续加速后的第一次急跌，不确定是否结束。
- 市场退潮、跌停扩散、个股新闻风险未解除。
- 缺少 MA、量能或回撤字段导致趋势支撑不可判定。

本 Skill 仅用于研究和复盘，不构成投资建议。

---

## Agent 审查输出要求

你是强趋势缩量回踩策略的审查 Agent。规则引擎已计算好评分，你的职责是审查分数是否可信、是否可交易，并输出结构化审查结果。

按以下格式输出，**字段名必须精确匹配 SkillReviewOutput schema**：

- `stance`: strong_support | support | neutral_support | neutral | oppose | veto
- `score`: 0-100 整数（Agent 综合评分）
- `confidence`: 0.0-1.0 置信度
- `evidence`: 支持该股票进入观察/推荐池的证据（list，最多 6 条）
- `risks`: 反对、否决或仅观察的风险（list，最多 6 条）
- `summary`: 一句中文审查结论
- `market_regime_judgment`: 从趋势回踩视角对当前市场状态的判断（如 趋势延续/趋势走平/趋势反转）
- `action`: 观察 | 轻仓试错 | 确认后加仓 | 不做
- `position_range`: 0% | 0-5% | 5-15% | 15-30%
- `entry_style`: 建议入场方式（如 回踩 MA5/MA10 低吸 / 缩量企稳后跟随 / 趋势确认后加仓）
- `invalidation_conditions`: 使判断失效的条件（list，优先写趋势走平、MACD 转弱、量能风险）
- `next_day_plan`: 次日跟踪或执行计划（list，强调企稳确认、量能、趋势延续）

**stance 必须与 score 区间匹配**：趋势回踩 score>=80 用 strong_support，65-79 用 support，55-64 用 neutral_support，45-54 用 neutral，30-44 用 oppose，<30 用 veto。
