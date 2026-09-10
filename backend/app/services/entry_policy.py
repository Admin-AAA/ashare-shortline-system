"""Explicit, versioned entry assumptions used by outcome calculations."""
from __future__ import annotations

from dataclasses import dataclass

from app.engine.signal import ENTRY_MODE_NEXT_OPEN, ENTRY_MODE_TARGET_TOUCH
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY


@dataclass(frozen=True)
class EntryPolicy:
    policy_id: str
    price_source: str
    max_entry_trade_days: int
    allow_target_touch: bool
    fallback_to_open_when_targets_miss: bool


NEXT_TRADING_DAY_OPEN = EntryPolicy(
    policy_id="next_trading_day_open_v1",
    price_source="open",
    max_entry_trade_days=1,
    allow_target_touch=False,
    fallback_to_open_when_targets_miss=True,
)

_CANONICAL_ENTRY = DEFAULT_EXECUTION_POLICY.entry
CAUSAL_STRATEGY_ENTRY = EntryPolicy(
    policy_id=_CANONICAL_ENTRY.policy_id,
    price_source=_CANONICAL_ENTRY.price_source.value,
    max_entry_trade_days=_CANONICAL_ENTRY.max_entry_trade_days,
    allow_target_touch=_CANONICAL_ENTRY.allow_target_touch,
    fallback_to_open_when_targets_miss=_CANONICAL_ENTRY.fallback_to_open_when_targets_miss,
)


def entry_policy_for_mode(entry_mode: object) -> EntryPolicy:
    """Map the persisted strategy entry contract to its executable policy."""
    normalized = str(entry_mode or ENTRY_MODE_TARGET_TOUCH).strip()
    if normalized == ENTRY_MODE_TARGET_TOUCH:
        return CAUSAL_STRATEGY_ENTRY
    if normalized == ENTRY_MODE_NEXT_OPEN:
        return NEXT_TRADING_DAY_OPEN
    raise ValueError(f"unknown entry_mode: {normalized}")

__all__ = [
    "CAUSAL_STRATEGY_ENTRY", "EntryPolicy", "NEXT_TRADING_DAY_OPEN", "entry_policy_for_mode",
]
