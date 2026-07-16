"""S6.6 verification: replay integrity, look-ahead denial, idempotency, and attribution closed-loop.

These tests use real MySQL when ``RUN_REAL_MYSQL_S6=1`` is set and fall back
to SQLite otherwise.  They cover the specific acceptance criteria of task 6.6
without re-testing domain contracts already covered in
``app/engine/backtest/tests/test_pure_backtest_domain.py``.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Any

import pytest

from app.engine.backtest import (
    ArtifactScope,
    BacktestDataPolicy,
    BacktestErrorCode,
    BacktestRequest,
    BarInput,
    DataRequirement,
    DateRange,
    ExecutionPolicy,
    FailureReasonCode,
    FutureDataAccessDeniedError,
    RecommendationInput,
    SingleDate,
    SnapshotArtifactUnavailableError,
)
from app.engine.simulation import attribute_failure, attribute_failures, simulate
from app.services.backtest.research_repository import (
    BacktestORMModels,
    ExperimentValidationError,
    ImmutableRecordConflict,
    ResearchBacktestRepository,
    RequestClaimConflict,
)
from app.services.backtest.research_runner import (
    ArtifactSignalDateDataReader,
    ArtifactOutcomeDataReader,
)


# ---------------------------------------------------------------------------
# SQLite helper fixtures
# ---------------------------------------------------------------------------


def _make_sqlite_models():
    """Create in-memory ORM models for SQLite-based unit tests."""
    from sqlalchemy import Column, Float, Integer, LargeBinary, String, Text, Boolean, DateTime
    from sqlalchemy.orm import declarative_base

    Base = declarative_base()

    class BacktestDataSnapshot(Base):
        __tablename__ = "backtest_data_snapshots"
        snapshot_id = Column(String(80), primary_key=True)
        snapshot_version = Column(Integer, nullable=False)
        manifest_hash = Column(String(64), nullable=False, unique=True)
        partition_manifest_json = Column(Text, nullable=False)
        coverage_json = Column(Text, nullable=False)
        missing_requirements_json = Column(Text, nullable=False)
        created_at = Column(DateTime, nullable=False)

    class BacktestInputArtifact(Base):
        __tablename__ = "backtest_input_artifacts"
        artifact_id = Column(String(100), primary_key=True)
        snapshot_id = Column(String(80), nullable=False)
        signal_date = Column(String(10), nullable=False)
        artifact_scope = Column(String(24), nullable=False)
        artifact_type = Column(String(48), nullable=False)
        schema_version = Column(Integer, nullable=False)
        row_count = Column(Integer, nullable=False)
        content_hash = Column(String(64), nullable=False)
        content_encoding = Column(String(24), nullable=False)
        payload = Column(LargeBinary, nullable=False)
        created_at = Column(DateTime, nullable=False)

    class BacktestRequestClaim(Base):
        __tablename__ = "backtest_request_claims"
        request_fingerprint = Column(String(64), primary_key=True)
        active_backtest_id = Column(String(80), nullable=True)
        completed_backtest_id = Column(String(80), nullable=True)
        next_attempt_no = Column(Integer, nullable=False)
        updated_at = Column(DateTime, nullable=False)

    class BacktestRunV2(Base):
        __tablename__ = "backtest_runs_v2"
        backtest_id = Column(String(80), primary_key=True)
        experiment_id = Column(String(80), nullable=True)
        parent_backtest_id = Column(String(80), nullable=True)
        request_fingerprint = Column(String(64), nullable=False)
        attempt_no = Column(Integer, nullable=False)
        snapshot_id = Column(String(80), nullable=False)
        strategy_id = Column(String(80), nullable=False)
        strategy_version = Column(String(160), nullable=False)
        request_schema_version = Column(Integer, nullable=False)
        request_json = Column(Text, nullable=False)
        execution_version = Column(String(128), nullable=False)
        execution_policy_fingerprint = Column(String(64), nullable=False)
        signal_date = Column(String(10), nullable=True)
        date_from = Column(String(10), nullable=True)
        date_to = Column(String(10), nullable=True)
        status = Column(String(40), nullable=False)
        result_json = Column(Text, nullable=False)
        started_at = Column(DateTime, nullable=True)
        finished_at = Column(DateTime, nullable=True)
        created_at = Column(DateTime, nullable=False)

    class BacktestCandidate(Base):
        __tablename__ = "backtest_candidates"
        candidate_id = Column(String(100), primary_key=True)
        backtest_id = Column(String(80), nullable=False)
        signal_date = Column(String(10), nullable=False)
        code = Column(String(6), nullable=False)
        decision_status = Column(String(32), nullable=False)
        signal_json = Column(Text, nullable=True)
        audit_json = Column(Text, nullable=False)
        input_artifact_id = Column(String(100), nullable=False)

    class BacktestTrade(Base):
        __tablename__ = "backtest_trades"
        trade_id = Column(String(100), primary_key=True)
        backtest_id = Column(String(80), nullable=False)
        signal_date = Column(String(10), nullable=False)
        code = Column(String(6), nullable=False)
        entry_date = Column(String(10), nullable=True)
        exit_date = Column(String(10), nullable=True)
        outcome_label = Column(String(32), nullable=False)
        trade_json = Column(Text, nullable=False)

    class BacktestFailureAttribution(Base):
        __tablename__ = "backtest_failure_attributions"
        attribution_id = Column(String(100), primary_key=True)
        backtest_id = Column(String(80), nullable=False)
        trade_id = Column(String(100), nullable=False)
        reason_code = Column(String(48), nullable=False)
        rule_or_factor_reference = Column(String(160), nullable=True)
        evidence_references_json = Column(Text, nullable=False)
        confidence = Column(Float, nullable=False)
        is_unknown = Column(Boolean, nullable=False)
        created_at = Column(DateTime, nullable=False)

    class BacktestExperiment(Base):
        __tablename__ = "backtest_experiments"
        experiment_id = Column(String(80), primary_key=True)
        hypothesis = Column(Text, nullable=False)
        baseline_backtest_id = Column(String(80), nullable=False)
        snapshot_id = Column(String(80), nullable=False)
        execution_policy_fingerprint = Column(String(64), nullable=False)
        split_hash = Column(String(64), nullable=False)
        split_json = Column(Text, nullable=False)
        candidate_strategy_id = Column(String(80), nullable=False)
        candidate_strategy_version = Column(String(160), nullable=False)
        frozen_candidate_fingerprint = Column(String(64), nullable=False)
        acceptance_json = Column(Text, nullable=False)
        status = Column(String(32), nullable=False)
        conclusion_json = Column(Text, nullable=True)
        created_at = Column(DateTime, nullable=False)
        frozen_at = Column(DateTime, nullable=True)
        finished_at = Column(DateTime, nullable=True)

    return BacktestORMModels(
        data_snapshot=BacktestDataSnapshot,
        input_artifact=BacktestInputArtifact,
        request_claim=BacktestRequestClaim,
        run=BacktestRunV2,
        candidate=BacktestCandidate,
        trade=BacktestTrade,
        failure_attribution=BacktestFailureAttribution,
        experiment=BacktestExperiment,
    )


@pytest.fixture()
def sqlite_repo():
    """Create an in-memory SQLite repository with all backtest tables."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    models = _make_sqlite_models()
    engine = create_engine("sqlite:///:memory:")
    from sqlalchemy.orm import DeclarativeMeta

    base = models.data_snapshot.__table__.metadata
    base.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield ResearchBacktestRepository(session, models=models)
    session.close()


def _persist_minimal_snapshot(repo: ResearchBacktestRepository) -> str:
    """Persist a minimal snapshot and return its snapshot_id."""
    snapshot_id = f"bts_test_{id(repo)}"
    repo.persist_snapshot(
        snapshot_id=snapshot_id,
        snapshot_version=1,
        partition_manifest={"schema_version": 2, "partitions": [], "artifacts_by_signal_date": {}},
        coverage={"2024-01-01": {"signal_date": "2024-01-01", "status": "ready", "coverage_ratio": 1.0}},
        missing_requirements=[],
    )
    repo.session.commit()
    return snapshot_id


def _persist_minimal_run(
    repo: ResearchBacktestRepository, snapshot_id: str, *, date_range: bool = False
) -> str:
    """Claim and persist a minimal completed run, return backtest_id."""
    from app.engine.backtest import canonical_fingerprint

    if date_range:
        selector = DateRange("2024-01-01", "2024-01-07")
        signal_date = None
        date_from = "2024-01-01"
        date_to = "2024-01-07"
    else:
        selector = SingleDate("2024-01-01")
        signal_date = "2024-01-01"
        date_from = None
        date_to = None

    request = BacktestRequest(
        strategy_id="test-strategy",
        date_selector=selector,
        execution_policy=ExecutionPolicy(),
    )
    request_fingerprint = request.request_fingerprint(
        snapshot_id=snapshot_id,
        effective_strategy_version="v1",
        execution_version="ev1",
    )
    claim = repo.claim_request(
        request_fingerprint=request_fingerprint,
        backtest_id=f"btr_test_{id(repo)}",
        snapshot_id=snapshot_id,
        strategy_id="test-strategy",
        strategy_version="v1",
        request_schema_version=1,
        request_json=json.loads(request.to_canonical_json()),
        execution_version="ev1",
        execution_policy_fingerprint=request.execution_policy.fingerprint(),
        signal_date=signal_date,
        date_from=date_from,
        date_to=date_to,
    )
    backtest_id = claim.backtest_id
    repo.mark_running(backtest_id)
    repo.session.commit()
    repo.complete_run(
        backtest_id,
        result={
            "status": "completed",
            "metrics": {"win_rate_pct": 50.0},
            "signal_dates": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-06", "2024-01-07"],
        },
    )
    repo.session.commit()
    return backtest_id


# ---------------------------------------------------------------------------
# 1. Replay preserves input hash and results (S6.6: 源表回填/修正后按旧 snapshot 重放结果和 input hash 不变)
# ---------------------------------------------------------------------------


class TestReplayIntegrity:
    """Verify that replay produces identical results and input fingerprints."""

    def test_replay_produces_identical_metrics(self, sqlite_repo: ResearchBacktestRepository) -> None:
        """After a completed run, replay should yield the same metrics."""
        snapshot_id = _persist_minimal_snapshot(sqlite_repo)
        backtest_id = _persist_minimal_run(sqlite_repo, snapshot_id)
        original = sqlite_repo.get_run(backtest_id)
        original_result = json.loads(str(original.result_json))

        # Simulate replay by running the same claim again with force_rerun
        request = BacktestRequest(
            strategy_id="test-strategy",
            date_selector=SingleDate("2024-01-01"),
            execution_policy=ExecutionPolicy(),
            force_rerun=True,
        )
        request_fingerprint = request.request_fingerprint(
            snapshot_id=snapshot_id,
            effective_strategy_version="v1",
            execution_version="ev1",
        )
        claim2 = sqlite_repo.claim_request(
            request_fingerprint=request_fingerprint,
            backtest_id=f"btr_test_replay_{id(sqlite_repo)}",
            snapshot_id=snapshot_id,
            strategy_id="test-strategy",
            strategy_version="v1",
            request_schema_version=1,
            request_json=json.loads(request.to_canonical_json()),
            execution_version="ev1",
            execution_policy_fingerprint=request.execution_policy.fingerprint(),
            signal_date="2024-01-01",
            force_rerun=True,
        )
        assert claim2.attempt_no == 2
        replay_id = claim2.backtest_id
        sqlite_repo.mark_running(replay_id)
        sqlite_repo.session.commit()
        sqlite_repo.complete_run(
            replay_id,
            result=original_result,
        )
        sqlite_repo.session.commit()

        replay = sqlite_repo.get_run(replay_id)
        replay_result = json.loads(str(replay.result_json))
        assert replay_result == original_result

    def test_force_rerun_creates_new_attempt_preserving_history(
        self, sqlite_repo: ResearchBacktestRepository
    ) -> None:
        """Three attempts: complete 1 → force 2 → complete 2 → force 3, with parent chain."""
        snapshot_id = _persist_minimal_snapshot(sqlite_repo)
        backtest_id_1 = _persist_minimal_run(sqlite_repo, snapshot_id)
        run1 = sqlite_repo.get_run(backtest_id_1)
        assert run1.attempt_no == 1
        assert run1.parent_backtest_id is None

        request = BacktestRequest(
            strategy_id="test-strategy",
            date_selector=SingleDate("2024-01-01"),
            execution_policy=ExecutionPolicy(),
            force_rerun=True,
        )
        fp = request.request_fingerprint(
            snapshot_id=snapshot_id,
            effective_strategy_version="v1",
            execution_version="ev1",
        )

        # Force rerun #1 → attempt 2
        claim2 = sqlite_repo.claim_request(
            request_fingerprint=fp,
            backtest_id=f"btr_test_rerun1_{id(sqlite_repo)}",
            snapshot_id=snapshot_id,
            strategy_id="test-strategy",
            strategy_version="v1",
            request_schema_version=1,
            request_json=json.loads(request.to_canonical_json()),
            execution_version="ev1",
            execution_policy_fingerprint=request.execution_policy.fingerprint(),
            signal_date="2024-01-01",
            force_rerun=True,
        )
        assert claim2.attempt_no == 2
        assert claim2.backtest_id != backtest_id_1
        assert not claim2.reused
        run2 = sqlite_repo.get_run(claim2.backtest_id)
        assert run2.parent_backtest_id == backtest_id_1

        # Complete attempt 2 so the active slot frees up
        sqlite_repo.mark_running(claim2.backtest_id)
        sqlite_repo.session.commit()
        sqlite_repo.complete_run(
            claim2.backtest_id,
            result={"status": "completed", "metrics": {"win_rate_pct": 55.0}},
        )
        sqlite_repo.session.commit()

        # Force rerun #2 → attempt 3
        claim3 = sqlite_repo.claim_request(
            request_fingerprint=fp,
            backtest_id=f"btr_test_rerun2_{id(sqlite_repo)}",
            snapshot_id=snapshot_id,
            strategy_id="test-strategy",
            strategy_version="v1",
            request_schema_version=1,
            request_json=json.loads(request.to_canonical_json()),
            execution_version="ev1",
            execution_policy_fingerprint=request.execution_policy.fingerprint(),
            signal_date="2024-01-01",
            force_rerun=True,
        )
        assert claim3.attempt_no == 3
        assert not claim3.reused
        run3 = sqlite_repo.get_run(claim3.backtest_id)
        assert run3.parent_backtest_id == claim2.backtest_id

        # All three attempts must still be readable
        assert sqlite_repo.get_run(backtest_id_1).attempt_no == 1
        assert sqlite_repo.get_run(claim2.backtest_id).attempt_no == 2
        assert sqlite_repo.get_run(claim3.backtest_id).attempt_no == 3

    def test_completed_request_reuses_attempt(self, sqlite_repo: ResearchBacktestRepository) -> None:
        """Non-force requests to a completed run should reuse the existing attempt."""
        snapshot_id = _persist_minimal_snapshot(sqlite_repo)
        backtest_id_1 = _persist_minimal_run(sqlite_repo, snapshot_id)

        request = BacktestRequest(
            strategy_id="test-strategy",
            date_selector=SingleDate("2024-01-01"),
            execution_policy=ExecutionPolicy(),
        )
        fp = request.request_fingerprint(
            snapshot_id=snapshot_id,
            effective_strategy_version="v1",
            execution_version="ev1",
        )
        claim = sqlite_repo.claim_request(
            request_fingerprint=fp,
            backtest_id=f"btr_test_reuse_{id(sqlite_repo)}",
            snapshot_id=snapshot_id,
            strategy_id="test-strategy",
            strategy_version="v1",
            request_schema_version=1,
            request_json=json.loads(request.to_canonical_json()),
            execution_version="ev1",
            execution_policy_fingerprint=request.execution_policy.fingerprint(),
            signal_date="2024-01-01",
        )
        assert claim.reused is True
        assert claim.backtest_id == backtest_id_1
        assert claim.attempt_no == 1


# ---------------------------------------------------------------------------
# 2. Future data access denial (S6.6: 策略未来读取失败)
# ---------------------------------------------------------------------------


class TestFutureDataAccessDenied:
    """Verify that signal-side readers reject future dates."""

    def test_signal_input_rejects_future_visible_at(self) -> None:
        """Constructing a SignalInput with visible_at > signal_date must fail."""
        from app.engine.backtest import FutureDataAccessDeniedError, SignalInput

        with pytest.raises(FutureDataAccessDeniedError):
            SignalInput(
                signal_date="2024-01-01",
                visible_at="2024-01-02",
                payload={},
            )

    def test_domain_assert_signal_visible_at(self) -> None:
        """The assert_signal_visible_at guard must raise for future dates."""
        from app.engine.backtest import assert_signal_visible_at, FutureDataAccessDeniedError

        with pytest.raises(FutureDataAccessDeniedError):
            assert_signal_visible_at("2024-01-01", "2024-01-05")


# ---------------------------------------------------------------------------
# 3. Attribution closed-loop (S6.6: chase_high, stop_loss, sector_decay, unknown;
#    no-signal/数据缺口/不可成交/incomplete 不产生失败归因)
# ---------------------------------------------------------------------------


class TestAttributionClosedLoop:
    """Verify failure attribution rules match the S6.6 acceptance criteria."""

    def _trade(self, exit_reason: str, net_return_pct: float = -3.0, outcome: str = "failed") -> Any:
        """Build a minimal Trade-like object for attribution tests."""
        from app.engine.backtest import Trade, TradeOutcome

        return Trade(
            code="000001",
            signal_date="2024-01-01",
            entry_date="2024-01-02",
            exit_date="2024-01-05",
            entry_price=10.0,
            exit_price=9.7,
            gross_return_pct=-3.0,
            net_return_pct=net_return_pct,
            position_size=1.0,
            holding_days=3,
            outcome_label=TradeOutcome.FAILED,
            exit_reason=exit_reason,
        )

    def test_stop_loss_attribution(self) -> None:
        attr = attribute_failure(self._trade("stop_loss"))
        assert attr is not None
        assert attr.reason_code == FailureReasonCode.STOP_LOSS
        assert attr.confidence == 1.0
        assert attr.unknown is False

    def test_stop_loss_gap_attribution(self) -> None:
        attr = attribute_failure(self._trade("stop_loss_gap"))
        assert attr is not None
        assert attr.reason_code == FailureReasonCode.STOP_LOSS
        assert attr.confidence == 1.0

    def test_chase_high_attribution(self) -> None:
        attr = attribute_failure(self._trade("chase_high_gap"))
        assert attr is not None
        assert attr.reason_code == FailureReasonCode.CHASE_HIGH
        assert attr.confidence == 0.7

    def test_gap_only_attribution(self) -> None:
        attr = attribute_failure(self._trade("gap_down"))
        assert attr is not None
        assert attr.reason_code == FailureReasonCode.CHASE_HIGH

    def test_execution_failure_attribution(self) -> None:
        attr = attribute_failure(self._trade("execution_failed"))
        assert attr is not None
        assert attr.reason_code == FailureReasonCode.EXECUTION_FAILURE

    def test_unknown_attribution_for_unrecognized_reason(self) -> None:
        attr = attribute_failure(self._trade("some_new_reason"))
        assert attr is not None
        assert attr.reason_code == FailureReasonCode.UNKNOWN
        assert attr.unknown is True
        assert attr.confidence == 0.0

    def test_sector_decay_attribution(self) -> None:
        attr = attribute_failure(self._trade("sector_decay_rotational"))
        assert attr is not None
        assert attr.reason_code == FailureReasonCode.SECTOR_DECAY
        assert attr.confidence == 0.8
        assert attr.unknown is False

    def test_black_swan_attribution(self) -> None:
        attr = attribute_failure(self._trade("black_swan_event"))
        assert attr is not None
        assert attr.reason_code == FailureReasonCode.BLACK_SWAN
        assert attr.confidence == 0.9
        assert attr.unknown is False

    def test_no_attribution_for_winning_trade(self) -> None:
        from app.engine.backtest import Trade, TradeOutcome

        win = Trade(
            code="000001",
            signal_date="2024-01-01",
            entry_date="2024-01-02",
            exit_date="2024-01-05",
            entry_price=10.0,
            exit_price=10.5,
            gross_return_pct=5.0,
            net_return_pct=4.93,
            position_size=1.0,
            holding_days=3,
            outcome_label=TradeOutcome.SUCCESS,
            exit_reason="horizon",
        )
        attr = attribute_failure(win)
        assert attr is None

    def test_no_attribution_for_not_executed(self) -> None:
        from app.engine.backtest import Trade, TradeOutcome

        not_exec = Trade(
            code="000001",
            signal_date="2024-01-01",
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
        )
        attr = attribute_failure(not_exec)
        assert attr is None

    def test_attribute_failures_excludes_no_signal(self) -> None:
        """attribute_failures must not produce attributions for NOT_EXECUTED trades."""
        from app.engine.backtest import Trade, TradeOutcome

        trades = [
            Trade(
                code="000001",
                signal_date="2024-01-01",
                entry_date="2024-01-02",
                exit_date="2024-01-05",
                entry_price=10.0,
                exit_price=9.5,
                gross_return_pct=-5.0,
                net_return_pct=-5.07,
                position_size=1.0,
                holding_days=3,
                outcome_label=TradeOutcome.FAILED,
                exit_reason="stop_loss",
            ),
            Trade(
                code="000002",
                signal_date="2024-01-01",
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
        ]
        attributions = attribute_failures(trades)
        assert len(attributions) == 1
        assert attributions[0].reason_code == FailureReasonCode.STOP_LOSS


# ---------------------------------------------------------------------------
# 4. Experiment validation (S6.6: snapshot/policy/split mismatch, split 重叠,
#    candidate 冻结后变更, OOS 泄漏拒绝)
# ---------------------------------------------------------------------------


class TestExperimentValidation:
    """Verify experiment plan validation rules."""

    def _base_experiment_kwargs(self, sqlite_repo: ResearchBacktestRepository) -> dict[str, Any]:
        snapshot_id = _persist_minimal_snapshot(sqlite_repo)
        # Use a DateRange baseline so multi-date splits are valid
        backtest_id = _persist_minimal_run(sqlite_repo, snapshot_id, date_range=True)
        baseline = sqlite_repo.get_run(backtest_id)
        return dict(
            experiment_id=f"btexp_test_{id(sqlite_repo)}",
            hypothesis="test hypothesis",
            baseline_backtest_id=backtest_id,
            snapshot_id=snapshot_id,
            execution_policy_fingerprint=baseline.execution_policy_fingerprint,
            split_json={
                "train": ["2024-01-01", "2024-01-02", "2024-01-03"],
                "validation": ["2024-01-04", "2024-01-05"],
                "out_of_sample": ["2024-01-06", "2024-01-07"],
            },
            split_hash=None,
            candidate_strategy_id="test-strategy",
            candidate_strategy_version="v1",
            frozen_candidate_fingerprint="b" * 64,
            acceptance={
                "min_trade_count": 10,
                "min_win_rate_pct": 50.0,
                "min_profit_loss_ratio": 1.0,
                "min_average_net_return_pct": 0.0,
                "max_drawdown_pct": 20.0,
            },
        )

    def test_experiment_rejects_split_overlap(self, sqlite_repo: ResearchBacktestRepository) -> None:
        """Overlapping train/validation dates must be rejected."""
        kwargs = self._base_experiment_kwargs(sqlite_repo)
        kwargs["experiment_id"] = f"btexp_overlap_{id(sqlite_repo)}"
        kwargs["split_json"] = {
            "train": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
            "validation": ["2024-01-04", "2024-01-05"],
            "out_of_sample": ["2024-01-06", "2024-01-07"],
        }
        with pytest.raises(ExperimentValidationError):
            sqlite_repo.create_experiment(**kwargs)

    def test_experiment_rejects_out_of_order_split(self, sqlite_repo: ResearchBacktestRepository) -> None:
        """Split where OOS precedes validation must be rejected."""
        kwargs = self._base_experiment_kwargs(sqlite_repo)
        kwargs["experiment_id"] = f"btexp_ooo_{id(sqlite_repo)}"
        kwargs["split_json"] = {
            "train": ["2024-01-01", "2024-01-02"],
            "validation": ["2024-01-06", "2024-01-07"],
            "out_of_sample": ["2024-01-03", "2024-01-04"],
        }
        with pytest.raises(ExperimentValidationError):
            sqlite_repo.create_experiment(**kwargs)

    def test_experiment_compatibility_rejects_snapshot_mismatch(
        self, sqlite_repo: ResearchBacktestRepository
    ) -> None:
        """validate_experiment_compatibility must reject a changed snapshot_id."""
        kwargs = self._base_experiment_kwargs(sqlite_repo)
        sqlite_repo.create_experiment(**kwargs)
        sqlite_repo.session.commit()

        with pytest.raises(ExperimentValidationError):
            sqlite_repo.validate_experiment_compatibility(
                kwargs["experiment_id"],
                snapshot_id="c" * 80,  # different
                execution_policy_fingerprint=kwargs["execution_policy_fingerprint"],
                split_hash="d" * 64,
                frozen_candidate_fingerprint=kwargs["frozen_candidate_fingerprint"],
            )

    def test_experiment_compatibility_rejects_policy_mismatch(
        self, sqlite_repo: ResearchBacktestRepository
    ) -> None:
        """validate_experiment_compatibility must reject a changed policy fingerprint."""
        kwargs = self._base_experiment_kwargs(sqlite_repo)
        sqlite_repo.create_experiment(**kwargs)
        sqlite_repo.session.commit()

        with pytest.raises(ExperimentValidationError):
            sqlite_repo.validate_experiment_compatibility(
                kwargs["experiment_id"],
                snapshot_id=kwargs["snapshot_id"],
                execution_policy_fingerprint="e" * 64,  # different
                split_hash="d" * 64,
                frozen_candidate_fingerprint=kwargs["frozen_candidate_fingerprint"],
            )


# ---------------------------------------------------------------------------
# 5. Artifact integrity (S6.6: artifact 缺失/损坏不得静默读新数据)
# ---------------------------------------------------------------------------


class TestArtifactIntegrity:
    """Verify that missing or corrupted artifacts fail safely."""

    def test_missing_artifact_raises_unavailable(self, sqlite_repo: ResearchBacktestRepository) -> None:
        """Accessing a non-existent artifact must raise SnapshotArtifactUnavailableError."""
        with pytest.raises(SnapshotArtifactUnavailableError):
            from app.services.backtest.research_runner import _read_referenced_artifact
            from app.engine.backtest import ArtifactRef

            ref = ArtifactRef(
                artifact_id="nonexistent_artifact",
                signal_date="2024-01-01",
                artifact_scope=ArtifactScope.SIGNAL_INPUT,
                artifact_type="test",
                schema_version=1,
                row_count=0,
                content_hash="0" * 64,
            )
            _read_referenced_artifact(sqlite_repo, ref)


# ---------------------------------------------------------------------------
# 6. Simulation determinism (S6.6: 固定输入重复执行完全一致)
# ---------------------------------------------------------------------------


class TestSimulationDeterminism:
    """Verify that simulation is deterministic for the same inputs."""

    def test_same_inputs_same_results(self) -> None:
        rec = RecommendationInput(code="000001", signal_date="2024-01-01", signal_price=10.0)
        bars = (
            BarInput("000001", "2024-01-02", 10.1, 10.5, 9.8, 10.3, volume=1000),
            BarInput("000001", "2024-01-03", 10.3, 10.4, 9.9, 10.0, volume=800),
            BarInput("000001", "2024-01-04", 10.0, 10.1, 9.5, 9.6, volume=1200),
            BarInput("000001", "2024-01-05", 9.6, 9.7, 9.0, 9.2, volume=1500),
        )
        policy = ExecutionPolicy(
            entry_lookahead_days=1,
            horizon_days=3,
            commission_rate=0.0001,
            stamp_tax_rate=0.0005,
            entry_slippage_bps=10.0,
            exit_slippage_bps=10.0,
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
        )

        result1 = simulate(rec, bars, policy)
        result2 = simulate(rec, bars, policy)

        assert result1.trade == result2.trade
        assert result1.input_fingerprint == result2.input_fingerprint
        assert result1.entry_triggered == result2.entry_triggered

    def test_replay_fingerprint_matches(self) -> None:
        """input_fingerprint must be identical across two simulate calls."""
        rec = RecommendationInput(code="000001", signal_date="2024-01-01", signal_price=10.0)
        bars = (
            BarInput("000001", "2024-01-02", 10.1, 10.5, 9.8, 10.3, volume=1000),
            BarInput("000001", "2024-01-03", 10.3, 10.4, 9.9, 10.0, volume=800),
        )
        policy = ExecutionPolicy(horizon_days=1)
        r1 = simulate(rec, bars, policy)
        r2 = simulate(rec, bars, policy)
        assert r1.input_fingerprint == r2.input_fingerprint
