"""Forward-only lifecycle columns for versioned workbench run requests.

This migration augments ``recommendation_runs`` only.  It neither mutates nor
removes historical Recommendation/RecommendationOutcome evidence.  Existing
legacy rows remain readable; only newly enqueued workbench runs populate the
canonical request, revision, checkpoint and lease columns.
"""
from __future__ import annotations

from ..contract import Migration, MigrationContext, MigrationOperation, OperationState, define_migration
from ..preflight import DdlOperation, DdlTarget


_TABLE = "recommendation_runs"
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("request_schema_version", "INTEGER NULL"),
    ("request_json", "LONGTEXT NULL"),
    ("execution_version", "VARCHAR(128) NULL"),
    ("run_revision", "INTEGER NOT NULL DEFAULT 0"),
    ("lifecycle_phase", "VARCHAR(32) NULL"),
    ("checkpoint_side_effect_key", "VARCHAR(160) NULL"),
    ("lease_owner_id", "VARCHAR(160) NULL"),
    ("lease_token", "VARCHAR(128) NULL"),
    ("lease_acquired_at", "DATETIME(6) NULL"),
    ("lease_expires_at", "DATETIME(6) NULL"),
    ("stable_error_code", "VARCHAR(80) NULL"),
)
_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("idx_recommendation_runs_lease", ("status", "lease_expires_at")),
    ("idx_recommendation_runs_lifecycle", ("lifecycle_phase", "run_revision")),
)


def _column_state(context: MigrationContext, column: str) -> OperationState:
    return OperationState.SATISFIED if context.has_column(_TABLE, column) else OperationState.PENDING


def _index_state(context: MigrationContext, index: str) -> OperationState:
    return OperationState.SATISFIED if context.has_index(_TABLE, index) else OperationState.PENDING


def _column_operation(column: str, definition: str) -> MigrationOperation:
    return MigrationOperation(
        name=f"add_{_TABLE}_{column}",
        checksum_material=f"ALTER {_TABLE} ADD COLUMN {column} {definition}",
        precondition=lambda context, column=column: _column_state(context, column),
        apply=lambda context, column=column, definition=definition: context.add_column(_TABLE, column, definition),
        postcondition=lambda context, column=column: _column_state(context, column),
    )


def _index_operation(index: str, columns: tuple[str, ...]) -> MigrationOperation:
    target = DdlTarget(
        migration_version="V002_workbench_run_lifecycle",
        operation=DdlOperation.ADD_INDEX,
        table=_TABLE,
        index_columns=columns,
        algorithm="INPLACE",
        lock="NONE",
    )
    return MigrationOperation(
        name=f"create_{index}",
        checksum_material=f"CREATE INDEX {index} ON {_TABLE} ({','.join(columns)})",
        precondition=lambda context, index=index: _index_state(context, index),
        apply=lambda context, index=index, columns=columns: context.create_index(
            _TABLE,
            index,
            columns,
            ddl_target=target,
        ),
        postcondition=lambda context, index=index: _index_state(context, index),
    )


def _verify(context: MigrationContext) -> None:
    missing_columns = [column for column, _definition in _COLUMNS if not context.has_column(_TABLE, column)]
    missing_indexes = [index for index, _columns in _INDEXES if not context.has_index(_TABLE, index)]
    if missing_columns or missing_indexes:
        raise RuntimeError(
            "workbench run lifecycle schema is incomplete: "
            f"columns={','.join(missing_columns) or '-'} indexes={','.join(missing_indexes) or '-'}"
        )


migration = define_migration(
    version="V002_workbench_run_lifecycle",
    description="Add canonical request, CAS revision, checkpoint and lease columns to workbench runs",
    operations=(
        *(_column_operation(column, definition) for column, definition in _COLUMNS),
        *(_index_operation(index, columns) for index, columns in _INDEXES),
    ),
    verify=_verify,
)


def migrations() -> tuple[Migration, ...]:
    return (migration,)
