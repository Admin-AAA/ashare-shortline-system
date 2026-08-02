from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.engine.backtest import (
    BacktestRequest,
    BacktestValidationError,
    DateRange,
    ExecutionPolicy,
    SingleDate,
)
from app.services.backtest.research_runner import ResearchBacktestRunner


def _runner(signal_dates: tuple[str, ...]):
    runner = object.__new__(ResearchBacktestRunner)
    descriptor = SimpleNamespace(effective_version="strategy-v1")
    strategy = object()
    bundle = SimpleNamespace(
        signal_dates=signal_dates,
        snapshot=SimpleNamespace(snapshot_id="bts_shared"),
    )
    runner._resolve_strategy = lambda request: (descriptor, strategy)
    runner._request_with_validated_parameters = lambda request, resolved: request
    runner._load_snapshot_bundle = lambda snapshot_id: bundle
    runner._execute = lambda request, resolved, implementation, loaded: {
        "strategy_version": request.strategy_version,
        "snapshot_id": loaded.snapshot.snapshot_id,
        "implementation": implementation,
    }
    return runner, strategy


def test_resolve_request_freezes_installed_strategy_version_without_capturing_data() -> None:
    runner, _ = _runner(("2026-01-02",))
    request = BacktestRequest(
        strategy_id="fixture",
        date_selector=SingleDate("2026-01-02"),
        execution_policy=ExecutionPolicy(),
    )

    resolved = runner.resolve_request(request)

    assert resolved.strategy_version == "strategy-v1"


def test_run_on_snapshot_executes_the_resolved_request_without_capture() -> None:
    runner, strategy = _runner(("2026-01-02", "2026-01-03"))
    request = BacktestRequest(
        strategy_id="fixture",
        date_selector=DateRange("2026-01-01", "2026-01-04"),
        execution_policy=ExecutionPolicy(),
    )

    result = runner.run_on_snapshot(request, "bts_shared")

    assert result == {
        "strategy_version": "strategy-v1",
        "snapshot_id": "bts_shared",
        "implementation": strategy,
    }


def test_run_on_snapshot_rejects_a_snapshot_outside_the_request_dates() -> None:
    runner, _ = _runner(("2026-01-05",))
    request = BacktestRequest(
        strategy_id="fixture",
        date_selector=DateRange("2026-01-01", "2026-01-04"),
        execution_policy=ExecutionPolicy(),
    )

    with pytest.raises(BacktestValidationError, match="snapshot signal dates"):
        runner.run_on_snapshot(request, "bts_shared")
