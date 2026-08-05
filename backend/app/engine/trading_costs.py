"""A 股短线交易成本投影；数值唯一来源为 canonical ExecutionPolicy。"""
from __future__ import annotations

from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY

_FEES = DEFAULT_EXECUTION_POLICY.fees
COMMISSION_RATE_PCT = round(_FEES.commission_rate_per_side * 2 * 100, 10)
STAMP_TAX_RATE_PCT = round(_FEES.stamp_tax_rate_on_exit * 100, 10)
ADDITIONAL_COST_PCT = round(_FEES.round_trip_pct, 10)


def is_net_profitable(gross_return_pct: float) -> bool:
    """滑点后、尚未扣税费的收益必须严格覆盖 canonical round-trip costs。"""

    return float(gross_return_pct) > ADDITIONAL_COST_PCT


__all__ = [
    "ADDITIONAL_COST_PCT",
    "COMMISSION_RATE_PCT",
    "STAMP_TAX_RATE_PCT",
    "is_net_profitable",
]
