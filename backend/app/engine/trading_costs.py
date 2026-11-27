"""A 股短线交易成本投影；数值唯一来源为 canonical ExecutionPolicy。"""
from __future__ import annotations

from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY

_FEES = DEFAULT_EXECUTION_POLICY.fees
COMMISSION_RATE_PCT = round(_FEES.commission_rate_per_side * 2 * 100, 10)
STAMP_TAX_RATE_PCT = round(_FEES.stamp_tax_rate_on_exit * 100, 10)
ADDITIONAL_COST_PCT = round(_FEES.round_trip_pct, 10)
# 两个周期的成本口径统一到同一来源（L-229）：
# 短线：执行引擎在成交价里各吃 entry_bps/exit_bps 的滑点，再从收益里扣 ADDITIONAL_COST_PCT 税费；
# 中长线：按开盘价结算，没有逐笔滑点模型，整体扣「税费 + 同一政策的双边滑点」。
# 两边的往返总成本因此相等，都来自 DEFAULT_EXECUTION_POLICY（当前 0.07 + 0.20 = 0.27）。
# 2026-11-27 用 5min 数据实测中线开盘价对首 5 分钟 VWAP 的滑点均值 −0.02%（n=1629），0.20% 是保守假设。
_SLIPPAGE = DEFAULT_EXECUTION_POLICY.slippage
SLIPPAGE_ROUND_TRIP_PCT = round((_SLIPPAGE.entry_bps + _SLIPPAGE.exit_bps) / 100.0, 10)
TOTAL_ROUND_TRIP_COST_PCT = round(ADDITIONAL_COST_PCT + SLIPPAGE_ROUND_TRIP_PCT, 10)


def is_net_profitable(gross_return_pct: float) -> bool:
    """滑点后、尚未扣税费的收益必须严格覆盖 canonical round-trip costs。"""

    return float(gross_return_pct) > ADDITIONAL_COST_PCT


def net_return_after_cost(gross_return_pct: float) -> float:
    """Convert the simulator's slippage-adjusted gross return to canonical net return."""

    return round(float(gross_return_pct) - ADDITIONAL_COST_PCT, 10)


__all__ = [
    "ADDITIONAL_COST_PCT",
    "SLIPPAGE_ROUND_TRIP_PCT",
    "TOTAL_ROUND_TRIP_COST_PCT",
    "COMMISSION_RATE_PCT",
    "STAMP_TAX_RATE_PCT",
    "is_net_profitable",
    "net_return_after_cost",
]
