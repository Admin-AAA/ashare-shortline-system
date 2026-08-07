"""Explicit lifecycle coordinator around the recommendation run contracts.

The legacy workbench still owns business stages, but all status, lease and checkpoint
mutations go through this module and :class:`RunRepository`. Provider/session
resources and future stages are constructor-injected so this module stays independent
of Agent, database, temporary-file, and vendor implementations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import inspect
import logging
import os
import socket
from typing import TYPE_CHECKING, Final, Protocol, Sequence, Union, runtime_checkable

from .contracts import RUN_PHASE_ORDER, RunActor, RunPhase, RunRequest, RunStageResult, RunStatus, RunTrigger
from .repository import (
    CheckpointCommit,
    CompareAndSetOutcome,
    LeaseRenewal,
    LeaseRequest,
    RunCheckpoint,
    RunRecord,
    RunRepository,
    TransitionCommit,
)
from .state_machine import ResumeCheck, RunStateMachine
from .stages.contracts import StageData, StageOutput

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
    """Opaque ownership proof held by one worker between lifecycle CAS writes."""

    run_id: str
    token: str
    revision: int

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("worker lease requires a non-empty run_id")
        if not self.token.strip():
            raise ValueError("worker lease requires a non-empty token")
        if self.revision < 0:
            raise ValueError("worker lease revision cannot be negative")


@dataclass(frozen=True, slots=True)
class RunContext:
    """Immutable typed state supplied to one explicitly registered stage.

    The context is a projection of the durable run record at the point a worker owns
    its lease. It deliberately has no arbitrary payload dictionary: stage-specific
    recommendation, progress, market, and review data stay behind that stage's
    constructor-injected Port until those legacy bodies are migrated in later tasks.
    """

    run_id: str
    request: RunRequest
    worker_lease: WorkerLease
    checkpoint: RunCheckpoint | None = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run context requires a non-empty run_id")
        if self.worker_lease.run_id != self.run_id:
            raise ValueError("run context lease must belong to the same run_id")
        if self.worker_lease.revision < 0:
            raise ValueError("run context lease revision cannot be negative")

    @property
    def last_committed_phase(self) -> RunPhase | None:
        """Durable checkpoint from which the coordinator calculates the next stage."""
        return self.checkpoint.phase if self.checkpoint is not None else None

    @property
    def side_effect_key(self) -> str | None:
        """Idempotency key paired atomically with ``last_committed_phase``."""
        return self.checkpoint.side_effect_key if self.checkpoint is not None else None

    def with_checkpoint(self, checkpoint: RunCheckpoint, *, revision: int) -> "RunContext":
        """Return the next immutable context after the repository CAS succeeds."""
        return RunContext(
            run_id=self.run_id,
            request=self.request,
            worker_lease=WorkerLease(
                run_id=self.run_id,
                token=self.worker_lease.token,
                revision=revision,
            ),
            checkpoint=checkpoint,
        )


@runtime_checkable
class RunStage(Protocol):
    """A synchronous constructor-injected run stage."""

    phase: RunPhase

    def execute(self, context: RunContext, data: StageData) -> StageOutput:
        """Perform one side effect and return a typed hand-off to the next stage."""


@runtime_checkable
class AsyncRunStage(Protocol):
    """An async constructor-injected run stage (e.g. LLM review with concurrency)."""

    phase: RunPhase

    async def execute(self, context: RunContext, data: StageData) -> StageOutput:
        """Perform one async side effect and return a typed hand-off to the next stage."""


AnyStage = RunStage | AsyncRunStage


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
    dedicated DB/session wrapper, and a temporary artifact directory. ``release`` is
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
            # Cleanup is best effort after the lifecycle CAS. Preserve the original
            # run outcome while still making the error visible to operators.
            logger.warning("one or more run resource leases failed to release: %s", first_error)


@dataclass(frozen=True, slots=True)
class RunCoordinatorDependencies:
    """The single composition input used by production and coordinator tests.

    Stages are pre-constructed with their own explicit Ports/repositories. The
    coordinator receives only their protocol, avoiding vendor/ORM imports while
    allowing later tasks to migrate one legacy phase at a time.
    """

    repository: RunRepository
    stages: tuple[AnyStage, ...] = ()
    resource_leases: RunResourceLeases | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stages", tuple(self.stages))


class RunCoordinator:
    """Coordinate durable lifecycle transitions and typed stage checkpoints."""

    def __init__(
        self,
        repository: RunRepository,
        resource_leases: RunResourceLeases | None = None,
        *,
        stages: Sequence[AnyStage] = (),
    ) -> None:
        self._repository = repository
        self._resource_leases = resource_leases
        self._stages = self._index_stages(stages)

    @classmethod
    def from_dependencies(cls, dependencies: RunCoordinatorDependencies) -> "RunCoordinator":
        """Build a coordinator through the shared dependency-assembly entry point."""
        return cls(
            dependencies.repository,
            dependencies.resource_leases,
            stages=dependencies.stages,
        )

    @staticmethod
    def _index_stages(stages: Sequence[AnyStage]) -> dict[RunPhase, AnyStage]:
        indexed: dict[RunPhase, AnyStage] = {}
        for stage in stages:
            phase = stage.phase
            if phase not in RUN_PHASE_ORDER:
                raise ValueError(f"stage phase {phase.value} is not executable")
            if phase in indexed:
                raise ValueError(f"duplicate stage registration for {phase.value}")
            indexed[phase] = stage
        return indexed

    @property
    def stage_phases(self) -> tuple[RunPhase, ...]:
        """Registered phases in their one canonical execution order."""
        return tuple(phase for phase in RUN_PHASE_ORDER if phase in self._stages)

    @staticmethod
    def worker_owner_id() -> str:
        return f"{socket.gethostname()}:{os.getpid()}"

    def _release_resources(self) -> None:
        if self._resource_leases is not None:
            self._resource_leases.release()

    @staticmethod
    def _context_from_record(
        record: RunRecord,
        worker_lease: WorkerLease,
        *,
        now: datetime,
    ) -> RunContext | None:
        """Project a record only when it still belongs to this exact worker lease."""
        if (
            record.run_id != worker_lease.run_id
            or record.status is not RunStatus.RUNNING
            or record.revision != worker_lease.revision
            or record.lease is None
            or record.lease.token != worker_lease.token
            or not record.lease.is_active_at(now)
        ):
            return None
        return RunContext(
            run_id=record.run_id,
            request=record.request,
            worker_lease=worker_lease,
            checkpoint=record.checkpoint,
        )

    def next_stage(
        self,
        worker_lease: WorkerLease,
        *,
        now: datetime,
    ) -> tuple[AnyStage, RunContext] | None:
        """Load the sole legal next stage owned by the calling worker.

        Missing registrations deliberately return ``None``: task 5.1 only installs
        the stage contract, and later tasks migrate legacy phases incrementally. An
        out-of-order durable checkpoint still raises from the state machine rather
        than being silently skipped.
        """
        record = self._repository.get_run(worker_lease.run_id)
        if record is None:
            return None
        context = self._context_from_record(record, worker_lease, now=now)
        if context is None:
            return None
        phase = RunStateMachine.next_phase(context.last_committed_phase)
        if phase is None:
            return None
        stage = self._stages.get(phase)
        return (stage, context) if stage is not None else None

    def commit_stage_result(
        self,
        context: RunContext,
        result: RunStageResult,
        *,
        now: datetime,
    ) -> RunContext | None:
        """Atomically checkpoint a completed stage after its side effect succeeds.

        The single repository operation writes phase, side-effect key, and heartbeat
        under the context's revision and lease token. A lost lease or stale revision
        returns ``None`` and leaves recovery/error policy to the existing lifecycle
        caller; this helper intentionally does not change legacy phase error paths.
        """
        RunStateMachine.require_next_checkpoint(
            last_committed_phase=context.last_committed_phase,
            phase=result.phase,
        )
        checkpoint = RunCheckpoint(
            phase=result.phase,
            side_effect_key=result.side_effect_key,
            heartbeat_at=now,
        )
        committed = self._repository.commit_checkpoint(
            context.run_id,
            CheckpointCommit(
                expected_revision=context.worker_lease.revision,
                lease_token=context.worker_lease.token,
                checkpoint=checkpoint,
            ),
        )
        if not committed.applied or committed.record is None:
            return None
        return self._context_from_record(
            committed.record,
            WorkerLease(
                run_id=context.worker_lease.run_id,
                token=context.worker_lease.token,
                revision=committed.record.revision,
            ),
            now=now,
        )

    def execute_next_stage(
        self,
        worker_lease: WorkerLease,
        *,
        now: datetime,
        data: StageData | None = None,
    ) -> tuple[StageOutput, RunContext] | None:
        """Execute and atomically checkpoint one registered next stage.

        Synchronous stages run inline; async stages must be awaited by the caller
        through :meth:`execute_next_stage_async`.  Stage exceptions intentionally
        propagate so the caller's failure classification and resource cleanup path
        owns the terminal CAS.
        """
        pending = self.next_stage(worker_lease, now=now)
        if pending is None:
            return None
        stage, context = pending
        if inspect.iscoroutinefunction(getattr(stage, "execute", None)):
            raise TypeError(
                f"stage {stage.phase.value} is async; await execute_next_stage_async instead"
            )
        stage_data = data if data is not None else StageData()
        output = stage.execute(context, stage_data)
        if output.result.phase is not stage.phase:
            raise ValueError(
                f"stage {stage.phase.value} returned result for {output.result.phase.value}"
            )
        committed_context = self.commit_stage_result(context, output.result, now=now)
        if committed_context is None:
            return None
        return output, committed_context

    async def execute_next_stage_async(
        self,
        worker_lease: WorkerLease,
        *,
        now: datetime,
        data: StageData | None = None,
    ) -> tuple[StageOutput, RunContext] | None:
        """Async variant for :class:`AsyncRunStage` phases such as review."""
        pending = self.next_stage(worker_lease, now=now)
        if pending is None:
            return None
        stage, context = pending
        stage_data = data if data is not None else StageData()
        if inspect.iscoroutinefunction(getattr(stage, "execute", None)):
            output = await stage.execute(context, stage_data)
        else:
            output = stage.execute(context, stage_data)
        if output.result.phase is not stage.phase:
            raise ValueError(
                f"stage {stage.phase.value} returned result for {output.result.phase.value}"
            )
        committed_context = self.commit_stage_result(context, output.result, now=now)
        if committed_context is None:
            return None
        return output, committed_context

    def start_dispatch(self, run_id: str, *, now: datetime) -> bool:
        """Claim a queued run for one dispatcher before any worker may start."""
        record = self._repository.get_run(run_id)
        if record is None or record.status is not RunStatus.QUEUED:
            return False
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

    def renew_worker_lease(
        self,
        worker_lease: WorkerLease,
        *,
        now: datetime,
        ttl: timedelta,
    ) -> WorkerLease | None:
        """Renew only the calling worker's lease and return its new revision."""
        renewed = self._repository.renew_lease(
            worker_lease.run_id,
            LeaseRenewal(
                token=worker_lease.token,
                now=now,
                ttl=ttl,
                expected_revision=worker_lease.revision,
            ),
        )
        if not renewed.applied or renewed.record is None:
            return None
        return WorkerLease(
            run_id=worker_lease.run_id,
            token=worker_lease.token,
            revision=renewed.record.revision,
        )

    def spawn_failed(self, run_id: str, *, now: datetime, message: str) -> bool:
        """Fail only a queued run whose worker has not acquired ownership."""
        try:
            record = self._repository.get_run(run_id)
            if record is None or record.status is not RunStatus.QUEUED:
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
        run_id: str | WorkerLease,
        *,
        now: datetime,
        actor: RunActor = RunActor.API,
        message: str = "run cancelled",
    ) -> bool:
        """Cancel work through one CAS, requiring proof for worker-originated cancellation."""
        try:
            worker_lease = run_id if isinstance(run_id, WorkerLease) else None
            normalized_run_id = worker_lease.run_id if worker_lease is not None else run_id
            if actor is RunActor.WORKER and worker_lease is None:
                return False
            if actor is not RunActor.WORKER and worker_lease is not None:
                return False
            record = self._repository.get_run(normalized_run_id)
            if record is None or record.status not in {
                RunStatus.QUEUED,
                RunStatus.RUNNING,
                RunStatus.INTERRUPTED,
            }:
                return False
            if worker_lease is not None and self._context_from_record(
                record,
                worker_lease,
                now=now,
            ) is None:
                return False
            transition = RunStateMachine.transition(
                current_status=record.status,
                trigger=RunTrigger.CANCEL,
                actor=actor,
                error_message=message,
            )
            result = self._repository.compare_and_set_transition(
                normalized_run_id,
                TransitionCommit(
                    expected_revision=(
                        worker_lease.revision if worker_lease is not None else record.revision
                    ),
                    transition=transition,
                    now=now,
                    lease_token=worker_lease.token if worker_lease is not None else None,
                ),
            )
            return result.applied
        finally:
            self._release_resources()

    def resume(
        self,
        run_id: str,
        *,
        now: datetime,
        verified_resume: ResumeCheck,
    ) -> bool:
        """Resume only a persistence checkpoint verified by the database capability."""
        record = self._repository.get_run(run_id)
        if record is None or record.status is not RunStatus.INTERRUPTED:
            return False
        if (
            verified_resume.last_committed_phase is not record.last_committed_phase
            or verified_resume.side_effect_key != record.side_effect_key
            or record.has_active_lease(now)
        ):
            return False
        transition = RunStateMachine.transition(
            current_status=record.status,
            trigger=RunTrigger.RESUME,
            actor=RunActor.OPERATOR,
            resume=verified_resume,
        )
        result = self._repository.compare_and_set_transition(
            run_id,
            TransitionCommit(expected_revision=record.revision, transition=transition, now=now),
        )
        return result.applied

    def sync_checkpoint(
        self,
        worker_lease: WorkerLease,
        *,
        legacy_phase: str,
        now: datetime,
    ) -> WorkerLease | None:
        """Checkpoint a legacy phase only for the caller-owned worker lease."""
        phase = LEGACY_PHASE_TO_RUN_PHASE.get(legacy_phase)
        if phase is None or phase is RunPhase.COMPLETED:
            return None
        record = self._repository.get_run(worker_lease.run_id)
        if record is None or self._context_from_record(record, worker_lease, now=now) is None:
            return None
        if record.last_committed_phase is phase:
            # Legacy subphases (for example market_data/scope_filter) share one
            # durable phase; they are audit-only after the first atomic commit.
            return worker_lease
        try:
            RunStateMachine.require_next_checkpoint(
                last_committed_phase=record.last_committed_phase,
                phase=phase,
            )
        except ValueError:
            return None
        result = self._repository.commit_checkpoint(
            worker_lease.run_id,
            CheckpointCommit(
                expected_revision=worker_lease.revision,
                lease_token=worker_lease.token,
                checkpoint=RunCheckpoint(
                    phase=phase,
                    side_effect_key=f"legacy:{worker_lease.run_id}:{legacy_phase}",
                    heartbeat_at=now,
                ),
            ),
        )
        if not result.applied or result.record is None:
            return None
        return WorkerLease(
            run_id=worker_lease.run_id,
            token=worker_lease.token,
            revision=result.record.revision,
        )

    def complete(self, worker_lease: WorkerLease, *, now: datetime) -> bool:
        try:
            record = self._repository.get_run(worker_lease.run_id)
            if (
                record is None
                or record.status is not RunStatus.RUNNING
                or record.revision != worker_lease.revision
                or record.lease is None
                or record.lease.token != worker_lease.token
            ):
                return False
            transition = RunStateMachine.transition(
                current_status=record.status,
                trigger=RunTrigger.COMPLETE,
                actor=RunActor.WORKER,
                last_committed_phase=record.last_committed_phase,
                side_effect_key=record.side_effect_key,
            )
            result = self._repository.compare_and_set_transition(
                worker_lease.run_id,
                TransitionCommit(
                    expected_revision=worker_lease.revision,
                    transition=transition,
                    now=now,
                    lease_token=worker_lease.token,
                ),
            )
            return result.applied
        finally:
            self._release_resources()

    def fail(self, worker_lease: WorkerLease, *, now: datetime, message: str) -> bool:
        try:
            record = self._repository.get_run(worker_lease.run_id)
            if (
                record is None
                or record.status is not RunStatus.RUNNING
                or record.revision != worker_lease.revision
                or record.lease is None
                or record.lease.token != worker_lease.token
            ):
                return False
            transition = RunStateMachine.transition(
                current_status=record.status,
                trigger=RunTrigger.STAGE_FAILED,
                actor=RunActor.WORKER,
                error_message=message,
            )
            result = self._repository.compare_and_set_transition(
                worker_lease.run_id,
                TransitionCommit(
                    expected_revision=worker_lease.revision,
                    transition=transition,
                    now=now,
                    lease_token=worker_lease.token,
                ),
            )
            return result.applied
        finally:
            self._release_resources()

    def resume_next_phase(
        self,
        run_id: str,
        *,
        now: datetime,
        verified_resume: ResumeCheck,
    ) -> None:
        """Validate completion-only recovery; no business phase may execute."""
        record = self._repository.get_run(run_id)
        if (
            record is None
            or record.status is not RunStatus.INTERRUPTED
            or record.last_committed_phase is not verified_resume.last_committed_phase
            or record.side_effect_key != verified_resume.side_effect_key
            or record.has_active_lease(now)
        ):
            return None
        return RunStateMachine.validate_resume(verified_resume)


class LegacyWorkbenchRunCoordinator(RunCoordinator):
    """Compatibility name for existing lifecycle callers pending the task 5.7 switch.

    New code must use :class:`RunCoordinator`; this subclass preserves current
    imports without changing the legacy workbench phase bodies or call sites.
    """


def assemble_run_coordinator(dependencies: RunCoordinatorDependencies) -> RunCoordinator:
    """Build the public lifecycle coordinator from the shared composition contract."""
    return RunCoordinator.from_dependencies(dependencies)


__all__ = [
    "LEGACY_PHASE_TO_RUN_PHASE",
    "LegacyWorkbenchRunCoordinator",
    "AnyStage",
    "AsyncRunStage",
    "ResourceLease",
    "RunContext",
    "RunCoordinator",
    "RunCoordinatorDependencies",
    "RunResourceLeases",
    "RunStage",
    "RunStageResult",
    "WorkerLease",
    "assemble_run_coordinator",
]
