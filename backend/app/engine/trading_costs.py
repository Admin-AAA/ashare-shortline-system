"""A 股短线交易成本的唯一口径。"""
from __future__ import annotations

COMMISSION_RATE_PCT = 0.02
STAMP_TAX_RATE_PCT = 0.05
ADDITIONAL_COST_PCT = COMMISSION_RATE_PCT + STAMP_TAX_RATE_PCT


def is_net_profitable(gross_return_pct: float) -> bool:
    """滑点后、尚未扣税费的收益必须严格覆盖额外交易成本。"""
    return float(gross_return_pct) > ADDITIONAL_COST_PCT


__all__ = [
    "ADDITIONAL_COST_PCT",
    "COMMISSION_RATE_PCT",
    "STAMP_TAX_RATE_PCT",
    "is_net_profitable",
]
