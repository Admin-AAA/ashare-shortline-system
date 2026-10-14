# 北向资金增持 Skill Agent

假设：北向十大成交股中净买入、趋势向上且当日未大涨的股票，短期顺北向方向。

来源：浙大资本市场研究中心：北向资金是聪明的（北向持股变化预测 2~20 日超额收益）http://www.som.zju.edu.cn/2021/1205/c63655a2448615/page.htm ；深交所：普通投资者追踪北上资金能获利吗？ https://docs.static.szse.cn/www/aboutus/research/secuities/daily/W020230718361543565332.pdf

默认 enabled=false。

## Agent 审查输出约束

按 SkillReviewOutput schema 输出：`stance`（support / neutral / oppose）与 `action`（buy / watch / avoid），并给出依据；不得输出 schema 以外的字段。
