"""Transaction-bound SQLAlchemy repository for immutable auxiliary snapshots.

The repository never opens, commits, or rolls back a transaction. A caller supplies
one SQLAlchemy ``Connection`` already bound to the desired transaction. These Core
table declarations share the canonical metadata used by Alembic.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    Index,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.db.session import Base
from app.services.auxiliary.provider_results import (
    ResponseCompletenessEvidence,
    SnapshotPublishBlockedError,
)
from app.services.auxiliary.snapshot_codec import decode_canonical_payload
from app.services.auxiliary.snapshot_contracts import (
    CanonicalPayload,
    CanonicalRequestPartition,
    ImmutablePartitionSnapshot,
    ImmutablePartitionSnapshotRow,
    PartitionSnapshotStatus,
    SnapshotFailure,
)


class SnapshotRepositoryError(RuntimeError):
    """Base persistence error for immutable auxiliary snapshots."""


class SnapshotBatchCollisionError(SnapshotRepositoryError):
    """An existing batch_id has different immutable metadata or rows."""


class SnapshotPointerIntegrityError(SnapshotRepositoryError):
    """An active pointer references a missing or incompatible immutable batch."""


class SnapshotAuditOnlyAccessError(SnapshotPublishBlockedError):
    """An audit-only dataset attempted to access active-pointer semantics."""


_AUDIT_ONLY_DATASETS = frozenset({"institutional_survey"})
BATCH_TABLE = "auxiliary_snapshot_batches"
ROW_TABLE = "auxiliary_snapshot_rows"
POINTER_TABLE = "auxiliary_active_partition_snapshots"


def _require_pointer_capability(dataset: str) -> None:
    """Reject the exact permanently audit-only dataset before any pointer I/O."""
    if dataset in _AUDIT_ONLY_DATASETS:
        raise SnapshotAuditOnlyAccessError(
            f"audit-only dataset {dataset!r} cannot publish or read an active pointer"
        )


@runtime_checkable
class ImmutablePartitionSnapshotRepositoryPort(Protocol):
    def persist(self, snapshot: ImmutablePartitionSnapshot) -> "SnapshotPersistResult": ...

    def publish(
        self,
        snapshot: ImmutablePartitionSnapshot,
        completeness: ResponseCompletenessEvidence | None,
    ) -> "SnapshotPublishResult": ...

    def load_active(
        self,
        *,
        dataset: str,
        provider: str,
        endpoint: str,
        request_partition: CanonicalRequestPartition,
    ) -> ImmutablePartitionSnapshot | None: ...


snapshot_metadata = Base.metadata

snapshot_batches = Table(
    BATCH_TABLE,
    snapshot_metadata,
    Column("batch_id", String(80), nullable=False),
    Column("dataset", String(64), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("endpoint", String(96), nullable=False),
    Column("request_partition_bytes", LargeBinary, nullable=False),
    Column("partition_hash", CHAR(64), nullable=False),
    Column("partition_schema_version", Integer, nullable=False),
    Column("requested_as_of", DateTime(timezone=False), nullable=False),
    Column("fetched_at", DateTime(timezone=False), nullable=False),
    Column("status", String(24), nullable=False),
    Column("identity_schema_version", Integer, nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("content_hash", CHAR(64), nullable=False),
    Column("failure_code", String(80), nullable=True),
    Column("failure_message", Text, nullable=True),
    Column("created_at", DateTime(timezone=False), nullable=False),
    PrimaryKeyConstraint("batch_id", name="pk_aux_snapshot_batches"),
    UniqueConstraint(
        "dataset",
        "provider",
        "endpoint",
        "partition_hash",
        "batch_id",
        name="uq_aux_snapshot_batch_partition",
    ),
    CheckConstraint(
        "status IN ('failure', 'successful_empty', 'complete')",
        name="ck_aux_snapshot_batch_status",
    ),
    CheckConstraint("row_count >= 0", name="ck_aux_snapshot_batch_row_count"),
)
Index(
    "idx_aux_snapshot_batches_partition_time",
    snapshot_batches.c.dataset,
    snapshot_batches.c.provider,
    snapshot_batches.c.endpoint,
    snapshot_batches.c.partition_hash,
    snapshot_batches.c.fetched_at,
)
Index("idx_aux_snapshot_batches_status", snapshot_batches.c.status, snapshot_batches.c.created_at)

snapshot_rows = Table(
    ROW_TABLE,
    snapshot_metadata,
    Column("batch_id", String(80), nullable=False),
    Column("row_ordinal", Integer, nullable=False),
    Column("source_id", String(255), nullable=True),
    Column("payload_hash", CHAR(64), nullable=False),
    Column("payload_bytes", LargeBinary, nullable=False),
    PrimaryKeyConstraint("batch_id", "row_ordinal", name="pk_aux_snapshot_rows"),
    ForeignKeyConstraint(
        ("batch_id",),
        (f"{BATCH_TABLE}.batch_id",),
        name="fk_aux_snapshot_rows_batch",
        ondelete="RESTRICT",
    ),
    CheckConstraint("row_ordinal >= 0", name="ck_aux_snapshot_row_ordinal"),
)
Index("idx_aux_snapshot_rows_payload_hash", snapshot_rows.c.payload_hash)

active_partition_snapshots = Table(
    POINTER_TABLE,
    snapshot_metadata,
    Column("dataset", String(64), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("endpoint", String(96), nullable=False),
    Column("partition_hash", CHAR(64), nullable=False),
    Column("active_batch_id", String(80), nullable=False),
    Column("published_at", DateTime(timezone=False), nullable=False),
    PrimaryKeyConstraint(
        "dataset",
        "provider",
        "endpoint",
        "partition_hash",
        name="pk_aux_active_partition",
    ),
    UniqueConstraint("active_batch_id", name="uq_aux_active_partition_batch"),
    ForeignKeyConstraint(
        ("dataset", "provider", "endpoint", "partition_hash", "active_batch_id"),
        (
            f"{BATCH_TABLE}.dataset",
            f"{BATCH_TABLE}.provider",
            f"{BATCH_TABLE}.endpoint",
            f"{BATCH_TABLE}.partition_hash",
            f"{BATCH_TABLE}.batch_id",
        ),
        name="fk_aux_active_partition_batch",
        ondelete="RESTRICT",
    ),
)


@dataclass(frozen=True, slots=True)
class SnapshotPersistResult:
    batch_id: str
    created: bool
    row_count: int


@dataclass(frozen=True, slots=True)
class SnapshotPublishResult:
    batch_id: str
    pointer_changed: bool
    published_at: datetime


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SnapshotRepositoryError("snapshot datetime must include a timezone")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise SnapshotRepositoryError(f"expected binary snapshot payload, got {type(value).__name__}")


class SQLAlchemyImmutablePartitionSnapshotRepository:
    """Persist and publish snapshots inside one caller-owned transaction."""

    def __init__(
        self,
        connection: Connection,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._connection = connection
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def persist(self, snapshot: ImmutablePartitionSnapshot) -> SnapshotPersistResult:
        existing = self._select_batch(snapshot.batch_id, for_update=True)
        if existing is not None:
            self._assert_exact(snapshot, existing)
            return SnapshotPersistResult(snapshot.batch_id, False, snapshot.row_count)

        try:
            self._connection.execute(insert(snapshot_batches).values(self._batch_values(snapshot)))
        except IntegrityError:
            raced = self._select_batch(snapshot.batch_id, for_update=True)
            if raced is None:
                raise
            self._assert_exact(snapshot, raced)
            return SnapshotPersistResult(snapshot.batch_id, False, snapshot.row_count)

        if snapshot.rows:
            self._connection.execute(
                insert(snapshot_rows),
                [self._row_values(row) for row in snapshot.rows],
            )
        inserted = self._select_batch(snapshot.batch_id, for_update=True)
        if inserted is None:
            raise SnapshotRepositoryError(f"persisted batch disappeared: {snapshot.batch_id}")
        self._assert_exact(snapshot, inserted)
        return SnapshotPersistResult(snapshot.batch_id, True, snapshot.row_count)

    def publish(
        self,
        snapshot: ImmutablePartitionSnapshot,
        completeness: ResponseCompletenessEvidence | None,
    ) -> SnapshotPublishResult:
        _require_pointer_capability(snapshot.dataset)
        if snapshot.status is PartitionSnapshotStatus.FAILURE:
            raise SnapshotPublishBlockedError("FAILURE snapshot must never be published")
        if completeness is None:
            raise SnapshotPublishBlockedError("missing response completeness evidence")
        observed_fields: set[str] = set()
        observed_field_sets: list[tuple[str, ...]] = []
        for row in snapshot.rows:
            value = decode_canonical_payload(row.lossless_payload)
            if not isinstance(value, Mapping):
                raise SnapshotPublishBlockedError("provider snapshot rows must decode to mappings")
            fields = tuple(str(field) for field in value.keys())
            observed_fields.update(fields)
            observed_field_sets.append(fields)
        completeness.require_complete(
            row_count=snapshot.row_count,
            observed_fields=tuple(sorted(observed_fields)),
            observed_field_sets=tuple(observed_field_sets),
        )

        stored = self._select_batch(snapshot.batch_id, for_update=True)
        if stored is None:
            raise SnapshotPointerIntegrityError(
                f"cannot publish unpersisted snapshot batch: {snapshot.batch_id}"
            )
        self._assert_exact(snapshot, stored)

        key = self._pointer_key(snapshot)
        statement = select(active_partition_snapshots).where(
            *(
                active_partition_snapshots.c[column] == value
                for column, value in key.items()
            )
        ).with_for_update()
        current = self._connection.execute(statement).mappings().one_or_none()
        if current is not None and current["active_batch_id"] == snapshot.batch_id:
            return SnapshotPublishResult(
                batch_id=snapshot.batch_id,
                pointer_changed=False,
                published_at=_utc_aware(current["published_at"]),
            )

        published_at = self._clock()
        published_db = _utc_naive(published_at)
        if current is None:
            self._connection.execute(
                insert(active_partition_snapshots).values(
                    **key,
                    active_batch_id=snapshot.batch_id,
                    published_at=published_db,
                )
            )
        else:
            self._connection.execute(
                update(active_partition_snapshots)
                .where(
                    *(
                        active_partition_snapshots.c[column] == value
                        for column, value in key.items()
                    )
                )
                .values(active_batch_id=snapshot.batch_id, published_at=published_db)
            )
        return SnapshotPublishResult(
            batch_id=snapshot.batch_id,
            pointer_changed=True,
            published_at=_utc_aware(published_db),
        )

    def load_active(
        self,
        *,
        dataset: str,
        provider: str,
        endpoint: str,
        request_partition: CanonicalRequestPartition,
    ) -> ImmutablePartitionSnapshot | None:
        _require_pointer_capability(dataset)
        pointer = self._connection.execute(
            select(active_partition_snapshots).where(
                active_partition_snapshots.c.dataset == dataset,
                active_partition_snapshots.c.provider == provider,
                active_partition_snapshots.c.endpoint == endpoint,
                active_partition_snapshots.c.partition_hash == request_partition.partition_hash,
            )
        ).mappings().one_or_none()
        if pointer is None:
            return None
        batch = self._select_batch(str(pointer["active_batch_id"]), for_update=False)
        if batch is None:
            raise SnapshotPointerIntegrityError(
                f"active pointer references missing batch: {pointer['active_batch_id']}"
            )
        expected = {
            "dataset": dataset,
            "provider": provider,
            "endpoint": endpoint,
            "partition_hash": request_partition.partition_hash,
        }
        if any(batch[column] != value for column, value in expected.items()):
            raise SnapshotPointerIntegrityError("active pointer partition does not match its batch")
        if (
            int(batch["partition_schema_version"]) != request_partition.schema_version
            or _bytes(batch["request_partition_bytes"]) != request_partition.payload.content
        ):
            raise SnapshotPointerIntegrityError("active pointer request partition bytes do not match")
        return self._snapshot_from_records(batch, self._select_rows(str(batch["batch_id"])))

    def _select_batch(self, batch_id: str, *, for_update: bool) -> Mapping[str, object] | None:
        statement = select(snapshot_batches).where(snapshot_batches.c.batch_id == batch_id)
        if for_update:
            statement = statement.with_for_update()
        return self._connection.execute(statement).mappings().one_or_none()

    def _select_rows(self, batch_id: str) -> tuple[Mapping[str, object], ...]:
        result = self._connection.execute(
            select(snapshot_rows)
            .where(snapshot_rows.c.batch_id == batch_id)
            .order_by(snapshot_rows.c.row_ordinal)
        ).mappings()
        return tuple(result.all())

    def _assert_exact(
        self,
        snapshot: ImmutablePartitionSnapshot,
        stored_batch: Mapping[str, object],
    ) -> None:
        expected_batch = self._batch_values(snapshot)
        expected_batch.pop("created_at")
        actual_batch = dict(stored_batch)
        actual_batch.pop("created_at", None)
        actual_batch["request_partition_bytes"] = _bytes(actual_batch["request_partition_bytes"])
        if actual_batch != expected_batch:
            raise SnapshotBatchCollisionError(
                f"batch_id collision with different immutable metadata: {snapshot.batch_id}"
            )
        actual_rows = self._select_rows(snapshot.batch_id)
        expected_rows = tuple(self._row_values(row) for row in snapshot.rows)
        normalized_rows = tuple(
            {
                **dict(row),
                "payload_bytes": _bytes(row["payload_bytes"]),
            }
            for row in actual_rows
        )
        if normalized_rows != expected_rows:
            raise SnapshotBatchCollisionError(
                f"batch_id collision with different immutable rows: {snapshot.batch_id}"
            )

    def _batch_values(self, snapshot: ImmutablePartitionSnapshot) -> dict[str, object]:
        return {
            "batch_id": snapshot.batch_id,
            "dataset": snapshot.dataset,
            "provider": snapshot.provider,
            "endpoint": snapshot.endpoint,
            "request_partition_bytes": snapshot.request_partition.payload.content,
            "partition_hash": snapshot.request_partition.partition_hash,
            "partition_schema_version": snapshot.request_partition.schema_version,
            "requested_as_of": _utc_naive(snapshot.requested_as_of),
            "fetched_at": _utc_naive(snapshot.fetched_at),
            "status": snapshot.status.value,
            "identity_schema_version": snapshot.identity_schema_version,
            "row_count": snapshot.row_count,
            "content_hash": snapshot.content_hash,
            "failure_code": snapshot.failure.code if snapshot.failure else None,
            "failure_message": snapshot.failure.message if snapshot.failure else None,
            "created_at": _utc_naive(self._clock()),
        }

    @staticmethod
    def _row_values(row: ImmutablePartitionSnapshotRow) -> dict[str, object]:
        return {
            "batch_id": row.batch_id,
            "row_ordinal": row.row_ordinal,
            "source_id": row.source_id,
            "payload_hash": row.payload_hash,
            "payload_bytes": row.lossless_payload.content,
        }

    @staticmethod
    def _pointer_key(snapshot: ImmutablePartitionSnapshot) -> dict[str, str]:
        return {
            "dataset": snapshot.dataset,
            "provider": snapshot.provider,
            "endpoint": snapshot.endpoint,
            "partition_hash": snapshot.request_partition.partition_hash,
        }

    @staticmethod
    def _snapshot_from_records(
        batch: Mapping[str, object],
        rows: Sequence[Mapping[str, object]],
    ) -> ImmutablePartitionSnapshot:
        batch_id = str(batch["batch_id"])
        snapshot_rows = tuple(
            ImmutablePartitionSnapshotRow(
                batch_id=batch_id,
                row_ordinal=int(row["row_ordinal"]),
                source_id=str(row["source_id"]) if row["source_id"] is not None else None,
                lossless_payload=CanonicalPayload(
                    content=_bytes(row["payload_bytes"]),
                    content_hash=str(row["payload_hash"]),
                ),
            )
            for row in rows
        )
        failure = None
        if batch["failure_code"] is not None:
            failure = SnapshotFailure(
                code=str(batch["failure_code"]),
                message=str(batch["failure_message"]),
            )
        return ImmutablePartitionSnapshot(
            batch_id=batch_id,
            dataset=str(batch["dataset"]),
            provider=str(batch["provider"]),
            endpoint=str(batch["endpoint"]),
            request_partition=CanonicalRequestPartition(
                schema_version=int(batch["partition_schema_version"]),
                payload=CanonicalPayload(
                    content=_bytes(batch["request_partition_bytes"]),
                    content_hash=str(batch["partition_hash"]),
                ),
            ),
            requested_as_of=_utc_aware(batch["requested_as_of"]),
            fetched_at=_utc_aware(batch["fetched_at"]),
            status=PartitionSnapshotStatus(str(batch["status"])),
            identity_schema_version=int(batch["identity_schema_version"]),
            rows=snapshot_rows,
            row_count=int(batch["row_count"]),
            content_hash=str(batch["content_hash"]),
            failure=failure,
        )


__all__ = [
    "ImmutablePartitionSnapshotRepositoryPort",
    "SQLAlchemyImmutablePartitionSnapshotRepository",
    "SnapshotAuditOnlyAccessError",
    "SnapshotBatchCollisionError",
    "SnapshotPersistResult",
    "SnapshotPointerIntegrityError",
    "SnapshotPublishResult",
    "SnapshotRepositoryError",
    "active_partition_snapshots",
    "snapshot_batches",
    "snapshot_metadata",
    "snapshot_rows",
]
