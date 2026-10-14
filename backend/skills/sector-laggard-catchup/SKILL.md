# 主线板块补涨 Skill Agent

研究原型，规则见 strategy.yaml 注释。默认 enabled=false。

## Agent 审查输出约束

按 SkillReviewOutput schema 输出：`stance`（support / neutral / oppose）与 `action`（buy / watch / avoid），并给出依据；不得输出 schema 以外的字段。
