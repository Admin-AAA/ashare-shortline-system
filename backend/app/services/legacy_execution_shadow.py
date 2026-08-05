"""Versioned legacy execution adapter for shadow parity only.

This is the audited pre-canonical ``engine/simulation.py`` behavior from HEAD
9d81483. It is intentionally not imported by formal/backtest production paths;
its only consumer is the parity harness, where it represents the historical
authoritative result under the same immutable DTO input.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.engine.backtest.contracts import (
    BacktestErrorCode,
    BacktestValidationError,
    BarInput,
    ExecutionPolicy,
    RecommendationInput,
    Trade,
    TradeOutcome,
)
from app.engine.canonical import canonical_fingerprint

LEGACY_EXECUTION_ADAPTER_VERSION = "engine-simulation@HEAD-9d81483"


def _ordered_bars(recommendation: RecommendationInput, bars: Iterable[BarInput]) -> tuple[BarInput, ...]:
    normalized = tuple(bars)
    if not all(isinstance(bar, BarInput) for bar in normalized):
        raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "bars must contain only BarInput values")
    filtered = tuple(
        bar for bar in normalized
        if bar.code == recommendation.code and bar.trade_date > recommendation.signal_date
    )
    dates = tuple(bar.trade_date for bar in filtered)
    if len(set(dates)) != len(dates):
        raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "bars must not contain duplicate dates")
    return tuple(sorted(filtered, key=lambda bar: bar.trade_date))


def _entry_bar(recommendation: RecommendationInput, bars: tuple[BarInput, ...], policy: ExecutionPolicy):
    for index, bar in enumerate(bars[: policy.entry_lookahead_days]):
        if not bar.tradable:
            if policy.skip_untradable_entry_bars:
                continue
            return None
        if policy.max_entry_gap_pct is not None:
            gap_pct = (bar.open / recommendation.signal_price - 1) * 100
            if gap_pct > policy.max_entry_gap_pct:
                continue
        return index, bar
    return None


def _exit_for_bar(bar: BarInput, *, entry_price: float, policy: ExecutionPolicy):
    stop_price = entry_price * (1 - policy.stop_loss_pct / 100) if policy.stop_loss_pct is not None else None
    take_profit_price = entry_price * (1 + policy.take_profit_pct / 100) if policy.take_profit_pct is not None else None
    stop_exit = None
    if stop_price is not None:
        if bar.open <= stop_price:
            stop_exit = (bar.open, "stop_loss_gap")
        elif bar.low <= stop_price:
            stop_exit = (stop_price, "stop_loss")
    take_profit_exit = None
    if take_profit_price is not None:
        if bar.open >= take_profit_price:
            take_profit_exit = (bar.open, "take_profit_gap")
        elif bar.high >= take_profit_price:
            take_profit_exit = (take_profit_price, "take_profit")
    if stop_exit is not None and take_profit_exit is not None:
        return stop_exit if policy.intraday_exit_priority.value == "stop_loss" else take_profit_exit
    return stop_exit or take_profit_exit


def simulate_legacy_shadow(
    recommendation: RecommendationInput,
    bars: tuple[BarInput, ...] | Iterable[BarInput],
    policy: ExecutionPolicy,
) -> Trade:
    """Return the historical HEAD trade projection; never persists or mutates."""
    if not isinstance(recommendation, RecommendationInput):
        raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "recommendation must be a RecommendationInput")
    if not isinstance(policy, ExecutionPolicy):
        raise BacktestValidationError(BacktestErrorCode.INVALID_EXECUTION_POLICY, "policy must be an ExecutionPolicy")
    ordered = _ordered_bars(recommendation, bars)
    entry = _entry_bar(recommendation, ordered, policy)
    if entry is None:
        return Trade(
            code=recommendation.code, signal_date=recommendation.signal_date,
            entry_date=None, exit_date=None, entry_price=None, exit_price=None,
            gross_return_pct=0.0, net_return_pct=0.0, position_size=0.0,
            holding_days=0, outcome_label=TradeOutcome.NOT_EXECUTED,
            exit_reason="entry_not_filled",
        )
    entry_index, entry_bar = entry
    entry_price = round(entry_bar.open * (1 + policy.entry_slippage_bps / 10_000), 10)
    holding_bars = ordered[entry_index:entry_index + policy.horizon_days]
    raw_exit_price = holding_bars[-1].close
    exit_date = holding_bars[-1].trade_date
    exit_reason = "horizon"
    holding_days = len(holding_bars)
    for offset, bar in enumerate(holding_bars):
        resolution = _exit_for_bar(bar, entry_price=entry_price, policy=policy)
        if resolution is None:
            continue
        raw_exit_price, exit_reason = resolution
        exit_date = bar.trade_date
        holding_days = offset + 1
        break
    exit_price = round(raw_exit_price * (1 - policy.exit_slippage_bps / 10_000), 10)
    gross = round((exit_price / entry_price - 1) * 100, 10)
    net = round(gross - policy.fee_pct, 10)
    return Trade(
        code=recommendation.code, signal_date=recommendation.signal_date,
        entry_date=entry_bar.trade_date, exit_date=exit_date,
        entry_price=entry_price, exit_price=exit_price,
        gross_return_pct=gross, net_return_pct=net,
        position_size=policy.position_size, holding_days=holding_days,
        outcome_label=TradeOutcome.SUCCESS if net > 0 else TradeOutcome.FAILED,
        exit_reason=exit_reason,
    )


__all__ = ["LEGACY_EXECUTION_ADAPTER_VERSION", "simulate_legacy_shadow"]
