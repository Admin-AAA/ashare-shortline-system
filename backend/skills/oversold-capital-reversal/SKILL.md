# 超跌资金反包 Skill Agent

研究原型，规则见 strategy.yaml 注释。只评估短线 1-3 天机会，不做估值判断。默认 enabled=false，需按 hold-out 协议（2026-07-01 之后为样本外）通过后才可加入正式预设。

## Agent 审查输出约束

按 SkillReviewOutput schema 输出：`stance`（support / neutral / oppose）与 `action`（buy / watch / avoid），并给出依据；不得输出 schema 以外的字段。
