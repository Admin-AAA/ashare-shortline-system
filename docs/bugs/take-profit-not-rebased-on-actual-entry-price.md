# Bug 报告：止盈价未随实际入场价重算，跳空低开后固定止盈路径失效

> 状态：**已修复**（main commit `86ad8fc`，2026-07-13 23:26，self-improve 实例）。
> 本报告作为 bug 历史档案归档，记录根因、数据链路与影响面；附带问题 1/2 仍开放。
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
- 603991 案例修正后：take_profit 从 176.60 重算为 `154.21 × (176.6/168.19) = 161.88`（+5%），7-13 high 164.42 > 161.88 → 触发 `take_profit_hit`，退出价 161.88，收益 +4.88%，相对推荐价 −3.76%，如实标 failed。

## 附带问题（仍开放）

1. **跳空低开 −8.31% 仍入场**。`MAX_ENTRY_GAP_PCT=10`（行 40，调用处行 1770–1772）只过滤 `gap_pct > 10`（高开），低开完全不过滤。`entry_rule="等待买点确认后再参与"`，7-13 开盘 154.21 已跌破所有买点（ideal 165.67 / 次买 163.14），却仍 `entry_triggered=1`。这是前端显示"未命中买点"但 outcome 有持仓的直接矛盾来源。`86ad8fc` 未触及此问题。

2. **一条记录三套基准**。静态指标（`max_return` / `close_return`）按 `recommend_price=168.19` 算（见 `_static_window_metrics`，行 1893 `baseline_entry = rec.recommend_price`），`executed_return` 按 `executed_entry_price=154.36` 算，前端展示的止盈/止损又是推荐时的绝对价。复盘页用户对不上是必然的。`86ad8fc` 修复了 take_profit 的执行基准，但前端展示基准未统一。

## 建议修复方向

- **A（核心，已实施）**：`_resolve_take_profit_price` 按实际入场价比例重算。✅ 已由 `86ad8fc` 实施。
- **B（建议，未实施）**：跳空低开加下限过滤（如 `gap < −6%` 放弃入场），或"未命中买点"时直接不建仓。
- **C（展示，未实施）**：前端在跳空入场场景标注"实际入场价 ¥154.36，目标已按比例重算"，把三套基准统一。

## 验证数据来源

- 数据库：本地 MySQL `ashare_workbench`，`recommendation_outcomes` / `recommendations` / `daily_kbars` 表。
- 代码：`backend/app/services/outcome_service.py`（诊断时 commit `1e3d081`；修复 commit `86ad8fc`）。
- 修复影响面数据：`scripts/diagnose_take_profit_rebase.py`（`86ad8fc` 新增，只读）。

⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。
