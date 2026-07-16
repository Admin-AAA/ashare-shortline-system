"""Local-only DataReadinessPort tests for P0 research backtests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.engine.backtest import (
    AuxiliaryDataRequirement,
    BacktestRequest,
    DataRequirement,
    ExecutionPolicy,
    SingleDate,
)
from app.models.tables import DailyKbar, Stock
from app.services.backtest.research_runner import LocalDataReadinessAdapter, LocalResearchSnapshotBuilder


@dataclass(frozen=True)
class _Bar:
    trade_date: str
    open: float = 10.0
    high: float = 11.0
    low: float = 9.0
    close: float = 10.5


class _EmptyReadSession:
    """Read-only fake: its APIs prove the adapter never requires a write path."""

    def scalars(self, _statement):
        return SimpleNamespace(all=lambda: [])

    def scalar(self, _statement):
        return None


def _policy() -> ExecutionPolicy:
    return ExecutionPolicy(entry_lookahead_days=1, horizon_days=3)


def _requirement(**overrides) -> DataRequirement:
    values = {
        "lookback_bars": 2,
        "future_label_horizon_days": 0,
        "requires_daily_bars": True,
        "requires_minute_bars": False,
        "minimum_coverage": 1.0,
    }
    values.update(overrides)
    return DataRequirement(**values)


def test_local_readiness_requires_lookback_and_full_execution_future_window() -> None:
    adapter = LocalDataReadinessAdapter(_EmptyReadSession())
    rows = {
        "000001": [
            _Bar("2026-01-01"),
            _Bar("2026-01-02"),
            _Bar("2026-01-03"),
            _Bar("2026-01-04"),
            _Bar("2026-01-05"),
            _Bar("2026-01-06"),
        ]
    }

    ready = adapter.assess(
        signal_date="2026-01-02",
        codes=("000001",),
        daily_rows_by_code=rows,
        data_requirement=_requirement(),
        execution_policy=_policy(),
    )
    assert ready.is_ready
    assert ready.coverage_ratio == 1.0

    missing_lookback = adapter.assess(
        signal_date="2026-01-01",
        codes=("000001",),
        daily_rows_by_code=rows,
        data_requirement=_requirement(lookback_bars=2),
        execution_policy=_policy(),
    )
    assert missing_lookback.status.value == "insufficient_data"
    assert {gap.requirement for gap in missing_lookback.gaps} == {"daily_kbars_lookback"}

    missing_future = adapter.assess(
        signal_date="2026-01-03",
        codes=("000001",),
        daily_rows_by_code=rows,
        data_requirement=_requirement(),
        execution_policy=_policy(),
    )
    assert missing_future.status.value == "incomplete_evaluation"
    assert {gap.requirement for gap in missing_future.gaps} == {"daily_kbars_future_window"}
    assert "requires 4 valid future daily bars" in missing_future.gaps[0].detail


def test_local_readiness_reports_minute_benchmark_and_auxiliary_gaps_without_refreshing() -> None:
    session = _EmptyReadSession()
    adapter = LocalDataReadinessAdapter(session)
    rows = {
        "000001": [
            _Bar("2026-01-01"),
            _Bar("2026-01-02"),
            _Bar("2026-01-03"),
            _Bar("2026-01-04"),
            _Bar("2026-01-05"),
            _Bar("2026-01-06"),
        ]
    }

    result = adapter.assess(
        signal_date="2026-01-02",
        codes=("000001",),
        daily_rows_by_code=rows,
        data_requirement=_requirement(
            requires_minute_bars=True,
            benchmark_code="000300.SH",
            auxiliary_datasets=(AuxiliaryDataRequirement("capital_flow", minimum_coverage=0.8),),
        ),
        execution_policy=_policy(),
    )

    assert result.status.value == "insufficient_data"
    assert {gap.requirement for gap in result.gaps} == {
        "minute_kbars_signal_input",
        "benchmark:000300.SH",
        "auxiliary_datasets",
    }


def test_snapshot_builder_applies_data_requirement_without_writing_market_tables() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[DailyKbar.__table__, Stock.__table__])
    session = sessionmaker(bind=engine)()
    try:
        signal_day = date(2026, 3, 1)
        rows = [
            DailyKbar(
                code="000001",
                trade_date=(signal_day - timedelta(days=59) + timedelta(days=index)).isoformat(),
                open=10,
                high=11,
                low=9,
                close=10,
                volume=100,
            )
            for index in range(64)
        ]
        session.add_all(rows)
        session.commit()
        before_count = session.query(DailyKbar).count()
        request = BacktestRequest(
            strategy_id="readiness-integration",
            date_selector=SingleDate(signal_day.isoformat()),
            execution_policy=_policy(),
            target_codes=("000001",),
        )
        builder = LocalResearchSnapshotBuilder(session)

        ready_bundle = builder.capture(request, data_requirement=_requirement(lookback_bars=60))
        insufficient_bundle = builder.capture(request, data_requirement=_requirement(lookback_bars=61))

        assert ready_bundle.snapshot.coverage_by_signal_date[signal_day.isoformat()].is_ready
        insufficient = insufficient_bundle.snapshot.coverage_by_signal_date[signal_day.isoformat()]
        assert insufficient.status.value == "insufficient_data"
        assert insufficient.gaps[0].requirement == "daily_kbars_lookback"
        assert session.query(DailyKbar).count() == before_count
    finally:
        session.close()
        engine.dispose()
