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

# 按通过上线标准的回测口径入场（2026-10-27 用户定）：§282 BASE 回测走
# PE._simulate_card → execution_core.execute_trade/_resolve_entry，只按开盘价买：入场窗口内
# 跳过不可成交 / 一字涨停 / 开盘相对推荐价 > max_entry_gap_pct 的日子，第一个可买日开盘买入，
# 不看理想/次级买点触碰。实盘结算与盘中确认对这些策略用同一规则。碰买点规则若要再用，需另行预注册检验。
BACKTEST_OPEN_WINDOW = EntryPolicy(
    policy_id="backtest_open_window_v1",
    price_source="open_window",
    max_entry_trade_days=int(DEFAULT_EXECUTION_POLICY.entry_lookahead_days),
    allow_target_touch=False,
    fallback_to_open_when_targets_miss=True,
)
_BACKTEST_OPEN_WINDOW_STRATEGY_IDS = frozenset({
    "sector-relative-strength-leader",
})


def entry_policy_for_mode(
    entry_mode: object,
    *,
    legacy_strategy_id: object = None,
    backtest_open_window: bool = True,
) -> EntryPolicy:
    """Map persisted entry metadata to an executable policy.

    backtest_open_window=False：结算规则 r20261019 及更早的口径（板块龙头仍走碰买点），
    只给「按记录自己的旧版本重算」用。

    New recommendations own an explicit ``entry_mode`` in their sealed snapshot.
    Historical recommendations predate that field, so the two momentum/relay
    strategies are deterministically migrated by their persisted bucket without
    mutating the sealed recommendation evidence.
    """
    normalized = str(entry_mode or "").strip()
    strategy_id = str(legacy_strategy_id or "").strip()
    if (backtest_open_window and strategy_id in _BACKTEST_OPEN_WINDOW_STRATEGY_IDS
            and normalized != ENTRY_MODE_NEXT_OPEN):
        return BACKTEST_OPEN_WINDOW
    if not normalized:
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
    if normalized in {CAUSAL_STRATEGY_ENTRY.policy_id, BACKTEST_OPEN_WINDOW.policy_id}:
        # BACKTEST_OPEN_WINDOW 就是默认执行合约在回测路径（execute_trade）里的入场行为
        return DEFAULT_EXECUTION_POLICY.fingerprint()
    if normalized == NEXT_TRADING_DAY_OPEN.policy_id:
        return execution_policy_for_next_open_entry().fingerprint()
    raise ValueError(f"unknown entry_policy_id: {normalized}")


def execution_policy_for_next_open_entry() -> ExecutionPolicy:
    """出场、费用、仓位沿用当前版本，只把 entry 换成次日开盘。"""

    return execution_policy_with_entry_mode(DEFAULT_EXECUTION_POLICY, ENTRY_MODE_NEXT_OPEN)


def entry_window_policy_for_mode(
    entry_mode: object,
    *,
    template: EntryWindowPolicy | None = None,
    legacy_strategy_id: object = None,
) -> EntryWindowPolicy:
    """按策略声明的入场方式给出 entry 段；其余 entry 旋钮沿用 template。"""

    base = template or DEFAULT_EXECUTION_POLICY.entry
    entry = entry_policy_for_mode(
        entry_mode,
        legacy_strategy_id=legacy_strategy_id,
    )
    if entry.policy_id == NEXT_TRADING_DAY_OPEN.policy_id:
        return EntryWindowPolicy(
            policy_id=NEXT_TRADING_DAY_OPEN.policy_id,
            price_source=EntryPriceSource.NEXT_TRADING_DAY_OPEN,
            max_entry_trade_days=NEXT_TRADING_DAY_OPEN.max_entry_trade_days,
            allow_target_touch=NEXT_TRADING_DAY_OPEN.allow_target_touch,
            fallback_to_open_when_targets_miss=(
                NEXT_TRADING_DAY_OPEN.fallback_to_open_when_targets_miss
            ),
            skip_untradable_bars=base.skip_untradable_bars,
        )
    return base


def execution_policy_with_entry_mode(
    policy: ExecutionPolicy,
    entry_mode: object,
    *,
    legacy_strategy_id: object = None,
) -> ExecutionPolicy:
    """把策略声明的入场方式套到给定执行合约上，出场、费用、仓位保持不变。"""

    return replace(
        policy,
        entry=entry_window_policy_for_mode(
            entry_mode,
            template=policy.entry,
            legacy_strategy_id=legacy_strategy_id,
        ),
    )


def execution_policy_for_entry_mode(entry_mode: object) -> ExecutionPolicy:
    """按策略声明的入场方式返回当前版本的完整执行合约。"""

    return execution_policy_with_entry_mode(DEFAULT_EXECUTION_POLICY, entry_mode)


__all__ = [
    "BACKTEST_OPEN_WINDOW",
    "CAUSAL_STRATEGY_ENTRY",
    "EntryPolicy",
    "NEXT_TRADING_DAY_OPEN",
    "entry_policy_for_mode",
    "entry_window_policy_for_mode",
    "execution_policy_for_entry_mode",
    "execution_policy_for_next_open_entry",
    "execution_policy_with_entry_mode",
    "execution_policy_fingerprint_for_entry_policy",
]
