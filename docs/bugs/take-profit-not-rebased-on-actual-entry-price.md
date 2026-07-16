# Bug 报告：止盈价未随实际入场价重算，跳空低开后固定止盈路径失效

> 状态：**已修复**（main commit `86ad8fc`，2026-07-13 23:26，self-improve 实例）。
> ⚠️ 修复曾于 module extraction（`d29e326` 把函数搬到 `outcome_simulation.py`）中丢失，
> 搬过去的是修复前版本；已于 2026-07-16 在 `outcome_simulation.py` 重新落地
> （commit 见 git log，恢复 `recommend_price` 参数 + 调用点传参 + 5 个回归测试）。
> 附带问题 1 已结案（数据证伪，不修）；附带问题 2 已实施（前端双基准标注）。
> 本报告作为 bug 历史档案归档，记录根因、数据链路与影响面。
> 首次诊断时间：2026-07-13 22:41。
> 触发场景：复盘页 `http://localhost:5174/history` 显示 603991 至正股份（推荐日 2026-07-10）标"移动止盈触发 +5.61% 成功 持仓 1 天"，但当日行情未达固定止盈价 176.60。

## 摘要

`_resolve_take_profit_price` 与 `_resolve_stop_loss_price` 对"跳空低开后入场"的归一化处理不对称：止损价会基于实际入场价重算，止盈价只做上限裁剪、不按比例重算。导致跳空低开后设计上 +5% 的止盈目标被相对抬到 +14.5%，几乎不可能触发，原本应走 `take_profit_hit` 的路径全被推给 `trailing_stop_hit` 兜底。trailing 用实际入场价算出 +5.61% 退出并标记 success，但相对推荐价实为 −2.24%（高点）/ −3.39%（收盘）的亏损。

修复（`86ad8fc`）按本报告"建议修复方向 A"实施：用推荐时隐含止盈比例 `implied_tp_pct = raw_target / recommend_price − 1` 重算 `entry × (1 + implied_tp_pct)`，再 clamp 到 `[entry×1.05, entry×1.15]`，与 `_resolve_stop_loss_price` 对称。

## 受影响记录

- `recommendation_id`：`rec_20260710_001_304a`
- 代码：603991 至正股份
- 推荐日：2026-07-10，`recommend_price = 168.19`
- feature_snapshot：`stop_loss=158.94`、`take_profit=176.6`、`ideal_buy=165.67`、`secondary_buy=163.14`
- 入场日：2026-07-13（T+1），`entry_price = 154.36`（= 7-13 开盘 154.21 + 10bps 滑点）
- 7-13 跳空低开：`(154.21 − 168.19) / 168.19 = −8.31%`
- 退出日：2026-07-13（持仓 1 天），`exit_price = 163.02`
- `executed_return = +5.61`，`exit_reason = trailing_stop_hit`，`outcome_label = success`
- DB 实际字段：`stop_loss_price = 145.73`（重算 −5.5%）、`take_profit_price = 176.6`（**未重算**）
- 7-13 K 线：`O=154.21  H=164.42  L=154.21  C=162.49  pct=+0.39%`
- 静态指标（基于 recommend_price 168.19）：`max_return = −2.24`、`min_return = −8.31`、`close_return = −3.39`

## 数据链路

```
推荐价 168.19  ──7-13 跳空低开 −8.31%──▶  实际入场价 154.36
   │                                         │
   ├─ 止损 158.94 (−5.5%)                    ├─ 止损 145.73 (重算 −5.5%)        ✓ 跟着重算
   ├─ 止盈 176.60 (+5%)                      ├─ 止盈 176.60 (+14.5%，未重算)    ✗ 没重算
   │                                         │
   │                                         ├─ 7-13 high 164.42 (+6.52%) < 176.60 → 固定止盈路径失效
   │                                         ├─ trailing：high +6.52% ≥ 2% 激活，回撤到 163.18 触发
   │                                         ├─ 退出价 163.02，+5.61%（相对入场价 154.36）
   │                                         └─ 相对推荐价 168.19：−2.24% (high) / −3.39% (close)
   │
   └─ 盈亏比设计 1:1  ──错配──▶  实际 止损 −5.5% / 止盈 +14.5%  →  盈亏比 1:2.6
```

trailing 内部自洽（hwm +6.52% → 触发价 163.18 → 退出 163.02 → +5.61%），问题不在 trailing，而在它本不该被触发——固定止盈路径被抬高的止盈价堵死了。

## 根因

文件：`backend/app/services/outcome_service.py`

### `_resolve_stop_loss_price`（2140–2156）——会重算

```python
max_stop = raw_entry_price * (1 + DEFAULT_STOP_LOSS_PCT / 100.0)   # entry × 0.945
if raw_stop <= 0 or raw_stop >= raw_entry_price:                   # 跳空低开穿透时 raw_stop ≥ entry
    return round(max_stop, 2)                                      # → 重算为 entry × 0.945
min_stop = raw_entry_price * (1 + MAX_STOP_LOSS_PCT / 100.0)       # entry × 0.943
normalized = min(raw_stop, max_stop)
normalized = max(normalized, min_stop)
return round(normalized, 2)
```

跳空低开后 `raw_stop=158.94 ≥ raw_entry_price=154.21`，命中 `raw_stop >= raw_entry_price` 分支，重算为 `154.21 × 0.945 = 145.73`。

### `_resolve_take_profit_price`（修复前 2159–2169）——不会重算

```python
fallback = raw_entry_price * (1 + DEFAULT_TAKE_PROFIT_PCT / 100.0)  # entry × 1.05
if raw_target <= raw_entry_price:
    return round(fallback, 2)                                       # 几乎不触发（止盈本就高于入场价）
max_tp = raw_entry_price * (1 + MAX_TAKE_PROFIT_PCT / 100.0)        # entry × 1.15
normalized = min(raw_target, max_tp)                                # 仅上限裁剪
return round(normalized, 2)
```

跳空低开后 `raw_target=176.6 > raw_entry_price=154.21`，不进 `raw_target <= raw_entry_price` 分支；`176.6 < entry × 1.15 = 177.34`，上限裁剪也不生效，**原值保留**。结果 `176.6` 相对入场价变成 +14.5%，而非设计的 +5%（应为 161.92）。

### 不对称的本质

| 函数 | 异常分支触发条件 | 跳空低开后行为 |
|---|---|---|
| `_resolve_stop_loss_price` | `raw_stop >= raw_entry_price`（止损价高于入场价，明显异常） | 重算为 `entry × 0.945` |
| `_resolve_take_profit_price`（修复前） | `raw_target <= raw_entry_price`（止盈价低于入场价，几乎不发生） | 不重算，原值保留 |

止盈价高于入场价是常态，无法用"是否高于入场价"判断该不该重算，所以 take_profit 缺少与 stop_loss 对称的"按实际入场价比例重算"逻辑。

相关常量（行 29–38）：
- `DEFAULT_TAKE_PROFIT_PCT = 5.0`，`MAX_TAKE_PROFIT_PCT = 15.0`
- `DEFAULT_STOP_LOSS_PCT = -5.5`，`MAX_STOP_LOSS_PCT = -5.7`
- `MAX_ENTRY_GAP_PCT = 10.0`（仅过滤跳空高开 >10%，不过滤低开）

调用处（修复前行 1798）：
```python
take_profit_price = _resolve_take_profit_price(raw_entry_price, feature_snapshot)
```

## 修复状态（commit `86ad8fc`）

**已实施"建议修复方向 A"**，由 self-improve 实例于 2026-07-13 23:26 提交到本地 main：

```python
def _resolve_take_profit_price(
    raw_entry_price: float | None,
    feature_snapshot: dict[str, Any],
    recommend_price: float | None = None,          # 新增
) -> float:
    raw_target = _as_float(feature_snapshot.get("take_profit"))
    if raw_entry_price is None or raw_entry_price <= 0:
        return round(raw_target, 2) if raw_target > 0 else 0.0
    fallback = raw_entry_price * (1 + DEFAULT_TAKE_PROFIT_PCT / 100.0)
    if raw_target <= 0:
        return round(fallback, 2)
    # 跳空导致实际入场价偏离推荐价：按推荐时隐含的止盈比例重算
    if recommend_price and recommend_price > 0:
        implied_tp_pct = raw_target / recommend_price - 1.0
        raw_target = raw_entry_price * (1 + implied_tp_pct)
    max_tp = raw_entry_price * (1 + MAX_TAKE_PROFIT_PCT / 100.0)
    normalized = min(raw_target, max_tp)    # 不高于 +15%
    normalized = max(normalized, fallback)  # 不低于 +5%
    return round(normalized, 2)
```

调用点同步传入 `recommend_price=recommend_price`（`rec.recommend_price` 已有，行 1798）。

### 验证与影响面（来自 `86ad8fc` commit message）

- 新增 5 个回归测试（gap 低开重算 / 无跳空不变 / 上限 clamp / 下限 clamp / 无 recommend 退回旧行为）：`pytest` outcome 相关 27 passed，无回归。
- 新增只读诊断脚本 `scripts/diagnose_take_profit_rebase.py`，量化历史影响面：**601 条已结算中 60 条（10%）止盈价被异常抬高 >3pp**，含 603991 案例（`stored +14.4% → corrected +4.9%`，错标 success），证实 bug 真实存在且广泛。
- 603991 案例修正后：take_profit 从 176.60 重算为 `154.21 × (176.6/168.19) ≈ 161.92`（+5%），7-13 high 164.42 > 161.92 → 触发 `take_profit_hit`，退出价 161.76（含退出滑点），executed_return +4.79%（相对实入场 154.36），outcome_label success。相对推荐价 168.19 为 −3.82%，但用户未按推荐价成交（次日跳空低开），故 executed_return 锚定实入场、标 success 正确（见附带问题 2 的基准口径说明）。

## 附带问题（结案）

1. **跳空低开 −8.31% 仍入场 → 数据证伪，不修。** 原假设"低开入场是问题"经历史数据验证不成立：修复止盈重算后，低开 ≤ −5% 的 7 单全部 success（平均 +5.4%），低开 ≤ −3% 区间无系统性亏损——低开本身是折扣价，后续反弹即触发重算后的止盈。胜率断崖出现在 −4%~-3% 区间（WR 61.5%，样本 26），但样本量不足以支撑阈值，且该区间失败多为 trailing/stop_loss 退出，与低开幅度无因果。603991 当时显示"亏损却标 success"的根因是止盈价 bug（已修），不是低开入场本身。故不加低开下限过滤（`MAX_ENTRY_GAP_PCT` 维持仅过滤高开 >10%）。注：A股 T+1，推荐价是信号日收盘参考价，次日开盘实际买入，跳空是常态，非异常。

2. **三套基准不统一 → 前端双基准标注，已实施（2026-07-16）。** `HistoryPage.tsx` 在跳空入场（|entry_price − recommend_price| / recommend_price > 3%）场景：①价格/风控列在推荐设计价下方补"实入场/实止损/实止盈（跳空，按实入场重算）"行；②收益列在 executed_return 旁标"相对实入场"，并补一行"X% 相对推荐价"（用 exit_price 与 recommend_price 算），颜色反映真实方向。保留双口径不强制统一——executed_return 锚定实入场是用户"按推荐下单实际赚多少"的真实口径，相对推荐价仅作信号日参考。阈值 3% 避免正常滑点（−1% 桶 316 单）误标跳空。

## 建议修复方向

- **A（核心，已实施）**：`_resolve_take_profit_price` 按实际入场价比例重算。✅ 已由 `86ad8fc` 实施；曾因 module extraction 丢失，2026-07-16 在 `outcome_simulation.py` 重新落地。
- **B（建议，已结案不修）**：跳空低开加下限过滤——数据证伪，见附带问题 1。
- **C（展示，已实施）**：前端跳空入场场景双基准标注，见附带问题 2。

## 验证数据来源

- 数据库：本地 MySQL `ashare_workbench`，`recommendation_outcomes` / `recommendations` / `daily_kbars` 表。
- 代码：`backend/app/services/outcome_service.py`（诊断时 commit `1e3d081`；修复 commit `86ad8fc`）。
- 修复影响面数据：`scripts/diagnose_take_profit_rebase.py`（`86ad8fc` 新增，只读）。

⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。
