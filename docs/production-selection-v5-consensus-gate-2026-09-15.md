# production_selection v5 落地：弱势/震荡"共识闸门"（L-117）

## 1. 一句话

在**弱势 / 震荡**市场基态下，只保留"至少 2 个非否决策略背书"的候选（不足者剔除、由下一名按
`final_score` 自然递补）；强牛 / 偏多不动。三窗口全管线重放口径 Δcohort 全部为正，
三窗口均通过 `evaluate_challenger`。

## 2. 为什么

1. **单策略信号在弱/震荡里更像噪声**：L-110 的分策略消融已显示弱市里"以单策略为主"的选中卡
   W1 平均 −1.30%（n=111）、W3 −0.33%；而三窗被选中卡背书数 n_sig≥2 的组平均收益更高。
2. **强趋势里单策略动量有效**：全 regime 版（`consensus2`）在 W2 把 WR 从 56.2% 压到 55.9%、
   MDD 变差，说明强牛日的"单策略高分票"不该被削——故把规则限定在弱势/震荡。
3. **横截面质量闸门而非择时**：不空仓、不整日 abstain，剔一张补一张（与 L-093/L-109 的
   尾部否决同族形态）。

## 3. 证据（面板口径，`scripts/research_entry_rule_panel.py`）

产物：`backend/output/research/entry_rule_panel_consensus.json`；复现：

```bash
cd backend && .venv/bin/python scripts/research_entry_rule_panel.py \
  --rules consensus2,consensus2_weak,consensus2_weak_osc,consensus2_soft6,consensus2_soft6_weak,consensus2_hi70 \
  --out output/research/entry_rule_panel_consensus.json
```

| 规则 | W1 Δcohort / 闸门 | W2 | W3 |
| --- | --- | --- | --- |
| `consensus2`（全 regime，至少 2 背书） | +0.0953 PASS | +0.0965 PASS | +0.1547 PASS |
| `consensus2_weak`（仅弱势） | +0.0886 PASS | −0.0108 PASS | +0.1939 PASS |
| **`consensus2_weak_osc`（弱势/震荡，落地版本）** | **+0.1054 PASS** | **+0.0696 PASS** | **+0.1594 PASS** |
| `consensus2_soft6_weak`（弱/震荡软降 6 分） | +0.0698 PASS | +0.0537 PASS | −0.0049 PASS |
| `consensus2_hi70`（只认 ≥70 分背书） | +0.1241 FALSIFIED | −0.1286 PASS | −0.1759 FALSIFIED |

落地版本 `consensus2_weak_osc` 三窗绝对口径（面板近似）：

| 窗口 | n | WR | avg_net | sum_net | PF |
| --- | --- | --- | --- | --- | --- |
| W1 | 1080 | 58.5% | +0.4506 | +404.3 | 1.240 |
| W2 | 639 | 55.4% | +0.2410 | +155.4 | 1.118 |
| W3 | 89 | 51.6% | −0.2017 | −5.1 | 0.978 |

对照改动前基线（v4 默认，全管线重放）：W1 +0.3452/57.2%/1.186/+374.5 ·
W2 +0.1714/56.2%/1.077/+111.2 · W3 −0.3611/46.7%/0.857/−37.9。

## 4. 实现

- `backend/app/policies/production_selection.v5.json`：v4 全量 + `consensus_min_signals: 2` +
  `consensus_regimes: ["弱势","震荡"]`（v1-v4 artifact 逐位不变，保持 frozen 语义）。
- `backend/app/engine/production_policy.py`：`ProductionSelectionPolicy` 新增两个字段（默认 0/()，
  即关闭）；`consensus_regimes` 规范化为 tuple；默认版本 4 → 5。
- `backend/app/engine/aggregator.py`：`apply_pool_rules` 在 dedup 之后、尾部否决之前执行闸门，
  剔除时写 `policy_drop_owner="consensus_gate"` + `policy_verdict_steps`（与既有规则同形的归因）。
- `backend/tests/test_consensus_gate_policy.py`：5 项（弱势剔除并递补 / 震荡同样生效 / 强牛不动 /
  v1-v4 行为不变 / 硬否决卡不被改写 drop_owner）。

## 5. 三窗端到端验证（发布口径）

<!-- VALIDATION_TABLE -->

## 6. 本轮同时证伪的方向（不重做）

同一次筛查（`output/research/entry_rule_panel_rules.json`）里三窗全过者只有共识族，以下**全部 FALSIFIED**：

- 恐慌/广度闸门：`panic20`（2/3）、`panic40`、`panic60`、`upratio035`、`upratio045`
  ——"普跌日不进场"在 W2 一致变差；W3 的亏损日并不是可用 `limit_down_count/up_ratio` 识别的极端日。
- 情绪过热闸门：`euphoria120`、`euphoria150`（2/3）——W3 Δ≡0（该窗没有达到阈值的日子）。
- 追量闸门：`vol5_20_ge20` / `vol5_20_ge25`——三窗全部大幅变差（放量票本身是好票）。
- 非主线剔除/惩罚：`non_main_p4/p8/drop`——W1 全部为负；W3 虽好（`non_main_drop` W3 +0.386/WR61.2%），
  但 W1 −0.060 使其无法三窗通过（与 `off_main_theme_penalty_canonical.json` 的面板口径结论一致）。
- 共识加分（promotion）方向：`consensus_boost6/10`、`consensus_boost*_weak_osc`——W2/W3 全面变差。
  即该轴的正确用法是"削低共识"而不是"抬高共识"。

## 7. 相关

- L-117（本条）、L-116（MDD 符号口径，本轮自纠）、L-115（面板 vs 全管线口径）、L-110（生产同构消融）、
  L-010（唯一闸门）、L-058（逐月同号）。
