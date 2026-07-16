"""Forward-only generic persistence for immutable auxiliary partition snapshots.

V024 creates only new owned tables.  It does not inspect, backfill, delete, or replace
legacy auxiliary projection tables.  All DDL goes through ``MigrationContext`` and is
verified by exact primary-key, UNIQUE, foreign-key, and non-unique index shapes.
"""
from __future__ import annotations

from collections.abc import Mapping

from ..contract import (
    ForeignKeySignature,
    Migration,
    MigrationContext,
    MigrationOperation,
    OperationState,
    define_migration,
)


VERSION = "V024_auxiliary_partition_snapshots"
BATCH_TABLE = "auxiliary_snapshot_batches"
ROW_TABLE = "auxiliary_snapshot_rows"
POINTER_TABLE = "auxiliary_active_partition_snapshots"

Schema = tuple[
    tuple[tuple[str, str], ...],
    tuple[str, ...],
    tuple[tuple[str, tuple[str, ...], bool], ...],
]

_SCHEMA: Mapping[str, Schema] = {
    BATCH_TABLE: (
        (
            ("batch_id", "VARCHAR(80) NOT NULL"),
            ("dataset", "VARCHAR(64) NOT NULL"),
            ("provider", "VARCHAR(64) NOT NULL"),
            ("endpoint", "VARCHAR(96) NOT NULL"),
            ("request_partition_bytes", "LONGBLOB NOT NULL"),
            ("partition_hash", "CHAR(64) NOT NULL"),
            ("partition_schema_version", "SMALLINT NOT NULL"),
            ("requested_as_of", "DATETIME(6) NOT NULL"),
            ("fetched_at", "DATETIME(6) NOT NULL"),
            ("status", "VARCHAR(24) NOT NULL"),
            ("identity_schema_version", "SMALLINT NOT NULL"),
            ("row_count", "INTEGER NOT NULL"),
            ("content_hash", "CHAR(64) NOT NULL"),
            ("failure_code", "VARCHAR(80) NULL"),
            ("failure_message", "TEXT NULL"),
            ("created_at", "DATETIME(6) NOT NULL"),
        ),
        (
            "CONSTRAINT pk_aux_snapshot_batches PRIMARY KEY (batch_id)",
            "CONSTRAINT uq_aux_snapshot_batch_partition UNIQUE (dataset, provider, endpoint, partition_hash, batch_id)",
            "CONSTRAINT ck_aux_snapshot_batch_status CHECK (status IN ('failure', 'successful_empty', 'complete'))",
            "CONSTRAINT ck_aux_snapshot_batch_row_count CHECK (row_count >= 0)",
        ),
        (
            (
                "idx_aux_snapshot_batches_partition_time",
                ("dataset", "provider", "endpoint", "partition_hash", "fetched_at"),
                False,
            ),
            ("idx_aux_snapshot_batches_status", ("status", "created_at"), False),
        ),
    ),
    ROW_TABLE: (
        (
            ("batch_id", "VARCHAR(80) NOT NULL"),
            ("row_ordinal", "INTEGER NOT NULL"),
            ("source_id", "VARCHAR(255) NULL"),
            ("payload_hash", "CHAR(64) NOT NULL"),
            ("payload_bytes", "LONGBLOB NOT NULL"),
        ),
        (
            "CONSTRAINT pk_aux_snapshot_rows PRIMARY KEY (batch_id, row_ordinal)",
            "CONSTRAINT fk_aux_snapshot_rows_batch FOREIGN KEY (batch_id) REFERENCES auxiliary_snapshot_batches (batch_id) ON DELETE RESTRICT",
            "CONSTRAINT ck_aux_snapshot_row_ordinal CHECK (row_ordinal >= 0)",
        ),
        (("idx_aux_snapshot_rows_payload_hash", ("payload_hash",), False),),
    ),
    POINTER_TABLE: (
        (
            ("dataset", "VARCHAR(64) NOT NULL"),
            ("provider", "VARCHAR(64) NOT NULL"),
            ("endpoint", "VARCHAR(96) NOT NULL"),
            ("partition_hash", "CHAR(64) NOT NULL"),
            ("active_batch_id", "VARCHAR(80) NOT NULL"),
            ("published_at", "DATETIME(6) NOT NULL"),
        ),
        (
            "CONSTRAINT pk_aux_active_partition PRIMARY KEY (dataset, provider, endpoint, partition_hash)",
            "CONSTRAINT uq_aux_active_partition_batch UNIQUE (active_batch_id)",
            "CONSTRAINT fk_aux_active_partition_batch FOREIGN KEY (dataset, provider, endpoint, partition_hash, active_batch_id) REFERENCES auxiliary_snapshot_batches (dataset, provider, endpoint, partition_hash, batch_id) ON DELETE RESTRICT",
        ),
        (),
    ),
}

_PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    BATCH_TABLE: ("batch_id",),
    ROW_TABLE: ("batch_id", "row_ordinal"),
    POINTER_TABLE: ("dataset", "provider", "endpoint", "partition_hash"),
}
_EXPECTED_UNIQUES: Mapping[str, tuple[tuple[str, ...], ...]] = {
    BATCH_TABLE: (("dataset", "provider", "endpoint", "partition_hash", "batch_id"),),
    ROW_TABLE: (),
    POINTER_TABLE: (("active_batch_id",),),
}
_EXPECTED_FOREIGN_KEYS: Mapping[str, tuple[ForeignKeySignature, ...]] = {
    BATCH_TABLE: (),
    ROW_TABLE: (
        ForeignKeySignature(("batch_id",), BATCH_TABLE, ("batch_id",), "RESTRICT"),
    ),
    POINTER_TABLE: (
        ForeignKeySignature(
            ("dataset", "provider", "endpoint", "partition_hash", "active_batch_id"),
            BATCH_TABLE,
            ("dataset", "provider", "endpoint", "partition_hash", "batch_id"),
            "RESTRICT",
        ),
    ),
}
_TYPE_ALIASES: Mapping[str, tuple[str, ...]] = {
    "VARCHAR": ("varchar",),
    "CHAR": ("char",),
    "LONGBLOB": ("longblob", "blob"),
    "SMALLINT": ("smallint",),
    "DATETIME": ("datetime",),
    "INTEGER": ("integer",),
    "TEXT": ("text",),
}


def _definition_type(definition: str) -> tuple[str, ...]:
    token = definition.split("(", 1)[0].split()[0].upper()
    return _TYPE_ALIASES[token]


def _definition_nullable(definition: str) -> bool:
    return " NOT NULL" not in f" {definition.upper()}"


def _shape_errors(context: MigrationContext, table: str) -> tuple[str, ...]:
    if not context.has_table(table):
        return ("missing table",)
    columns, _constraints, indexes = _SCHEMA[table]
    errors: list[str] = []
    primary_key = _PRIMARY_KEYS[table]
    verify_mysql_column_shape = context.dialect_name == "mysql"
    for column, definition in columns:
        signature = context.column_signature(table, column)
        if signature is None:
            errors.append(f"missing column {column}")
            continue
        # SQLite contract tests intentionally do not pretend to be MySQL type
        # acceptance: reflection collapses LONGBLOB/DATETIME precision and PK
        # nullability.  Exact deployed type/null/default checks remain mandatory on
        # the target MySQL dialect; SQLite still verifies every column plus exact
        # PK/UNIQUE/FK/index shapes below.
        if verify_mysql_column_shape:
            if signature.data_type not in _definition_type(definition):
                errors.append(f"column {column} type={signature.data_type}")
            expected_nullable = _definition_nullable(definition)
            if signature.nullable is not expected_nullable:
                errors.append(f"column {column} nullable={signature.nullable}")
            if signature.default is not None:
                errors.append(f"column {column} has unexpected default")
    if context.primary_key_columns(table) != primary_key:
        errors.append(f"primary key={context.primary_key_columns(table)!r}")
    actual_uniques = tuple(sorted(signature.columns for signature in context.unique_signatures(table)))
    if actual_uniques != tuple(sorted(_EXPECTED_UNIQUES[table])):
        errors.append(f"unique shapes={actual_uniques!r}")
    if context.foreign_key_signatures(table) != _EXPECTED_FOREIGN_KEYS[table]:
        errors.append(f"foreign keys={context.foreign_key_signatures(table)!r}")
    for name, index_columns, unique in indexes:
        if not context.has_index_shape(
            table,
            name,
            columns=index_columns,
            unique=unique,
        ):
            errors.append(f"index {name} shape")
    return tuple(errors)


def _table_state(context: MigrationContext, table: str) -> OperationState:
    return OperationState.SATISFIED if not _shape_errors(context, table) else OperationState.PENDING


def _apply_table(context: MigrationContext, table: str) -> None:
    columns, constraints, indexes = _SCHEMA[table]
    context.create_table(table, columns, constraints=constraints)
    for name, index_columns, unique in indexes:
        context.create_index(table, name, index_columns, unique=unique)


def _operation(table: str) -> MigrationOperation:
    columns, constraints, indexes = _SCHEMA[table]
    return MigrationOperation(
        name=f"create_{table}",
        checksum_material=repr(
            {
                "table": table,
                "columns": columns,
                "constraints": constraints,
                "indexes": indexes,
                "primary_key": _PRIMARY_KEYS[table],
                "uniques": _EXPECTED_UNIQUES[table],
                "foreign_keys": _EXPECTED_FOREIGN_KEYS[table],
                "purpose": "immutable auxiliary snapshot persistence; no legacy mutation or backfill",
            }
        ),
        precondition=lambda context, table=table: _table_state(context, table),
        apply=lambda context, table=table: _apply_table(context, table),
        postcondition=lambda context, table=table: _table_state(context, table),
    )


def _verify(context: MigrationContext) -> None:
    errors = {
        table: _shape_errors(context, table)
        for table in _SCHEMA
        if _shape_errors(context, table)
    }
    if errors:
        details = "; ".join(f"{table}: {', '.join(items)}" for table, items in errors.items())
        raise RuntimeError(f"auxiliary snapshot schema is incomplete or incompatible: {details}")


migration = define_migration(
    version=VERSION,
    description="Create immutable auxiliary partition batches, rows and active pointers",
    operations=tuple(_operation(table) for table in _SCHEMA),
    verify=_verify,
)


def migrations() -> tuple[Migration, ...]:
    return (migration,)


__all__ = [
    "BATCH_TABLE",
    "POINTER_TABLE",
    "ROW_TABLE",
    "VERSION",
    "migration",
    "migrations",
]
