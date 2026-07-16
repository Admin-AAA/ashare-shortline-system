"""SQLite contract tests for P0 request-claim state transitions.

These validate repository semantics only.  They intentionally do not claim to
cover MySQL/InnoDB double-connection concurrency, which remains a separate
safety-gated acceptance check.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.tables import BacktestDataSnapshot, BacktestRequestClaim, BacktestRunV2
from app.services.backtest.research_repository import (
    ImmutableRecordConflict,
    ResearchBacktestRepository,
)


_FINGERPRINT = "a" * 64
_POLICY_FINGERPRINT = "b" * 64
_SNAPSHOT_ID = "snapshot_claim_fixture"


@pytest.fixture
def repository():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            BacktestDataSnapshot.__table__,
            BacktestRequestClaim.__table__,
            BacktestRunV2.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        repo = ResearchBacktestRepository(session)
        repo.persist_snapshot(
            snapshot_id=_SNAPSHOT_ID,
            snapshot_version=2,
            partition_manifest={
                "schema_version": 2,
                "partitions": [],
                "artifacts_by_signal_date": {},
            },
            coverage={},
            missing_requirements=[],
        )
        session.commit()
        yield repo
    finally:
        session.close()
        engine.dispose()


def _claim(
    repository: ResearchBacktestRepository,
    *,
    fingerprint: str = _FINGERPRINT,
    backtest_id: str,
    force_rerun: bool = False,
):
    return repository.claim_request(
        request_fingerprint=fingerprint,
        backtest_id=backtest_id,
        snapshot_id=_SNAPSHOT_ID,
        strategy_id="claim-contract-strategy",
        strategy_version="strategy-v1",
        request_schema_version=1,
        request_json={"strategy_id": "claim-contract-strategy"},
        execution_version="execution-v1:claim-contract",
        execution_policy_fingerprint=_POLICY_FINGERPRINT,
        signal_date="2026-01-02",
        force_rerun=force_rerun,
    )


def test_claim_reuses_active_and_completed_attempts_then_force_reruns(repository) -> None:
    first = _claim(repository, backtest_id="btr_claim_1")
    assert (first.backtest_id, first.attempt_no, first.status, first.reused) == (
        "btr_claim_1",
        1,
        "queued",
        False,
    )
    active_reuse = _claim(repository, backtest_id="btr_unused_while_active", force_rerun=True)
    assert active_reuse.backtest_id == "btr_claim_1"
    assert active_reuse.reused is True
    assert active_reuse.reuse_reason == "active"

    claim = repository.session.get(BacktestRequestClaim, _FINGERPRINT)
    assert claim.active_backtest_id == "btr_claim_1"
    assert claim.completed_backtest_id is None
    assert claim.next_attempt_no == 2

    repository.mark_running("btr_claim_1")
    repository.complete_run("btr_claim_1", result={"status": "completed"})
    repository.session.commit()

    completed_reuse = _claim(repository, backtest_id="btr_unused_after_complete")
    assert completed_reuse.backtest_id == "btr_claim_1"
    assert completed_reuse.attempt_no == 1
    assert completed_reuse.reuse_reason == "completed"

    forced = _claim(repository, backtest_id="btr_claim_2", force_rerun=True)
    assert (forced.backtest_id, forced.attempt_no, forced.reused) == ("btr_claim_2", 2, False)
    forced_row = repository.get_run("btr_claim_2")
    assert forced_row.parent_backtest_id == "btr_claim_1"

    claim = repository.session.get(BacktestRequestClaim, _FINGERPRINT)
    assert claim.active_backtest_id == "btr_claim_2"
    assert claim.completed_backtest_id == "btr_claim_1"
    assert claim.next_attempt_no == 3


def test_retriable_terminal_attempt_creates_next_attempt_and_preserves_history(repository) -> None:
    first = _claim(repository, fingerprint="c" * 64, backtest_id="btr_retry_1")
    repository.mark_running(first.backtest_id)
    repository.fail_run(
        first.backtest_id,
        outcome_status="incomplete_evaluation",
        result={"status": "incomplete_evaluation"},
    )
    repository.session.commit()

    retry = _claim(repository, fingerprint="c" * 64, backtest_id="btr_retry_2")
    assert (retry.backtest_id, retry.attempt_no, retry.reused) == ("btr_retry_2", 2, False)
    retry_row = repository.get_run("btr_retry_2")
    assert retry_row.parent_backtest_id == "btr_retry_1"
    assert repository.get_run("btr_retry_1").status == "incomplete_evaluation"


def test_claim_rejects_backtest_id_reuse_across_semantic_requests(repository) -> None:
    _claim(repository, backtest_id="btr_immutable_id")
    with pytest.raises(ImmutableRecordConflict):
        _claim(
            repository,
            fingerprint="d" * 64,
            backtest_id="btr_immutable_id",
        )
