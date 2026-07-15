"""Explicit lifecycle coordinator around the recommendation run contracts.

The legacy workbench still owns business stages, but all status, lease and checkpoint
mutations go through this module and :class:`RunRepository`.  Provider/session
resources are injected as small releasable leases so terminal and failure paths can be
verified without importing Agent, database, or temporary-file implementations here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import os
import socket
from typing import Final, Protocol, runtime_checkable

from .contracts import RunActor, RunPhase, RunStatus, RunTrigger
from .repository import (
    CheckpointCommit,
    CompareAndSetOutcome,
    LeaseRenewal,
    LeaseRequest,
    RunCheckpoint,
    TransitionCommit,
)
from .state_machine import ResumeCheck, RunStateMachine

logger = logging.getLogger(__name__)


LEGACY_PHASE_TO_RUN_PHASE: Final[dict[str, RunPhase]] = {
    "prepare": RunPhase.PREPARE,
    "market_data": RunPhase.MARKET_DATA,
    "scope_filter": RunPhase.MARKET_DATA,
    "auxiliary_data": RunPhase.AUXILIARY_DATA,
    "feature_build": RunPhase.FEATURES,
    "rule_scan": RunPhase.STRATEGY_EVALUATION,
    "skill_gate": RunPhase.STRATEGY_EVALUATION,
    "skill_review": RunPhase.REVIEW,
    "aggregate": RunPhase.AGGREGATION,
    "persist": RunPhase.PERSISTENCE,
    "completed": RunPhase.COMPLETED,
}


@dataclass(frozen=True, slots=True)
class WorkerLease:
    run_id: str
    token: str
    revision: int


@runtime_checkable
class ResourceLease(Protocol):
    """A process-local resource that must be released exactly once."""

    def release(self) -> None:
        """Release the resource held by the current run."""


@dataclass(slots=True)
class RunResourceLeases:
    """Optional Agent/DB/temporary-resource cleanup bundle.

    The durable database worker lease is cleared by the repository's terminal CAS;
    these three hooks cover process-local resources such as an Agent session, a
    dedicated DB/session wrapper, and a temporary artifact directory.  ``release`` is
    idempotent and attempts every hook even if one cleanup callback raises.
    """

    agent: ResourceLease | None = None
    database: ResourceLease | None = None
    temporary: ResourceLease | None = None
    _released: bool = field(default=False, init=False, repr=False)

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        first_error: Exception | None = None
        for lease in (self.agent, self.database, self.temporary):
            if lease is None:
                continue
            try:
                lease.release()
            except Exception as exc:  # cleanup must not strand the other resources
                logger.exception("run resource lease release failed")
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            # Cleanup is best effort after the lifecycle CAS.  Preserve the original
            # run outcome while still making the error visible to operators.
            logger.warning("one or more run resource leases failed to release: %s", first_error)


class LegacyWorkbenchRunCoordinator:
    """Translate legacy pipeline checkpoints to the durable state machine."""

    def __init__(self, repository, resource_leases: RunResourceLeases | None = None) -> None:
        self._repository = repository
        self._resource_leases = resource_leases

    @staticmethod
    def worker_owner_id() -> str:
        return f"{socket.gethostname()}:{os.getpid()}"

    def _release_resources(self) -> None:
        if self._resource_leases is not None:
            self._resource_leases.release()

    def start_dispatch(self, run_id: str, *, now: datetime) -> bool:
        record = self._repository.get_run(run_id)
        if record is None:
            return False
        if record.status is RunStatus.RUNNING:
            return True
        transition = RunStateMachine.transition(
            current_status=record.status,
            trigger=RunTrigger.START,
            actor=RunActor.DISPATCHER,
        )
        result = self._repository.compare_and_set_transition(
            run_id,
            TransitionCommit(expected_revision=record.revision, transition=transition, now=now),
        )
        return result.outcome is CompareAndSetOutcome.APPLIED

    def acquire_worker_lease(
        self,
        run_id: str,
        *,
        now: datetime,
        ttl: timedelta,
        owner_id: str | None = None,
    ) -> WorkerLease | None:
        record = self._repository.get_run(run_id)
        if record is None or record.status is not RunStatus.RUNNING:
            return None
        acquired = self._repository.acquire_lease(
            run_id,
            LeaseRequest(
                owner_id=owner_id or self.worker_owner_id(),
                now=now,
                ttl=ttl,
                expected_revision=record.revision,
            ),
        )
        if not acquired.applied or acquired.record is None or acquired.record.lease is None:
            return None
        return WorkerLease(run_id, acquired.record.lease.token, acquired.record.revision)

    def renew_worker_lease(self, run_id: str, *, now: datetime, ttl: timedelta) -> bool:
        """Advance heartbeat only while the exact stored lease is still unexpired."""
        record = self._repository.get_run(run_id)
        if record is None or record.status is not RunStatus.RUNNING or record.lease is None:
            return False
        renewed = self._repository.renew_lease(
            run_id,
            LeaseRenewal(
                token=record.lease.token,
                now=now,
                ttl=ttl,
                expected_revision=record.revision,
            ),
        )
        return renewed.applied

    def spawn_failed(self, run_id: str, *, now: datetime, message: str) -> bool:
        """Record dispatcher failure with ``worker_spawn_failed`` and release hooks."""
        try:
            record = self._repository.get_run(run_id)
            if record is None or record.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                return False
            transition = RunStateMachine.transition(
                current_status=record.status,
                trigger=RunTrigger.SPAWN_FAILED,
                actor=RunActor.DISPATCHER,
                error_message=message,
            )
            result = self._repository.compare_and_set_transition(
                run_id,
                TransitionCommit(expected_revision=record.revision, transition=transition, now=now),
            )
            return result.applied
        finally:
            self._release_resources()

    def heartbeat_timeout(self, run_id: str, *, now: datetime, message: str | None = None) -> bool:
        """Move a stale running run to interrupted and invalidate its durable lease."""
        try:
            record = self._repository.get_run(run_id)
            if record is None or record.status is not RunStatus.RUNNING:
                return False
            transition = RunStateMachine.transition(
                current_status=record.status,
                trigger=RunTrigger.HEARTBEAT_TIMEOUT,
                actor=RunActor.WATCHDOG,
                error_message=message or "worker heartbeat timeout",
            )
            result = self._repository.compare_and_set_transition(
                run_id,
                TransitionCommit(expected_revision=record.revision, transition=transition, now=now),
            )
            return result.applied
        finally:
            self._release_resources()

    def cancel(
        self,
        run_id: str,
        *,
        now: datetime,
        actor: RunActor = RunActor.API,
        message: str = "run cancelled",
    ) -> bool:
        """Cancel queued/running/interrupted work through one CAS transition."""
        try:
            record = self._repository.get_run(run_id)
            if record is None or record.status not in {
                RunStatus.QUEUED,
                RunStatus.RUNNING,
                RunStatus.INTERRUPTED,
            }:
                return False
            transition = RunStateMachine.transition(
                current_status=record.status,
                trigger=RunTrigger.CANCEL,
                actor=actor,
                error_message=message,
            )
            token = record.lease.token if actor is RunActor.WORKER and record.lease is not None else None
            result = self._repository.compare_and_set_transition(
                run_id,
                TransitionCommit(
                    expected_revision=record.revision,
                    transition=transition,
                    now=now,
                    lease_token=token,
                ),
            )
            return result.applied
        finally:
            self._release_resources()

    def resume(self, run_id: str, *, now: datetime) -> bool:
        """Validate an interrupted checkpoint before a worker leases it."""
        record = self._repository.get_run(run_id)
        if record is None or record.status is not RunStatus.INTERRUPTED:
            return False
        resume = ResumeCheck(
            last_committed_phase=record.last_committed_phase,
            side_effect_key=record.side_effect_key,
            has_active_lease=record.has_active_lease(now),
        )
        transition = RunStateMachine.transition(
            current_status=record.status,
            trigger=RunTrigger.RESUME,
            actor=RunActor.OPERATOR,
            resume=resume,
        )
        result = self._repository.compare_and_set_transition(
            run_id,
            TransitionCommit(expected_revision=record.revision, transition=transition, now=now),
        )
        return result.applied

    def sync_checkpoint(self, run_id: str, *, legacy_phase: str, now: datetime) -> bool:
        """Commit only the next distinct RunPhase; repeated legacy subphases are audit-only."""
        phase = LEGACY_PHASE_TO_RUN_PHASE.get(legacy_phase)
        if phase is None or phase is RunPhase.COMPLETED:
            return False
        record = self._repository.get_run(run_id)
        if record is None or record.status is not RunStatus.RUNNING or record.lease is None:
            return False
        if record.last_committed_phase is phase:
            # Legacy subphases (for example market_data/scope_filter) share one
            # durable phase; they are audit-only after the first atomic commit.
            return True
        try:
            RunStateMachine.require_next_checkpoint(
                last_committed_phase=record.last_committed_phase,
                phase=phase,
            )
        except ValueError:
            return False
        result = self._repository.commit_checkpoint(
            run_id,
            CheckpointCommit(
                expected_revision=record.revision,
                lease_token=record.lease.token,
                checkpoint=RunCheckpoint(
                    phase=phase,
                    side_effect_key=f"legacy:{run_id}:{legacy_phase}",
                    heartbeat_at=now,
                ),
            ),
        )
        return result.applied

    def complete(self, run_id: str, *, now: datetime) -> bool:
        try:
            record = self._repository.get_run(run_id)
            if record is None or record.status is not RunStatus.RUNNING or record.lease is None:
                return False
            transition = RunStateMachine.transition(
                current_status=record.status,
                trigger=RunTrigger.COMPLETE,
                actor=RunActor.WORKER,
                last_committed_phase=record.last_committed_phase,
                side_effect_key=record.side_effect_key,
            )
            result = self._repository.compare_and_set_transition(
                run_id,
                TransitionCommit(
                    expected_revision=record.revision,
                    transition=transition,
                    now=now,
                    lease_token=record.lease.token,
                ),
            )
            return result.applied
        finally:
            self._release_resources()

    def fail(self, run_id: str, *, now: datetime, message: str) -> bool:
        try:
            record = self._repository.get_run(run_id)
            if record is None or record.status is not RunStatus.RUNNING:
                return False
            transition = RunStateMachine.transition(
                current_status=record.status,
                trigger=RunTrigger.STAGE_FAILED,
                actor=RunActor.WORKER,
                error_message=message,
            )
            result = self._repository.compare_and_set_transition(
                run_id,
                TransitionCommit(
                    expected_revision=record.revision,
                    transition=transition,
                    now=now,
                    lease_token=record.lease.token if record.lease is not None else None,
                ),
            )
            return result.applied
        finally:
            self._release_resources()

    def resume_next_phase(self, run_id: str, *, now: datetime) -> RunPhase | None:
        record = self._repository.get_run(run_id)
        if record is None:
            return None
        return RunStateMachine.validate_resume(
            ResumeCheck(
                last_committed_phase=record.last_committed_phase,
                side_effect_key=record.side_effect_key,
                has_active_lease=record.has_active_lease(now),
            )
        )


__all__ = [
    "LEGACY_PHASE_TO_RUN_PHASE",
    "LegacyWorkbenchRunCoordinator",
    "ResourceLease",
    "RunResourceLeases",
    "WorkerLease",
]
