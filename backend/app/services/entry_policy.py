"""Explicit, versioned entry assumptions used by outcome calculations."""
from __future__ import annotations

from dataclasses import dataclass, replace

from app.engine.signal import ENTRY_MODE_NEXT_OPEN, ENTRY_MODE_TARGET_TOUCH
from app.engine.execution_policy import (
    DEFAULT_EXECUTION_POLICY,
    EntryPriceSource,
    EntryWindowPolicy,
    ExecutionPolicy,
)


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


def execution_policy_fingerprint_for_entry_policy(entry_policy_id: object) -> str:
    """Fingerprint the complete execution contract actually used for entry."""

    normalized = str(entry_policy_id or "").strip()
    if normalized == CAUSAL_STRATEGY_ENTRY.policy_id:
        return DEFAULT_EXECUTION_POLICY.fingerprint()
    if normalized == NEXT_TRADING_DAY_OPEN.policy_id:
        return execution_policy_for_next_open_entry().fingerprint()
    raise ValueError(f"unknown entry_policy_id: {normalized}")


def execution_policy_for_next_open_entry() -> ExecutionPolicy:
    """出场、费用、仓位沿用当前版本，只把 entry 换成次日开盘。"""

    return replace(
        DEFAULT_EXECUTION_POLICY,
        entry=EntryWindowPolicy(
            policy_id=NEXT_TRADING_DAY_OPEN.policy_id,
            price_source=EntryPriceSource.NEXT_TRADING_DAY_OPEN,
            max_entry_trade_days=NEXT_TRADING_DAY_OPEN.max_entry_trade_days,
            allow_target_touch=NEXT_TRADING_DAY_OPEN.allow_target_touch,
            fallback_to_open_when_targets_miss=(
                NEXT_TRADING_DAY_OPEN.fallback_to_open_when_targets_miss
            ),
            skip_untradable_bars=DEFAULT_EXECUTION_POLICY.entry.skip_untradable_bars,
        ),
    )


def execution_policy_for_entry_mode(entry_mode: object) -> ExecutionPolicy:
    """按策略声明的入场方式返回完整执行合约。"""

    entry = entry_policy_for_mode(entry_mode)
    if entry.policy_id == NEXT_TRADING_DAY_OPEN.policy_id:
        return execution_policy_for_next_open_entry()
    return DEFAULT_EXECUTION_POLICY


__all__ = [
    "CAUSAL_STRATEGY_ENTRY",
    "EntryPolicy",
    "NEXT_TRADING_DAY_OPEN",
    "entry_policy_for_mode",
    "execution_policy_for_entry_mode",
    "execution_policy_for_next_open_entry",
    "execution_policy_fingerprint_for_entry_policy",
]
