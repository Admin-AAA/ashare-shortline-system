"""Adapter-facing repository contracts for durable, exclusive recommendation runs.

No SQLAlchemy models are imported here.  A future MySQL adapter must map these value
objects to ``recommendation_runs`` plus the forward migration's request, revision and
lease columns.  The Port makes transaction boundaries and compare-and-set semantics
observable in tests before that adapter is wired into ``workbench_service``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol, runtime_checkable

from app.services.runs.contracts import (
    RunErrorCode,
    RunPhase,
    RunRequest,
    RunStatus,
    RunTransition,
)


class CompareAndSetOutcome(str, Enum):
    APPLIED = "applied"
    NOT_FOUND = "not_found"
    DUPLICATE = "duplicate"
    REVISION_MISMATCH = "revision_mismatch"
    STATUS_MISMATCH = "status_mismatch"
    CONFLICT = "conflict"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True, slots=True)
class RunFailure:
    """Stable failure information safe for persistence and user-facing projection."""

    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("failure code 不能为空")
        if not self.message.strip():
            raise ValueError("failure message 不能为空")


@dataclass(frozen=True, slots=True)
class RunCheckpoint:
    """A side effect that was committed successfully and is therefore resumable."""

    phase: RunPhase
    side_effect_key: str
    heartbeat_at: datetime

    def __post_init__(self) -> None:
        if self.phase is RunPhase.COMPLETED:
            raise ValueError("completed 不能作为 checkpoint phase")
        if not self.side_effect_key.strip():
            raise ValueError("checkpoint 必须包含 side_effect_key")


@dataclass(frozen=True, slots=True)
class RunLease:
    """Opaque worker ownership proof; it is never part of request_json."""

    owner_id: str
    token: str
    acquired_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.owner_id.strip():
            raise ValueError("lease owner_id 不能为空")
        if not self.token.strip():
            raise ValueError("lease token 不能为空")
        if self.expires_at <= self.acquired_at:
            raise ValueError("lease expires_at 必须晚于 acquired_at")

    def is_active_at(self, now: datetime) -> bool:
        return self.expires_at > now


@dataclass(frozen=True, slots=True)
class RunRecord:
    """The complete repository projection required by a coordinator or adapter."""

    run_id: str
    request: RunRequest
    request_json: str
    request_schema_version: int
    execution_version: str | None
    status: RunStatus
    revision: int
    current_phase: RunPhase | None
    checkpoint: RunCheckpoint | None
    heartbeat_at: datetime | None
    lease: RunLease | None
    failure: RunFailure | None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id 不能为空")
        if self.revision < 0:
            raise ValueError("revision 不能为负数")
        if self.request_schema_version != self.request.schema_version:
            raise ValueError("request_schema_version 必须与 RunRequest 一致")
        if self.request_json != self.request.to_canonical_json():
            raise ValueError("request_json 必须是 RunRequest canonical JSON")
        if self.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED} and self.lease is not None:
            raise ValueError("终态 run 不能保留活跃 lease")

    @property
    def run_mode(self) -> str:
        """Convenience projection for adapters that preserve the indexed column."""
        return self.request.run_mode.value

    @property
    def last_committed_phase(self) -> RunPhase | None:
        """Expose the persisted checkpoint name without leaking storage details."""
        return self.checkpoint.phase if self.checkpoint is not None else None

    @property
    def side_effect_key(self) -> str | None:
        """Return the idempotency key tied to ``last_committed_phase``."""
        return self.checkpoint.side_effect_key if self.checkpoint is not None else None

    def has_active_lease(self, now: datetime) -> bool:
        """Whether another worker still owns this run at ``now``."""
        return self.lease is not None and self.lease.is_active_at(now)


@dataclass(frozen=True, slots=True)
class CreateQueuedRun:
    """Command persisted and committed before a dispatcher is permitted to spawn."""

    run_id: str
    request: RunRequest
    execution_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.execution_version, str) or not self.execution_version.strip():
            raise ValueError("新的 queued Run 必须提供非空 execution_version")

    @property
    def request_json(self) -> str:
        return self.request.to_canonical_json()

    @property
    def request_schema_version(self) -> int:
        return self.request.schema_version


@dataclass(frozen=True, slots=True)
class LeaseRequest:
    """An ownership request; an adapter creates the opaque token only on success."""

    owner_id: str
    now: datetime
    ttl: timedelta
    expected_revision: int | None = None

    def __post_init__(self) -> None:
        if not self.owner_id.strip():
            raise ValueError("lease owner_id 不能为空")
        if self.ttl <= timedelta(0):
            raise ValueError("lease ttl 必须大于零")
        if self.expected_revision is not None and self.expected_revision < 0:
            raise ValueError("expected_revision 不能为负数")


@dataclass(frozen=True, slots=True)
class LeaseRenewal:
    """A worker heartbeat that must compare the lease token atomically."""

    token: str
    now: datetime
    ttl: timedelta
    expected_revision: int | None = None

    def __post_init__(self) -> None:
        if not self.token.strip():
            raise ValueError("lease token 不能为空")
        if self.ttl <= timedelta(0):
            raise ValueError("lease ttl 必须大于零")


@dataclass(frozen=True, slots=True)
class CheckpointCommit:
    """Atomic run progress update after its corresponding side effect succeeds."""

    expected_revision: int
    lease_token: str
    checkpoint: RunCheckpoint

    def __post_init__(self) -> None:
        if self.expected_revision < 0:
            raise ValueError("expected_revision 不能为负数")
        if not self.lease_token.strip():
            raise ValueError("checkpoint commit 需要 lease token")


@dataclass(frozen=True, slots=True)
class TransitionCommit:
    """A state-machine decision plus the optimistic-concurrency expectations."""

    expected_revision: int
    transition: RunTransition
    now: datetime
    lease_token: str | None = None

    def __post_init__(self) -> None:
        if self.expected_revision < 0:
            raise ValueError("expected_revision 不能为负数")
        if self.lease_token is not None and not self.lease_token.strip():
            raise ValueError("lease token 不能为空白")


@dataclass(frozen=True, slots=True)
class CompareAndSetResult:
    """Conflict-aware result returned instead of silently overwriting a run."""

    outcome: CompareAndSetOutcome
    record: RunRecord | None
    stable_error_code: str | None = None

    @property
    def applied(self) -> bool:
        return self.outcome is CompareAndSetOutcome.APPLIED

    @classmethod
    def not_found(cls) -> "CompareAndSetResult":
        return cls(CompareAndSetOutcome.NOT_FOUND, None, RunErrorCode.RUN_NOT_FOUND.value)

    @classmethod
    def lease_lost(cls, record: RunRecord | None = None) -> "CompareAndSetResult":
        return cls(CompareAndSetOutcome.LEASE_LOST, record, RunErrorCode.LEASE_LOST.value)


@runtime_checkable
class RunRepository(Protocol):
    """A transactional repository Port, designed for a SQLAlchemy/MySQL adapter.

    Required implementation semantics:

    * ``create_queued_run`` inserts the indexed run columns and canonical request
      fields in one transaction.  The caller commits this write *before* spawning a
      worker.
    * every conditional write compares ``revision`` and the declared current status;
      conflicting workers return a non-``APPLIED`` result rather than overwriting.
    * lease acquire succeeds only when no unexpired lease exists; renew/release and
      worker-owned mutations compare the opaque token.
    * ``commit_checkpoint`` atomically persists ``last_committed_phase`` (the
      checkpoint phase), ``side_effect_key`` and ``heartbeat_at`` after the side
      effect transaction succeeds.  It must reject duplicate/out-of-order phases.
    * a terminal transition atomically clears the lease and writes the final status,
      failure information (if any), heartbeat and completion timestamp.
    """

    def create_queued_run(self, command: CreateQueuedRun) -> CompareAndSetResult:
        """Persist a queued run and canonical request; duplicate IDs return DUPLICATE."""

    def get_run(self, run_id: str) -> RunRecord | None:
        """Load and parse the canonical request rather than reconstructing payloads."""

    def acquire_lease(self, run_id: str, request: LeaseRequest) -> CompareAndSetResult:
        """CAS-acquire an expired/unowned lease and update heartbeat in one write."""

    def renew_lease(self, run_id: str, renewal: LeaseRenewal) -> CompareAndSetResult:
        """CAS-renew one active lease token and atomically advance heartbeat."""

    def release_lease(
        self,
        run_id: str,
        *,
        token: str,
        expected_revision: int | None = None,
    ) -> CompareAndSetResult:
        """Release only the caller's lease; a stale worker must not clear another's."""

    def commit_checkpoint(self, run_id: str, commit: CheckpointCommit) -> CompareAndSetResult:
        """Atomically commit phase, side-effect key and heartbeat after success."""

    def compare_and_set_transition(
        self,
        run_id: str,
        commit: TransitionCommit,
    ) -> CompareAndSetResult:
        """Apply a RunStateMachine transition only when status/revision/lease match."""


__all__ = [
    "CheckpointCommit",
    "CompareAndSetOutcome",
    "CompareAndSetResult",
    "CreateQueuedRun",
    "LeaseRenewal",
    "LeaseRequest",
    "RunCheckpoint",
    "RunFailure",
    "RunLease",
    "RunRecord",
    "RunRepository",
    "TransitionCommit",
]
