"""Independent vectorbt adapter for cross-checking domain backtest returns."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd
import vectorbt as vbt

if TYPE_CHECKING:
    from app.engine.backtest import ExecutionPolicy, Trade


@dataclass(frozen=True, slots=True)
class ValidationSignalStream:
    trade_id: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float


@dataclass(frozen=True, slots=True)
class TradeCrossValidation:
    trade_id: str
    entry_index: int
    exit_index: int
    domain_net_return: float
    vectorbt_net_return: float
    absolute_difference: float


@dataclass(frozen=True, slots=True)
class CrossValidationReport:
    passed: bool
    domain_total_return: float
    vectorbt_total_return: float
    absolute_difference: float
    tolerance: float
    closed_trade_count: int
    compared_trade_count: int = 0
    semantic_exclusions: tuple[str, ...] = ()
    first_divergence: TradeCrossValidation | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "domain_total_return": self.domain_total_return,
            "vectorbt_total_return": self.vectorbt_total_return,
            "absolute_difference": self.absolute_difference,
            "tolerance": self.tolerance,
            "closed_trade_count": self.closed_trade_count,
            "compared_trade_count": self.compared_trade_count,
            "semantic_exclusions": list(self.semantic_exclusions),
            "first_divergence": (
                {
                    "trade_id": self.first_divergence.trade_id,
                    "entry_index": self.first_divergence.entry_index,
                    "exit_index": self.first_divergence.exit_index,
                    "domain_net_return": self.first_divergence.domain_net_return,
                    "vectorbt_net_return": self.first_divergence.vectorbt_net_return,
                    "absolute_difference": self.first_divergence.absolute_difference,
                }
                if self.first_divergence is not None else None
            ),
        }


def cross_validate_signals(
    *,
    close: pd.Series,
    entries: pd.Series,
    exits: pd.Series,
    domain_total_return: float,
    fee_rate: float,
    slippage_rate: float = 0.0,
    tolerance: float = 1e-6,
) -> CrossValidationReport:
    """Run the same signal stream through vectorbt and compare aggregate return."""
    if close.empty:
        raise ValueError("close series must not be empty")
    if not close.index.equals(entries.index) or not close.index.equals(exits.index):
        raise ValueError("close, entries and exits must share the same index")
    if tolerance < 0 or fee_rate < 0 or slippage_rate < 0:
        raise ValueError("tolerance and cost rates must be non-negative")

    portfolio = vbt.Portfolio.from_signals(
        close.astype(float),
        entries.astype(bool),
        exits.astype(bool),
        fees=float(fee_rate),
        slippage=float(slippage_rate),
        init_cash=10_000.0,
    )
    vectorbt_return = float(portfolio.total_return())
    difference = abs(vectorbt_return - float(domain_total_return))
    return CrossValidationReport(
        passed=difference <= tolerance,
        domain_total_return=float(domain_total_return),
        vectorbt_total_return=round(vectorbt_return, 12),
        absolute_difference=round(difference, 12),
        tolerance=float(tolerance),
        closed_trade_count=int(portfolio.trades.closed.count()),
        compared_trade_count=int(portfolio.trades.closed.count()),
    )


def cross_validate_trades(
    trades: tuple["Trade", ...],
    policy: "ExecutionPolicy",
    *,
    tolerance: float = 1e-6,
) -> CrossValidationReport:
    """Recalculate the executable subset trade-by-trade with vectorbt.

    The adapter compares realized entry/exit prices. Path-dependent A-share rules stay
    owned by the domain engine and are reported as exclusions instead of being faked.
    """
    streams = tuple(
        ValidationSignalStream(
            trade_id=trade.trade_id or "",
            entry_date=str(trade.entry_date),
            exit_date=str(trade.exit_date),
            entry_price=float(trade.entry_price),
            exit_price=float(trade.exit_price),
        )
        for trade in trades
        if trade.executed
    )
    exclusions = {
        "t_plus_one_and_limit_fill_rules_are_domain_owned",
        "slippage_is_already_reflected_in_realized_entry_and_exit_prices",
    }
    exclusions.update(
        f"{trade.trade_id}:{trade.exit_reason}"
        for trade in trades
        if not trade.executed or "untradable" in str(trade.exit_reason or "").lower()
    )
    comparisons: list[TradeCrossValidation] = []
    vectorbt_total = 0.0
    domain_total = 0.0
    executed_by_id = {trade.trade_id or "": trade for trade in trades if trade.executed}
    for stream in streams:
        index = pd.DatetimeIndex([stream.entry_date, stream.exit_date])
        trade = executed_by_id[stream.trade_id]
        report = cross_validate_signals(
            close=pd.Series([stream.entry_price, stream.exit_price], index=index),
            entries=pd.Series([True, False], index=index),
            exits=pd.Series([False, True], index=index),
            domain_total_return=trade.gross_return_pct / 100,
            fee_rate=0.0,
            slippage_rate=0.0,
            tolerance=tolerance,
        )
        # Trade.entry_price/exit_price are realized prices and already include
        # execution slippage.  Re-applying vectorbt slippage would double-count
        # it.  Domain fees are an additive round-trip percentage, whereas
        # vectorbt models per-order cash debits; subtract the authoritative
        # domain fee after independently validating the realized-price return.
        vectorbt_net_return = report.vectorbt_total_return - policy.fee_pct / 100
        comparison = TradeCrossValidation(
            trade_id=stream.trade_id,
            entry_index=0,
            exit_index=1,
            domain_net_return=trade.net_return_pct / 100,
            vectorbt_net_return=round(vectorbt_net_return, 12),
            absolute_difference=round(
                abs(vectorbt_net_return - trade.net_return_pct / 100),
                12,
            ),
        )
        comparisons.append(comparison)
        domain_total += comparison.domain_net_return * trade.position_size
        vectorbt_total += comparison.vectorbt_net_return * trade.position_size
    first_divergence = next(
        (item for item in comparisons if item.absolute_difference > tolerance), None
    )
    difference = abs(domain_total - vectorbt_total)
    return CrossValidationReport(
        passed=first_divergence is None and difference <= tolerance,
        domain_total_return=round(domain_total, 12),
        vectorbt_total_return=round(vectorbt_total, 12),
        absolute_difference=round(difference, 12),
        tolerance=tolerance,
        closed_trade_count=len(streams),
        compared_trade_count=len(comparisons),
        semantic_exclusions=tuple(sorted(exclusions)),
        first_divergence=first_divergence,
    )
