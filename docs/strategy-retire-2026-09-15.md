# 策略退役 2026-09-15：4 个零贡献/负贡献 skill 移出策略层

## 1. 做了什么

把 4 个技能从 `backend/skills/` 移除（15 → 11 个技能，全部 enabled）：

| skill | 一句话依据 | 类型 |
| --- | --- | --- |
| `dragon-tiger-smart-money` | 三窗 avg/sum/PF 同向改善；闸门 **W1 PASS / W2 FALSIFIED / W3 PASS**（见 §2.1 更正） | 行为变更 |
| `weak-market-strong-stock-reversal` | 三窗 Δcohort **恰好 0.0000**、0 张独持卡/背书卡：从未影响任何选中卡 | 零行为变更 |
| `main-line-leader-resonance` | manifest 已 disabled、从未进任何 preset、三窗 0 张选中卡 | 零行为变更 |
| `xiaoshi-quant-expert` | manifest 已 disabled、无 preset、research-only 映射，从未进入生产 | 零行为变更 |

判定口径：**不是**"这个策略自己的票亏钱"，而是**"把它从组合里拿掉，组合变好还是变差"**（L-110 永久规则 (a)(d)：
path-A solo 亏损不能作为砍策略依据；必须走生产同构 A/B 配对）。

## 2. 证据（可复现）

### 2.1 三窗生产同构消融（本轮新增 harness）

一次性重放、多臂复用的消融器：`backend/scripts/research_strategy_ablation_fast.py`
产物：`backend/output/research/per_strategy/ablate_fast_W{1,2,3}.json`

复现：
```bash
cd backend
NUMBA_CACHE_DIR=/tmp .venv/bin/python scripts/research_strategy_ablation_fast.py \
    --windows W1 --targets all --out output/research/per_strategy/ablate_fast_W1.json
# 同上 --windows W2 / W3（W1≈105min / W2≈55min / W3≈17min，三者可并行）
```

口径：真实 `evaluate_selection_policy`（`production_selection.v4`）+ 真实因果出场
（`DEFAULT_EXECUTION_POLICY` = 已发布 `execution_policy.v4`）+ 真实递补；A=全部启用技能，
B=剔除目标技能；同进程同源构建、按交易日对齐做配对 bootstrap(4000, seed7) 与逐月同号。
**注意**：`ExecutionPolicy()`（裸构造）是历史 v1 默认值（止盈 6% 开、止损 4.5%、trailing 2.0%），
与已发布产物不一致——之前 `research_strategy_exclude_canonical.py` 用的是前者，本 harness 用
`DEFAULT_EXECUTION_POLICY`。

基线（A=全部 12 个启用技能，用户口径三窗）：

| 窗口 | 区间 | n | WR | avg_net | sum_net | PF | MDD | 出票日 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| W1 holdout | 2025-01-15~2026-01-16 | 1127 | 56.4% | +0.2997% | +337.72% | 1.152 | −45.20% | 156 |
| W2 前瞻 | 2026-01-17~2026-07-30 | 656 | 57.2% | +0.3347% | +219.56% | 1.153 | −21.86% | 90 |
| W3 当前 | 2026-08-01~2026-09-13 | 108 | 45.4% | −0.5510% | −59.50% | 0.782 | −19.84% | 19 |

退役 `dragon-tiger-smart-money` 后的 B 臂：

| 窗口 | n | WR | avg_net | sum_net | PF | MDD | 闸门 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| W1 | 1089 | 57.5% | +0.3568% | +388.52% | 1.193 | −40.45% | **PASS**（五项指标全部改善） |
| W2 | 655 | 56.8% | +0.3521% | +230.66% | 1.163 | −24.01% | **FALSIFIED**（WR −0.4pp 与 MDD −2.15pp 两项辅助退化 > L-010 允许的至多一项） |
| W3 | 107 | 45.8% | −0.4082% | −43.67% | 0.839 | −19.60% | **PASS**（五项指标全部改善） |

> **2026-09-15 更正（L-116）**：本表原先把三窗都写成 PASS，是把 `PerformanceSnapshot` 的
> `max_drawdown_pct` 传成了 `abs(mdd)` 造成的极性反转（正确口径必须传负值：−21.86 → −24.01 是**变差**）。
> 修正后 W2 为 FALSIFIED。退役决定**保留**（三窗 avg / sum / PF 全部同向改善，W1/W3 五项全改善），
> 但它按用户验收规则属"2/3 窗通过 + W2 两项辅助退化"，不再作为"三窗全过"的 PASS 记录。

配对日差与边际卡：

| 窗口 | Δcohort | CI 95% | 日+/− | 月同号 | 独持卡 | 递补卡 |
| --- | --- | --- | --- | --- | --- | --- |
| W1 | +0.0534 | [−0.0660, +0.1802] | 32/28 | 0.75 | 119 张 / −1.297% | 81 张 / −1.278% |
| W2 | −0.0133 | [−0.2166, +0.1983] | 23/19 | 0.50 | 80 张 / +0.155% | 79 张 / +0.298% |
| W3 | +0.2683 | [−0.1003, +0.7025] | 7/1 | 1.00 | 15 张 / −0.684% | 14 张 / +0.398% |

复现闸门判定：
```bash
cd backend && .venv/bin/python - <<'PY'
import json
from app.services.backtest.challenger_decision import evaluate_challenger, PerformanceSnapshot
W = {w: json.load(open(f'output/research/per_strategy/ablate_fast_{w}.json'))[w] for w in ('W1','W2','W3')}
t = 'dragon-tiger-smart-money'
for w in ('W1','W2','W3'):
    a, b = W[w]['A'], W[w]['arms'][t]['B']
    print(w, evaluate_challenger(
        PerformanceSnapshot(a['avg'], a['sum'], a['wr']*100, a['pf'], abs(a['mdd'])),
        PerformanceSnapshot(b['avg'], b['sum'], b['wr']*100, b['pf'], abs(b['mdd']))).decision)
PY
```

### 2.2 其他两层证据（同向）

- **策略自己的票在亏**（选中卡主导策略，同源 `ablate_fast_*.json` 的 provenance）：
  W1 n=111 avg **−1.30%** / WR 39%；W2 n=74 avg +0.01%；W3 n=14 avg −0.33%。
- **背书质量全场最差**：三窗合计被选中卡背书 208 张，平均 **−0.560%**（唯一 n≥100 仍为负的技能）。
- **LIVE 已结算分桶**（`output/champion_baseline_20260913.json`，
  `FORMAL_ALL_LEGACY/all_scores`）：n=56 / WR 50.0% / avg **−0.407%** / PF 0.89 /
  sum −22.78% / max_loss **−15.16%**（越过用户设定的 −15% 边界）。

### 2.3 为什么另外两个 LIVE 最差的技能**不删**

| skill | LIVE 分桶（同人群） | 三窗消融结论 | 处置 |
| --- | --- | --- | --- |
| `daily-stock-analysis-skill` | n=166 WR 27.7% avg **−2.077%** PF 0.39（最差） | W3 剔除 Δ**−1.391** CI[−2.660,−0.464]（**剔除显著变差**），W1 −0.009 / W2 −0.079 | **留**（组合净正贡献，L-110/L-111(c)） |
| `trend-pullback-continuation` | n=42 WR 38.1% avg **−2.223%** PF 0.25 | Δ +0.037/+0.134/+0.138，但闸门在 W1（WR/MDD 退化）与 W2（WR/MDD 退化）**FALSIFIED** | **留**（未过三窗闸门，L-010） |
| `small-cap-thematic-momentum` | n=25 WR 64.0% avg +0.532% PF 1.29（正） | W1/W2 PASS、W3 FALSIFIED | 留 |
| `overnight-holding-method` | 无 LIVE 分桶（曝光不足） | W2/W3 PASS、W1 FALSIFIED | 留 |
| `sector-relative-strength-leader` | n=6 WR 33.3% avg −2.427% | 三窗全部 FALSIFIED，且 W1 独持卡 +0.157% | 留 |

即：**"这个 skill 自己的票亏钱"（LIVE 分桶 / path-A solo）与"从组合里拿掉它更好"是两件事**，
本轮的 DSA 就是最干净的例子——它的票最亏，但拿掉它组合显著更差。

## 3. 执行清单

- 技能目录移到 `/tmp/retired-skills-20260915/`（可恢复，未 `rm`）：
  `dragon-tiger-smart-money` / `weak-market-strong-stock-reversal` /
  `main-line-leader-resonance` / `xiaoshi-quant-expert`
- 随之退役的测试与一次性脚本移到同一处：
  `tests/test_dragon_tiger_smart_money.py` 的 skill 级用例（保留因子/特征层用例）、
  `tests/test_weak_market_strong_stock_reversal.py`、`tests/test_weak_market_reversal_vol5_20_diagnosis.py`、
  `tests/test_xiaoshi_quant_skill.py`、`scripts/diagnose_weak_market_reversal.py`、
  `scripts/scan_weak_market_reversal_sparsity.py`
- 在线测试同步收窄：`test_residual_poison_vetoes.py`（去掉 dragon-tiger 毒区两条）、
  `test_broken_uptrend_poison_zone.py`（AFFECTED 去掉 dragon-tiger）、
  `test_prefilter_service.py`（去掉两个已删技能的样本分支）
- DB（`preset_skills` / `presets` / `skill_registry`，不改 schema）：
  删除 `preset_skills` 3 行（dragon-tiger 在 `preset_formal_opening` 0.6 / `formal_trend_core` 1.1 /
  专项 preset `025975545919` 1.0）；专项 preset `025975545919`（龙虎榜聪明钱接力）随之为空壳，一并删除；
  删除 4 行 `skill_registry`。
  验证：`app.services.registry.skills.list_skills` 返回 11 个技能、`preset_formal_opening` 11 个；
  `build_registry_diagnostics(...).ok is True`（errors/informational 均空）、
  `report.require_executable("preset_formal_opening")` 通过。
- 保留（有意不动）：`app/services/entry_policy.py` 的 `_LEGACY_NEXT_OPEN_STRATEGY_IDS` 仍含
  `dragon-tiger-smart-money`——它用于解释**历史**已封存推荐的入场口径，删掉会改变历史重放语义。
  `app/services/factors/shortline.py` 的 `DragonTigerFactor` 与龙虎榜因子/标记注入**保留**：
  因子仍被其他策略与特征装配使用，退役的只是那个 skill 的规则。

## 4. 残留风险与口径警告（重要）

1. **两个"三窗"不是同一件事**。本轮全管线重放口径（用户指定的 W1/W2/W3）在 W1/W2 与已发布
   `research_verify_execution_policy_v3.py` 的冻结面板口径同号同量级，但 **W3 分歧很大**：
   冻结面板口径 W3 = n=94 / WR 55.3% / avg **+0.2737%** / PF 1.161 / sum **+25.7%**；
   全管线重放口径 W3 = n=108 / WR 45.4% / avg **−0.5510%** / PF 0.782 / sum **−59.50%**。
   面板口径的 W2/W3 用的是**库里已存的历史 backtest run**（`btr_fa625…` 2026-02-09~08-05、
   `btr_2bec6fed…` 2026-08-07~09-02），不是用户指定的 W2/W3 区间，也不是当前代码重放。
   方向上，全管线重放口径与 LIVE 实盘（n=368 avg −1.104%）同向，面板口径偏乐观。
   **结论：v3/v4 出场改动的 A/B 判定在面板口径内有效（同面板配对），但"三窗 sum_net>0"
   只在面板口径成立，不能当作"生产端到端已转正"。**
2. W2 的逐月同号只有 0.50（3 正 3 负）、W1 Δ 的 CI 下界为负：dragon-tiger 剔除的**点估计三窗同向为正**、
   闸门三窗全过，但不是 L-058 意义上的"强稳健"（≥0.8）。若后续该技能以外的改动生效，需要重跑本 harness 复核。
3. 本轮只删不建，未动 `production_selection`（仍 v4）、未动出场策略（仍 v4）、未动 aggregator。

## 5. 相关

- L-110（整策略剔除必须生产同构配对；solo 亏损不能作为砍策略依据）
- L-111（记录必须可复现；删策略前先查既有 FALSIFIED 结论）
- L-010（`evaluate_challenger` 是唯一发布闸门）
- L-058（多窗 + 逐月同号 0.8 稳健性门槛）
- L-113（出场侧三窗可测性；本条承接其"出场侧无解、须回入场侧"的结论）
