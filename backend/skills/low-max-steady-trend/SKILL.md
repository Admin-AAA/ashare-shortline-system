# 低MAX稳健趋势 Skill Agent

假设：避开彩票型（近 20 日有涨停/长上影）股票，选均线多头、温和放量、回撤小的稳健趋势股。

来源：Bali, Cakici & Whitelaw (2011) Maxing out, JFE https://www.nber.org/papers/w14804 ；Loss from the chasing of MAX stocks: Evidence from China https://www.sciencedirect.com/science/article/abs/pii/S1062940821000966

默认 enabled=false。

## Agent 审查输出约束

按 SkillReviewOutput schema 输出：`stance`（support / neutral / oppose）与 `action`（buy / watch / avoid），并给出依据；不得输出 schema 以外的字段。
