"""Task 1.3 lifecycle and repository contract tests."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import RecommendationRun
from app.services.runs.contracts import (
    RUN_ERROR_MESSAGE_MAX_CHARS,
    RunActor,
    RunErrorCode,
    RunPhase,
    RunStatus,
    RunTrigger,
    parse_run_request,
)
from app.services.runs.coordinator import (
    LegacyWorkbenchRunCoordinator,
    RunResourceLeases,
    WorkerLease,
)
from app.services.runs.repository import (
    CheckpointCommit,
    CompareAndSetOutcome,
    CreateQueuedRun,
    LeaseRequest,
    RunCheckpoint,
    TransitionCommit,
)
from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository
from app.services.runs.state_machine import (
    ResumeCheck,
    RunStateMachine,
    RunStateTransitionError,
)


NOW = datetime(2026, 7, 14, 9, 0, 0)


def _repository() -> tuple[Session, SQLAlchemyRunRepository]:
    engine = create_engine("sqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    session = Session(engine)
    return session, SQLAlchemyRunRepository(session)


def _create(repo: SQLAlchemyRunRepository, session: Session, run_id: str):
    created = repo.create_queued_run(
        CreateQueuedRun(
            run_id=run_id,
            request=parse_run_request({"run_mode": "experiment", "run_name": run_id}),
            execution_version="exec-v1",
            created_at=NOW,
        )
    )
    assert created.applied
    session.commit()
    return repo.get_run(run_id)


def _start_and_lease(repo: SQLAlchemyRunRepository, session: Session, run_id: str):
    record = repo.get_run(run_id)
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
            ttl=timedelta(seconds=10),
            expected_revision=record.revision,
        ),
    )
    assert acquired.applied and acquired.record is not None and acquired.record.lease is not None
    session.commit()
    return acquired.record


def _commit_all_phases(repo: SQLAlchemyRunRepository, session: Session, run_id: str, start_revision: int, token: str):
    revision = start_revision
    for index, phase in enumerate((
        RunPhase.PREPARE,
        RunPhase.MARKET_DATA,
        RunPhase.AUXILIARY_DATA,
        RunPhase.FEATURES,
        RunPhase.STRATEGY_EVALUATION,
        RunPhase.REVIEW,
        RunPhase.AGGREGATION,
        RunPhase.PERSISTENCE,
    ), start=2):
        committed = repo.commit_checkpoint(
            run_id,
            CheckpointCommit(
                expected_revision=revision,
                lease_token=token,
                checkpoint=RunCheckpoint(
                    phase=phase,
                    side_effect_key=f"{phase.value}:run",
                    heartbeat_at=NOW + timedelta(seconds=index),
                ),
            ),
        )
        assert committed.applied
        revision += 1
    session.commit()
    return repo.get_run(run_id)


def test_transition_table_declares_actor_target_terminal_and_recovery_contracts():
    queued_spawn = RunStateMachine.legal_transitions(RunStatus.QUEUED, actor=RunActor.DISPATCHER)
    spawn = next(item for item in queued_spawn if item.trigger is RunTrigger.SPAWN_FAILED)
    assert spawn.target_status is RunStatus.FAILED
    assert spawn.terminal is True
    assert spawn.stable_error_code is RunErrorCode.WORKER_SPAWN_FAILED

    running_watchdog = next(
        item for item in RunStateMachine.legal_transitions(RunStatus.RUNNING, actor=RunActor.WATCHDOG)
        if item.trigger is RunTrigger.HEARTBEAT_TIMEOUT
    )
    assert running_watchdog.target_status is RunStatus.INTERRUPTED
    assert running_watchdog.terminal is False
    assert running_watchdog.recovery_condition == "checkpoint_and_no_active_lease"

    complete = next(
        item for item in RunStateMachine.legal_transitions(RunStatus.RUNNING, actor=RunActor.WORKER)
        if item.trigger is RunTrigger.COMPLETE
    )
    assert complete.terminal is True
    assert complete.requires_checkpoint is True
    assert complete.requires_active_lease is True

    assert RunStateMachine.legal_transitions(RunStatus.COMPLETED) == ()
    assert RunStateMachine.legal_transitions(RunStatus.FAILED) == ()
    assert RunStateMachine.legal_transitions(RunStatus.CANCELLED) == ()


def test_repository_atomically_commits_checkpoint_tuple_and_completed_clears_lease():
    session, repo = _repository()
    try:
        _create(repo, session, "run-completed")
        leased = _start_and_lease(repo, session, "run-completed")
        assert leased.lease is not None

        checkpointed = _commit_all_phases(
            repo,
            session,
            "run-completed",
            leased.revision,
            leased.lease.token,
        )
        assert checkpointed is not None
        assert checkpointed.last_committed_phase is RunPhase.PERSISTENCE
        assert checkpointed.side_effect_key == "persistence:run"
        assert checkpointed.heartbeat_at == NOW + timedelta(seconds=9)

        completed_transition = RunStateMachine.transition(
            current_status=checkpointed.status,
            trigger=RunTrigger.COMPLETE,
            actor=RunActor.WORKER,
            last_committed_phase=checkpointed.last_committed_phase,
            side_effect_key=checkpointed.side_effect_key,
        )
        completed = repo.compare_and_set_transition(
            "run-completed",
            TransitionCommit(
                expected_revision=checkpointed.revision,
                transition=completed_transition,
                now=NOW + timedelta(seconds=10),
                lease_token=leased.lease.token,
            ),
        )
        assert completed.applied and completed.record is not None
        assert completed.record.status is RunStatus.COMPLETED
        assert completed.record.lease is None
        assert completed.record.revision == checkpointed.revision + 1
    finally:
        session.close()


def test_repository_rejects_stale_revision_and_expired_worker_lease():
    session, repo = _repository()
    try:
        _create(repo, session, "run-cas")
        leased = _start_and_lease(repo, session, "run-cas")
        assert leased.lease is not None

        current = repo.get_run("run-cas")
        assert current is not None and current.lease is not None
        watchdog_transition = RunStateMachine.transition(
            current_status=current.status,
            trigger=RunTrigger.HEARTBEAT_TIMEOUT,
            actor=RunActor.WATCHDOG,
        )
        live_watchdog = repo.compare_and_set_transition(
            "run-cas",
            TransitionCommit(
                expected_revision=current.revision,
                transition=watchdog_transition,
                now=NOW + timedelta(seconds=2),
            ),
        )
        assert live_watchdog.outcome is CompareAndSetOutcome.LEASE_LOST

        stale = repo.commit_checkpoint(
            "run-cas",
            CheckpointCommit(
                expected_revision=0,
                lease_token=leased.lease.token,
                checkpoint=RunCheckpoint(
                    phase=RunPhase.PREPARE,
                    side_effect_key="prepare:stale",
                    heartbeat_at=NOW + timedelta(seconds=2),
                ),
            ),
        )
        assert stale.outcome is CompareAndSetOutcome.REVISION_MISMATCH
        assert stale.stable_error_code == RunErrorCode.COMPARE_AND_SET_CONFLICT.value

        current = repo.get_run("run-cas")
        assert current is not None and current.lease is not None
        expired = repo.commit_checkpoint(
            "run-cas",
            CheckpointCommit(
                expected_revision=current.revision,
                lease_token=current.lease.token,
                checkpoint=RunCheckpoint(
                    phase=RunPhase.PREPARE,
                    side_effect_key="prepare:expired",
                    heartbeat_at=NOW + timedelta(seconds=20),
                ),
            ),
        )
        assert expired.outcome is CompareAndSetOutcome.LEASE_LOST
        unchanged = repo.get_run("run-cas")
        assert unchanged is not None and unchanged.last_committed_phase is None
    finally:
        session.close()


def test_two_repository_sessions_prevent_stale_transition_and_lease_claims():
    """Independent sessions can observe stale state but only one SQL CAS wins."""
    engine = create_engine("sqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    first_session = Session(engine)
    second_session = Session(engine)
    first = SQLAlchemyRunRepository(first_session)
    second = SQLAlchemyRunRepository(second_session)
    try:
        _create(first, first_session, "run-two-session-cas")

        first_queued = first.get_run("run-two-session-cas")
        second_queued = second.get_run("run-two-session-cas")
        assert first_queued is not None and second_queued is not None
        start = RunStateMachine.transition(
            current_status=first_queued.status,
            trigger=RunTrigger.START,
            actor=RunActor.DISPATCHER,
        )
        started = first.compare_and_set_transition(
            "run-two-session-cas",
            TransitionCommit(expected_revision=first_queued.revision, transition=start, now=NOW),
        )
        assert started.applied
        first_session.commit()

        # The second session deliberately uses the queued snapshot it read before
        # the first session committed.  Its conditional update must not overwrite.
        stale_start = second.compare_and_set_transition(
            "run-two-session-cas",
            TransitionCommit(expected_revision=second_queued.revision, transition=start, now=NOW),
        )
        assert stale_start.outcome is CompareAndSetOutcome.REVISION_MISMATCH
        assert stale_start.stable_error_code == RunErrorCode.COMPARE_AND_SET_CONFLICT.value

        first_running = first.get_run("run-two-session-cas")
        second_running = second.get_run("run-two-session-cas")
        assert first_running is not None and second_running is not None
        first_lease = first.acquire_lease(
            "run-two-session-cas",
            LeaseRequest(
                owner_id="first-worker",
                now=NOW + timedelta(seconds=1),
                ttl=timedelta(seconds=30),
                expected_revision=first_running.revision,
            ),
        )
        assert first_lease.applied and first_lease.record is not None
        assert first_lease.record.lease is not None
        first_session.commit()

        # A second worker retains a pre-claim revision but cannot replace the
        # committed opaque token or acquire an overlapping lease.
        stale_lease = second.acquire_lease(
            "run-two-session-cas",
            LeaseRequest(
                owner_id="second-worker",
                now=NOW + timedelta(seconds=2),
                ttl=timedelta(seconds=30),
                expected_revision=second_running.revision,
            ),
        )
        assert stale_lease.outcome is CompareAndSetOutcome.REVISION_MISMATCH
        persisted = second.get_run("run-two-session-cas")
        assert persisted is not None and persisted.lease is not None
        assert persisted.lease.owner_id == "first-worker"
        assert persisted.lease.token == first_lease.record.lease.token
    finally:
        first_session.close()
        second_session.close()
        engine.dispose()


def test_spawn_failure_failed_cancelled_and_interrupted_resume_are_explicit():
    session, repo = _repository()
    try:
        _create(repo, session, "run-spawn-failure")
        coordinator = LegacyWorkbenchRunCoordinator(repo)
        assert coordinator.spawn_failed(
            "run-spawn-failure",
            now=NOW + timedelta(seconds=1),
            message="Popen failed",
        )
        session.commit()
        failed = repo.get_run("run-spawn-failure")
        assert failed is not None and failed.status is RunStatus.FAILED
        assert failed.failure is not None
        assert failed.failure.code == RunErrorCode.WORKER_SPAWN_FAILED.value
        assert failed.lease is None

        _create(repo, session, "run-cancelled")
        assert coordinator.cancel("run-cancelled", now=NOW + timedelta(seconds=1))
        session.commit()
        cancelled = repo.get_run("run-cancelled")
        assert cancelled is not None and cancelled.status is RunStatus.CANCELLED
        assert cancelled.failure is not None
        assert cancelled.failure.code == RunErrorCode.RUN_CANCELLED.value

        _create(repo, session, "run-interrupted")
        leased = _start_and_lease(repo, session, "run-interrupted")
        assert leased.lease is not None
        first = repo.commit_checkpoint(
            "run-interrupted",
            CheckpointCommit(
                expected_revision=leased.revision,
                lease_token=leased.lease.token,
                checkpoint=RunCheckpoint(
                    phase=RunPhase.PREPARE,
                    side_effect_key="prepare:run-interrupted",
                    heartbeat_at=NOW + timedelta(seconds=2),
                ),
            ),
        )
        assert first.applied
        session.commit()
        assert coordinator.heartbeat_timeout(
            "run-interrupted",
            now=NOW + timedelta(seconds=20),
        )
        session.commit()
        interrupted = repo.get_run("run-interrupted")
        assert interrupted is not None
        assert interrupted.status is RunStatus.INTERRUPTED
        assert interrupted.lease is None
        assert interrupted.last_committed_phase is RunPhase.PREPARE

        with pytest.raises(RunStateTransitionError) as exc_info:
            coordinator.resume(
                "run-interrupted",
                now=NOW + timedelta(seconds=21),
                verified_resume=ResumeCheck(
                    last_committed_phase=RunPhase.PREPARE,
                    side_effect_key="prepare:run-interrupted",
                    has_active_lease=False,
                    collection_seal_verified=True,
                ),
            )
        assert (
            exc_info.value.code
            is RunErrorCode.RESUME_REQUIRES_PERSISTENCE_CHECKPOINT
        )
        unchanged = repo.get_run("run-interrupted")
        assert unchanged is not None
        assert unchanged.status is RunStatus.INTERRUPTED
        assert unchanged.last_committed_phase is RunPhase.PREPARE
    finally:
        session.close()


def test_stage_failure_and_terminal_paths_release_counted_agent_db_and_temp_leases():
    class CountedLease:
        def __init__(self) -> None:
            self.release_count = 0

        def release(self) -> None:
            self.release_count += 1

    session, repo = _repository()
    leases = [CountedLease(), CountedLease(), CountedLease()]
    resources = RunResourceLeases(
        agent=leases[0],
        database=leases[1],
        temporary=leases[2],
    )
    coordinator = LegacyWorkbenchRunCoordinator(repo, resources)
    try:
        _create(repo, session, "run-failed-resource-cleanup")
        leased = _start_and_lease(repo, session, "run-failed-resource-cleanup")
        assert leased.lease is not None
        worker_lease = WorkerLease(
            run_id="run-failed-resource-cleanup",
            token=leased.lease.token,
            revision=leased.revision,
        )
        assert coordinator.fail(
            worker_lease,
            now=NOW + timedelta(seconds=2),
            message="stage failed",
        )
        session.commit()
        assert [lease.release_count for lease in leases] == [1, 1, 1]
        assert resources.released

        # Cleanup is idempotent even if a caller retries terminal handling.
        assert coordinator.fail(
            worker_lease,
            now=NOW + timedelta(seconds=3),
            message="duplicate failure",
        ) is False
        assert [lease.release_count for lease in leases] == [1, 1, 1]
    finally:
        session.close()


def test_oversized_failure_message_is_clamped_so_the_failure_write_cannot_fail():
    """A failure message must never be able to overflow ``error_message`` (TEXT).

    Regression for a real production stall: a SQLAlchemy error carried its whole
    statement plus bound parameters (a large JSON column) into the message, the
    UPDATE recording the failure then raised DataError 1406 "Data too long for
    column 'error_message'", and the run sat in RUNNING until stale-run recovery
    relabelled it "服务重启或进程退出" — hiding the true cause.
    """
    oversized = "原始异常：" + "x" * 100_000

    transition = RunStateMachine.transition(
        current_status=RunStatus.RUNNING,
        trigger=RunTrigger.STAGE_FAILED,
        actor=RunActor.WORKER,
        error_message=oversized,
    )

    assert transition.error_message is not None
    assert len(transition.error_message) == RUN_ERROR_MESSAGE_MAX_CHARS
    # The head carries the diagnosis, and the tail says it was cut.
    assert transition.error_message.startswith("原始异常：")
    assert transition.error_message.endswith("（错误信息过长已截断）")
    # MySQL TEXT is byte-limited; stay inside it even for all-CJK messages.
    assert len(("中" * RUN_ERROR_MESSAGE_MAX_CHARS).encode("utf-8")) < 65535


def test_clamped_failure_message_persists_through_compare_and_set():
    session, repo = _repository()
    try:
        _create(repo, session, "run-oversized-failure")
        leased = _start_and_lease(repo, session, "run-oversized-failure")
        assert leased.lease is not None
        transition = RunStateMachine.transition(
            current_status=RunStatus.RUNNING,
            trigger=RunTrigger.STAGE_FAILED,
            actor=RunActor.WORKER,
            error_message="y" * 100_000,
        )
        assert repo.compare_and_set_transition(
            "run-oversized-failure",
            TransitionCommit(
                expected_revision=leased.revision,
                transition=transition,
                now=NOW + timedelta(seconds=2),
                lease_token=leased.lease.token,
            ),
        ).applied
        session.commit()

        row = session.get(RecommendationRun, "run-oversized-failure")
        assert row is not None
        assert row.status == "failed"
        assert row.error_message is not None
        assert len(row.error_message) == RUN_ERROR_MESSAGE_MAX_CHARS
    finally:
        session.close()


def test_short_failure_message_is_left_untouched():
    transition = RunStateMachine.transition(
        current_status=RunStatus.RUNNING,
        trigger=RunTrigger.STAGE_FAILED,
        actor=RunActor.WORKER,
        error_message="stage failed: no candidates",
    )
    assert transition.error_message == "stage failed: no candidates"
