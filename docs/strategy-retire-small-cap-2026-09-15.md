# 策略退役 2026-09-15（第 2 批）：small-cap-thematic-momentum

用户授权："如果策略很垃圾，没法继续优化了，你有权限删除"。

## 1. 依据（三层口径全部为负，且移除无代价）

| 视角（口径） | W1 | W2 | W3（最近） |
| --- | --- | --- | --- |
| 单策略组合（该 skill 的卡 + 真实 policy/出场） | n68 −0.03% PF0.98 | n21 −0.78% PF0.68 | n7 −0.56% PF0.80 |
| 它主导的选中卡 | n7 −0.74% PF0.60 | n4 −0.57% PF0.62 | n7 **−1.54%** PF0.56 |
| 剔除其主导卡后的组合（Δ，正=移除更好） | +0.011pp | +0.001pp | +0.011pp |

- 单策略与主导卡两个视角在**三个窗口全负**；移除它不伤组合（三窗 Δ 均 ≥0）。
- 早期 v2 全策略消融（L-110）也给出同向：剔除此 skill 的日 cohort Δ 三窗为 +0.034/+0.033/0.000（CI 跨 0）。
- **反证（必须记录）**：LIVE 已结算分桶 n=25 / WR 64.0% / avg **+0.53%** / PF 1.29 是正的。
  该样本来自更早的 LIVE 窗口、且 n 小；与三个窗口的回测口径（含最近窗口）方向相反。
  取舍：用户的取舍口径是"以最近周期为主"，且它在最近窗口的两个视角都是负的 → 退役。

## 2. 执行清单

- 目录移到 `/tmp/retired-skills-20260915/`：`backend/skills/small-cap-thematic-momentum/`
- 随之退役：`backend/tests/test_small_cap_momentum_skill.py`、`backend/scripts/run_smallcap_frozen_holdout.py`
- 在线测试同步收窄：`tests/test_prefilter_service.py` 去掉该 skill 的样本分支
- 配置清理：`app/services/backtest/strategy_quality.py` 删除它的 shadow-only `StrategyQualityConfig` 条目
- DB（不改 schema）：`preset_skills` 2 行（`preset_formal_opening` w1.2 + 专项预设 `9e88f6d9f2f7`）、
  空壳专项预设 `9e88f6d9f2f7`（小盘题材动量）1 行、`skill_registry` 1 行

## 3. 影响

- 组合层面：该 skill 在最近窗口只主导 7 张卡、贡献为负；移除后按分数自然递补，预期近似中性偏好。
- 页面上它的三窗回测行会消失（artifact 由导出脚本按现存技能生成）。

## 4. 相关

- L-110（生产同构消融；solo 亏损不能作为唯一依据）、L-111（记录可复现）、L-118（近期窗口优先的取舍口径由用户明确给出）。
