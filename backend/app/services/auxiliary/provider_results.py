"""Typed provider-result boundary and explicit response-completeness evidence."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.services.auxiliary.snapshot_contracts import (
    CanonicalPayload,
    CanonicalRequestPartition,
    IDENTITY_SCHEMA_VERSION,
    ImmutablePartitionSnapshot,
    ImmutablePartitionSnapshotRow,
    PartitionSnapshotStatus,
    SnapshotFailure,
    multiset_hash_from_payload_hashes,
)


class ProviderResultError(ValueError):
    """Raised when a provider boundary result is internally contradictory."""


class SnapshotPublishBlockedError(ProviderResultError):
    """Raised when a terminal provider response is not safe to publish."""


@dataclass(frozen=True, slots=True)
class PaginationEvidence:
    page_count: int
    all_pages_fetched: bool
    terminal_cursor: str | None = None

    def __post_init__(self) -> None:
        if self.page_count <= 0:
            raise ProviderResultError("pagination page_count must be positive")
        if self.terminal_cursor is not None and not self.terminal_cursor.strip():
            raise ProviderResultError("terminal_cursor must be None or non-blank")


@dataclass(frozen=True, slots=True)
class LimitEvidence:
    returned_rows: int
    truncation_ruled_out: bool
    requested_limit: int | None = None
    provider_reported_total: int | None = None

    def __post_init__(self) -> None:
        if self.returned_rows < 0:
            raise ProviderResultError("returned_rows must not be negative")
        if self.requested_limit is not None and self.requested_limit <= 0:
            raise ProviderResultError("requested_limit must be positive when present")
        if self.provider_reported_total is not None and self.provider_reported_total < 0:
            raise ProviderResultError("provider_reported_total must not be negative")
        if self.requested_limit is not None and self.returned_rows > self.requested_limit:
            raise ProviderResultError("returned_rows exceeds the declared request limit")
        if (
            self.provider_reported_total is not None
            and self.provider_reported_total < self.returned_rows
        ):
            raise ProviderResultError(
                "provider_reported_total must not be less than returned_rows"
            )


@dataclass(frozen=True, slots=True)
class FieldSetEvidence:
    response_fields: tuple[str, ...]
    all_returned_fields_captured: bool
    requested_all_fields: bool
    requested_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(not field.strip() for field in (*self.response_fields, *self.requested_fields)):
            raise ProviderResultError("field-set evidence cannot contain blank field names")
        if len(self.response_fields) != len(set(self.response_fields)):
            raise ProviderResultError("response_fields cannot contain duplicates")
        if len(self.requested_fields) != len(set(self.requested_fields)):
            raise ProviderResultError("requested_fields cannot contain duplicates")
        if not self.response_fields:
            raise ProviderResultError("field-set evidence must capture at least one field")
        if self.requested_all_fields and self.requested_fields:
            raise ProviderResultError("requested_all_fields cannot be combined with requested_fields")
        if not self.requested_all_fields and not self.requested_fields:
            raise ProviderResultError("field-set evidence must declare all-fields or explicit requested_fields")


@dataclass(frozen=True, slots=True)
class ResponseCompletenessEvidence:
    """Evidence that pagination, provider limits, and field capture are complete."""

    pagination: PaginationEvidence
    limit: LimitEvidence
    field_set: FieldSetEvidence

    def require_complete(
        self,
        *,
        row_count: int,
        observed_fields: Sequence[str],
        observed_field_sets: Sequence[Sequence[str]] | None = None,
    ) -> None:
        problems: list[str] = []
        if row_count < 0:
            problems.append("snapshot row count must not be negative")
        if self.pagination.all_pages_fetched is not True or self.pagination.terminal_cursor is not None:
            problems.append("pagination is not terminal")
        if self.limit.returned_rows != row_count:
            problems.append("limit evidence row count does not match the snapshot")
        if self.limit.truncation_ruled_out is not True:
            problems.append("provider truncation was not ruled out")
        if (
            self.limit.provider_reported_total is not None
            and self.limit.provider_reported_total != row_count
        ):
            problems.append("provider reported total does not match fetched rows")
        if self.field_set.all_returned_fields_captured is not True:
            problems.append("not all returned fields were captured")

        response_fields = set(self.field_set.response_fields)
        actual_fields = set(observed_fields)
        if row_count == 0:
            if actual_fields:
                problems.append("empty snapshot has observed row fields")
        elif actual_fields != response_fields:
            problems.append("response field evidence does not exactly match observed row fields")

        if observed_field_sets is not None:
            if len(observed_field_sets) != row_count:
                problems.append("field evidence row count does not match the snapshot")
            elif any(set(field_set) != response_fields for field_set in observed_field_sets):
                problems.append("response field evidence does not match every returned row")

        if (
            not self.field_set.requested_all_fields
            and not set(self.field_set.requested_fields) <= response_fields
        ):
            problems.append("requested fields are absent from the response field set")
        if problems:
            raise SnapshotPublishBlockedError(
                "response completeness evidence rejected publish: " + "; ".join(problems)
            )


@dataclass(frozen=True, slots=True)
class ProviderCanonicalRow:
    payload: CanonicalPayload
    source_id: str | None = None
    field_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source_id is not None and not self.source_id.strip():
            raise ProviderResultError("source_id must be None or non-blank")
        if any(not name.strip() for name in self.field_names):
            raise ProviderResultError("provider field names cannot be blank")
        if len(self.field_names) != len(set(self.field_names)):
            raise ProviderResultError("provider field names cannot contain duplicates")

    @classmethod
    def from_mapping(
        cls,
        row: Mapping[str, Any],
        *,
        source_id: str | None = None,
    ) -> "ProviderCanonicalRow":
        if not isinstance(row, Mapping):
            raise ProviderResultError("provider rows must be mappings")
        return cls(
            payload=CanonicalPayload.from_value(row),
            source_id=source_id,
            field_names=tuple(str(key) for key in row.keys()),
        )


@dataclass(frozen=True, slots=True)
class ProviderPartitionResult:
    """One typed FAILURE/SUCCESSFUL_EMPTY/COMPLETE provider partition result.

    A successful empty response may omit completeness evidence only for a dedicated
    audit-only capture.  Such a result remains immutable evidence but is rejected by
    ``require_publishable`` and can therefore never advance an active pointer.
    """

    batch_id: str
    dataset: str
    provider: str
    endpoint: str
    request_partition: CanonicalRequestPartition
    requested_as_of: datetime
    fetched_at: datetime
    status: PartitionSnapshotStatus
    rows: tuple[ProviderCanonicalRow, ...]
    completeness: ResponseCompletenessEvidence | None
    failure: SnapshotFailure | None

    def __post_init__(self) -> None:
        if self.status is PartitionSnapshotStatus.FAILURE:
            if self.rows or self.failure is None:
                raise ProviderResultError("FAILURE requires failure details and no rows")
            if self.completeness is not None:
                raise ProviderResultError("FAILURE must not claim response completeness")
        elif self.status is PartitionSnapshotStatus.SUCCESSFUL_EMPTY:
            if self.rows or self.failure is not None:
                raise ProviderResultError("SUCCESSFUL_EMPTY requires no rows and no failure")
        elif self.status is PartitionSnapshotStatus.COMPLETE:
            if not self.rows or self.failure is not None:
                raise ProviderResultError("COMPLETE requires rows and no failure")

    @classmethod
    def from_rows(
        cls,
        *,
        batch_id: str,
        dataset: str,
        provider: str,
        endpoint: str,
        request_partition: CanonicalRequestPartition,
        requested_as_of: datetime,
        fetched_at: datetime,
        rows: Sequence[Mapping[str, Any]],
        completeness: ResponseCompletenessEvidence | None,
        source_ids: Sequence[str | None] | None = None,
        allow_empty_without_completeness: bool = False,
    ) -> "ProviderPartitionResult":
        ids = tuple(source_ids) if source_ids is not None else (None,) * len(rows)
        if len(ids) != len(rows):
            raise ProviderResultError("source_ids length must match provider rows")
        canonical_rows = tuple(
            ProviderCanonicalRow.from_mapping(row, source_id=ids[index])
            for index, row in enumerate(rows)
        )
        empty_failure_code: str | None = None
        empty_failure_message: str | None = None
        if not canonical_rows and completeness is None and not allow_empty_without_completeness:
            empty_failure_code = "provider_completeness_evidence_missing"
            empty_failure_message = "empty provider response has no completeness evidence"
        elif not canonical_rows and completeness is not None:
            try:
                completeness.require_complete(row_count=0, observed_fields=())
            except SnapshotPublishBlockedError:
                empty_failure_code = "provider_completeness_evidence_incomplete"
                empty_failure_message = "empty provider response completeness evidence is incomplete"
        return cls(
            batch_id=batch_id,
            dataset=dataset,
            provider=provider,
            endpoint=endpoint,
            request_partition=request_partition,
            requested_as_of=requested_as_of,
            fetched_at=fetched_at,
            status=(
                PartitionSnapshotStatus.FAILURE
                if empty_failure_code is not None
                else (
                    PartitionSnapshotStatus.COMPLETE
                    if canonical_rows
                    else PartitionSnapshotStatus.SUCCESSFUL_EMPTY
                )
            ),
            rows=canonical_rows if empty_failure_code is None else (),
            completeness=completeness if empty_failure_code is None else None,
            failure=(
                SnapshotFailure(code=empty_failure_code, message=empty_failure_message)
                if empty_failure_code is not None and empty_failure_message is not None
                else None
            ),
        )

    @classmethod
    def failed(
        cls,
        *,
        batch_id: str,
        dataset: str,
        provider: str,
        endpoint: str,
        request_partition: CanonicalRequestPartition,
        requested_as_of: datetime,
        fetched_at: datetime,
        code: str,
        message: str,
    ) -> "ProviderPartitionResult":
        return cls(
            batch_id=batch_id,
            dataset=dataset,
            provider=provider,
            endpoint=endpoint,
            request_partition=request_partition,
            requested_as_of=requested_as_of,
            fetched_at=fetched_at,
            status=PartitionSnapshotStatus.FAILURE,
            rows=(),
            completeness=None,
            failure=SnapshotFailure(code=code, message=message),
        )

    @property
    def observed_fields(self) -> tuple[str, ...]:
        return tuple(sorted({field for row in self.rows for field in row.field_names}))

    def require_publishable(self) -> None:
        if self.status is PartitionSnapshotStatus.FAILURE:
            raise SnapshotPublishBlockedError("FAILURE provider result must never be published")
        if self.completeness is None:
            raise SnapshotPublishBlockedError("missing response completeness evidence")
        self.completeness.require_complete(
            row_count=len(self.rows),
            observed_fields=self.observed_fields,
            observed_field_sets=tuple(row.field_names for row in self.rows),
        )

    def to_snapshot(self) -> ImmutablePartitionSnapshot:
        snapshot_rows = tuple(
            ImmutablePartitionSnapshotRow(
                batch_id=self.batch_id,
                row_ordinal=ordinal,
                lossless_payload=row.payload,
                source_id=row.source_id,
            )
            for ordinal, row in enumerate(self.rows)
        )
        return ImmutablePartitionSnapshot(
            batch_id=self.batch_id,
            dataset=self.dataset,
            provider=self.provider,
            endpoint=self.endpoint,
            request_partition=self.request_partition,
            requested_as_of=self.requested_as_of,
            fetched_at=self.fetched_at,
            status=self.status,
            identity_schema_version=IDENTITY_SCHEMA_VERSION,
            rows=snapshot_rows,
            row_count=len(snapshot_rows),
            content_hash=multiset_hash_from_payload_hashes(row.payload_hash for row in snapshot_rows),
            failure=self.failure,
        )


__all__ = [
    "FieldSetEvidence",
    "LimitEvidence",
    "PaginationEvidence",
    "ProviderCanonicalRow",
    "ProviderPartitionResult",
    "ProviderResultError",
    "ResponseCompletenessEvidence",
    "SnapshotPublishBlockedError",
]
