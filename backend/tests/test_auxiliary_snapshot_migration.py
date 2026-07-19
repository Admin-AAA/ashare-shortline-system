from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from app.db.migrations import all_migrations
from app.db.migrations.runner import MigrationRunner
from app.db.migrations.versions.v024_auxiliary_partition_snapshots import (
    BATCH_TABLE,
    POINTER_TABLE,
    ROW_TABLE,
    VERSION,
    migration,
)


class RecordingRestrictedContext:
    """Records only the whitelisted methods used by V024 apply functions."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def create_table(self, table, columns, *, constraints=()):
        self.calls.append(("create_table", table, tuple(columns), tuple(constraints)))

    def create_index(self, table, index, columns, *, unique=False):
        self.calls.append(("create_index", table, index, tuple(columns), unique))


def test_v024_is_discovered_once_in_stable_forward_order_with_stable_checksum() -> None:
    first = all_migrations()
    second = all_migrations()
    versions = tuple(item.version for item in first)
    second_by_version = {item.version: item for item in second}

    assert versions == tuple(sorted(versions))
    assert versions.count(VERSION) == 1
    assert migration.checksum == second_by_version[VERSION].checksum
    assert migration.checksum == "08a7bc09e1ce8086dbc4189d7ecca8ead2c4df0416baa57982ccbe64059fe72a"


def test_v024_apply_uses_only_restricted_create_operations() -> None:
    context = RecordingRestrictedContext()

    for operation in migration.operations:
        operation.apply(context)  # type: ignore[arg-type]

    assert [call[0:2] for call in context.calls if call[0] == "create_table"] == [
        ("create_table", BATCH_TABLE),
        ("create_table", ROW_TABLE),
        ("create_table", POINTER_TABLE),
    ]
    assert all(call[0] in {"create_table", "create_index"} for call in context.calls)


def test_v024_sqlite_contract_is_reentrant_and_preserves_legacy_rows() -> None:
    """SQLite contract test only; this is not real-MySQL acceptance."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE block_trades (id INTEGER PRIMARY KEY, code VARCHAR(6) NOT NULL)")
        )
        connection.execute(text("INSERT INTO block_trades (id, code) VALUES (1, '000001')"))

    runner = MigrationRunner(engine, (migration,))
    first = runner.run()
    second = runner.run()

    assert first.applied_versions == (VERSION,)
    assert second.skipped_versions == (VERSION,)
    inspector = inspect(engine)
    assert {BATCH_TABLE, ROW_TABLE, POINTER_TABLE} <= set(inspector.get_table_names())
    assert tuple(inspector.get_pk_constraint(BATCH_TABLE)["constrained_columns"]) == ("batch_id",)
    assert tuple(inspector.get_pk_constraint(ROW_TABLE)["constrained_columns"]) == (
        "batch_id",
        "row_ordinal",
    )
    assert tuple(inspector.get_pk_constraint(POINTER_TABLE)["constrained_columns"]) == (
        "dataset",
        "provider",
        "endpoint",
        "partition_hash",
    )

    row_indexes = {item["name"]: item for item in inspector.get_indexes(ROW_TABLE)}
    assert row_indexes["idx_aux_snapshot_rows_payload_hash"]["column_names"] == ["payload_hash"]
    assert row_indexes["idx_aux_snapshot_rows_payload_hash"]["unique"] == 0
    row_uniques = inspector.get_unique_constraints(ROW_TABLE)
    assert not any(item["column_names"] == ["payload_hash"] for item in row_uniques)

    pointer_uniques = inspector.get_unique_constraints(POINTER_TABLE)
    assert any(item["column_names"] == ["active_batch_id"] for item in pointer_uniques)
    row_foreign_keys = inspector.get_foreign_keys(ROW_TABLE)
    assert any(
        item["constrained_columns"] == ["batch_id"]
        and item["referred_table"] == BATCH_TABLE
        and item["referred_columns"] == ["batch_id"]
        for item in row_foreign_keys
    )
    pointer_foreign_keys = inspector.get_foreign_keys(POINTER_TABLE)
    assert any(
        item["constrained_columns"]
        == ["dataset", "provider", "endpoint", "partition_hash", "active_batch_id"]
        and item["referred_table"] == BATCH_TABLE
        for item in pointer_foreign_keys
    )
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM block_trades")) == 1
        assert connection.scalar(text("SELECT code FROM block_trades WHERE id = 1")) == "000001"
