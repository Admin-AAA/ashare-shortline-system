"""Focused pure contract tests for the S4.5 auxiliary identity correction.

No test opens a database connection, executes migrations, or calls a provider.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from decimal import Decimal
from itertools import permutations

import pytest

from app.services.auxiliary.production import (
    AUDIT_ONLY_AUXILIARY_DATASET_KEYS,
    AUDIT_ONLY_IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS,
    AUXILIARY_AUDIT_ONLY_DATASET_INVENTORY,
    AUXILIARY_DATASET_COMPLETION,
    AUXILIARY_DATASET_INVENTORY,
    AUXILIARY_UNIQUE_MIGRATION_PLANS,
    IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS,
    STATE_ATOMIC_UPSERT_DATASET_KEYS,
    AuxiliaryCompletionState,
    AuxiliaryInventoryError,
    AuxiliaryPersistenceIdentity,
    AuxiliarySnapshotDatasetCompletion,
    AuxiliaryStateDatasetCompletion,
    validate_auxiliary_migration_inventory,
)
from app.services.auxiliary.snapshot_contracts import (
    CanonicalRequestPartition,
    ImmutablePartitionSnapshot,
    ImmutablePartitionSnapshotRow,
    PartitionSnapshotStatus,
    SnapshotContractError,
    SnapshotFailure,
    canonical_multiset_hash,
    canonical_payload_bytes,
    canonical_payload_hash,
)


REQUESTED_AS_OF = datetime(2026, 5, 18, tzinfo=timezone.utc)
FETCHED_AT = datetime(2026, 5, 18, 1, tzinfo=timezone.utc)
PARTITION = CanonicalRequestPartition.from_value(
    {"trade_date": date(2026, 5, 18), "codes": ("000001.SZ",)}
)


def test_canonicalization_preserves_null_empty_zero_and_literal_nan_distinctions() -> None:
    values = (None, "", 0, "nan", float("nan"))
    encodings = tuple(canonical_payload_bytes(value) for value in values)
    hashes = tuple(canonical_payload_hash(value) for value in values)

    assert len(set(encodings)) == len(values)
    assert len(set(hashes)) == len(values)
    assert b'"string","nan"' in encodings[3]
    assert b'"float_special","nan"' in encodings[4]


def test_canonicalization_is_nfc_map_order_decimal_and_date_deterministic() -> None:
    decomposed = "Cafe\u0301"
    composed = "Caf\u00e9"
    left = {
        "z": Decimal("1.2300"),
        "name": decomposed,
        "when": date(2026, 5, 18),
        "nested": {"b": 2, "a": 1},
    }
    right = {
        "nested": {"a": 1, "b": 2},
        "when": date(2026, 5, 18),
        "name": composed,
        "z": Decimal("1.23"),
    }

    assert canonical_payload_bytes(left) == canonical_payload_bytes(right)
    assert canonical_payload_hash(left) == canonical_payload_hash(right)
    assert b'"decimal","1.23"' in canonical_payload_bytes(left)
    assert b'"date","2026-05-18"' in canonical_payload_bytes(left)


def test_multiset_hash_is_permutation_invariant_but_duplicate_multiplicity_sensitive() -> None:
    payloads = ({"event": "A"}, {"event": "A"}, {"event": "B"})
    hashes = {canonical_multiset_hash(candidate) for candidate in permutations(payloads)}

    assert len(hashes) == 1
    assert canonical_multiset_hash(payloads) != canonical_multiset_hash(payloads[:2])
    assert canonical_multiset_hash(payloads) != canonical_multiset_hash((payloads[0], payloads[2]))


def test_snapshot_row_identity_is_only_batch_and_ordinal_and_is_immutable() -> None:
    first = ImmutablePartitionSnapshotRow.from_payload(
        batch_id="batch-1",
        row_ordinal=0,
        source_id="provider-id-duplicated",
        payload={"price": Decimal("10.00")},
    )
    duplicate = ImmutablePartitionSnapshotRow.from_payload(
        batch_id="batch-1",
        row_ordinal=1,
        source_id="provider-id-duplicated",
        payload={"price": Decimal("10.00")},
    )

    assert first.identity == ("batch-1", 0)
    assert duplicate.identity == ("batch-1", 1)
    assert first.payload_hash == duplicate.payload_hash
    with pytest.raises(FrozenInstanceError):
        first.row_ordinal = 2  # type: ignore[misc]


def test_complete_snapshot_preserves_duplicate_rows_and_uses_lossless_payload_hashing() -> None:
    raw = {"provider_only_field": "nan", "amount": Decimal("0"), "optional": None}
    snapshot = ImmutablePartitionSnapshot.from_payloads(
        batch_id="batch-complete",
        dataset="block_trade",
        provider="tushare_pro",
        endpoint="block_trade",
        request_partition=PARTITION,
        requested_as_of=REQUESTED_AS_OF,
        fetched_at=FETCHED_AT,
        payloads=(raw, raw),
        source_ids=(None, None),
    )

    assert snapshot.status is PartitionSnapshotStatus.COMPLETE
    assert snapshot.row_count == 2
    assert [row.identity for row in snapshot.rows] == [
        ("batch-complete", 0),
        ("batch-complete", 1),
    ]
    assert snapshot.rows[0].payload_hash == snapshot.rows[1].payload_hash
    assert snapshot.content_hash == canonical_multiset_hash((raw, raw))


def test_failure_and_successful_empty_are_distinct_terminal_contracts() -> None:
    successful_empty = ImmutablePartitionSnapshot.from_payloads(
        batch_id="batch-empty",
        dataset="share_float",
        provider="tushare_pro",
        endpoint="share_float",
        request_partition=PARTITION,
        requested_as_of=REQUESTED_AS_OF,
        fetched_at=FETCHED_AT,
        payloads=(),
    )
    failed = ImmutablePartitionSnapshot.failed(
        batch_id="batch-failed",
        dataset="share_float",
        provider="tushare_pro",
        endpoint="share_float",
        request_partition=PARTITION,
        requested_as_of=REQUESTED_AS_OF,
        fetched_at=FETCHED_AT,
        failure=SnapshotFailure(code="provider_timeout", message="request timed out"),
    )

    assert successful_empty.status is PartitionSnapshotStatus.SUCCESSFUL_EMPTY
    assert successful_empty.failure is None
    assert failed.status is PartitionSnapshotStatus.FAILURE
    assert failed.failure is not None
    assert successful_empty.content_hash == failed.content_hash
    with pytest.raises(SnapshotContractError, match="successful-empty"):
        ImmutablePartitionSnapshot(
            batch_id="invalid-empty",
            dataset="share_float",
            provider="tushare_pro",
            endpoint="share_float",
            request_partition=PARTITION,
            requested_as_of=REQUESTED_AS_OF,
            fetched_at=FETCHED_AT,
            status=PartitionSnapshotStatus.SUCCESSFUL_EMPTY,
            identity_schema_version=1,
            rows=(),
            row_count=0,
            content_hash=successful_empty.content_hash,
            failure=SnapshotFailure(code="wrong", message="must not be present"),
        )


def test_inventory_separates_exactly_thirteen_state_four_publishable_snapshots_and_one_audit_only_artifact() -> None:
    state = {entry.key for entry in AUXILIARY_DATASET_INVENTORY if entry.is_state_dataset}
    snapshots = {entry.key for entry in AUXILIARY_DATASET_INVENTORY if entry.is_snapshot_dataset}
    audit_only = {entry.key for entry in AUXILIARY_AUDIT_ONLY_DATASET_INVENTORY}

    assert state == STATE_ATOMIC_UPSERT_DATASET_KEYS
    assert snapshots == IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS
    assert audit_only == AUDIT_ONLY_AUXILIARY_DATASET_KEYS
    assert audit_only == AUDIT_ONLY_IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS
    assert len(state) == 13
    assert len(snapshots) == 4
    assert len(AUXILIARY_DATASET_INVENTORY) == 17
    assert state.isdisjoint(snapshots)
    assert snapshots.isdisjoint(audit_only)
    for entry in AUXILIARY_DATASET_INVENTORY:
        if entry.key in state:
            assert entry.identity_semantics is AuxiliaryPersistenceIdentity.STATE_ATOMIC_UPSERT
            assert entry.natural_key
        else:
            assert entry.identity_semantics is AuxiliaryPersistenceIdentity.IMMUTABLE_PARTITION_SNAPSHOT
            assert entry.natural_key == ()
            assert entry.row_identity == ("batch_id", "row_ordinal")
            assert entry.provider and entry.endpoint
    assert AUXILIARY_AUDIT_ONLY_DATASET_INVENTORY[0].view_key == "audit.institutional_survey"


def test_readiness_tracks_real_mysql_only_for_state_and_snapshot_acceptance_for_events() -> None:
    assert set(AUXILIARY_UNIQUE_MIGRATION_PLANS) == STATE_ATOMIC_UPSERT_DATASET_KEYS
    for dataset_key in STATE_ATOMIC_UPSERT_DATASET_KEYS:
        record = AUXILIARY_DATASET_COMPLETION[dataset_key]
        assert isinstance(record, AuxiliaryStateDatasetCompletion)
        assert record.unique_migration is AuxiliaryCompletionState.PENDING_REAL_MYSQL
        assert record.two_connection_acceptance is AuxiliaryCompletionState.PENDING_REAL_MYSQL
    for dataset_key in IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS:
        record = AUXILIARY_DATASET_COMPLETION[dataset_key]
        assert isinstance(record, AuxiliarySnapshotDatasetCompletion)
        assert record.snapshot_schema is AuxiliaryCompletionState.PENDING_SNAPSHOT_SCHEMA
        assert record.provider_contract is AuxiliaryCompletionState.PENDING_PROVIDER_CONTRACT
        assert record.publish_acceptance is AuxiliaryCompletionState.PENDING_PUBLISH_ACCEPTANCE
        assert not hasattr(record, "unique_migration")


def test_inventory_validation_rejects_event_dataset_accidentally_added_to_unique_plans() -> None:
    accidental = dict(AUXILIARY_UNIQUE_MIGRATION_PLANS)
    accidental["block_trade"] = AUXILIARY_UNIQUE_MIGRATION_PLANS["capital_flow"]

    with pytest.raises(AuxiliaryInventoryError, match="must never appear"):
        validate_auxiliary_migration_inventory(plans=accidental)
