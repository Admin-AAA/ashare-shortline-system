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

_LEGACY_NEXT_OPEN_STRATEGY_IDS = frozenset({
    "chen-xiaoqun-skill",
    "dragon-tiger-smart-money",
})


def entry_policy_for_mode(
    entry_mode: object,
    *,
    legacy_strategy_id: object = None,
) -> EntryPolicy:
    """Map persisted entry metadata to an executable policy.

    New recommendations own an explicit ``entry_mode`` in their sealed snapshot.
    Historical recommendations predate that field, so the two momentum/relay
    strategies are deterministically migrated by their persisted bucket without
    mutating the sealed recommendation evidence.
    """
    normalized = str(entry_mode or "").strip()
    if not normalized:
        strategy_id = str(legacy_strategy_id or "").strip()
        normalized = (
            ENTRY_MODE_NEXT_OPEN
            if strategy_id in _LEGACY_NEXT_OPEN_STRATEGY_IDS
            else ENTRY_MODE_TARGET_TOUCH
        )
    if normalized == ENTRY_MODE_TARGET_TOUCH:
        return CAUSAL_STRATEGY_ENTRY
    if normalized == ENTRY_MODE_NEXT_OPEN:
        return NEXT_TRADING_DAY_OPEN
    raise ValueError(f"unknown entry_mode: {normalized}")

__all__ = [
    "CAUSAL_STRATEGY_ENTRY", "EntryPolicy", "NEXT_TRADING_DAY_OPEN", "entry_policy_for_mode",
]
