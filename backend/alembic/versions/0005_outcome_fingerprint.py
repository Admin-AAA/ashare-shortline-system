"""Add outcome calculation fingerprints and current-projection CAS columns.

Revision 0005 is additive, forward-only, and re-entrant.  It applies only the
reviewed M-OUTCOME operations to the existing outcome version history and
current projection; it never backfills, deletes, deduplicates, or rewrites
historical evidence.
"""
from __future__ import annotations

from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from app.db.production_hardening_migration_plan import (
    OUTCOME_BATCH,
    ColumnSpec,
    MigrationOperation,
    OperationKind,
    canonical_type_family,
)


revision = "0005_outcome_fingerprint"
down_revision = "0004_evidence_provenance"
branch_labels = None
depends_on = None

OUTCOME_MANIFEST_CHECKSUM = (
    "d2516a2093c333ba31f5b328b54ec532e77cfca7a23d76ab34f849c8fdc090d8"
)

_PENDING = "pending"
_SATISFIED = "satisfied_no_op"
_BLOCKED = "blocked"


def _runtime_type(column: ColumnSpec) -> sa.types.TypeEngine[Any]:
    family = canonical_type_family(column.type_family)
    if family == "varchar":
        return sa.String(length=column.length)
    if family == "char":
        return sa.CHAR(length=column.length)
    if family == "datetime":
        return sa.DateTime().with_variant(mysql.DATETIME(fsp=column.fsp), "mysql")
    if family == "smallint":
        return sa.SmallInteger()
    if family == "bigint":
        return sa.BigInteger()
    raise RuntimeError(f"unsupported M-OUTCOME type family: {column.type_family}")


def _runtime_column(column: ColumnSpec) -> sa.Column[Any]:
    return sa.Column(
        column.name,
        _runtime_type(column),
        nullable=column.nullable,
        server_default=(
            sa.text(column.server_default)
            if column.server_default is not None
            else None
        ),
    )


def _observed_type_family(sql_type: sa.types.TypeEngine[Any]) -> str:
    family = canonical_type_family(type(sql_type).__name__)
    return {"smallinteger": "smallint", "biginteger": "bigint"}.get(family, family)


def _column_is_compatible(
    expected: ColumnSpec,
    observed: dict[str, Any],
    *,
    dialect_name: str,
) -> bool:
    sql_type = observed["type"]
    if _observed_type_family(sql_type) != canonical_type_family(expected.type_family):
        return False
    if bool(observed.get("nullable", True)) != expected.nullable:
        return False
    if expected.length is not None and getattr(sql_type, "length", None) != expected.length:
        return False
    if (
        dialect_name == "mysql"
        and expected.fsp is not None
        and getattr(sql_type, "fsp", None) != expected.fsp
    ):
        return False
    return True


def _operation_status(
    bind: sa.engine.Connection,
    operation: MigrationOperation,
) -> tuple[str, str]:
    inspector = sa.inspect(bind)
    if operation.table not in set(inspector.get_table_names()):
        return _BLOCKED, "required existing outcome table is absent"

    if operation.kind is OperationKind.ADD_COLUMN:
        if operation.column is None:
            return _BLOCKED, "add-column contract is invalid"
        columns = {
            str(item["name"]): item for item in inspector.get_columns(operation.table)
        }
        observed = columns.get(operation.column.name)
        if observed is None:
            return _PENDING, "column is absent"
        if not _column_is_compatible(
            operation.column,
            observed,
            dialect_name=bind.dialect.name,
        ):
            return _BLOCKED, "column exists with an incompatible definition"
        return _SATISFIED, "compatible column already exists"

    if operation.kind is OperationKind.CREATE_INDEX:
        if operation.index is None:
            return _BLOCKED, "create-index contract is invalid"
        indexes = {
            str(item["name"]): item
            for item in inspector.get_indexes(operation.table)
            if item.get("name")
        }
        observed = indexes.get(operation.index.name)
        if observed is None:
            return _PENDING, "index is absent"
        columns = tuple(str(item) for item in (observed.get("column_names") or ()))
        if (
            columns != operation.index.columns
            or bool(observed.get("unique", False)) != operation.index.unique
        ):
            return _BLOCKED, "index exists with incompatible columns or uniqueness"
        return _SATISFIED, "compatible index already exists"

    return _BLOCKED, "M-OUTCOME permits only additive columns and indexes"


def _assert_preconditions(bind: sa.engine.Connection) -> None:
    blocked = [
        f"{operation.object_name}: {reason}"
        for operation in OUTCOME_BATCH.operations
        for status, reason in [_operation_status(bind, operation)]
        if status == _BLOCKED
    ]
    if blocked:
        raise RuntimeError("M-OUTCOME precondition failed: " + "; ".join(blocked))


def _apply_operation(operation: MigrationOperation) -> None:
    if operation.kind is OperationKind.ADD_COLUMN and operation.column is not None:
        op.add_column(operation.table, _runtime_column(operation.column))
        return
    if operation.kind is OperationKind.CREATE_INDEX and operation.index is not None:
        op.create_index(
            operation.index.name,
            operation.table,
            list(operation.index.columns),
            unique=operation.index.unique,
        )
        return
    raise RuntimeError(f"unsupported M-OUTCOME operation: {operation.operation_id}")


def upgrade() -> None:
    if OUTCOME_BATCH.checksum != OUTCOME_MANIFEST_CHECKSUM:
        raise RuntimeError(
            "M-OUTCOME manifest checksum does not match the reviewed task-0.3 plan"
        )

    bind = op.get_bind()
    _assert_preconditions(bind)
    for operation in OUTCOME_BATCH.operations:
        status, reason = _operation_status(bind, operation)
        if status == _SATISFIED:
            continue
        if status != _PENDING:
            raise RuntimeError(
                f"M-OUTCOME operation blocked ({operation.object_name}): {reason}"
            )
        _apply_operation(operation)
        post_status, post_reason = _operation_status(bind, operation)
        if post_status != _SATISFIED:
            raise RuntimeError(
                f"M-OUTCOME postcondition failed ({operation.object_name}): "
                f"{post_reason}"
            )

    incomplete = [
        operation.object_name
        for operation in OUTCOME_BATCH.operations
        if _operation_status(bind, operation)[0] != _SATISFIED
    ]
    if incomplete:
        raise RuntimeError(f"M-OUTCOME final verification failed: {incomplete}")


def downgrade() -> None:
    raise RuntimeError(
        "Outcome provenance migrations are forward-only; roll back read-path flags instead"
    )
