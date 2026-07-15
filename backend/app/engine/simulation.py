"""Deterministic, side-effect-free execution simulation and backtest metrics.

The functions in this module operate only on immutable DTOs from
``app.engine.backtest``.  They deliberately do not import ORM models, sessions,
services, configuration, or module-level execution parameters.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from app.engine.backtest.contracts import (
    BacktestErrorCode,
    BacktestMetrics,
    BacktestValidationError,
    BarInput,
    EquityPoint,
    ExecutionPolicy,
    FailureAttribution,
    FailureReasonCode,
    RecommendationInput,
    SampleConfidence,
    Trade,
    TradeOutcome,
    canonical_fingerprint,
)


@dataclass(frozen=True)
class SimulationResult:
    """The fully deterministic result for one recommendation execution attempt."""

    trade: Trade
    entry_triggered: bool
    input_fingerprint: str

    @property
    def status(self) -> str:
        return "executed" if self.entry_triggered else "not_executed"


# Naming this alias keeps the simulation boundary explicit while retaining one
# authoritative policy value object for application callers.
SimulationPolicy = ExecutionPolicy


def _ordered_bars(recommendation: RecommendationInput, bars: Iterable[BarInput]) -> tuple[BarInput, ...]:
    normalized = tuple(bars)
    if not all(isinstance(bar, BarInput) for bar in normalized):
        raise BacktestValidationError(
            BacktestErrorCode.INVALID_SIMULATION_INPUT,
            "bars must contain only BarInput values",
        )
    filtered = tuple(bar for bar in normalized if bar.code == recommendation.code and bar.trade_date > recommendation.signal_date)
    dates = tuple(bar.trade_date for bar in filtered)
    if len(set(dates)) != len(dates):
        raise BacktestValidationError(
            BacktestErrorCode.INVALID_SIMULATION_INPUT,
            "bars must not contain duplicate dates for the recommendation code",
        )
    # Sorting means adapter query order can never change a historical result.
    return tuple(sorted(filtered, key=lambda bar: bar.trade_date))


def _entry_bar(
    recommendation: RecommendationInput,
    bars: tuple[BarInput, ...],
    policy: ExecutionPolicy,
) -> tuple[int, BarInput] | None:
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


def _exit_for_bar(
    bar: BarInput,
    *,
    entry_price: float,
    policy: ExecutionPolicy,
) -> tuple[float, str] | None:
    stop_price = entry_price * (1 - policy.stop_loss_pct / 100) if policy.stop_loss_pct is not None else None
    take_profit_price = entry_price * (1 + policy.take_profit_pct / 100) if policy.take_profit_pct is not None else None

    stop_exit: tuple[float, str] | None = None
    if stop_price is not None:
        if bar.open <= stop_price:
            stop_exit = (bar.open, "stop_loss_gap")
        elif bar.low <= stop_price:
            stop_exit = (stop_price, "stop_loss")

    take_profit_exit: tuple[float, str] | None = None
    if take_profit_price is not None:
        if bar.open >= take_profit_price:
            take_profit_exit = (bar.open, "take_profit_gap")
        elif bar.high >= take_profit_price:
            take_profit_exit = (take_profit_price, "take_profit")

    if stop_exit is not None and take_profit_exit is not None:
        return stop_exit if policy.intraday_exit_priority.value == "stop_loss" else take_profit_exit
    return stop_exit or take_profit_exit


def simulate(
    recommendation: RecommendationInput,
    bars: tuple[BarInput, ...] | Iterable[BarInput],
    policy: SimulationPolicy,
) -> SimulationResult:
    """Simulate one recommendation using only future OHLC bars supplied by the caller.

    Entry occurs at an eligible next-bar open plus entry slippage.  Stops/take
    profits are evaluated in a documented conservative order, and final exits
    use the horizon bar close.  Returns are percentage points; commission and
    stamp tax are deducted after execution slippage.
    """

    if not isinstance(recommendation, RecommendationInput):
        raise BacktestValidationError(
            BacktestErrorCode.INVALID_SIMULATION_INPUT,
            "recommendation must be a RecommendationInput",
        )
    if not isinstance(policy, ExecutionPolicy):
        raise BacktestValidationError(
            BacktestErrorCode.INVALID_EXECUTION_POLICY,
            "policy must be an ExecutionPolicy",
        )

    ordered = _ordered_bars(recommendation, bars)
    entry = _entry_bar(recommendation, ordered, policy)
    input_fingerprint = canonical_fingerprint(
        {"recommendation": recommendation, "bars": ordered, "policy": policy}
    )
    if entry is None:
        return SimulationResult(
            trade=Trade(
                code=recommendation.code,
                signal_date=recommendation.signal_date,
                entry_date=None,
                exit_date=None,
                entry_price=None,
                exit_price=None,
                gross_return_pct=0.0,
                net_return_pct=0.0,
                position_size=0.0,
                holding_days=0,
                outcome_label=TradeOutcome.NOT_EXECUTED,
                exit_reason="entry_not_filled",
            ),
            entry_triggered=False,
            input_fingerprint=input_fingerprint,
        )

    entry_index, entry_bar = entry
    entry_price = round(entry_bar.open * (1 + policy.entry_slippage_bps / 10_000), 10)
    holding_bars = ordered[entry_index : entry_index + policy.horizon_days]
    # An entry bar is always part of the selected horizon.
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
    gross_return_pct = round((exit_price / entry_price - 1) * 100, 10)
    net_return_pct = round(gross_return_pct - policy.fee_pct, 10)
    outcome = TradeOutcome.SUCCESS if net_return_pct > 0 else TradeOutcome.FAILED
    return SimulationResult(
        trade=Trade(
            code=recommendation.code,
            signal_date=recommendation.signal_date,
            entry_date=entry_bar.trade_date,
            exit_date=exit_date,
            entry_price=entry_price,
            exit_price=exit_price,
            gross_return_pct=gross_return_pct,
            net_return_pct=net_return_pct,
            position_size=policy.position_size,
            holding_days=holding_days,
            outcome_label=outcome,
            exit_reason=exit_reason,
        ),
        entry_triggered=True,
        input_fingerprint=input_fingerprint,
    )


def build_equity_curve(trades: Iterable[Trade], *, initial_equity: float = 1.0) -> tuple[EquityPoint, ...]:
    """Aggregate execution exits into a deterministic daily, normalized equity curve."""

    if initial_equity <= 0:
        raise BacktestValidationError(
            BacktestErrorCode.INVALID_SIMULATION_INPUT,
            "initial_equity must be positive",
        )
    returns_by_date: dict[str, float] = defaultdict(float)
    for trade in trades:
        if not isinstance(trade, Trade):
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "trades must contain Trade values")
        if trade.executed:
            assert trade.exit_date is not None  # Guaranteed by Trade validation.
            returns_by_date[trade.exit_date] += trade.portfolio_return_pct

    equity = float(initial_equity)
    peak = equity
    points: list[EquityPoint] = []
    for trade_date in sorted(returns_by_date):
        daily_return_pct = returns_by_date[trade_date]
        equity *= 1 + daily_return_pct / 100
        peak = max(peak, equity)
        drawdown_pct = (peak - equity) / peak * 100 if peak else 0.0
        points.append(
            EquityPoint(
                trade_date=trade_date,
                equity=equity,
                daily_return_pct=daily_return_pct,
                drawdown_pct=drawdown_pct,
            )
        )
    return tuple(points)


def _confidence(executed_count: int) -> SampleConfidence:
    if executed_count < 10:
        return SampleConfidence.INSUFFICIENT
    if executed_count < 30:
        return SampleConfidence.LOW
    if executed_count < 100:
        return SampleConfidence.MEDIUM
    return SampleConfidence.HIGH


def compute_metrics(
    trades: Iterable[Trade],
    *,
    initial_equity: float = 1.0,
) -> BacktestMetrics:
    """Compute pure, reproducible performance metrics from isolated trade DTOs."""

    ordered = tuple(trades)
    if not all(isinstance(trade, Trade) for trade in ordered):
        raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "trades must contain Trade values")
    executed = tuple(trade for trade in ordered if trade.executed)
    returns = tuple(trade.net_return_pct for trade in executed)
    wins = tuple(value for value in returns if value > 0)
    losses = tuple(value for value in returns if value < 0)
    equity_curve = build_equity_curve(executed, initial_equity=initial_equity)
    final_equity = equity_curve[-1].equity if equity_curve else initial_equity
    total_return_pct = (final_equity / initial_equity - 1) * 100
    profit_loss_ratio = sum(wins) / abs(sum(losses)) if losses else None
    return BacktestMetrics(
        sample_count=len(ordered),
        executed_trade_count=len(executed),
        unexecuted_count=len(ordered) - len(executed),
        win_count=len(wins),
        loss_count=len(losses),
        win_rate_pct=len(wins) * 100 / len(executed) if executed else 0.0,
        average_net_return_pct=sum(returns) / len(returns) if returns else 0.0,
        profit_loss_ratio=profit_loss_ratio,
        total_net_return_pct=total_return_pct,
        max_drawdown_pct=max((point.drawdown_pct for point in equity_curve), default=0.0),
        confidence=_confidence(len(executed)),
    )


def attribute_failure(trade: Trade) -> FailureAttribution | None:
    """Create a minimal deterministic attribution only for executed losing trades."""

    if not trade.executed or trade.net_return_pct >= 0:
        return None
    reason_text = (trade.exit_reason or "").lower()
    if reason_text.startswith("stop_loss"):
        reason = FailureReasonCode.STOP_LOSS
        confidence = 1.0
    elif "gap" in reason_text or "chase" in reason_text:
        reason = FailureReasonCode.CHASE_HIGH
        confidence = 0.7
    elif "execution" in reason_text or "untradable" in reason_text:
        reason = FailureReasonCode.EXECUTION_FAILURE
        confidence = 0.7
    else:
        reason = FailureReasonCode.UNKNOWN
        confidence = 0.0
    return FailureAttribution(
        trade_id=trade.trade_id or "",
        reason_code=reason,
        rule_or_factor_reference=trade.exit_reason,
        confidence=confidence,
        unknown=reason is FailureReasonCode.UNKNOWN,
    )


def attribute_failures(trades: Iterable[Trade]) -> tuple[FailureAttribution, ...]:
    """Return ordered failure attributions, excluding no-signal and non-loss samples."""

    return tuple(
        attribution
        for attribution in (attribute_failure(trade) for trade in trades)
        if attribution is not None
    )


__all__ = [
    "SimulationPolicy",
    "SimulationResult",
    "attribute_failure",
    "attribute_failures",
    "build_equity_curve",
    "compute_metrics",
    "simulate",
]
