# 主线相对强度龙头

这是一个板块轮动与相对强度策略，先看行业/概念是否成为主线，再在主线里找相对强、容量够、资金配合的龙头候选。

## 策略依据

- 行业动量研究表明，行业层面的收益延续可以解释一部分个股动量，短线系统可以先筛主线再筛个股。
- A 股短线里，主线持续性通常体现为板块梯队、容量、涨停扩散、资金共振和个股超额收益，而不是孤立个股上涨。
- 当前系统已提供 `is_main_theme`、`sector_ladder_completeness_score`、`industry_capacity_like_count`、`primary_excess20`、`industry_positive_excess20_count`、`capital_resonance_score` 等共享因子，可以直接落地。

## 入场条件

1. 股票未 ST，无负面公告，成交额不低于 3 亿元，流通市值不低于 20 亿元。
2. 必须属于当前主线或板块梯队分达到 50 以上。
3. 个股 20 日超额收益为正，且行业内有多只股票同步强于市场。
4. 价格位置不能破坏趋势，回撤和上影线压力不能过大。
5. 资金共振、板块梯队完整、突破或接近高点者排序优先。
6. 若仅满足主线与相对强度，但突破、支撑或资金条件不足，只标记为观察，不提高评分。

## 出场条件

1. 主线退潮，板块梯队断层或龙头负反馈扩散。
2. 个股相对强度转弱，跌回平台且不再领涨板块。
3. 资金共振转背离或主力净流出扩大。

## 风控条件

1. 非主线孤立上涨直接降权或排除。
2. 20 日回撤过大、跌离高点过远、上影线偏多，视为强度失效。
3. 涨停情绪过热、20cm 风险闸门阻断时过滤或降权。
4. 有新闻或公告风险时排除。

## 仓位/排序规则

- 按“主线确认 > 板块梯队 > 个股超额收益 > 资金共振 > 位置健康”排序。
- 主线龙头和容量票优先；相对强度尚可但买点不完整者只观察，不因标签本身加分。
- 市场弱势时降低仓位；板块梯队不完整时不做追涨。

## 所需数据字段

`amount_yi`、`free_mv_yi`、`turnover_rate`、`pct`、`chg20`、`pos60`、`pos120`、`off_high_pct`、`breakout20`、`primary_excess20`、`primary_excess60`、`industry_capacity_like_count`、`industry_positive_excess20_count`、`is_main_theme`、`sector_ladder_completeness_score`、`sector_ladder_height`、`capital_resonance_score`、`shortline_leader_score`、`max_dd20`、`upper_shadow_count20`。

## 不适用场景

- 无主线轮动、题材快速一日游、板块梯队断层。
- 大盘退潮，强势股补跌扩散。
- 行业字段或概念板块覆盖不足，导致主线识别不可靠。
- 非主线庄股、孤票和流动性不足标的。

本 Skill 仅用于研究和复盘，不构成投资建议。
