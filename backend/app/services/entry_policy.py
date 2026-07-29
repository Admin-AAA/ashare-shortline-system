"""Explicit, versioned entry assumptions used by outcome calculations."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntryPolicy:
    policy_id: str
    price_source: str
    max_entry_trade_days: int
    allow_target_touch: bool


NEXT_TRADING_DAY_OPEN = EntryPolicy(
    policy_id="next_trading_day_open_v1",
    price_source="open",
    max_entry_trade_days=1,
    allow_target_touch=False,
)

LEGACY_MIXED_ENTRY = EntryPolicy(
    policy_id="legacy_mixed_entry_v1",
    price_source="target_then_open",
    max_entry_trade_days=2,
    allow_target_touch=True,
)


__all__ = ["EntryPolicy", "LEGACY_MIXED_ENTRY", "NEXT_TRADING_DAY_OPEN"]
