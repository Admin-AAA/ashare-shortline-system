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
)
from app.engine.execution_core import (
    CanonicalExecutionError,
    ExecutionBar,
    ExecutionIntent,
    ExecutionRequest,
    ExecutionStatus,
    IntradayExitEvidence,
    execute_trade,
)


@dataclass(frozen=True)
class SimulationResult:
    """The fully deterministic result for one recommendation execution attempt."""

    trade: Trade
    entry_triggered: bool
    input_fingerprint: str
    intraday_exit_evidence: IntradayExitEvidence | None

    @property
    def status(self) -> str:
        if self.trade.executed:
            return "executed"
        return "open_t1_locked" if self.entry_triggered else "not_executed"


# Naming this alias keeps the simulation boundary explicit while retaining one
# authoritative policy value object for application callers.
SimulationPolicy = ExecutionPolicy


def _to_execution_request(
    recommendation: RecommendationInput,
    bars: tuple[BarInput, ...] | Iterable[BarInput],
) -> ExecutionRequest:
    normalized = tuple(bars)
    if not all(isinstance(bar, BarInput) for bar in normalized):
        raise BacktestValidationError(
            BacktestErrorCode.INVALID_SIMULATION_INPUT,
            "bars must contain only BarInput values",
        )
    return ExecutionRequest(
        intent=ExecutionIntent(
            code=recommendation.code,
            signal_date=recommendation.signal_date,
            signal_price=recommendation.signal_price,
            recommendation_id=recommendation.recommendation_id,
        ),
        bars=tuple(
            ExecutionBar(
                code=bar.code,
                trade_date=bar.trade_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                tradable=bar.tradable,
                sellable=bar.tradable,
            )
            for bar in normalized
        ),
    )


def simulate(
    recommendation: RecommendationInput,
    bars: tuple[BarInput, ...] | Iterable[BarInput],
    policy: SimulationPolicy,
) -> SimulationResult:
    """Adapt research DTOs to the single canonical execution core."""
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
    try:
        result = execute_trade(_to_execution_request(recommendation, bars), policy)
    except CanonicalExecutionError as exc:
        raise BacktestValidationError(
            BacktestErrorCode.INVALID_SIMULATION_INPUT,
            str(exc),
        ) from exc

    if result.status is not ExecutionStatus.CLOSED:
        reason = (
            "horizon_close_t1_locked"
            if result.status is ExecutionStatus.OPEN_T1_LOCKED
            else result.entry.reason
        )
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
                exit_reason=reason,
            ),
            entry_triggered=result.entry.triggered,
            input_fingerprint=result.input_fingerprint,
            intraday_exit_evidence=None,
        )

    assert result.entry.trade_date is not None
    assert result.entry.executed_price is not None
    assert result.exit is not None
    outcome = (
        TradeOutcome.SUCCESS
        if result.net_return_pct > 0
        else TradeOutcome.FAILED
    )
    return SimulationResult(
        trade=Trade(
            code=recommendation.code,
            signal_date=recommendation.signal_date,
            entry_date=result.entry.trade_date,
            exit_date=result.exit.trade_date,
            entry_price=result.entry.executed_price,
            exit_price=result.exit.executed_price,
            gross_return_pct=result.gross_return_pct,
            net_return_pct=result.net_return_pct,
            position_size=result.position_size,
            holding_days=result.exit.holding_days,
            outcome_label=outcome,
            exit_reason=result.exit.reason,
        ),
        entry_triggered=True,
        input_fingerprint=result.input_fingerprint,
        intraday_exit_evidence=result.exit.intraday_exit_evidence,
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
        # 同日多笔 trade 的 portfolio_return 线性累加是聚合展示用的简化模型：
        # 极端弱势日（多笔止损叠加）可能让累加 < -100%，使 equity 数值变负。
        # 这是聚合口径的数值边界，并非单笔 trade 真实亏光——clamp 到下界避免
        # EquityPoint 校验 fatal，保留 drawdown=100% 的信号而非让整个回测失败。
        if equity < 0:
            equity = 0.0
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
    elif "sector" in reason_text or "decay" in reason_text:
        reason = FailureReasonCode.SECTOR_DECAY
        confidence = 0.8
    elif "black_swan" in reason_text or "crash" in reason_text:
        reason = FailureReasonCode.BLACK_SWAN
        confidence = 0.9
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
