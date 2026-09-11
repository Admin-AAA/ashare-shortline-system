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
from app.models.tables import (
    BacktestCandidate,
    BacktestDataSnapshot,
    BacktestRunV2,
    BacktestTrade,
)
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
    code: str = "300001",
    position_size: float = 0.1,
) -> Trade:
    return Trade(
        trade_id=trade_id,
        code=code,
        signal_date=signal_date,
        entry_date=signal_date,
        exit_date=exit_date,
        entry_price=10.0,
        exit_price=11.0,
        gross_return_pct=net_return_pct,
        net_return_pct=net_return_pct,
        position_size=position_size,
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


def _persist_candidate(
    session,
    run_id: str,
    *,
    signal_date: str,
    code: str,
    score: float,
) -> None:
    session.add(
        BacktestCandidate(
            candidate_id=f"{run_id}-{signal_date}-{code}",
            backtest_id=run_id,
            signal_date=signal_date,
            code=code,
            decision_status="signal",
            signal_json=json.dumps({"score": score}),
            audit_json="{}",
            input_artifact_id=f"artifact-{signal_date}",
        )
    )


def test_profile_selects_daily_top_10_and_excludes_rank_11_from_metrics() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            BacktestRunV2.__table__,
            BacktestCandidate.__table__,
            BacktestTrade.__table__,
            BacktestDataSnapshot.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        target = _target()
        _persist_run(session, target, version="current-v2", run_id="run-top10")
        signal_date = "2026-06-01"
        for index in range(1, 12):
            code = f"300{index:03d}"
            _persist_trade(
                session,
                "run-top10",
                _trade(
                    f"trade-{index}",
                    signal_date=signal_date,
                    exit_date="2026-06-02",
                    net_return_pct=-99 if index == 11 else 1,
                    code=code,
                ),
            )
            # 第 10、11 名同分，必须由代码升序稳定决定边界。
            _persist_candidate(
                session,
                "run-top10",
                signal_date=signal_date,
                code=code,
                score=100 - min(index, 10),
            )
        session.commit()

        profile = StrategyBacktestProfiles(session).read(target)

        assert profile is not None
        assert profile["selection_policy"] == "daily_score_top_n"
        assert profile["daily_selection_limit"] == 10
        assert profile["raw_signal_count"] == 11
        assert profile["selected_signal_count"] == 10
        assert profile["selected_signal_days"] == 1
        assert profile["win_rate_pct"] == 100.0
        assert profile["average_net_return_pct"] == 1.0
        assert "total_return_pct" not in profile
        assert "max_drawdown_pct" not in profile
    finally:
        session.close()
        engine.dispose()


def test_current_version_profile_excludes_old_version_and_reports_signal_metrics_by_exit_month() -> None:
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
            _trade(
                "jan-win",
                signal_date="2026-01-08",
                exit_date="2026-01-09",
                net_return_pct=10,
                position_size=0.01,
            ),
        )
        _persist_trade(
            session,
            "run-current",
            _trade(
                "jan-loss",
                signal_date="2026-01-12",
                exit_date="2026-01-13",
                net_return_pct=-5,
                position_size=0.99,
            ),
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
        assert profile["monthly_metrics"] == [
            {
                "month": "2026-01",
                "raw_signal_count": 2,
                "selected_signal_count": 2,
                "executed_trade_count": 2,
                "success_count": 1,
                "hit_rate": 0.5,
                "avg_return": 2.5,
            },
            {
                "month": "2026-02",
                "raw_signal_count": 1,
                "selected_signal_count": 1,
                "executed_trade_count": 1,
                "success_count": 1,
                "hit_rate": 1.0,
                "avg_return": 20.0,
            },
            *[
                {
                    "month": month,
                    "raw_signal_count": 0,
                    "selected_signal_count": 0,
                    "executed_trade_count": 0,
                    "success_count": 0,
                    "hit_rate": None,
                    "avg_return": None,
                }
                for month in ("2026-03", "2026-04", "2026-05", "2026-06")
            ],
        ]
        assert "total_return_pct" not in profile
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
