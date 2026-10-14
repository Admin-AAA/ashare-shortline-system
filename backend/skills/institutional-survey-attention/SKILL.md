# 机构调研关注 Skill Agent

假设：近期有较多基金参与调研、趋势向上、当日温和上涨的股票，机构关注度提升带来短期资金流入。

来源：Cheng, Du, Wang & Wang (2019) Do corporate site visits impact stock prices? Contemporary Accounting Research；Barber & Odean (2008) All that glitters, Review of Financial Studies

默认 enabled=false。

## Agent 审查输出约束

按 SkillReviewOutput schema 输出：`stance`（support / neutral / oppose）与 `action`（buy / watch / avoid），并给出依据；不得输出 schema 以外的字段。
