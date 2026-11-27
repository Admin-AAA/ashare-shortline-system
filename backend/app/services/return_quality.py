"""策略收益质量指标（纯计算，无 IO、无副作用）。

背景（2026-11-15 评审）：系统原有指标是「每笔均值 / 胜率 / PF / 单笔 MDD」，
这个口径无法区分「稳定小赚」和「靠少数几笔翻回来」。`quality_metrics` 补上去两端均值、
前 10 笔利润占比和尾部离散，**只增字段、不改既有字段**。

账户层面的组合收益与回撤不在这里：唯一模型是 app/services/overlap_portfolio.py
（交易日历固定槽位、空槽算现金、走完全部持有期）。原 `portfolio_metrics` 以信号日为时间轴、
止于最后一个信号日、按当天覆盖的批数均分资金，同一批中线交易报 +7.2% / 回撤 −12.5%，
同成本的交易日历模型是 −0.28%，2026-11-27 删除（L-203 修订）。

口径约定：
- 收益单位与调用方一致（通常为「百分比」，即 4.0 表示 +4%）。
- 样本不足时返回 None 而不是 0，避免把「没数据」误读成「零收益」。
"""
from __future__ import annotations

from statistics import median, pstdev
from typing import Any, Sequence

# 去两端需要的样本量；低于此值不做 trim（否则丢两笔就改变分布形状）
_TRIM_MIN_N = 20


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


def quality_metrics(rets: Sequence[float], *, trim_pct: float = 0.05) -> dict[str, Any]:
    """收益质量。输入是每笔收益（百分比），顺序无关。

    返回字段：
    - `trim_avg_ret`：去掉最大 / 最小各 trim_pct 后的均值。与 avg 差得越远，
      说明收益越依赖尾部；转负即「去掉极端交易后不赚钱」。
    - `top10_profit_share_pct`：最大的 10 笔之和 / **净利润** × 100。
      >100% 表示这 10 笔赚的比最终利润还多（其余净亏），即彩票型。
      净利润 ≤0 时无意义，返回 None（此时应直接看 trim_avg_ret < 0）。
    - `top10_share_of_gross_pct`：最大的 10 笔之和 / **全部盈利之和** × 100。
      这是纯集中度，恒在 0~100%，用于区分「利润厚」与「利润薄但同样集中」。
    - `median_ret`：中位数。明显高于 avg 时，均值被左尾大亏拖累。
    - `ret_std` / `worst_ret` / `best_ret`：尾部形状。
    """
    vals = [float(x) for x in rets if x is not None]
    n = len(vals)
    if n == 0:
        return {
            "trim_avg_ret": None,
            "top10_profit_share_pct": None,
            "top10_share_of_gross_pct": None,
            "median_ret": None,
            "ret_std": None,
            "worst_ret": None,
            "best_ret": None,
        }
    desc = sorted(vals, reverse=True)
    if n >= _TRIM_MIN_N:
        k = max(1, int(round(n * trim_pct)))
        trimmed = desc[k : n - k] if n - 2 * k > 0 else desc
    else:
        trimmed = desc
    gross_pos = sum(v for v in vals if v > 0)
    net = sum(vals)
    top10 = sum(desc[: min(10, n)])
    return {
        "trim_avg_ret": _r(sum(trimmed) / len(trimmed)),
        # 对净利润取占比：只有这样才能识别「前 10 笔赚的比总利润还多」
        "top10_profit_share_pct": _r(100 * top10 / net, 1) if net > 0 else None,
        # 对盈利总额取占比：纯集中度，恒 ≤100%
        "top10_share_of_gross_pct": _r(100 * top10 / gross_pos, 1) if gross_pos > 0 else None,
        "median_ret": _r(median(vals)),
        "ret_std": _r(pstdev(vals), 2) if n > 1 else None,
        "worst_ret": _r(min(vals)),
        "best_ret": _r(max(vals)),
    }
