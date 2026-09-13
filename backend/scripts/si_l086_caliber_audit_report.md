# L-086 三层毒区口径审计：头条 +8.3pp 全部来自非 canonical 层，canonical LIVE_FORWARD 层增量为精确 0（2026-09-13，第五轮）

## 摘要

L-086（`docs/LIVE路径仲裁-三层毒区相位排序纠错-2026-09-12.md`，位于分叉分支
`feat/strong-bull-ivol-veto-v3`，commit `83b98b1`）在其"去重生产口径 241 笔唯一推荐、205 笔已结算"
的样本上报告：加三层毒区后 **top30 净胜率 48.6% → 56.9%（+8.3pp）**，且跨三套权重稳健、零 hard_veto，
是当前最可能被合并进 main 的生产改动。

本轮把该样本**按 `evidence_class` 分层重做金标准反事实**（真实调用 `run_strategy`、逐策略取
strategy.yaml 生产权重），得到结论：

> **L-086 的全部增益都落在 `historical_reconstruction` 层（top30 +9.7pp）；而 §2 唯一承认的
> canonical `live_forward` 层，三层毒区的增量为精确的 +0.0pp / +0.000%（三个窗口全部逐位相等）。
> 三层里有两层在该层命中数为 0。**

同时发现该"毒区"标签在 `None` 层**符号反转**（三层条件在该层胜率 100% / 75% / 66.7%，全部"有毒条件反而最好"）。
真实的微观结构毒性不可能因为推荐来源类别不同而反号 —— 这是**口径污染**（L-001）而非可发布因子。

**决定：MEASUREMENT_FIX。** 不声称收益提升，不修改任何生产策略 yaml；结论是 L-086 的
发布证据不满足 §2 真实性硬闸门，其 +8.3pp 必须在 canonical 口径上重新挣得后才能合并。

---

## 一、方法（与 L-088 金标准一致，唯一变量 = 是否注入三层毒区）

### 1.1 反事实的两个 bank

| bank | 构造 |
|---|---|
| **base（去毒区）** | 逐个 `strategy.yaml` 剥离名字属于 `POISON_ORDER` 的 phase |
| **treat（前置三层）** | 在剥离后的 phases **最前面**插入三层，顺序固定 强势(cap58) → RSI(cap58) → leader(cap62) |

关键实现细节（都来自 L-086 自己发现的陷阱，本轮忠实复现）：

1. **必须前置而非末尾**：`strategy_runner` 对 phases 是"从上到下取第一个 `when` 为真的 phase，命中即 break，
   只应用这一个"。插在末尾会被更早的宽松软 cap（如 chao 弱市 cap68）抢先命中而**静默失效**。
2. **必须对既有 `hard_veto` 让位**：每个毒区 `when` 追加 `and not (<该策略每个既有 hard_veto 的 when>)`，
   否则新软 cap 会抢到核心风控前面把它架空（L-086 已明文否决无 guard 的 raw 方案）。
3. **数据有效性守卫**：`rsi_24 > 0`、`shortline_leader_score > 0`。`evaluator` 对缺失字段 `_coerce(None)=0.0`，
   不加 `>0` 会让缺该字段的票被当成"最低值"误降权。`dsa_trend_score == 30` 天然安全（0≠30）。

> **脚本刻意把三层定义内联在 `POISON_DEF`，而不从磁盘 yaml 读取。** 原因：main 的
> `backend/skills/*/strategy.yaml` 里**已经躺着大量其它批次加入的毒区/降权 phase**
> （弱市超买毒区、弱市降权、弱市半吊子追高毒区、高位追高毒区、接近60日高位追高毒区、
> 爆量弱势衰竭毒区、中度回撤毒区，以及 T3 第185轮的低换手过滤）。若从磁盘读三层，就无法区分
> "本轮要测的三层"与"历史上已有的毒区"。
>
> **但必须明确一个边界**：内联只保证 **treat 侧**（注入的三层）在任意分支上完全一致；
> **base 侧 = 该分支自己的 yaml 减去这三层**，因此 **base 是有分支依赖的**。main 与
> `feat/strong-bull-ivol-veto-v3` 的 base 差异不止这三层（该分支还前置重排了各策略 phases、
> 并删除了 DSA 的"资金背离" hard_veto）。所以本审计正确回答的问题是
> **"在某一分支的既有策略组合上，再加这三层的边际效果是多少"**，而不是"三层的绝对效果"。
> 第四节.五用两个分支的对照量化了这一依赖。

### 1.2 样本（DB 持久重建，不依赖 /tmp 载荷）

`formal` + `completed` + `rec_*`，按 `(trade_date, code)` 去重，已结算 = `outcome_label ∈ {success, failed}`。

- 重建 **215** 个 pair，其中已结算 **172**
- 与 L-086 的 `/tmp/live_dedup_data.json`（241 唯一 pair）交叉核对：**命中 215 / 241 = 89.2%**
- 收益口径：`executed_return − 0.07%`（净成本）；窗口 = top-half / top40 / top30 按重打分降序取前 k%

---

## 二、population 的 evidence_class 构成

| evidence_class | pairs | **已结算** | 交易日跨度 | 天数 |
|---|---:|---:|---|---:|
| `historical_reconstruction` | 164 | **139** | 2026-05-14 → 2026-09-04 | 35 |
| `live_forward` | 24 | **9** | 2026-08-17 → 2026-09-10 | 6 |
| `None` | 20 | 17 | 2026-08-24 → 2026-09-03 | 3 |
| `unverified` | 7 | 7 | 2026-08-24 → 2026-09-09 | 2 |

**canonical `live_forward` 只占已结算 population 的 9/172 = 5.2%。**

即 L-086 的"205 笔已结算"里，约 **80.8% 是 `historical_reconstruction`** —— 一个 §2 明确不接受的类别。

---

## 三、机制证据：非 canonical 层是"隔了 median 30 天"才生成的

仅看 `evidence_class` 标签可以说"类别不符"；本轮进一步量化**它到底是什么时候生成的**：

| evidence_class | 行数 | `actual_generated_at − trade_date`（天） | 当日或前一日生成 |
|---|---:|---|---|
| `historical_reconstruction` | 332 | min **2** / median **30** / max **93** | **0 / 332** |
| `live_forward` | 9 | min 0 / median **0** / max 0 | **9 / 9** |
| `unverified` | 10 | 恒为 1 | 0 / 10 |
| `None` | 99 | （无 `actual_generated_at`） | — |

（`data_as_of <= trade_date` 在**所有**层都是 100% —— 声明的数据新鲜度都是因果的；
差异体现在**真实生成时点**：`historical_reconstruction` median 滞后 30 天，最多 93 天。）

这解释了为什么增益只出现在该层：一个在事后 30 天才执行的"历史重建"，其定线与调参过程天然
比当日实盘多出数十个交易日的可观察信息。**它不能作为发布证据**，而 L-086 的 +8.3pp 正是登在它上面。

---

## 四、金标准反事实（按 evidence_class 分层）

base = 剥离三层；treat = 前置三层。`dWR` = treat − base（pp），`dAvg` = treat − base（%）。

### 4.1 `ALL`（n=172，已结算全量）

| 窗口 | base WR / avg | treat WR / avg | dWR | dAvg |
|---|---|---|---|---|
| top_half | 53.5% / −0.513 | 59.3% / +0.345 | **+5.8pp** | +0.858 |
| top40 | 52.9% / −0.310 | 57.4% / +0.038 | **+4.5pp** | +0.348 |
| top30 | 58.8% / +0.473 | 62.7% / +0.533 | **+3.9pp** | +0.060 |

### 4.2 `historical_reconstruction`（n=139）← **全部增益在此**

| 窗口 | base WR / avg | treat WR / avg | dWR | dAvg |
|---|---|---|---|---|
| top_half | 55.1% / −0.290 | 59.4% / +0.489 | +4.3pp | +0.779 |
| top40 | 52.7% / −0.199 | 60.0% / +0.535 | +7.3pp | +0.734 |
| top30 | 53.7% / +0.130 | 63.4% / +0.944 | **+9.7pp** | +0.814 |

### 4.3 `live_forward`（n=9）← **§2 唯一承认的口径**

| 窗口 | base WR / avg | treat WR / avg | dWR | dAvg |
|---|---|---|---|---|
| top_half | 75.0% / +2.087 | 75.0% / +2.087 | **+0.0pp** | **+0.000** |
| top40 | 66.7% / +0.880 | 66.7% / +0.880 | **+0.0pp** | **+0.000** |
| top30 | 50.0% / −1.540 | 50.0% / −1.540 | **+0.0pp** | **+0.000** |

### 4.4 `None`（n=17）与 `unverified`（n=7）

`None`：top_half dWR 0.0pp / dAvg +0.339；top40 0.0pp / +0.039；top30 0.0pp / +0.000。
`unverified`：三个窗口**逐位相等**。

→ **除了 `historical_reconstruction`，其余每一层都拿不到增益。** `ALL` 的 +3.9pp 是该层稀释后的残影。

### 4.5 分支稳健性对照：头条数字在 3.5× 范围内游走，只有 canonical 层纹丝不动

同一脚本、同一 population，分别在 **L-086 自己的分支**（`feat/strong-bull-ivol-veto-v3` @ `0bb5140`）
和 **main**（`667d357`）上各跑一次。两边的 treat 侧注入完全相同，差异只来自 base 侧
（分支 yaml 组成不同）：

| 层 / 窗口 | 特征分支 dWR | main dWR | 特征分支 dAvg | main dAvg |
|---|---|---|---|---|
| `ALL` top30 | +3.9pp | **+13.7pp** | +0.060 | **+1.365** |
| `ALL` top_half | +5.8pp | （见运行输出） | +0.858 | — |
| `historical_reconstruction` top30 | +9.7pp | +7.3pp | +0.814 | +0.649 |
| **`live_forward` top30** | **+0.0pp** | **+0.0pp** | **+0.000** | **+0.000** |
| **`live_forward` top_half / top40** | **+0.0pp** | **+0.0pp** | **+0.000** | **+0.000** |
| **`live_forward` 毒区命中** | dsa **0** / rsi **3** / leader **0** | dsa **0** / rsi **3** / leader **0** | — | — |

两个结论：

1. **非 canonical 层的"增益"完全不稳定**：同一个"三层毒区"，`ALL` top30 的 dWR 在
   +3.9pp 与 +13.7pp 之间摆动（**3.5 倍**）。一个真实可发布的因子不会因为评测基线的
   既有毒区组成不同而放大 3.5 倍 —— 这进一步说明它是分层/基线产物。
2. **canonical `live_forward` 层的结论是分支不变量**：无论 base 换成哪一套策略组合，
   `dWR = +0.0pp`、`dAvg = +0.000`、且**第 1、3 层命中数恒为 0、第 2 层恒为 3**。
   §2 唯一承认的口径给出的答案，在两种评测基线下都是"三层一个决策都没改变"。

（`live_forward` 的 base 绝对收益在两个分支上不同（+0.285% vs −1.540%），因为 base 的策略组成
不同、打分排序不同 → 选出的 top30 成员不同；但**增量恒为 0**，这正是本节要锁的稳健性。）

---

## 五、为什么"毒区"不可信：它按来源类别反号

各层三层条件的命中数与**已实现**净收益（命中数 = 命中该条件的已结算笔数）：

| 层 | `dsa_trend_score==30` | `rsi_24 ∈ (0,48)` | `leader ∈ (0,35]` |
|---|---|---|---|
| `ALL` | n=15 WR 20.0% avg **−3.279** | n=22 WR 40.9% avg −1.745 | n=53 WR 43.4% avg −1.189 |
| `historical_reconstruction` | n=14 WR **14.3%** avg −3.58 | n=15 WR 33.3% avg −1.786 | n=44 WR 40.9% avg −1.254 |
| **`live_forward`** | **n=0** | n=3 WR 33.3% avg −3.603 | **n=0** |
| `None` | n=1 WR **100.0%** avg +0.93 | n=4 WR **75.0%** avg −0.2 | n=6 WR **66.7%** avg +0.198 |
| `unverified` | n=0 | n=0 | n=3 WR 33.3% avg −3.017 |

两点决定性观察：

1. **canonical 层里第 1、3 层命中数 = 0**。`dsa_trend_score == 30` 与 `leader ∈ (0,35]` 在 9 笔
   canonical 已结算里**从未出现**；第 2 层命中 3 笔，但把它们的分数压到 cap58 后**没有任何一只跨过
   top-half / top40 / top30 边界**，所以三个窗口逐位相等。**"0.0pp"的精确含义是：三层在 canonical
   population 上一个选股决策都没改变。**
2. **`None` 层符号反转**：同三个被称作"毒"的条件，在该层胜率分别是 **100% / 75% / 66.7%**，
   均值 +0.93 / −0.2 / +0.198 —— 三层里两层在该层是**正**的。市场微观结构的毒性不会因为
   推荐是"当日实盘"还是"事后重建"而反号。反号 ⇒ 该"毒性"是分层产物，不是市场事实。

---

## 六、可证伪性对照（证明本审计既能报 0 也能报赢）

一个只会输出 0 的审计毫无说服力，所以回归测试里锁了两个方向相反的人造对照：

- **对照 A（能报赢）**：前 5 只亏、后 5 只赢，base 把亏损票排在前面 → base top30 WR 0.0% / −1.0%；
  treat 反转为按收益排序 → treat top30 WR 100.0% / +1.0%，Δ = +2.0%。**审计能测出正增益。**
- **对照 B（能报精确 0）**：名次固定，只对严格在最大窗口（top_half=5）之外的名次降权 →
  treat 与 base 三个窗口**逐位相等**。**这正是 canonical `live_forward` 层实测到的形态。**

---

## 七、顺带核实的三项事实（本轮只读、未改动）

1. **三层毒区不在 main。** `git grep` 在三处 main 的 `backend/skills` 下命中 0 个文件；
   它们只在分叉分支 `feat/strong-bull-ivol-veto-v3`（`83b98b1` → `695674d`）上，
   与 main（`667d357`）**双向分叉 16 / 16**。所以本轮决定**不需要回滚任何生产代码**。
2. **兄弟产物 `main-line-leader-resonance` 被 self_check 判 BLOCKED（errors=2）**：
   `active_strategy_unassigned`（active 策略未进入任何 executable preset）+
   `active_strategy_unknown_declared_group`（声明了不存在的预设组 `experiment_main_line_resonance`）。
   同一 `strategy.yaml` 自身声明 `enabled: false`，而 `manifest.json` 声明 `status: active`
   —— 元数据自相矛盾。其 backtest 也是全 roster 最差：n=7236 WR 38.6% avg −1.52% PL 0.53。
   该 skill 在 main 树上**未被跟踪**，本轮未处置。
3. **self_check 的 D 项基线是硬编码常量。** `scripts/self_check.py:2531` 为
   `WIN_RATE_BASELINE: float | None = None`（`MAX_LOSS_BASELINE`、`PL_RATIO_BASELINE` 同），
   注释为"旧基线来自已废弃的成交口径，重放 champion 前不得计算 Δ"。后果：它**永远**打印
   "D 项规则覆盖：同口径 champion 尚未重放，跳过旧基线反事实判定"，`check_d_rule_coverage` 与
   `_forward_verification_flag` 因此**永久不执行** —— 即使上一轮已把 canonical 基线
   （n=9 / WR 55.6% / avg −0.839%）注册进 state 的 `canonical_production`。
   **state 有基线，诊断工具不读它。**

---

## 八、决定：MEASUREMENT_FIX

| 项 | 内容 |
|---|---|
| **主假设** | L-086 的三层毒区软降权在其声称的"+8.3pp top30"上可改善真实交易结果 |
| **唯一变量** | 是否在 phases 最前面注入三层毒区（base vs treat），其余全部固定 |
| **证据口径** | 分层金标准反事实；**canonical 层 = `live_forward`（§2 唯一承认）** |
| **四核心指标变化（canonical 层）** | 平均净收益 **+0.000%**、净胜率 **+0.0pp**、PF 不变、MDD 不变 —— 三个窗口全部逐位相等 |
| **四核心指标变化（非 canonical `historical_reconstruction` 层）** | 平均净收益 +0.814%、净胜率 +9.7pp（top30）—— **但该层 median 滞后 30 天才生成，且该"增益"在换评测基线后变成 +7.3pp / `ALL` 层从 +3.9pp 变 +13.7pp（3.5×），不稳健** |
| **CI / 样本量 / 跨窗口 / 覆盖率** | canonical 层 n=9（少样本、单一窗口，按协议**不作为否决**理由）；不适用 CI——**删除量为 0，不存在可检验的效应** |
| **决定** | **MEASUREMENT_FIX** —— 不发布、不改生产策略 yaml；判定 L-086 的发布证据不满足 §2 |

**为什么不是 FALSIFIED？** 本轮的发现不是"毒区是坏的交易规则"，而是**"现有证据无法支撑它"**：
canonical 层的 n=9 小到三层根本没机会触发（第 1、3 层命中 0 次），因此这一层**既不能证真也不能证伪**。
按 §2 的措辞，`historical_reconstruction` 不是可接受的发布证据 —— 所以正确的动作是把
L-086 降级为"待 canonical 样本积累后重验"，而不是宣布毒区无效。**本轮修的是测量可信度，不是规则本身。**

### 对后续的约束（必须成立，否则口径继续被污染）

1. **任何 challenger 的发布证据必须落在 canonical `live_forward` 层上。** 跨 `evidence_class`
   汇总（如 `ALL`）会把 `historical_reconstruction` 的 80.8% 混进来，本轮证明这会把 +0.0pp 放大成 +3.9pp。
2. **`feat/strong-bull-ivol-veto-v3` 合并前必须在 canonical 层重验三层毒区**，不得引用 +8.3pp。
3. **下一轮 P1：把 self_check 的 D 项基线接到 state 的 `canonical_production`**（带口径守卫），
   让 `check_d_rule_coverage` 重新生效。另：main 已在注册的 champion 提交 `253543d` 之后新增
   `7cd2bb1 选股策略 v2（production_selection.v2.json +41 行）` 与 `aggregator.py` +146 行等
   **入场侧**改动，故上一轮在 `253543d` 重放的 canonical 基线按 L-002 已再次失效，需在 `667d357` 重放。

---

## 九、可复现产物

| 文件 | 说明 |
|---|---|
| `backend/scripts/si_l086_caliber_audit.py` | 分层金标准反事实（内联三层定义，任意分支结果一致）；产物 `output/l086_caliber_audit.json` |
| `backend/scripts/si_probe_l086_population.py` | population 分层 + **生成时点滞后** + `data_as_of` 因果性探针 |
| `backend/tests/test_si_l086_caliber_audit.py` | 15 passed：bank 构造 / hard_veto 让位 guard / firing 计数 / counterfactual 语义 + **两个可证伪性对照** |

复现（worktree 内，纯只读）：

```bash
cd backend
python3 scripts/si_l086_caliber_audit.py     # 打印分层反事实，写 output/l086_caliber_audit.json
python3 scripts/si_probe_l086_population.py  # 打印分层构成 + median 30 天生成滞后
python3 -m pytest tests/test_si_l086_caliber_audit.py -q   # 15 passed
```
