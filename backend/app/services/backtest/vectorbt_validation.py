"""Independent vectorbt adapter for cross-checking domain backtest returns."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import vectorbt as vbt


@dataclass(frozen=True, slots=True)
class CrossValidationReport:
    passed: bool
    domain_total_return: float
    vectorbt_total_return: float
    absolute_difference: float
    tolerance: float
    closed_trade_count: int


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
    )
