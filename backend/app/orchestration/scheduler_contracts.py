"""Pure S7 scheduler reliability contracts.

The module owns no process, thread, database session, Prefect client, or network
resource.  It models the fencing/CAS decisions used by the existing Prefect
control plane and by :mod:`app.orchestration.scheduler_store`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
import json
from typing import Any, Generic, TypeVar


SCHEDULER_SCHEMA_VERSION = 1
MAX_BOUNDED_CATCH_UP = 100
_TERMINAL_OCCURRENCE_STATES = frozenset(
    {"succeeded", "failed_terminal", "skipped"}
)


class SchedulerContractError(ValueError):
    """Fail-closed contract violation carrying a stable operational code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ConcurrencyConfigurationBlocker(SchedulerContractError):
    """A pool/queue concurrency contract is absent or unsafe."""


class ClaimDisposition(StrEnum):
    ACQUIRED = "acquired"
    IDEMPOTENT = "idempotent"
    REJECTED_ACTIVE_OWNER = "rejected_active_owner"
    REJECTED_GRACE = "rejected_grace"
    REJECTED_TERMINAL = "rejected_terminal"
    CAS_LOST = "cas_lost"
    LEASE_LOST = "lease_lost"


class OccurrenceStatus(StrEnum):
    PLANNED = "planned"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    SKIPPED = "skipped"

    @property
    def terminal(self) -> bool:
        return self.value in _TERMINAL_OCCURRENCE_STATES


class MisfirePolicy(StrEnum):
    SKIP = "SKIP"
    RUN_ONCE_WITHIN_GRACE = "RUN_ONCE_WITHIN_GRACE"
    BOUNDED_CATCH_UP = "BOUNDED_CATCH_UP"


class FailureKind(StrEnum):
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"


class ReconciliationKind(StrEnum):
    CONSISTENT = "consistent"
    FALSE_GREEN = "false_green"
    STALE_RUNNING = "stale_running"
    ORPHAN_FLOW = "orphan_flow"
    ORPHAN_OCCURRENCE = "orphan_occurrence"
    STATE_MISMATCH = "state_mismatch"


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SchedulerContractError("NAIVE_TIME", f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _nonempty(value: str, field: str, maximum: int) -> str:
    normalized = str(value).strip()
    if not normalized or len(normalized) > maximum:
        raise SchedulerContractError(
            "INVALID_IDENTITY", f"{field} must contain 1..{maximum} characters"
        )
    return normalized


def _positive(value: int | str | None, field: str) -> int:
    if isinstance(value, str):
        normalized = value.strip()
        parsed = int(normalized) if normalized.isdecimal() else 0
    elif type(value) is int:
        parsed = value
    else:
        parsed = 0
    if parsed <= 0:
        raise ConcurrencyConfigurationBlocker(
            "SCHEDULER_CONCURRENCY_MISSING",
            f"{field} must be configured as an explicit positive integer",
        )
    return parsed


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fingerprint(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SchedulerConcurrencyConfig:
    """The single source contract for the Prefect pool and queue limits."""

    work_pool_name: str
    work_queue_name: str
    work_pool_limit: int
    work_queue_limit: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_pool_name", _nonempty(self.work_pool_name, "work_pool_name", 120))
        object.__setattr__(self, "work_queue_name", _nonempty(self.work_queue_name, "work_queue_name", 120))
        object.__setattr__(self, "work_pool_limit", _positive(self.work_pool_limit, "work_pool_limit"))
        object.__setattr__(self, "work_queue_limit", _positive(self.work_queue_limit, "work_queue_limit"))

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "SchedulerConcurrencyConfig":
        required = (
            "work_pool_name",
            "work_queue_name",
            "work_pool_limit",
            "work_queue_limit",
        )
        missing = [field for field in required if values.get(field) in (None, "")]
        if missing:
            raise ConcurrencyConfigurationBlocker(
                "SCHEDULER_CONCURRENCY_MISSING",
                f"missing scheduler concurrency fields: {','.join(missing)}",
            )
        return cls(**{field: values[field] for field in required})


@dataclass(frozen=True, slots=True)
class ControlPlaneOwnerIdentity:
    metadata_db_fingerprint: str
    owner_id: str
    host_identity: str
    process_id: int
    port: int

    def __post_init__(self) -> None:
        fingerprint = self.metadata_db_fingerprint.lower().strip()
        if len(fingerprint) != 64 or any(character not in "0123456789abcdef" for character in fingerprint):
            raise SchedulerContractError(
                "INVALID_METADATA_IDENTITY",
                "metadata_db_fingerprint must be a lowercase sha256 digest",
            )
        object.__setattr__(self, "metadata_db_fingerprint", fingerprint)
        object.__setattr__(self, "owner_id", _nonempty(self.owner_id, "owner_id", 160))
        object.__setattr__(self, "host_identity", _nonempty(self.host_identity, "host_identity", 160))
        if self.process_id <= 0 or not 0 < self.port <= 65535:
            raise SchedulerContractError("INVALID_OWNER_IDENTITY", "process_id and port must be positive")

    @classmethod
    def build(
        cls,
        *,
        metadata_db_fingerprint: str,
        host_identity: str,
        process_id: int,
        port: int,
        instance_nonce: str,
    ) -> "ControlPlaneOwnerIdentity":
        host = _nonempty(host_identity, "host_identity", 160)
        nonce = _nonempty(instance_nonce, "instance_nonce", 160)
        owner_id = _fingerprint(
            {
                "metadata_db_fingerprint": metadata_db_fingerprint,
                "host_identity": host,
                "process_id": process_id,
                "port": port,
                "instance_nonce": nonce,
            }
        )
        return cls(metadata_db_fingerprint, owner_id, host, process_id, port)


@dataclass(frozen=True, slots=True)
class ControlPlaneOwnerLease:
    identity: ControlPlaneOwnerIdentity
    lease_token: str
    heartbeat_at: datetime
    lease_expires_at: datetime
    grace: timedelta
    version: int
    override_actor: str | None = None
    override_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "lease_token", _nonempty(self.lease_token, "lease_token", 128))
        heartbeat = _aware_utc(self.heartbeat_at, "heartbeat_at")
        expiry = _aware_utc(self.lease_expires_at, "lease_expires_at")
        if expiry <= heartbeat or self.grace < timedelta(0) or self.version < 0:
            raise SchedulerContractError("INVALID_OWNER_LEASE", "owner lease timing/version is invalid")
        object.__setattr__(self, "heartbeat_at", heartbeat)
        object.__setattr__(self, "lease_expires_at", expiry)

    def active_at(self, now: datetime) -> bool:
        return _aware_utc(now, "now") < self.lease_expires_at

    def takeover_allowed_at(self, now: datetime) -> bool:
        return _aware_utc(now, "now") >= self.lease_expires_at + self.grace


@dataclass(frozen=True, slots=True)
class OwnerClaimCommand:
    identity: ControlPlaneOwnerIdentity
    lease_token: str
    now: datetime
    ttl: timedelta
    grace: timedelta
    expected_version: int | None
    override_actor: str | None = None
    override_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "lease_token", _nonempty(self.lease_token, "lease_token", 128))
        object.__setattr__(self, "now", _aware_utc(self.now, "now"))
        if self.ttl <= timedelta(0) or self.grace < timedelta(0):
            raise SchedulerContractError("INVALID_OWNER_LEASE", "ttl must be positive and grace non-negative")


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ClaimResult(Generic[T]):
    disposition: ClaimDisposition
    record: T | None
    reason_code: str

    @property
    def acquired(self) -> bool:
        return self.disposition in {ClaimDisposition.ACQUIRED, ClaimDisposition.IDEMPOTENT}


def claim_control_plane_owner(
    current: ControlPlaneOwnerLease | None,
    command: OwnerClaimCommand,
) -> ClaimResult[ControlPlaneOwnerLease]:
    """Apply one pure owner CAS; a second owner always fails closed."""

    if current is None:
        if command.expected_version not in (None, 0):
            return ClaimResult(ClaimDisposition.CAS_LOST, None, "OWNER_CAS_LOST")
        lease = ControlPlaneOwnerLease(
            command.identity,
            command.lease_token,
            command.now,
            command.now + command.ttl,
            command.grace,
            1,
            command.override_actor,
            command.override_reason,
        )
        return ClaimResult(ClaimDisposition.ACQUIRED, lease, "OWNER_ACQUIRED")
    if current.identity.metadata_db_fingerprint != command.identity.metadata_db_fingerprint:
        raise SchedulerContractError("OWNER_SCOPE_MISMATCH", "owner claim targets another metadata database")
    if command.expected_version != current.version:
        return ClaimResult(ClaimDisposition.CAS_LOST, current, "OWNER_CAS_LOST")
    same_owner = (
        current.identity.owner_id == command.identity.owner_id
        and current.lease_token == command.lease_token
    )
    if current.active_at(command.now):
        if not same_owner:
            return ClaimResult(
                ClaimDisposition.REJECTED_ACTIVE_OWNER,
                current,
                "ACTIVE_OWNER_EXISTS",
            )
        renewed = replace(
            current,
            heartbeat_at=command.now,
            lease_expires_at=command.now + command.ttl,
            grace=command.grace,
            version=current.version + 1,
        )
        return ClaimResult(ClaimDisposition.IDEMPOTENT, renewed, "OWNER_HEARTBEAT_RENEWED")
    if not current.takeover_allowed_at(command.now):
        return ClaimResult(ClaimDisposition.REJECTED_GRACE, current, "OWNER_EXPIRY_GRACE_ACTIVE")
    takeover = ControlPlaneOwnerLease(
        command.identity,
        command.lease_token,
        command.now,
        command.now + command.ttl,
        command.grace,
        current.version + 1,
        command.override_actor,
        command.override_reason,
    )
    return ClaimResult(ClaimDisposition.ACQUIRED, takeover, "OWNER_TAKEOVER_ACQUIRED")


def heartbeat_control_plane_owner(
    current: ControlPlaneOwnerLease,
    *,
    owner_id: str,
    lease_token: str,
    expected_version: int,
    now: datetime,
    ttl: timedelta,
) -> ClaimResult[ControlPlaneOwnerLease]:
    now = _aware_utc(now, "now")
    if expected_version != current.version:
        return ClaimResult(ClaimDisposition.CAS_LOST, current, "OWNER_CAS_LOST")
    if (
        owner_id != current.identity.owner_id
        or lease_token != current.lease_token
        or not current.active_at(now)
    ):
        return ClaimResult(ClaimDisposition.LEASE_LOST, current, "OWNER_LEASE_LOST")
    if ttl <= timedelta(0):
        raise SchedulerContractError("INVALID_OWNER_LEASE", "heartbeat ttl must be positive")
    renewed = replace(
        current,
        heartbeat_at=now,
        lease_expires_at=now + ttl,
        version=current.version + 1,
    )
    return ClaimResult(ClaimDisposition.ACQUIRED, renewed, "OWNER_HEARTBEAT_RENEWED")


@dataclass(frozen=True, slots=True)
class ScheduledOccurrenceIdentity:
    task_type: str
    business_date: date
    scope: str
    scheduled_for: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_type", _nonempty(self.task_type, "task_type", 40))
        object.__setattr__(self, "scope", _nonempty(self.scope, "scope", 120))
        if not isinstance(self.business_date, date) or isinstance(self.business_date, datetime):
            raise SchedulerContractError("INVALID_IDENTITY", "business_date must be a date")
        object.__setattr__(self, "scheduled_for", _aware_utc(self.scheduled_for, "scheduled_for"))

    @property
    def occurrence_key(self) -> str:
        return _fingerprint(
            {
                "schema_version": SCHEDULER_SCHEMA_VERSION,
                "task_type": self.task_type,
                "business_date": self.business_date.isoformat(),
                "scope": self.scope,
                "scheduled_for": self.scheduled_for.isoformat(timespec="microseconds"),
            }
        )


@dataclass(frozen=True, slots=True)
class ScheduledOccurrence:
    identity: ScheduledOccurrenceIdentity
    status: OccurrenceStatus = OccurrenceStatus.PLANNED
    claim_owner_id: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    lease_grace: timedelta = timedelta(0)
    heartbeat_at: datetime | None = None
    attempt_count: int = 0
    task_run_id: str | None = None
    correlation_id: str | None = None
    stable_error_code: str | None = None
    version: int = 0

    def __post_init__(self) -> None:
        if self.version < 0 or self.attempt_count < 0:
            raise SchedulerContractError("INVALID_OCCURRENCE", "occurrence counters cannot be negative")
        if self.lease_grace < timedelta(0):
            raise SchedulerContractError("INVALID_OCCURRENCE_LEASE", "lease grace cannot be negative")
        lease_fields = (self.claim_owner_id, self.lease_token, self.lease_expires_at, self.heartbeat_at)
        if any(item is not None for item in lease_fields) and not all(item is not None for item in lease_fields):
            raise SchedulerContractError("INVALID_OCCURRENCE_LEASE", "occurrence lease fields are atomic")
        if self.heartbeat_at is not None and self.lease_expires_at is not None:
            heartbeat = _aware_utc(self.heartbeat_at, "heartbeat_at")
            expiry = _aware_utc(self.lease_expires_at, "lease_expires_at")
            if expiry <= heartbeat:
                raise SchedulerContractError("INVALID_OCCURRENCE_LEASE", "lease expiry must follow heartbeat")
            object.__setattr__(self, "heartbeat_at", heartbeat)
            object.__setattr__(self, "lease_expires_at", expiry)

    @property
    def occurrence_key(self) -> str:
        return self.identity.occurrence_key

    def lease_active_at(self, now: datetime) -> bool:
        return self.lease_expires_at is not None and _aware_utc(now, "now") < self.lease_expires_at

    def takeover_allowed_at(self, now: datetime) -> bool:
        return (
            self.lease_expires_at is None
            or _aware_utc(now, "now") >= self.lease_expires_at + self.lease_grace
        )


@dataclass(frozen=True, slots=True)
class OccurrenceClaimCommand:
    owner_id: str
    lease_token: str
    now: datetime
    ttl: timedelta
    grace: timedelta
    expected_version: int
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_id", _nonempty(self.owner_id, "owner_id", 160))
        object.__setattr__(self, "lease_token", _nonempty(self.lease_token, "lease_token", 128))
        object.__setattr__(self, "now", _aware_utc(self.now, "now"))
        if self.ttl <= timedelta(0) or self.grace < timedelta(0):
            raise SchedulerContractError("INVALID_OCCURRENCE_LEASE", "ttl/grace are invalid")


def claim_occurrence(
    current: ScheduledOccurrence,
    command: OccurrenceClaimCommand,
) -> ClaimResult[ScheduledOccurrence]:
    if command.expected_version != current.version:
        return ClaimResult(ClaimDisposition.CAS_LOST, current, "OCCURRENCE_CAS_LOST")
    if current.status.terminal:
        return ClaimResult(ClaimDisposition.REJECTED_TERMINAL, current, "OCCURRENCE_TERMINAL")
    same_claim = current.claim_owner_id == command.owner_id and current.lease_token == command.lease_token
    if current.lease_active_at(command.now):
        return ClaimResult(
            ClaimDisposition.IDEMPOTENT if same_claim else ClaimDisposition.REJECTED_ACTIVE_OWNER,
            current,
            "OCCURRENCE_ALREADY_OWNED" if same_claim else "ACTIVE_OCCURRENCE_CLAIM_EXISTS",
        )
    if current.lease_expires_at is not None and not current.takeover_allowed_at(command.now):
        return ClaimResult(ClaimDisposition.REJECTED_GRACE, current, "OCCURRENCE_EXPIRY_GRACE_ACTIVE")
    claimed = replace(
        current,
        status=OccurrenceStatus.CLAIMED,
        claim_owner_id=command.owner_id,
        lease_token=command.lease_token,
        heartbeat_at=command.now,
        lease_expires_at=command.now + command.ttl,
        lease_grace=command.grace,
        attempt_count=current.attempt_count + 1,
        correlation_id=command.correlation_id or current.correlation_id,
        stable_error_code=None,
        version=current.version + 1,
    )
    return ClaimResult(ClaimDisposition.ACQUIRED, claimed, "OCCURRENCE_CLAIM_ACQUIRED")


def heartbeat_occurrence(
    current: ScheduledOccurrence,
    command: OccurrenceClaimCommand,
) -> ClaimResult[ScheduledOccurrence]:
    if command.expected_version != current.version:
        return ClaimResult(ClaimDisposition.CAS_LOST, current, "OCCURRENCE_CAS_LOST")
    if (
        current.status not in {OccurrenceStatus.CLAIMED, OccurrenceStatus.RUNNING}
        or current.claim_owner_id != command.owner_id
        or current.lease_token != command.lease_token
        or not current.lease_active_at(command.now)
    ):
        return ClaimResult(ClaimDisposition.LEASE_LOST, current, "OCCURRENCE_LEASE_LOST")
    renewed = replace(
        current,
        heartbeat_at=command.now,
        lease_expires_at=command.now + command.ttl,
        version=current.version + 1,
    )
    return ClaimResult(ClaimDisposition.ACQUIRED, renewed, "OCCURRENCE_HEARTBEAT_RENEWED")


def finish_occurrence(
    current: ScheduledOccurrence,
    command: OccurrenceClaimCommand,
    *,
    status: OccurrenceStatus,
    task_run_id: str | None = None,
    stable_error_code: str | None = None,
) -> ClaimResult[ScheduledOccurrence]:
    """Fence the business side-effect/terminal write with lease token + version."""

    if status not in {
        OccurrenceStatus.SUCCEEDED,
        OccurrenceStatus.FAILED_RETRYABLE,
        OccurrenceStatus.FAILED_TERMINAL,
        OccurrenceStatus.SKIPPED,
        OccurrenceStatus.RUNNING,
    }:
        raise SchedulerContractError("INVALID_TERMINAL_STATE", "unsupported occurrence transition")
    if current.status.terminal:
        return ClaimResult(ClaimDisposition.REJECTED_TERMINAL, current, "OCCURRENCE_TERMINAL")
    if command.expected_version != current.version:
        return ClaimResult(ClaimDisposition.CAS_LOST, current, "OCCURRENCE_CAS_LOST")
    if (
        current.claim_owner_id != command.owner_id
        or current.lease_token != command.lease_token
        or not current.lease_active_at(command.now)
    ):
        return ClaimResult(ClaimDisposition.LEASE_LOST, current, "OCCURRENCE_LEASE_LOST")
    finished = replace(
        current,
        status=status,
        task_run_id=task_run_id or current.task_run_id,
        stable_error_code=stable_error_code,
        version=current.version + 1,
    )
    return ClaimResult(ClaimDisposition.ACQUIRED, finished, f"OCCURRENCE_{status.value.upper()}")


@dataclass(frozen=True, slots=True)
class MisfireConfig:
    policy: MisfirePolicy
    grace: timedelta
    max_catch_up: int

    def __post_init__(self) -> None:
        if self.grace < timedelta(0):
            raise SchedulerContractError("INVALID_MISFIRE_CONFIG", "misfire grace cannot be negative")
        if self.policy is MisfirePolicy.SKIP:
            if self.max_catch_up != 0:
                raise SchedulerContractError("INVALID_MISFIRE_CONFIG", "SKIP requires max_catch_up=0")
        elif not 1 <= self.max_catch_up <= MAX_BOUNDED_CATCH_UP:
            raise SchedulerContractError(
                "INVALID_MISFIRE_CONFIG",
                f"max_catch_up must be between 1 and {MAX_BOUNDED_CATCH_UP}",
            )
        if self.policy is MisfirePolicy.RUN_ONCE_WITHIN_GRACE and self.max_catch_up != 1:
            raise SchedulerContractError("INVALID_MISFIRE_CONFIG", "RUN_ONCE_WITHIN_GRACE requires max_catch_up=1")


def plan_misfires(
    missed: tuple[ScheduledOccurrenceIdentity, ...] | list[ScheduledOccurrenceIdentity],
    *,
    now: datetime,
    config: MisfireConfig,
) -> tuple[ScheduledOccurrenceIdentity, ...]:
    """Return a deterministic, duplicate-free and strictly bounded catch-up plan."""

    now = _aware_utc(now, "now")
    unique = {item.occurrence_key: item for item in missed if item.scheduled_for <= now}
    ordered = sorted(unique.values(), key=lambda item: (item.scheduled_for, item.occurrence_key))
    if config.policy is MisfirePolicy.SKIP or not ordered:
        return ()
    eligible = [item for item in ordered if now - item.scheduled_for <= config.grace]
    if config.policy is MisfirePolicy.RUN_ONCE_WITHIN_GRACE:
        return tuple(eligible[-1:])
    return tuple(eligible[-config.max_catch_up :])


class SchedulerFailure(RuntimeError):
    """Typed failure that Prefect may classify without parsing error text."""

    kind: FailureKind

    def __init__(
        self,
        stable_error_code: str,
        operation: str,
        message: str,
        *,
        occurrence_key: str | None = None,
    ) -> None:
        self.stable_error_code = _nonempty(stable_error_code, "stable_error_code", 80)
        self.operation = _nonempty(operation, "operation", 120)
        self.occurrence_key = occurrence_key
        super().__init__(message)

    @property
    def retryable(self) -> bool:
        return self.kind is FailureKind.RETRYABLE


class RetryableSchedulerFailure(SchedulerFailure):
    kind = FailureKind.RETRYABLE


class NonRetryableSchedulerFailure(SchedulerFailure):
    kind = FailureKind.NON_RETRYABLE


def typed_scheduler_failure(
    error: BaseException,
    *,
    operation: str,
    retryable: bool,
    stable_error_code: str,
    occurrence_key: str | None = None,
) -> SchedulerFailure:
    if isinstance(error, SchedulerFailure):
        return error
    failure_type = RetryableSchedulerFailure if retryable else NonRetryableSchedulerFailure
    failure = failure_type(
        stable_error_code,
        operation,
        f"{operation} failed: {type(error).__name__}",
        occurrence_key=occurrence_key,
    )
    failure.__cause__ = error
    return failure


def prefect_retry_condition(_task: Any, _task_run: Any, state: Any) -> bool:
    """Prefect retry_condition_fn: retry only explicitly retryable typed failures."""

    try:
        result = state.result(raise_on_failure=False)
    except Exception as error:  # Prefect versions differ in failed-state extraction.
        result = error
    return isinstance(result, RetryableSchedulerFailure)


@dataclass(frozen=True, slots=True)
class PrefectFlowObservation:
    flow_run_id: str
    state: str
    occurrence_key: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "flow_run_id", _nonempty(self.flow_run_id, "flow_run_id", 160))
        object.__setattr__(self, "state", _nonempty(self.state, "state", 40).upper())


@dataclass(frozen=True, slots=True)
class ReconciliationFinding:
    kind: ReconciliationKind
    reason_code: str
    occurrence_key: str | None
    flow_run_id: str | None


def reconcile_occurrence(
    occurrence: ScheduledOccurrence | None,
    flow: PrefectFlowObservation | None,
    *,
    now: datetime,
) -> ReconciliationFinding:
    """Classify false-green, stale-running, and both orphan directions."""

    now = _aware_utc(now, "now")
    if occurrence is None and flow is not None:
        return ReconciliationFinding(ReconciliationKind.ORPHAN_FLOW, "FLOW_WITHOUT_OCCURRENCE", flow.occurrence_key, flow.flow_run_id)
    if occurrence is not None and flow is None:
        return ReconciliationFinding(ReconciliationKind.ORPHAN_OCCURRENCE, "OCCURRENCE_WITHOUT_FLOW", occurrence.occurrence_key, None)
    if occurrence is None or flow is None:  # both absent is an invalid reconciliation request
        raise SchedulerContractError("EMPTY_RECONCILIATION", "occurrence and flow cannot both be absent")
    if flow.occurrence_key not in (None, occurrence.occurrence_key):
        return ReconciliationFinding(ReconciliationKind.STATE_MISMATCH, "FLOW_OCCURRENCE_IDENTITY_MISMATCH", occurrence.occurrence_key, flow.flow_run_id)
    if flow.state == "COMPLETED" and occurrence.status not in {OccurrenceStatus.SUCCEEDED, OccurrenceStatus.SKIPPED}:
        return ReconciliationFinding(ReconciliationKind.FALSE_GREEN, "PREFECT_COMPLETED_BUSINESS_NOT_TERMINAL", occurrence.occurrence_key, flow.flow_run_id)
    if (
        occurrence.status in {OccurrenceStatus.CLAIMED, OccurrenceStatus.RUNNING}
        and not occurrence.lease_active_at(now)
    ):
        return ReconciliationFinding(ReconciliationKind.STALE_RUNNING, "OCCURRENCE_HEARTBEAT_STALE", occurrence.occurrence_key, flow.flow_run_id)
    if flow.state in {"FAILED", "CRASHED", "CANCELLED"} and occurrence.status is OccurrenceStatus.SUCCEEDED:
        return ReconciliationFinding(ReconciliationKind.STATE_MISMATCH, "BUSINESS_SUCCEEDED_FLOW_FAILED", occurrence.occurrence_key, flow.flow_run_id)
    return ReconciliationFinding(ReconciliationKind.CONSISTENT, "SCHEDULER_STATE_CONSISTENT", occurrence.occurrence_key, flow.flow_run_id)


PollResult = TypeVar("PollResult")


def run_fresh_transaction_round(
    session_factory: Callable[[], Any],
    reader: Callable[[Any], PollResult],
) -> PollResult:
    """Execute exactly one polling round in a newly-created short transaction."""

    session = session_factory()
    try:
        with session.begin():
            return reader(session)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
