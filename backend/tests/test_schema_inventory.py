"""Regression coverage for the exact legacy init_db schema inventory."""
from __future__ import annotations

from app.db.migrations.schema_inventory import (
    BASELINE_REQUIRED_COLUMNS,
    BASELINE_REQUIRED_INDEXES,
    FORBIDDEN_DESTRUCTIVE_TABLES,
    LEGACY_SCHEMA_INVENTORY,
)
from app.db.migrations.versions.v000_baseline import (
    BASELINE_REQUIRED_COLUMNS as V000_COLUMNS,
    BASELINE_REQUIRED_INDEXES as V000_INDEXES,
)


def test_inventory_is_authoritative_for_v000_baseline() -> None:
    assert dict(BASELINE_REQUIRED_COLUMNS) == dict(V000_COLUMNS)
    assert dict(BASELINE_REQUIRED_INDEXES) == dict(V000_INDEXES)
    assert len(BASELINE_REQUIRED_COLUMNS) == 27
    assert sum(len(columns) for columns in BASELINE_REQUIRED_COLUMNS.values()) == 84
    assert sum(len(indexes) for indexes in BASELINE_REQUIRED_INDEXES.values()) == 34


def test_inventory_has_explicit_target_version_and_exact_helper_surface() -> None:
    assert len(LEGACY_SCHEMA_INVENTORY) == 121
    assert {
        item.helper for item in LEGACY_SCHEMA_INVENTORY
    } >= {"_ensure_column", "_ensure_column_type", "_ensure_column_default", "_ensure_index", "_migrate_minute_kbars"}
    assert all(
        item.target_version == "V000" or item.target_version == "FORBIDDEN_NOT_MANAGED"
        for item in LEGACY_SCHEMA_INVENTORY
    )
    minute_frequency = next(
        item for item in LEGACY_SCHEMA_INVENTORY if item.table == "minute_kbars" and item.name == "freq"
    )
    assert minute_frequency.definition == "VARCHAR(5) DEFAULT '1min'"
    minute_unique = next(
        item
        for item in LEGACY_SCHEMA_INVENTORY
        if item.name == "uq_minute_kbar_code_freq_time"
    )
    assert minute_unique.kind == "unique"
    assert minute_unique.natural_key == ("code", "freq", "trade_time")


def test_destructive_v2_tables_are_negative_inventory_only() -> None:
    assert "raw_quotes" in FORBIDDEN_DESTRUCTIVE_TABLES
    assert "daily_indicators" in FORBIDDEN_DESTRUCTIVE_TABLES
    assert not any(item.table in FORBIDDEN_DESTRUCTIVE_TABLES for item in LEGACY_SCHEMA_INVENTORY)
    assert not any(item.destructive for item in LEGACY_SCHEMA_INVENTORY)
