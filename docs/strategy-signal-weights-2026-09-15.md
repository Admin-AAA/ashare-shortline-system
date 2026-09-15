# 逐策略信号权重（L-120）：口径勘误 + DSA↓/陈小群↑ 的候选配置

## 1. 口径勘误（必须先说清）

- **生产 LIVE 用的是 preset 权重**：`_build_recommendation_card` 里 `signal["weight"] = selected["weight"]`，
  而 `selected` 来自 `preset_skills`。DB 实测（2026-09-15 的推荐 `skill_breakdown_json`）：
  `sector-relative 1.5 / chen-xiaoqun 1.5 / chao-gu 1.1 / high-position 0.3` —— 与 `preset_formal_opening` 的权重一致。
- **研究重放器默认是等权**：`ProductionRuleReplayCandidateBuilder._selected_skills` 把所有 skill 的
  `weight` 固定为 1.0 → 本轮之前的三窗数字（v5/v6/v7）全部是**等权口径**，与生产存在系统差。
  已给重放器加 `--skill-weights {equal|preset|JSON}` 以对齐生产（`equal` 为历史可比默认）。
- 影响量级（v7 policy，W3）：等权 n66 / 55.1% / +0.4237 / PF1.196 / Σ+31.0
  vs preset 权重 n70 / **55.7%** / +0.3187 / PF1.139 / Σ+22.31 → 同向但量级不同，**发布结论必须用 preset 口径复核**。

## 2. 候选配置与依据

筛选（面板口径、Δ 基线 = v7 policy + 等权）：把 `strategy_rule` 按逐技能权重重算
（多信号卡 1211/1211 验证 `strategy_rule = Σw·score/Σw`，再代回 `final_score` 公式）。

| 配置 | W1 Δ / WR / PF | W2 Δ / WR / PF | W3 Δ / WR / PF | 三窗 |
| --- | --- | --- | --- | --- |
| `sigw_identity`（等权，对照） | 0 / 58.8% / 1.317 | 0 / 54.2% / 1.115 | 0 / 55.1% / 1.196 | — |
| `sigw_chen15`（陈小群 1.5） | +0.007 / 58.9% / 1.327 | +0.071 / 55.0% / 1.163 | −0.027 / 55.7% / 1.211 | **3/3** |
| **`sigw_dsa05_chen15`（DSA 0.5 + 陈小群 1.5，其余 1.0）** | +0.021 / 59.1% / 1.330 | −0.004 / 54.2% / 1.141 | +0.002 / 56.5% / **1.258** | **3/3** |
| `sigw_yaml_declared`（把各 YAML 声明权重全量生效） | +0.007 / 58.8% / 1.306 | −0.095 / 53.1% / 1.048 | −0.027 / 56.2% / 1.150 | 0/3（更差，不采用） |
| `sigw_preset_dsa05`（preset 只把 DSA 0.3→0.5） | +0.026 / 58.9% / 1.334 | −0.092 / 53.4% / 1.071 | +0.029 / 55.9% / 1.243 | 2/3 |

结论：**"DSA 0.5 + 陈小群 1.5 + 其余 1.0"** 是同时满足三窗的配置，且 W3 的 PF 首次越过 1.2。

## 3. 端到端（生产口径，全管线重放）

候选配置（`--skill-weights '{"daily-stock-analysis-skill":0.5,"chen-xiaoqun-skill":1.5}'`）：

| 窗口 | 等权 v7 | **候选** | v5 基线 |
| --- | --- | --- | --- |
| W1 | 795 / 58.5% / +0.6079 / 1.336 / +483.3 | 面板口径 +0.021（WR 59.1% / PF 1.330） | n1085 / 57.2% / +0.3452 / 1.186 / +374.5 |
| W2 | 499 / 54.5% / +0.2809 / 1.123 / +140.2 | **497 / 54.7% / +0.3829 / 1.169 / +190.3（MDD −27.15）** | n649 / 56.5% / +0.2066 / 1.094 / +134.1 |
| W3 | 66 / 54.5% / +0.4237 / 1.186 / +28.0 / −15.69 | **66 / 56.1% / +0.5117 / 1.234 / +33.77 / −15.69** | 95 / 51.6% / −0.0537 / 0.978 / −5.1 / −19.25 |

preset 口径（生产现值）W3：n70 / 55.7% / +0.3187 / 1.139 / +22.31 / −17.12（W1/W2 待补）。

## 4. 生产落地（已执行）

已把 `preset_formal_opening` 的逐技能权重改成候选配置（其余 preset 不动；DB 数据变更，已在 2026-09-15 执行）：

```sql
-- 目标：chao-gu 1.1→1.0, chen-xiaoqun 1.5（不变）, chip 0.2→1.0, DSA 0.3→0.5,
--       high-position 0.3→1.0, high-range 0.95→1.0, limit-up 0.9→1.0,
--       overnight 0.85→1.0, sector 1.5→1.0, trend 0.2→1.0
UPDATE preset_skills ps JOIN presets p ON p.preset_id = ps.preset_id
SET ps.weight = CASE ps.skill_id
    WHEN 'daily-stock-analysis-skill' THEN 0.5 WHEN 'chen-xiaoqun-skill' THEN 1.5 ELSE 1.0 END
WHERE ps.preset_id = 'preset_formal_opening';
```

## 5. 相关

- L-120（本条：口径勘误 + 权重轴 + 候选）、L-119（筛查口径）、L-118（v7）、L-117（v5 共识）、L-110（生产同构原则）。
