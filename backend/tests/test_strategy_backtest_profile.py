from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.backtest.strategy_profile as strategy_profile_module
from app.db.session import Base
from app.engine.backtest import (
    BacktestRequest,
    DateRange,
    ExecutionPolicy,
    Trade,
    TradeOutcome,
)
from app.models.tables import BacktestDataSnapshot, BacktestRunV2, BacktestTrade
from app.services.backtest.strategy_profile import (
    STRATEGY_PROFILE_EXPERIMENT_ID,
    StrategyBacktestProfileTarget,
    StrategyBacktestProfiles,
)
from app.services.backtest.research_runner import RESEARCH_SOURCE_VERSION


def _trade(
    trade_id: str,
    *,
    signal_date: str,
    exit_date: str,
    net_return_pct: float,
) -> Trade:
    return Trade(
        trade_id=trade_id,
        code="300001",
        signal_date=signal_date,
        entry_date=signal_date,
        exit_date=exit_date,
        entry_price=10.0,
        exit_price=11.0,
        gross_return_pct=net_return_pct,
        net_return_pct=net_return_pct,
        position_size=0.1,
        holding_days=1,
        outcome_label=(
            TradeOutcome.SUCCESS if net_return_pct > 0 else TradeOutcome.FAILED
        ),
    )


def _target() -> StrategyBacktestProfileTarget:
    return StrategyBacktestProfileTarget(
        strategy_id="current-strategy",
        strategy_version="current-v2",
        date_from="2026-01-01",
        date_to="2026-06-30",
        parameter_set={"minimum_signal_score": 72.0},
        execution_policy=ExecutionPolicy(position_size=0.1),
    )


def _persist_run(
    session,
    target: StrategyBacktestProfileTarget,
    *,
    version: str,
    run_id: str,
    status: str = "completed",
) -> None:
    request = BacktestRequest(
        strategy_id=target.strategy_id,
        strategy_version=version,
        date_selector=DateRange(target.date_from, target.date_to),
        execution_policy=target.execution_policy,
        parameter_set=target.parameter_set,
        experiment_id=STRATEGY_PROFILE_EXPERIMENT_ID,
    )
    session.add(
        BacktestRunV2(
            backtest_id=run_id,
            experiment_id=STRATEGY_PROFILE_EXPERIMENT_ID,
            parent_backtest_id=None,
            request_fingerprint=("a" if version == target.strategy_version else "b") * 64,
            attempt_no=1,
            snapshot_id=f"snapshot-{run_id}",
            strategy_id=target.strategy_id,
            strategy_version=version,
            request_schema_version=1,
            request_json=request.to_canonical_json(),
            execution_version="execution-v1",
            execution_policy_fingerprint=target.execution_policy.fingerprint(),
            signal_date=None,
            date_from=target.date_from,
            date_to=target.date_to,
            status=status,
            result_json=json.dumps({"status": "completed"}),
            started_at=datetime(2026, 7, 1, 9, 0),
            finished_at=datetime(2026, 7, 1, 9, 5),
            created_at=datetime(2026, 7, 1, 9, 0),
        )
    )
    session.add(
        BacktestDataSnapshot(
            snapshot_id=f"snapshot-{run_id}",
            snapshot_version=2,
            manifest_hash=f"m{run_id}"[:64],
            partition_manifest_json=(
                '{"schema_version": 2, "partitions": [{"dataset": "daily_kbars", '
                f'"source_version": "{RESEARCH_SOURCE_VERSION}", "row_count": 0, '
                '"content_hash": "x"}], "artifacts_by_signal_date": {}}'
            ),
            coverage_json="{}",
            missing_requirements_json="{}",
        )
    )


def _persist_trade(session, run_id: str, trade: Trade) -> None:
    session.add(
        BacktestTrade(
            trade_id=f"{run_id}-{trade.trade_id}",
            backtest_id=run_id,
            signal_date=trade.signal_date,
            code=trade.code,
            entry_date=trade.entry_date,
            exit_date=trade.exit_date,
            outcome_label=trade.outcome_label.value,
            trade_json=trade.to_canonical_json(),
        )
    )


def test_current_version_profile_excludes_old_version_and_compounds_by_exit_month() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            BacktestRunV2.__table__,
            BacktestTrade.__table__,
            BacktestDataSnapshot.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        target = _target()
        _persist_run(session, target, version="current-v2", run_id="run-current")
        _persist_trade(
            session,
            "run-current",
            _trade("jan-win", signal_date="2026-01-08", exit_date="2026-01-09", net_return_pct=10),
        )
        _persist_trade(
            session,
            "run-current",
            _trade("jan-loss", signal_date="2026-01-12", exit_date="2026-01-13", net_return_pct=-5),
        )
        _persist_trade(
            session,
            "run-current",
            _trade("feb-win", signal_date="2026-02-08", exit_date="2026-02-09", net_return_pct=20),
        )
        _persist_run(session, target, version="old-v1", run_id="run-old")
        _persist_trade(
            session,
            "run-old",
            _trade("old-win", signal_date="2026-03-08", exit_date="2026-03-09", net_return_pct=99),
        )
        session.commit()

        profile = StrategyBacktestProfiles(session).read(target)

        assert profile is not None
        assert profile["source"] == "canonical_backtest"
        assert profile["strategy_version"] == "current-v2"
        assert profile["backtest_id"] == "run-current"
        assert profile["executed_trade_count"] == 3
        assert profile["win_rate_pct"] == 66.6667
        assert profile["window_metrics"]["180"]["sample_count"] == 3
        assert profile["window_metrics"]["180"]["hit_rate"] == 0.666667
        assert profile["window_metrics"]["30"]["sample_count"] == 0
        assert profile["window_metrics"]["30"]["hit_rate"] is None
        assert profile["monthly_returns"] == [
            {"month": "2026-01", "return_pct": 0.495, "executed_trade_count": 2},
            {"month": "2026-02", "return_pct": 2.0, "executed_trade_count": 1},
            {"month": "2026-03", "return_pct": 0.0, "executed_trade_count": 0},
            {"month": "2026-04", "return_pct": 0.0, "executed_trade_count": 0},
            {"month": "2026-05", "return_pct": 0.0, "executed_trade_count": 0},
            {"month": "2026-06", "return_pct": 0.0, "executed_trade_count": 0},
        ]
        assert profile["total_return_pct"] == 2.5049
    finally:
        session.close()
        engine.dispose()


def test_profile_does_not_fall_back_to_a_different_parameter_set() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            BacktestRunV2.__table__,
            BacktestTrade.__table__,
            BacktestDataSnapshot.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        target = _target()
        different = StrategyBacktestProfileTarget(
            strategy_id=target.strategy_id,
            strategy_version=target.strategy_version,
            date_from=target.date_from,
            date_to=target.date_to,
            parameter_set={"minimum_signal_score": 65.0},
            execution_policy=target.execution_policy,
        )
        _persist_run(session, different, version=target.strategy_version, run_id="run-other-params")
        session.commit()

        assert StrategyBacktestProfiles(session).read(target) is None
    finally:
        session.close()
        engine.dispose()


def test_profile_never_projects_an_interrupted_run_as_completed() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            BacktestRunV2.__table__,
            BacktestTrade.__table__,
            BacktestDataSnapshot.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        target = _target()
        _persist_run(
            session,
            target,
            version=target.strategy_version,
            run_id="run-interrupted",
            status="interrupted",
        )
        session.commit()

        assert StrategyBacktestProfiles(session).read(target) is None
    finally:
        session.close()
        engine.dispose()


def test_profile_refresh_uses_actionable_only_candidate_evidence(monkeypatch) -> None:
    target = _target()
    profiles = StrategyBacktestProfiles(object())  # type: ignore[arg-type]
    reads = iter(
        [
            None,
            {
                "backtest_id": "btr-profile",
                "profile_fingerprint": target.fingerprint,
            },
        ]
    )
    monkeypatch.setattr(profiles, "read", lambda _target: next(reads))
    monkeypatch.setattr(profiles, "_compatible_snapshot_id", lambda _target: None)
    captured = {}

    class FakeRunner:
        def __init__(self, _repository, **kwargs):
            captured.update(kwargs)

        def run(self, _request):
            return {"status": "completed", "reused": False}

    monkeypatch.setattr(
        strategy_profile_module,
        "ResearchBacktestRepository",
        lambda _db: object(),
    )
    monkeypatch.setattr(strategy_profile_module, "ResearchBacktestRunner", FakeRunner)

    outcome = profiles.refresh(target)

    assert captured["candidate_evidence_scope"] == "actionable"
    assert outcome["status"] == "completed"
