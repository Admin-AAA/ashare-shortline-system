# Champion 同口径基线重建（2026-09-11）

- 决策：`MEASUREMENT_FIX`（只修复真实性/测量能力，不声称提高收益）
- 脚本：`backend/scripts/si_champion_baseline_rebuild.py`
- 测试：`backend/tests/test_si_champion_baseline_rebuild.py`（10 passed）
- 产物：`output/champion_baseline_20260911.json`
- 证据口径：`champion_replay`（当前 main 代码因果重放，未修改 production 策略，0 次 LLM 调用）

## 1. 为什么必须做这一步

main 在 8 月下旬～9 月落了一串**因果/统计口径修复**：

| commit | 内容 |
|---|---|
| `d3ddab2` / `8e798ae` | 修复策略入场与未成交收益口径 |
| `e4bd796` / `420b8ff` | 迁移历史推荐入场口径 |
| `ef11a54` | 对齐 outcome accuracy 语义 |
| `511b8fe` | 未完成 horizon 的 outcome 保持 provisional |

这些提交同时收紧了 `LIVE_FORWARD` 判定（`evidence_class` + `actual_generated_at` + `data_as_of <= trade_date` + `source_snapshot_id` + `evidence_identity_hash` + 策略/执行指纹 + `code_revision` + `provenance_schema_version`）。

按 **L-002**：因果或统计口径变化后旧 champion 立即失效，必须先用当前代码重放**未修改的 production**，才能比较任何 challenger。此前 self_check 每轮都输出「同口径 champion 待重放（旧历史基线已失效，不计算 Δ）」，而 `self-improve-state.json` 仍挂着 545a8b9 时点的快照（n=55 / WR 63.6% / avg +2.039% / PF 2.93 / MDD −6.2%）。该快照已不可用作 champion。

## 2. 方法

在同一份 DB、同一份当前 main 代码下，用生产退出状态机 `_simulate_outcome(..., exit_resolver=None)`（`horizon=5`、当前生产止损 `-4.5%`、往返成本 `0.07%/笔`）重放三组 population：

- **A `LIVE_FORMAL_STRICT`**：canonical 前瞻口径（`official_sample_conditions()`）
- **B `FORMAL_ALL_LEGACY`**：旧 champion 快照的宽口径（全部 `formal` + `completed` + `rec_*`，不限制 `evidence_class`）
- **C `LEGACY − STRICT`**：B 相对 A 的差集，即旧口径多算进来的样本

组合曲线按「等权、按信号日聚合、abstain 计现金」重建（无仓位记录，无法重建真实仓位，已显式标注）。

## 3. 结果

### 3.1 canonical 前瞻口径（新 champion 基线）

| population | selected | replayable | settled | coverage | n | 交易日 | 净胜率 | 平均净收益 | PF | 平均盈利 | 平均亏损 | 最大单笔亏损 | 等权累计 | 等权 MDD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all_scores | 34 | 16 | 9 | 47.1% | 9 | 4 | 55.6% | **−0.839%** | **0.68** | +3.19% | −5.88% | −7.43% | −5.34% | −8.42% |
| clean≥72 | 16 | 5 | 5 | 31.2% | 5 | 4 | 40.0% | **−2.532%** | **0.21** | +1.72% | −5.36% | −6.20% | −10.30% | −11.52% |

9 笔明细（全部 `20cm`）：

| 推荐 | 代码 | 信号日 | 分数 | 净收益 | 标签 | 出场 |
|---|---|---|---|---|---|---|
| rec_20260817_001_d4b9 | 300790 | 2026-08-17 | 73.1 | −4.75% | failed | stop_loss_hit |
| rec_20260821_001_f7c2 | 688536 | 2026-08-21 | 85.8 | +2.06% | success | trailing_stop_hit |
| rec_20260821_002_4121 | 300373 | 2026-08-21 | 73.8 | −6.20% | failed | stop_loss_gap |
| rec_20260821_003_f2ad | 688525 | 2026-08-21 | 68.3 | −7.43% | failed | stop_loss_gap |
| rec_20260825_001_292e | 688536 | 2026-08-25 | 78.0 | −5.14% | failed | stop_loss_gap |
| rec_20260825_002_b1e5 | 300679 | 2026-08-25 | 71.4 | +5.71% | success | take_profit_hit |
| rec_20260825_003_7539 | 300394 | 2026-08-25 | 62.8 | +5.72% | success | take_profit_hit |
| rec_20260826_001_2b03 | 300604 | 2026-08-26 | 74.5 | +1.37% | success | trailing_stop_hit |
| rec_20260826_002_b8d7 | 688361 | 2026-08-26 | 69.0 | +1.11% | success | trailing_stop_hit |

亏损全部集中在 3 笔 `stop_loss_gap`（−6.20 / −7.43 / −5.14），与 **L-011 / L-061** 的「跳空止损是最大单项亏损、且非入场可识别」一致。

### 3.2 宽口径（旧 champion 快照口径）与差值

| population | n | 交易日 | 净胜率 | 平均净收益 | PF | 最大单笔亏损 | 等权累计 | 等权 MDD |
|---|---|---|---|---|---|---|---|---|
| B 全量 | 368 | 40 | 42.1% | −1.104% | 0.64 | −15.16% | −14.41% | −19.41% |
| B clean≥72 | 103 | 26 | 46.6% | −1.200% | 0.58 | −12.77% | −21.97% | −28.05% |
| C = B−A 全量 | 359 | 36 | 41.8% | −1.110% | 0.64 | −15.16% | — | — |
| C = B−A clean≥72 | 98 | 22 | 46.9% | −1.132% | 0.60 | −12.77% | — | — |

标签翻转矩阵（replay vs 存储 outcome，L-002 强制披露项）：**strict 全量 9 笔 success→success=5 / failed→failed=4，零翻转；clean≥72 5 笔 2/0/0/3，零翻转**。即当前代码重放与库内 outcome **完全一致**，本轮的差异不是代码错算，而是**样本口径**差异。

### 3.3 口径审计：为什么 n=55 → n=9

窄口径不是 bug，而是**正确排除了非前瞻样本**：

- 库内 formal completed run：`historical_reconstruction` 279 个、`live_forward` 17 个、`unverified` 2 个、`NULL` 13 个。
- 2026-09-02 / 09-03 / 09-04 的「正式推荐」共 100+ 笔**全部**来自 `REPLAY-历史推荐重建` 与 `停机补跑`（`evaluation_scope=historical_replay`），系统在那几天停机，样本由 09-09 事后重建产生 → 按硬门禁不得计入前瞻。
- 唯一进入 canonical 前瞻的是 08-17 / 08-21 / 08-25 / 08-26 / 09-09 / 09-10 / 09-11 的 `数据同步后正式推荐`（`trusted_formal_run_entrypoint`）。

**结论：旧快照（n=55、avg +2.039%、PF 2.93）在当前 canonical 口径下无法复现**；而且连宽松口径（n=368 / 103）也是负期望，说明「旧快照为正」既不是宽窄口径差异造成的，也无法用当前代码/数据复现 → 按 L-002 失效。

## 4. 缺口（如实报告，不作否决）

- 严格口径覆盖率 47.1%（all）/ 31.2%（clean≥72）：缺口 = 未结算（`horizon=5` 尚未走完的 09-09～09-11 推荐）+ 无可用日线 bars 的票。
- 前瞻样本 n=9、交易日 4；clean≥72 n=5、交易日 4。按主提示词统计协议，样本量本身**不是**发布否决条件，此处仅作为普通统计数字报告。
- 组合曲线为等权、按信号日聚合（无仓位记录），已标注，不与生产真实仓位口径混算。

## 5. 决定与后续

- **决定 `MEASUREMENT_FIX`**：本轮只重建了同口径 champion 基线，未修改任何生产代码、未发布候选。
- 新 champion 基线（`canonical 前瞻 / 20cm / 净成本 0.07%`）：**全量 n=9 avg −0.839% PF 0.68；clean≥72 n=5 avg −2.532% PF 0.21**。此后所有 challenger 必须相对**这一**基线在同一批输入上比较。
- 旧快照数字一律标记为「已失效历史口径」，不得再用于 Δ。
- 复现命令：`backend/.venv/bin/python backend/scripts/si_champion_baseline_rebuild.py`
