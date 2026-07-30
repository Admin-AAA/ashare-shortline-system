from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select

from app.db.session import Base
from app.services.auxiliary.provider_results import (
    FieldSetEvidence,
    LimitEvidence,
    PaginationEvidence,
    ResponseCompletenessEvidence,
    SnapshotPublishBlockedError,
)
from app.services.auxiliary.snapshot_contracts import (
    CanonicalRequestPartition,
    ImmutablePartitionSnapshot,
    SnapshotFailure,
)
from app.services.auxiliary.snapshot_repository import (
    SQLAlchemyImmutablePartitionSnapshotRepository,
    SnapshotBatchCollisionError,
    active_partition_snapshots,
    snapshot_batches,
    snapshot_rows,
)


NOW = datetime(2026, 5, 18, 1, tzinfo=timezone.utc)
PARTITION = CanonicalRequestPartition.from_value(
    {"provider": "tushare_pro", "endpoint": "block_trade", "trade_date": "20260518"}
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _snapshot(
    batch_id: str,
    rows=({"ts_code": "000001.SZ", "price": Decimal("10.20")},),
    *,
    fetched_at: datetime = NOW,
) -> ImmutablePartitionSnapshot:
    return ImmutablePartitionSnapshot.from_payloads(
        batch_id=batch_id,
        dataset="block_trade",
        provider="tushare_pro",
        endpoint="block_trade",
        request_partition=PARTITION,
        requested_as_of=NOW - timedelta(hours=1),
        fetched_at=fetched_at,
        payloads=tuple(rows),
    )


def _evidence(row_count: int, fields=("ts_code", "price")) -> ResponseCompletenessEvidence:
    return ResponseCompletenessEvidence(
        pagination=PaginationEvidence(page_count=1, all_pages_fetched=True),
        limit=LimitEvidence(returned_rows=row_count, truncation_ruled_out=True),
        field_set=FieldSetEvidence(
            response_fields=tuple(fields),
            all_returned_fields_captured=True,
            requested_all_fields=True,
        ),
    )


def test_persist_is_exactly_once_and_same_batch_retry_requires_exact_rows() -> None:
    engine = _engine()
    duplicate = {"ts_code": "000001.SZ", "price": Decimal("10.20")}
    snapshot = _snapshot("batch-1", (duplicate, duplicate))

    with engine.begin() as connection:
        repository = SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=lambda: NOW)
        first = repository.persist(snapshot)
        second = repository.persist(snapshot)

        assert first.created is True
        assert second.created is False
        assert connection.scalar(select(func.count()).select_from(snapshot_batches)) == 1
        assert connection.scalar(select(func.count()).select_from(snapshot_rows)) == 2
        hashes = connection.execute(
            select(snapshot_rows.c.payload_hash).order_by(snapshot_rows.c.row_ordinal)
        ).scalars().all()
        assert hashes[0] == hashes[1]

        ordered = _snapshot(
            "batch-order",
            (duplicate, {"ts_code": "000002.SZ", "price": Decimal("9.00")}),
        )
        repository.persist(ordered)
        reordered = _snapshot(
            "batch-order",
            ({"ts_code": "000002.SZ", "price": Decimal("9.00")}, duplicate),
        )
        with pytest.raises(SnapshotBatchCollisionError, match="immutable rows"):
            repository.persist(reordered)

        metadata_collision = _snapshot("batch-1", (duplicate, duplicate), fetched_at=NOW + timedelta(seconds=1))
        with pytest.raises(SnapshotBatchCollisionError, match="immutable metadata"):
            repository.persist(metadata_collision)


def test_repository_does_not_commit_partial_batch_when_row_write_fails() -> None:
    engine = _engine()
    snapshot = _snapshot("batch-fault")

    class FailingRowConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, *args, **kwargs):
            if getattr(statement, "table", None) is snapshot_rows:
                raise RuntimeError("injected row write failure")
            return self.connection.execute(statement, *args, **kwargs)

    with engine.connect() as connection:
        transaction = connection.begin()
        repository = SQLAlchemyImmutablePartitionSnapshotRepository(  # type: ignore[arg-type]
            FailingRowConnection(connection),
            clock=lambda: NOW,
        )
        with pytest.raises(RuntimeError, match="injected row write failure"):
            repository.persist(snapshot)
        transaction.rollback()

    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(snapshot_batches)) == 0
        assert connection.scalar(select(func.count()).select_from(snapshot_rows)) == 0


def test_failure_is_persisted_but_never_published() -> None:
    engine = _engine()
    failed = ImmutablePartitionSnapshot.failed(
        batch_id="batch-failure",
        dataset="block_trade",
        provider="tushare_pro",
        endpoint="block_trade",
        request_partition=PARTITION,
        requested_as_of=NOW - timedelta(hours=1),
        fetched_at=NOW,
        failure=SnapshotFailure(code="provider_timeout", message="provider timed out"),
    )

    with engine.begin() as connection:
        repository = SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=lambda: NOW)
        assert repository.persist(failed).created is True
        with pytest.raises(SnapshotPublishBlockedError, match="never"):
            repository.publish(failed, _evidence(0))
        assert connection.scalar(select(func.count()).select_from(snapshot_batches)) == 1
        assert connection.scalar(select(func.count()).select_from(active_partition_snapshots)) == 0


def test_missing_or_incomplete_evidence_blocks_publish_for_complete_and_empty() -> None:
    engine = _engine()
    complete = _snapshot("batch-complete")
    empty = _snapshot("batch-empty", ())
    incomplete = ResponseCompletenessEvidence(
        pagination=PaginationEvidence(page_count=1, all_pages_fetched=False, terminal_cursor="next"),
        limit=LimitEvidence(returned_rows=1, truncation_ruled_out=False),
        field_set=FieldSetEvidence(
            response_fields=("ts_code", "price"),
            all_returned_fields_captured=True,
            requested_all_fields=True,
        ),
    )

    with engine.begin() as connection:
        repository = SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=lambda: NOW)
        repository.persist(complete)
        repository.persist(empty)
        with pytest.raises(SnapshotPublishBlockedError, match="missing"):
            repository.publish(complete, None)
        with pytest.raises(SnapshotPublishBlockedError, match="pagination"):
            repository.publish(complete, incomplete)
        published = repository.publish(empty, _evidence(0))
        assert published.pointer_changed is True
        assert repository.load_active(
            dataset="block_trade",
            provider="tushare_pro",
            endpoint="block_trade",
            request_partition=PARTITION,
        ) == empty


def test_active_load_uses_only_pointer_and_pointer_update_is_transactional() -> None:
    engine = _engine()
    first = _snapshot("batch-first", ({"ts_code": "000001.SZ", "price": 10},))
    second = _snapshot("batch-second", ({"ts_code": "000001.SZ", "price": 11},))

    with engine.begin() as connection:
        repository = SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=lambda: NOW)
        repository.persist(first)
        # Persisted data is not a fallback when no pointer exists.
        assert repository.load_active(
            dataset="block_trade",
            provider="tushare_pro",
            endpoint="block_trade",
            request_partition=PARTITION,
        ) is None
        assert repository.publish(first, _evidence(1)).pointer_changed is True
        assert repository.publish(first, _evidence(1)).pointer_changed is False

    with engine.connect() as connection:
        transaction = connection.begin()
        repository = SQLAlchemyImmutablePartitionSnapshotRepository(
            connection,
            clock=lambda: NOW + timedelta(minutes=1),
        )
        repository.persist(second)
        repository.publish(second, _evidence(1))
        assert repository.load_active(
            dataset="block_trade",
            provider="tushare_pro",
            endpoint="block_trade",
            request_partition=PARTITION,
        ) == second
        transaction.rollback()

    with engine.connect() as connection:
        repository = SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=lambda: NOW)
        active = repository.load_active(
            dataset="block_trade",
            provider="tushare_pro",
            endpoint="block_trade",
            request_partition=PARTITION,
        )
        assert active == first
        assert connection.scalar(select(func.count()).select_from(snapshot_batches)) == 1


def test_audit_only_dataset_cannot_publish_or_read_active_pointers_with_forged_evidence() -> None:
    """The repository itself denies audit-only pointer misuse before any pointer I/O."""
    from app.services.auxiliary.snapshot_repository import SnapshotAuditOnlyAccessError

    engine = _engine()
    audit_partition = CanonicalRequestPartition.from_value(
        {"provider": "tushare_pro", "endpoint": "stk_surv", "ts_code": "000001.SZ"}
    )
    audit_snapshot = ImmutablePartitionSnapshot.from_payloads(
        batch_id="audit-only-forged-publish",
        dataset="institutional_survey",
        provider="tushare_pro",
        endpoint="stk_surv",
        request_partition=audit_partition,
        requested_as_of=NOW - timedelta(hours=1),
        fetched_at=NOW,
        payloads=({"ts_code": "000001.SZ", "surv_date": "20260518"},),
    )

    with engine.begin() as connection:
        repository = SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=lambda: NOW)
        repository.persist(audit_snapshot)
        forged_complete_evidence = _evidence(1, fields=("ts_code", "surv_date"))

        with pytest.raises(SnapshotPublishBlockedError, match="audit-only"):
            repository.publish(audit_snapshot, forged_complete_evidence)
        with pytest.raises(SnapshotAuditOnlyAccessError, match="audit-only"):
            repository.load_active(
                dataset="institutional_survey",
                provider="tushare_pro",
                endpoint="stk_surv",
                request_partition=audit_partition,
            )
        assert connection.scalar(select(func.count()).select_from(active_partition_snapshots)) == 0
