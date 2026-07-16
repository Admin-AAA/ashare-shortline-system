"""Pure immutable contracts for event-like auxiliary partition snapshots.

These contracts deliberately do not depend on ORM models, database sessions, live
fetchers, or migration discovery.  Source identity is computed from the lossless raw
provider payload represented by :class:`CanonicalPayload`; the existing auxiliary ORM
projection must never be used as source identity because it drops provider fields and
normalizes values lossyly.
"""
from __future__ import annotations

import base64
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
import math
import re
import unicodedata
from typing import Any


PARTITION_SNAPSHOT_DATASET_KEYS = frozenset(
    {"block_trade", "share_float", "holder_trade", "institutional_survey", "news_announcement"}
)
IDENTITY_SCHEMA_VERSION = 1
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MULTISET_DOMAIN = b"ashare-auxiliary-partition-multiset-v1\0"


class SnapshotContractError(ValueError):
    """Raised when snapshot identity, status, or canonical payload is invalid."""


class PartitionSnapshotStatus(str, Enum):
    """Terminal outcome of one provider partition request."""

    FAILURE = "failure"
    SUCCESSFUL_EMPTY = "successful_empty"
    COMPLETE = "complete"


def _canonical_decimal(value: Decimal) -> str:
    if not value.is_finite():
        special = "nan" if value.is_nan() else ("infinity" if value > 0 else "-infinity")
        return special
    if value == 0:
        return "0"
    rendered = format(value.normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _canonical_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SnapshotContractError("datetime payload values must include a timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_time(value: time) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SnapshotContractError("time payload values must include a timezone")
    return value.isoformat(timespec="microseconds")


def _canonical_node(value: Any) -> list[Any]:
    """Return a tagged JSON node which preserves supported source value types."""
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, str):
        return ["string", unicodedata.normalize("NFC", value)]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, Decimal):
        if value.is_finite():
            return ["decimal", _canonical_decimal(value)]
        return ["decimal_special", _canonical_decimal(value)]
    if isinstance(value, float):
        if math.isnan(value):
            return ["float_special", "nan"]
        if math.isinf(value):
            return ["float_special", "infinity" if value > 0 else "-infinity"]
        return ["decimal", _canonical_decimal(Decimal(str(value)))]
    if isinstance(value, datetime):
        return ["datetime", _canonical_datetime(value)]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, time):
        return ["time", _canonical_time(value)]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SnapshotContractError("canonical payload map keys must be strings")
            canonical_key = unicodedata.normalize("NFC", key)
            if canonical_key in normalized:
                raise SnapshotContractError(
                    f"canonical payload contains duplicate NFC-normalized map key: {canonical_key!r}"
                )
            normalized[canonical_key] = item
        return [
            "map",
            [[key, _canonical_node(normalized[key])] for key in sorted(normalized)],
        ]
    if isinstance(value, tuple):
        return ["tuple", [_canonical_node(item) for item in value]]
    if isinstance(value, list):
        return ["list", [_canonical_node(item) for item in value]]
    raise SnapshotContractError(
        f"unsupported canonical payload type: {type(value).__module__}.{type(value).__qualname__}"
    )


def canonical_payload_bytes(value: Any) -> bytes:
    """Serialize a value deterministically without conflating source value types.

    Strings and map keys use Unicode NFC.  Maps are ordered by normalized key.  Dates,
    datetimes, decimals, bytes, tuples, lists, null, booleans, integers, and special
    floating values all carry explicit type tags.  Consequently ``None``, ``""``,
    numeric zero, and the literal string ``"nan"`` remain distinct.
    """
    return json.dumps(
        _canonical_node(value),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_payload_hash(value: Any) -> str:
    """Hash the canonical lossless source representation, never an ORM projection."""
    return _sha256(canonical_payload_bytes(value))


def multiset_hash_from_payload_hashes(payload_hashes: Iterable[str]) -> str:
    """Hash a payload multiset independent of row order but sensitive to counts."""
    counts = Counter(payload_hashes)
    digest = hashlib.sha256()
    digest.update(_MULTISET_DOMAIN)
    for payload_hash, count in sorted(counts.items()):
        if not _HASH_PATTERN.fullmatch(payload_hash):
            raise SnapshotContractError(f"invalid SHA-256 payload hash: {payload_hash!r}")
        encoded_hash = payload_hash.encode("ascii")
        encoded_count = str(count).encode("ascii")
        digest.update(len(encoded_hash).to_bytes(2, "big"))
        digest.update(encoded_hash)
        digest.update(len(encoded_count).to_bytes(2, "big"))
        digest.update(encoded_count)
    return digest.hexdigest()


def canonical_multiset_hash(payloads: Iterable[Any]) -> str:
    """Canonical partition hash preserving duplicate payload multiplicity."""
    return multiset_hash_from_payload_hashes(canonical_payload_hash(item) for item in payloads)


@dataclass(frozen=True, slots=True)
class CanonicalPayload:
    """Immutable, lossless canonical representation of one raw provider value."""

    content: bytes
    content_hash: str

    def __post_init__(self) -> None:
        if self.content_hash != _sha256(self.content):
            raise SnapshotContractError("canonical payload content_hash does not match content")

    @classmethod
    def from_value(cls, value: Any) -> "CanonicalPayload":
        content = canonical_payload_bytes(value)
        return cls(content=content, content_hash=_sha256(content))


@dataclass(frozen=True, slots=True)
class CanonicalRequestPartition:
    """Canonical provider request partition captured before a fetch is attempted."""

    schema_version: int
    payload: CanonicalPayload

    def __post_init__(self) -> None:
        if self.schema_version <= 0:
            raise SnapshotContractError("request partition schema_version must be positive")

    @classmethod
    def from_value(cls, value: Any, *, schema_version: int = 1) -> "CanonicalRequestPartition":
        return cls(schema_version=schema_version, payload=CanonicalPayload.from_value(value))

    @property
    def partition_hash(self) -> str:
        return self.payload.content_hash


@dataclass(frozen=True, slots=True)
class SnapshotFailure:
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise SnapshotContractError("snapshot failure code and message must not be blank")


@dataclass(frozen=True, slots=True)
class ImmutablePartitionSnapshotRow:
    """One immutable source row whose sole identity is ``(batch_id, row_ordinal)``.

    ``source_id`` is metadata only and may be absent or duplicated.  ``payload_hash``
    is intentionally non-unique; equal payloads remain separate rows and contribute
    their full multiplicity to the partition content hash.
    """

    batch_id: str
    row_ordinal: int
    lossless_payload: CanonicalPayload
    source_id: str | None = None

    def __post_init__(self) -> None:
        if not self.batch_id.strip():
            raise SnapshotContractError("row batch_id must not be blank")
        if self.row_ordinal < 0:
            raise SnapshotContractError("row_ordinal must not be negative")
        if self.source_id is not None and not self.source_id.strip():
            raise SnapshotContractError("source_id must be None or a non-blank string")

    @classmethod
    def from_payload(
        cls,
        *,
        batch_id: str,
        row_ordinal: int,
        payload: Any,
        source_id: str | None = None,
    ) -> "ImmutablePartitionSnapshotRow":
        return cls(
            batch_id=batch_id,
            row_ordinal=row_ordinal,
            lossless_payload=CanonicalPayload.from_value(payload),
            source_id=source_id,
        )

    @property
    def identity(self) -> tuple[str, int]:
        return (self.batch_id, self.row_ordinal)

    @property
    def payload_hash(self) -> str:
        return self.lossless_payload.content_hash


@dataclass(frozen=True, slots=True)
class ImmutablePartitionSnapshot:
    """Immutable result of one event-like provider partition request."""

    batch_id: str
    dataset: str
    provider: str
    endpoint: str
    request_partition: CanonicalRequestPartition
    requested_as_of: datetime
    fetched_at: datetime
    status: PartitionSnapshotStatus
    identity_schema_version: int
    rows: tuple[ImmutablePartitionSnapshotRow, ...]
    row_count: int
    content_hash: str
    failure: SnapshotFailure | None = None

    def __post_init__(self) -> None:
        if not self.batch_id.strip():
            raise SnapshotContractError("batch_id must not be blank")
        if self.dataset not in PARTITION_SNAPSHOT_DATASET_KEYS:
            raise SnapshotContractError(f"dataset is not partition-snapshot classified: {self.dataset!r}")
        if not self.provider.strip() or not self.endpoint.strip():
            raise SnapshotContractError("provider and endpoint must not be blank")
        for label, value in (("requested_as_of", self.requested_as_of), ("fetched_at", self.fetched_at)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise SnapshotContractError(f"{label} must include a timezone")
        if self.identity_schema_version != IDENTITY_SCHEMA_VERSION:
            raise SnapshotContractError(
                f"unsupported identity_schema_version: {self.identity_schema_version}"
            )
        if self.row_count != len(self.rows):
            raise SnapshotContractError("row_count must equal the number of immutable rows")
        expected_ordinals = tuple(range(self.row_count))
        actual_ordinals = tuple(row.row_ordinal for row in self.rows)
        if actual_ordinals != expected_ordinals:
            raise SnapshotContractError("row ordinals must be immutable contiguous fetch-order positions")
        if any(row.batch_id != self.batch_id for row in self.rows):
            raise SnapshotContractError("every row batch_id must match its snapshot batch_id")
        identities = tuple(row.identity for row in self.rows)
        if len(identities) != len(set(identities)):
            raise SnapshotContractError("snapshot row identities must be unique")
        expected_hash = multiset_hash_from_payload_hashes(row.payload_hash for row in self.rows)
        if self.content_hash != expected_hash:
            raise SnapshotContractError("content_hash must equal the duplicate-sensitive payload multiset hash")
        if self.status is PartitionSnapshotStatus.FAILURE:
            if self.rows or self.failure is None:
                raise SnapshotContractError("failure snapshot must have no rows and include failure details")
        elif self.status is PartitionSnapshotStatus.SUCCESSFUL_EMPTY:
            if self.rows or self.failure is not None:
                raise SnapshotContractError("successful-empty snapshot must have no rows and no failure")
        elif self.status is PartitionSnapshotStatus.COMPLETE:
            if not self.rows or self.failure is not None:
                raise SnapshotContractError("complete snapshot must have rows and no failure")

    @classmethod
    def from_payloads(
        cls,
        *,
        batch_id: str,
        dataset: str,
        provider: str,
        endpoint: str,
        request_partition: CanonicalRequestPartition,
        requested_as_of: datetime,
        fetched_at: datetime,
        payloads: Sequence[Any],
        source_ids: Sequence[str | None] | None = None,
    ) -> "ImmutablePartitionSnapshot":
        ids = tuple(source_ids) if source_ids is not None else (None,) * len(payloads)
        if len(ids) != len(payloads):
            raise SnapshotContractError("source_ids length must match payloads length")
        rows = tuple(
            ImmutablePartitionSnapshotRow.from_payload(
                batch_id=batch_id,
                row_ordinal=ordinal,
                payload=payload,
                source_id=ids[ordinal],
            )
            for ordinal, payload in enumerate(payloads)
        )
        return cls(
            batch_id=batch_id,
            dataset=dataset,
            provider=provider,
            endpoint=endpoint,
            request_partition=request_partition,
            requested_as_of=requested_as_of,
            fetched_at=fetched_at,
            status=(PartitionSnapshotStatus.COMPLETE if rows else PartitionSnapshotStatus.SUCCESSFUL_EMPTY),
            identity_schema_version=IDENTITY_SCHEMA_VERSION,
            rows=rows,
            row_count=len(rows),
            content_hash=multiset_hash_from_payload_hashes(row.payload_hash for row in rows),
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
        failure: SnapshotFailure,
    ) -> "ImmutablePartitionSnapshot":
        return cls(
            batch_id=batch_id,
            dataset=dataset,
            provider=provider,
            endpoint=endpoint,
            request_partition=request_partition,
            requested_as_of=requested_as_of,
            fetched_at=fetched_at,
            status=PartitionSnapshotStatus.FAILURE,
            identity_schema_version=IDENTITY_SCHEMA_VERSION,
            rows=(),
            row_count=0,
            content_hash=multiset_hash_from_payload_hashes(()),
            failure=failure,
        )


__all__ = [
    "CanonicalPayload",
    "CanonicalRequestPartition",
    "IDENTITY_SCHEMA_VERSION",
    "ImmutablePartitionSnapshot",
    "ImmutablePartitionSnapshotRow",
    "PARTITION_SNAPSHOT_DATASET_KEYS",
    "PartitionSnapshotStatus",
    "SnapshotContractError",
    "SnapshotFailure",
    "canonical_multiset_hash",
    "canonical_payload_bytes",
    "canonical_payload_hash",
    "multiset_hash_from_payload_hashes",
]
