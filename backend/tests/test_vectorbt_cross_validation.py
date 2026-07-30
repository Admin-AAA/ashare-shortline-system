from __future__ import annotations

import pandas as pd

from app.services.backtest.vectorbt_validation import cross_validate_signals


def test_vectorbt_confirms_a_matching_domain_return() -> None:
    close = pd.Series([10.0, 11.0, 12.0, 11.0], index=pd.date_range("2026-01-01", periods=4))
    entries = pd.Series([True, False, False, False], index=close.index)
    exits = pd.Series([False, False, True, False], index=close.index)

    report = cross_validate_signals(
        close=close,
        entries=entries,
        exits=exits,
        domain_total_return=0.20,
        fee_rate=0.0,
    )

    assert report.passed is True
    assert report.vectorbt_total_return == 0.20
    assert report.closed_trade_count == 1


def test_vectorbt_reports_a_domain_engine_difference() -> None:
    close = pd.Series([10.0, 12.0], index=pd.date_range("2026-01-01", periods=2))
    entries = pd.Series([True, False], index=close.index)
    exits = pd.Series([False, True], index=close.index)

    report = cross_validate_signals(
        close=close,
        entries=entries,
        exits=exits,
        domain_total_return=0.15,
        fee_rate=0.0,
        tolerance=1e-6,
    )

    assert report.passed is False
    assert report.absolute_difference == 0.05
