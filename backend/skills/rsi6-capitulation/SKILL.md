# RSI6 恐慌出清（命令式，观察身份）

## 这是什么

一个**低频、市场级情绪**短线策略：只在「全市场急跌 + 跌停家数明显多于涨停家数」的日子出手，买入当日成交额最大的 10 只 A 股，+5% 止盈、-4.5% 止损、持 5 个交易日。

它回答的是「市场特别恐慌时该不该买」：不是所有下跌都叫恐慌，**要有跌停潮/跌停家数占优**才是真正的出清；涨停家数占优时反而是情绪高点，不该追。

## 信号（T 收盘可得）

- 等权全市场指数 RSI(6) < 20；
- `lu_ld = 涨停家数 / max(跌停家数, 1) <= 0.34`；
- 个股：非 ST、非涨停收盘、当日成交额 >= 1 亿；按成交额从大到小取前 10。

## 出场

- +5% 止盈、-4.5% 止损、关闭移动止损、持 5 个交易日（strategy.yaml `exit_overrides`）；
- T+1 开盘买，一字涨停买不进，跌停锁死顺延。

## 研究证据

- 预注册：`docs/research/rsi_exit_s1051_capitulation_filter.md`
- 结果：R12 40 笔 **+1.77%/笔 PF1.90**；N3 126 笔 **+1.21%/笔 PF1.55**；4 窗口 4/4、8 段 6/8；稳健型，回撤 −3.8%/−3.9%。
- 低频：R12 约 4 个信号日、N3 约 13 个；不设仓位上限，单日最多 10 只。

## Agent 审查输出约束（SkillReviewOutput）

**必须输出合法 JSON 对象**。按以下 schema 输出，字段与其它 B 类策略一致，
否则审查链路无法解析 stance / action：

```json
{
  "stance": "neutral_support",
  "score": 62,
  "confidence": 0.6,
  "evidence": ["等权全市场 RSI6=17 < 20", "lu_ld=0.21 跌停家数明显占优"],
  "risks": ["熊市段样本薄", "低频策略单日最多 10 只"],
  "summary": "恐慌出清条件成立，但仅观察身份运行",
  "market_regime_judgment": "恐慌出清",
  "action": "观察",
  "position_range": "0%（观察）",
  "entry_style": "T+1 开盘（仅回放，不发实盘指令）",
  "invalidation_conditions": ["RSI6 回升 >= 20", "涨停家数转为占优"],
  "next_day_plan": ["核对跌停家数口径", "复核 live_forward 与回放一致性"]
}
```

### stance → score 强制映射（违反即无效）

| stance | score 区间 |
|--------|-----------|
| `strong_support` | >= 80 |
| `support` | 65-79 |
| `neutral_support` | 55-64 |
| `neutral` | 45-54 |
| `oppose` | 30-44 |
| `veto` | < 30 |

### stance → action 强制映射
- `neutral` / `oppose` / `veto` → action 必须是 "观察" 或 "不做"
- `support` / `neutral_support` / `strong_support` → 本策略为**观察身份**，
  action 一律用 "观察"，不得输出「轻仓试错 / 确认后加仓」等实盘指令

### evidence vs risks 约束
- `evidence` = 看多理由，`risks` = 看空风险；禁止把风险写进 evidence
- 禁止引用未提供的字段、禁止给模糊建议

---

## 风险

- 熊市段（W0/W00）虽然过滤后为正，但样本薄（W00 仅 1 个信号日/10 笔）；
- 严格按 +5% 止盈/−4.5% 止损执行；实盘若用默认 +6% 止盈/移动止损，回测结论不成立；
- 观察身份运行，先看 live_forward 与回放一致性。
