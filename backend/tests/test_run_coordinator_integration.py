"""S5 RunCoordinator integration tests.

These tests exercise the real RunCoordinator with constructor-injected fake
adapters, verifying that the staged pipeline executes correctly without
accessing external providers (no Tushare, no OpenAI, no MySQL).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import RecommendationRun
from app.services.runs.contracts import (
    RunActor,
    RunPhase,
    RunRequest,
    RunStageResult,
    RunStatus,
    RunTrigger,
    parse_run_request,
)
from app.services.runs.coordinator import (
    RunCoordinator,
    RunCoordinatorDependencies,
    RunContext,
    RunResourceLeases,
    StageData,
    StageOutput,
    WorkerLease,
)
from app.services.runs.repository import (
    CheckpointCommit,
    CreateQueuedRun,
    LeaseRequest,
    RunCheckpoint,
    TransitionCommit,
)
from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository
from app.services.runs.state_machine import RunStateMachine


NOW = datetime(2026, 7, 15, 9, 0, 0)


# ---------------------------------------------------------------------------
# Fake stages for testing
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FakeStage:
    """A synchronous stage that records its execution and produces typed output."""

    phase: RunPhase
    executed: list[bool] = ()

    def execute(self, context: RunContext, data: StageData) -> StageOutput:
        # Mutate the list via the dataclass default (not frozen).
        object.__setattr__(self, "executed", (*self.executed, True))
        result = RunStageResult(
            phase=self.phase,
            side_effect_key=f"{self.phase.value}:{context.run_id}",
        )
        return StageOutput(result=result, data=data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repository() -> tuple[Session, SQLAlchemyRunRepository]:
    engine = create_engine("sqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    session = Session(engine)
    return session, SQLAlchemyRunRepository(session)


def _create_and_start(repo: SQLAlchemyRunRepository, session: Session, run_id: str):
    created = repo.create_queued_run(
        CreateQueuedRun(
            run_id=run_id,
            request=parse_run_request({"run_mode": "experiment", "run_name": run_id}),
            execution_version="exec-test-v1",
            created_at=NOW,
        )
    )
    assert created.applied
    session.commit()
    return repo.get_run(run_id)


def _start_and_lease(repo: SQLAlchemyRunRepository, session: Session, run_id: str):
    record = _create_and_start(repo, session, run_id)
    assert record is not None
    started = RunStateMachine.transition(
        current_status=record.status,
        trigger=RunTrigger.START,
        actor=RunActor.DISPATCHER,
    )
    assert repo.compare_and_set_transition(
        run_id,
        TransitionCommit(expected_revision=record.revision, transition=started, now=NOW),
    ).applied
    session.commit()
    record = repo.get_run(run_id)
    assert record is not None
    acquired = repo.acquire_lease(
        run_id,
        LeaseRequest(
            owner_id="worker-test",
            now=NOW + timedelta(seconds=1),
            ttl=timedelta(seconds=1200),
            expected_revision=record.revision,
        ),
    )
    assert acquired.applied and acquired.record is not None and acquired.record.lease is not None
    session.commit()
    return acquired.record


def _worker_lease(record) -> WorkerLease:
    assert record is not None
    assert record.lease is not None
    return WorkerLease(
        run_id=record.run_id,
        token=record.lease.token,
        revision=record.revision,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_coordinator_executes_registered_sync_stages():
    """Verify that the coordinator drives each registered sync stage in order."""
    session, repo = _repository()
    try:
        lease = _worker_lease(_start_and_lease(repo, session, "run-sync-stages"))

        fake_stages = [
            FakeStage(phase=RunPhase.PREPARE),
            FakeStage(phase=RunPhase.MARKET_DATA),
            FakeStage(phase=RunPhase.AUXILIARY_DATA),
            FakeStage(phase=RunPhase.FEATURES),
            FakeStage(phase=RunPhase.STRATEGY_EVALUATION),
            FakeStage(phase=RunPhase.REVIEW),
            FakeStage(phase=RunPhase.AGGREGATION),
            FakeStage(phase=RunPhase.PERSISTENCE),
        ]
        deps = RunCoordinatorDependencies(repository=repo, stages=fake_stages)
        coord = RunCoordinator.from_dependencies(deps)

        data = StageData()
        for expected_phase in (
            RunPhase.PREPARE, RunPhase.MARKET_DATA, RunPhase.AUXILIARY_DATA,
            RunPhase.FEATURES, RunPhase.STRATEGY_EVALUATION, RunPhase.REVIEW,
            RunPhase.AGGREGATION, RunPhase.PERSISTENCE,
        ):
            result = coord.execute_next_stage(lease, now=NOW, data=data)
            assert result is not None
            output, context = result
            assert output.result.phase is expected_phase
            data = output.data
            lease = context.worker_lease

        # All stages should have been executed exactly once.
        for stage in fake_stages:
            assert stage.executed == (True,), f"{stage.phase.value} not executed"

        # Verify the final checkpoint is PERSISTENCE.
        record = repo.get_run("run-sync-stages")
        assert record is not None
        assert record.last_committed_phase is RunPhase.PERSISTENCE
    finally:
        session.close()


def test_coordinator_execute_next_stage_returns_none_when_no_more_stages():
    """After all stages are committed, execute_next_stage returns None."""
    session, repo = _repository()
    try:
        lease = _worker_lease(_start_and_lease(repo, session, "run-no-more"))

        stage = FakeStage(phase=RunPhase.PREPARE)
        deps = RunCoordinatorDependencies(repository=repo, stages=(stage,))
        coord = RunCoordinator.from_dependencies(deps)

        result = coord.execute_next_stage(lease, now=NOW)
        assert result is not None  # first stage

        result = coord.execute_next_stage(lease, now=NOW)
        assert result is None  # no more stages registered
    finally:
        session.close()


def test_stage_data_accumulates_across_stages():
    """Verify that StageData is carried forward between stages."""
    session, repo = _repository()
    try:
        lease = _worker_lease(_start_and_lease(repo, session, "run-data-accum"))

        @dataclass(frozen=True, slots=True)
        class PrepareWithData:
            phase: RunPhase = RunPhase.PREPARE

            def execute(self, context: RunContext, data: StageData) -> StageOutput:
                result = RunStageResult(phase=self.phase, side_effect_key=f"prepare:{context.run_id}")
                return StageOutput(result=result, data=data.with_updates(execution_version="v1"))

        @dataclass(frozen=True, slots=True)
        class MarketWithData:
            phase: RunPhase = RunPhase.MARKET_DATA

            def execute(self, context: RunContext, data: StageData) -> StageOutput:
                # Should see execution_version from prepare.
                assert data.execution_version == "v1"
                result = RunStageResult(phase=self.phase, side_effect_key=f"market:{context.run_id}")
                return StageOutput(result=result, data=data.with_updates(params={"test": True}))

        deps = RunCoordinatorDependencies(
            repository=repo,
            stages=(PrepareWithData(), MarketWithData()),
        )
        coord = RunCoordinator.from_dependencies(deps)

        data = StageData()
        r1 = coord.execute_next_stage(lease, now=NOW, data=data)
        assert r1 is not None
        output1, context1 = r1
        assert output1.data.execution_version == "v1"

        r2 = coord.execute_next_stage(context1.worker_lease, now=NOW, data=output1.data)
        assert r2 is not None
        output2, _ = r2
        assert output2.data.execution_version == "v1"
        assert output2.data.params == {"test": True}
    finally:
        session.close()


def test_coordinator_fails_stage_propagates_exception():
    """A failing stage raises; the caller handles the terminal CAS."""
    session, repo = _repository()
    try:
        lease = _worker_lease(_start_and_lease(repo, session, "run-fail-stage"))

        @dataclass(frozen=True, slots=True)
        class FailingStage:
            phase: RunPhase = RunPhase.PREPARE

            def execute(self, context: RunContext, data: StageData) -> StageOutput:
                raise RuntimeError("stage exploded")

        deps = RunCoordinatorDependencies(repository=repo, stages=(FailingStage(),))
        coord = RunCoordinator.from_dependencies(deps)

        try:
            coord.execute_next_stage(lease, now=NOW)
            assert False, "should have raised"
        except RuntimeError as exc:
            assert "stage exploded" in str(exc)

        # The caller (pipeline) would then call coordinator.fail().
        assert coord.fail(lease, now=NOW, message="stage exploded")
        session.commit()

        record = repo.get_run("run-fail-stage")
        assert record is not None
        assert record.status is RunStatus.FAILED
    finally:
        session.close()


def test_resource_leases_released_on_failure():
    """When a stage fails, counted Agent/DB/temp leases are all released."""
    session, repo = _repository()

    class CountedLease:
        def __init__(self) -> None:
            self.release_count = 0
        def release(self) -> None:
            self.release_count += 1

    try:
        lease = _worker_lease(_start_and_lease(repo, session, "run-resource-fail"))
        leases = [CountedLease(), CountedLease(), CountedLease()]
        resources = RunResourceLeases(
            agent=leases[0], database=leases[1], temporary=leases[2],
        )

        @dataclass(frozen=True, slots=True)
        class FailingStage:
            phase: RunPhase = RunPhase.PREPARE
            def execute(self, context: RunContext, data: StageData) -> StageOutput:
                raise RuntimeError("boom")

        deps = RunCoordinatorDependencies(repository=repo, stages=(FailingStage(),), resource_leases=resources)
        coord = RunCoordinator.from_dependencies(deps)

        try:
            coord.execute_next_stage(lease, now=NOW)
        except RuntimeError:
            pass

        # The pipeline caller would call coordinator.fail(), which releases resources.
        coord.fail(lease, now=NOW, message="boom")
        session.commit()

        assert [lease.release_count for lease in leases] == [1, 1, 1]
        assert resources.released
    finally:
        session.close()


def test_checkpoint_ordering_is_enforced():
    """A stage that returns the wrong phase is rejected."""
    session, repo = _repository()
    try:
        lease = _worker_lease(_start_and_lease(repo, session, "run-order-check"))

        @dataclass(frozen=True, slots=True)
        class WrongPhaseStage:
            phase: RunPhase = RunPhase.PREPARE
            def execute(self, context: RunContext, data: StageData) -> StageOutput:
                # Returns result for FEATURES instead of PREPARE.
                result = RunStageResult(phase=RunPhase.FEATURES, side_effect_key="wrong")
                return StageOutput(result=result, data=data)

        deps = RunCoordinatorDependencies(repository=repo, stages=(WrongPhaseStage(),))
        coord = RunCoordinator.from_dependencies(deps)

        try:
            coord.execute_next_stage(lease, now=NOW)
            assert False, "should have raised"
        except ValueError as exc:
            assert "features" in str(exc).lower()
    finally:
        session.close()
