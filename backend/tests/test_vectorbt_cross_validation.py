from __future__ import annotations

import pandas as pd

from app.engine.backtest import ExecutionPolicy, Trade, TradeOutcome
from app.services.backtest.vectorbt_validation import cross_validate_signals, cross_validate_trades


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


def _trade(*, net_return_pct: float = 20.0) -> Trade:
    return Trade(
        code="000001",
        signal_date="2026-01-01",
        entry_date="2026-01-02",
        exit_date="2026-01-03",
        entry_price=10.0,
        exit_price=12.0,
        gross_return_pct=20.0,
        net_return_pct=net_return_pct,
        position_size=1.0,
        holding_days=1,
        outcome_label=TradeOutcome.SUCCESS,
    )


def test_real_trade_projection_matches_vectorbt_without_costs() -> None:
    policy = ExecutionPolicy(
        commission_rate=0,
        stamp_tax_rate=0,
        entry_slippage_bps=0,
        exit_slippage_bps=0,
    )

    report = cross_validate_trades((_trade(),), policy)

    assert report.passed is True
    assert report.compared_trade_count == 1
    assert report.first_divergence is None
    assert "t_plus_one_and_limit_fill_rules_are_domain_owned" in report.semantic_exclusions


def test_real_trade_projection_reports_first_divergence() -> None:
    policy = ExecutionPolicy(
        commission_rate=0,
        stamp_tax_rate=0,
        entry_slippage_bps=0,
        exit_slippage_bps=0,
    )

    report = cross_validate_trades((_trade(net_return_pct=15.0),), policy)

    assert report.passed is False
    assert report.first_divergence is not None
    assert report.first_divergence.trade_id.startswith("trade_")
