"""S7 Layer-1 scheduler reliability properties and examples.

Every property runs 100 generated examples.  The module uses immutable domain
objects and local fakes only: no Prefect server, MySQL connection, migration,
network request, or production scheduler is started.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from hypothesis import given, settings, strategies as st
import pytest

from app.db.production_hardening_migration_plan import SCHEDULER_BATCH, OperationKind
from app.orchestration.scheduler_contracts import (
    ClaimDisposition,
    ConcurrencyConfigurationBlocker,
    ControlPlaneOwnerIdentity,
    MisfireConfig,
    MisfirePolicy,
    NonRetryableSchedulerFailure,
    OccurrenceClaimCommand,
    OccurrenceStatus,
    OwnerClaimCommand,
    PrefectFlowObservation,
    ReconciliationKind,
    RetryableSchedulerFailure,
    ScheduledOccurrence,
    ScheduledOccurrenceIdentity,
    SchedulerConcurrencyConfig,
    claim_control_plane_owner,
    claim_occurrence,
    finish_occurrence,
    heartbeat_occurrence,
    plan_misfires,
    prefect_retry_condition,
    reconcile_occurrence,
    run_fresh_transaction_round,
    typed_scheduler_failure,
)

_NOW = datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc)
_METADATA_DB = "a" * 64


def _owner(index: int) -> ControlPlaneOwnerIdentity:
    return ControlPlaneOwnerIdentity.build(
        metadata_db_fingerprint=_METADATA_DB,
        host_identity=f"scheduler-host-{index}",
        process_id=10_000 + index,
        port=4_200 + index,
        instance_nonce=f"nonce-{index}",
    )


def _owner_command(
    index: int,
    *,
    now: datetime,
    ttl: timedelta,
    grace: timedelta,
    expected_version: int | None,
) -> OwnerClaimCommand:
    return OwnerClaimCommand(
        identity=_owner(index),
        lease_token=f"owner-token-{index}",
        now=now,
        ttl=ttl,
        grace=grace,
        expected_version=expected_version,
    )


def _identity(offset_seconds: int = 0) -> ScheduledOccurrenceIdentity:
    return ScheduledOccurrenceIdentity(
        task_type="recommendation_scan",
        business_date=date(2026, 8, 4),
        scope="market:cn-a",
        scheduled_for=_NOW + timedelta(seconds=offset_seconds),
    )


def _occurrence_command(
    index: int,
    *,
    now: datetime,
    ttl: timedelta,
    grace: timedelta,
    expected_version: int,
) -> OccurrenceClaimCommand:
    return OccurrenceClaimCommand(
        owner_id=f"claimant-{index}",
        lease_token=f"claim-token-{index}",
        now=now,
        ttl=ttl,
        grace=grace,
        expected_version=expected_version,
        correlation_id="corr-s7",
    )


@settings(max_examples=100, deadline=None)
@given(
    ttl_seconds=st.integers(min_value=2, max_value=600),
    grace_seconds=st.integers(min_value=0, max_value=300),
    contender_count=st.integers(min_value=2, max_value=12),
    rotation=st.integers(min_value=0, max_value=100),
)
def test_property_29_control_plane_ownership_is_exclusive(
    ttl_seconds: int,
    grace_seconds: int,
    contender_count: int,
    rotation: int,
) -> None:
    """Property 29: Control-plane ownership is exclusive."""
    ttl = timedelta(seconds=ttl_seconds)
    grace = timedelta(seconds=grace_seconds)
    initial = claim_control_plane_owner(
        None,
        _owner_command(0, now=_NOW, ttl=ttl, grace=grace, expected_version=0),
    )
    assert initial.acquired and initial.record is not None
    lease = initial.record

    active_time = _NOW + timedelta(seconds=1)
    rival = claim_control_plane_owner(
        lease,
        _owner_command(
            1,
            now=active_time,
            ttl=ttl,
            grace=grace,
            expected_version=lease.version,
        ),
    )
    assert rival.disposition is ClaimDisposition.REJECTED_ACTIVE_OWNER
    assert rival.record == lease

    renewal = claim_control_plane_owner(
        lease,
        OwnerClaimCommand(
            identity=lease.identity,
            lease_token=lease.lease_token,
            now=active_time,
            ttl=ttl,
            grace=grace,
            expected_version=lease.version,
        ),
    )
    assert renewal.disposition is ClaimDisposition.IDEMPOTENT
    assert renewal.record is not None
    lease = renewal.record

    takeover_time = lease.lease_expires_at + lease.grace
    order = list(range(1, contender_count + 1))
    shift = rotation % len(order)
    order = order[shift:] + order[:shift]
    snapshot_version = lease.version
    winners: list[int] = []
    current = lease
    for contender in order:
        result = claim_control_plane_owner(
            current,
            _owner_command(
                contender,
                now=takeover_time,
                ttl=ttl,
                grace=grace,
                expected_version=snapshot_version,
            ),
        )
        if result.disposition is ClaimDisposition.ACQUIRED:
            winners.append(contender)
        assert result.record is not None
        current = result.record

    assert len(winners) == 1
    assert current.identity.owner_id == _owner(winners[0]).owner_id
    assert current.version == snapshot_version + 1


@settings(max_examples=100, deadline=None)
@given(
    ttl_seconds=st.integers(min_value=3, max_value=600),
    grace_seconds=st.integers(min_value=2, max_value=300),
    claimant_count=st.integers(min_value=2, max_value=12),
    rotation=st.integers(min_value=0, max_value=100),
)
def test_property_30_scheduled_occurrence_claims_are_at_most_once(
    ttl_seconds: int,
    grace_seconds: int,
    claimant_count: int,
    rotation: int,
) -> None:
    """Property 30: Scheduled occurrence claims are at-most-once."""
    ttl = timedelta(seconds=ttl_seconds)
    grace = timedelta(seconds=grace_seconds)
    current = ScheduledOccurrence(_identity())
    order = list(range(claimant_count))
    shift = rotation % len(order)
    order = order[shift:] + order[:shift]

    initial_winners: list[int] = []
    for claimant in order:
        result = claim_occurrence(
            current,
            _occurrence_command(
                claimant,
                now=_NOW,
                ttl=ttl,
                grace=grace,
                expected_version=0,
            ),
        )
        if result.disposition is ClaimDisposition.ACQUIRED:
            initial_winners.append(claimant)
        assert result.record is not None
        current = result.record
    assert len(initial_winners) == 1
    assert current.claim_owner_id == f"claimant-{initial_winners[0]}"
    assert current.attempt_count == 1

    active_rival = (initial_winners[0] + 1) % claimant_count
    active = claim_occurrence(
        current,
        _occurrence_command(
            active_rival,
            now=_NOW + timedelta(seconds=1),
            ttl=ttl,
            grace=timedelta(0),
            expected_version=current.version,
        ),
    )
    assert active.disposition is ClaimDisposition.REJECTED_ACTIVE_OWNER

    heartbeat = heartbeat_occurrence(
        current,
        _occurrence_command(
            initial_winners[0],
            now=_NOW + timedelta(seconds=1),
            ttl=ttl,
            grace=grace,
            expected_version=current.version,
        ),
    )
    assert heartbeat.disposition is ClaimDisposition.ACQUIRED
    assert heartbeat.record is not None
    current = heartbeat.record
    assert current.attempt_count == 1

    assert current.lease_expires_at is not None
    grace_bypass = claim_occurrence(
        current,
        _occurrence_command(
            active_rival,
            now=current.lease_expires_at + grace / 2,
            ttl=ttl,
            grace=timedelta(0),
            expected_version=current.version,
        ),
    )
    assert grace_bypass.disposition is ClaimDisposition.REJECTED_GRACE

    takeover_time = current.lease_expires_at + current.lease_grace
    snapshot_version = current.version
    takeover_winners: list[int] = []
    for claimant in reversed(order):
        result = claim_occurrence(
            current,
            _occurrence_command(
                claimant + claimant_count,
                now=takeover_time,
                ttl=ttl,
                grace=grace,
                expected_version=snapshot_version,
            ),
        )
        if result.disposition is ClaimDisposition.ACQUIRED:
            takeover_winners.append(claimant + claimant_count)
        assert result.record is not None
        current = result.record
    assert len(takeover_winners) == 1
    assert current.attempt_count == 2

    winning_claimant = takeover_winners[0]
    terminal_command = _occurrence_command(
        winning_claimant,
        now=takeover_time + timedelta(seconds=1),
        ttl=ttl,
        grace=grace,
        expected_version=current.version,
    )
    committed = finish_occurrence(
        current,
        terminal_command,
        status=OccurrenceStatus.SUCCEEDED,
        task_run_id="task-run-winner",
    )
    assert committed.acquired and committed.record is not None
    current = committed.record

    duplicate = finish_occurrence(
        current,
        _occurrence_command(
            winning_claimant,
            now=takeover_time + timedelta(seconds=2),
            ttl=ttl,
            grace=grace,
            expected_version=current.version,
        ),
        status=OccurrenceStatus.SUCCEEDED,
        task_run_id="task-run-duplicate",
    )
    assert duplicate.disposition is ClaimDisposition.REJECTED_TERMINAL
    assert duplicate.record == current
    assert current.task_run_id == "task-run-winner"


@settings(max_examples=100, deadline=None)
@given(
    offsets=st.lists(
        st.integers(min_value=-7_200, max_value=600),
        min_size=0,
        max_size=120,
    ),
    policy=st.sampled_from(tuple(MisfirePolicy)),
    grace_seconds=st.integers(min_value=0, max_value=3_600),
    requested_bound=st.integers(min_value=1, max_value=30),
)
def test_property_31_misfire_planning_is_bounded(
    offsets: list[int],
    policy: MisfirePolicy,
    grace_seconds: int,
    requested_bound: int,
) -> None:
    """Property 31: Misfire planning is bounded."""
    missed = [_identity(offset) for offset in offsets]
    if missed:
        missed.extend(missed[: min(3, len(missed))])
    maximum = (
        0
        if policy is MisfirePolicy.SKIP
        else 1
        if policy is MisfirePolicy.RUN_ONCE_WITHIN_GRACE
        else requested_bound
    )
    config = MisfireConfig(policy, timedelta(seconds=grace_seconds), maximum)
    planned = plan_misfires(missed, now=_NOW, config=config)

    planned_keys = [item.occurrence_key for item in planned]
    input_keys = {item.occurrence_key for item in missed}
    assert len(planned_keys) == len(set(planned_keys))
    assert set(planned_keys) <= input_keys
    assert len(planned) <= maximum
    assert all(item.scheduled_for <= _NOW for item in planned)
    assert all(_NOW - item.scheduled_for <= config.grace for item in planned)
    if policy is MisfirePolicy.SKIP:
        assert planned == ()
    elif policy is MisfirePolicy.RUN_ONCE_WITHIN_GRACE:
        assert len(planned) <= 1


def test_concurrency_contract_has_one_positive_source_and_fails_closed() -> None:
    config = SchedulerConcurrencyConfig.from_mapping(
        {
            "work_pool_name": "ashare-local",
            "work_queue_name": "scheduled",
            "work_pool_limit": "4",
            "work_queue_limit": 2,
        }
    )
    assert config.work_pool_limit == 4
    assert config.work_queue_limit == 2

    for unsafe in (None, "", "0", "-1", 0, -1, True, 1.5):
        with pytest.raises(ConcurrencyConfigurationBlocker) as captured:
            SchedulerConcurrencyConfig(
                "ashare-local", "scheduled", unsafe, 1  # type: ignore[arg-type]
            )
        assert captured.value.code == "SCHEDULER_CONCURRENCY_MISSING"


def test_owner_conflict_expiry_and_cas_takeover_example() -> None:
    ttl = timedelta(seconds=10)
    grace = timedelta(seconds=5)
    acquired = claim_control_plane_owner(
        None,
        _owner_command(0, now=_NOW, ttl=ttl, grace=grace, expected_version=0),
    )
    assert acquired.record is not None
    lease = acquired.record

    conflict = claim_control_plane_owner(
        lease,
        _owner_command(1, now=_NOW + ttl, ttl=ttl, grace=grace, expected_version=1),
    )
    assert conflict.disposition is ClaimDisposition.REJECTED_GRACE

    takeover_at = _NOW + ttl + grace
    winner = claim_control_plane_owner(
        lease,
        _owner_command(1, now=takeover_at, ttl=ttl, grace=grace, expected_version=1),
    )
    loser = claim_control_plane_owner(
        winner.record,
        _owner_command(2, now=takeover_at, ttl=ttl, grace=grace, expected_version=1),
    )
    assert winner.disposition is ClaimDisposition.ACQUIRED
    assert loser.disposition is ClaimDisposition.CAS_LOST


def test_typed_retry_propagation_is_not_message_parsing() -> None:
    retryable = typed_scheduler_failure(
        TimeoutError("provider credential must not be copied"),
        operation="scheduled recommendation",
        retryable=True,
        stable_error_code="PROVIDER_TIMEOUT",
        occurrence_key="b" * 64,
    )
    terminal = typed_scheduler_failure(
        ValueError("invalid business input"),
        operation="scheduled recommendation",
        retryable=False,
        stable_error_code="BUSINESS_INPUT_INVALID",
    )

    class State:
        def __init__(self, value: BaseException) -> None:
            self.value = value

        def result(self, *, raise_on_failure: bool) -> BaseException:
            assert raise_on_failure is False
            return self.value

    assert isinstance(retryable, RetryableSchedulerFailure)
    assert isinstance(terminal, NonRetryableSchedulerFailure)
    assert prefect_retry_condition(None, None, State(retryable)) is True
    assert prefect_retry_condition(None, None, State(terminal)) is False
    assert retryable.stable_error_code == "PROVIDER_TIMEOUT"
    assert "credential" not in str(retryable)
    assert isinstance(retryable.__cause__, TimeoutError)


def test_bounded_misfire_policy_examples() -> None:
    missed = tuple(_identity(-minute * 60) for minute in range(1, 8))
    assert plan_misfires(
        missed,
        now=_NOW,
        config=MisfireConfig(MisfirePolicy.SKIP, timedelta(hours=1), 0),
    ) == ()
    run_once = plan_misfires(
        missed,
        now=_NOW,
        config=MisfireConfig(
            MisfirePolicy.RUN_ONCE_WITHIN_GRACE, timedelta(minutes=5), 1
        ),
    )
    catch_up = plan_misfires(
        missed,
        now=_NOW,
        config=MisfireConfig(
            MisfirePolicy.BOUNDED_CATCH_UP, timedelta(hours=1), 3
        ),
    )
    assert len(run_once) == 1
    assert len(catch_up) == 3
    assert [item.scheduled_for for item in catch_up] == sorted(
        item.scheduled_for for item in catch_up
    )


class _Transaction:
    def __init__(self, session: "_Session") -> None:
        self.session = session

    def __enter__(self) -> "_Transaction":
        self.session.begun += 1
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _Session:
    def __init__(self, committed: dict[str, int]) -> None:
        self.committed = committed
        self.begun = 0
        self.rollbacks = 0
        self.closed = False

    def begin(self) -> _Transaction:
        return _Transaction(self)

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_each_poll_round_uses_a_fresh_short_transaction_and_sees_commits() -> None:
    committed = {"version": 1}
    sessions: list[_Session] = []

    def factory() -> _Session:
        session = _Session(committed)
        sessions.append(session)
        return session

    first = run_fresh_transaction_round(factory, lambda session: session.committed["version"])
    committed["version"] = 2
    second = run_fresh_transaction_round(factory, lambda session: session.committed["version"])

    assert (first, second) == (1, 2)
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert all(session.begun == 1 and session.closed for session in sessions)

    def fail(_session: _Session) -> Any:
        raise RuntimeError("poll failed")

    with pytest.raises(RuntimeError, match="poll failed"):
        run_fresh_transaction_round(factory, fail)
    assert sessions[-1].rollbacks == 1
    assert sessions[-1].closed is True


def test_reconciliation_detects_false_green_stale_running_and_orphans() -> None:
    occurrence = ScheduledOccurrence(_identity())
    completed = PrefectFlowObservation("flow-completed", "completed", occurrence.occurrence_key)
    false_green = reconcile_occurrence(occurrence, completed, now=_NOW)
    assert false_green.kind is ReconciliationKind.FALSE_GREEN

    claimed = claim_occurrence(
        occurrence,
        _occurrence_command(
            1,
            now=_NOW,
            ttl=timedelta(seconds=10),
            grace=timedelta(seconds=5),
            expected_version=0,
        ),
    )
    assert claimed.record is not None
    running = PrefectFlowObservation("flow-running", "running", occurrence.occurrence_key)
    stale = reconcile_occurrence(
        claimed.record,
        running,
        now=_NOW + timedelta(seconds=11),
    )
    assert stale.kind is ReconciliationKind.STALE_RUNNING

    orphan = reconcile_occurrence(None, running, now=_NOW)
    assert orphan.kind is ReconciliationKind.ORPHAN_FLOW
    missing_flow = reconcile_occurrence(occurrence, None, now=_NOW)
    assert missing_flow.kind is ReconciliationKind.ORPHAN_OCCURRENCE


def test_scheduler_migration_plan_is_coordinated_and_additive_only() -> None:
    assert SCHEDULER_BATCH.proposed_revision == "0008_scheduler_claim"
    assert SCHEDULER_BATCH.depends_on == "0007_portfolio_risk"
    assert SCHEDULER_BATCH.implementation_tasks == ("7.1", "7.2", "7.3")
    assert {operation.kind for operation in SCHEDULER_BATCH.operations} <= {
        OperationKind.ADD_COLUMN,
        OperationKind.CREATE_TABLE,
        OperationKind.CREATE_INDEX,
    }
    assert {check.check_id for check in SCHEDULER_BATCH.unique_key_checks} == {
        "scheduled_occurrence_key",
        "scheduled_occurrence_identity",
        "control_plane_database",
    }
