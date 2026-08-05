"""Add scheduler occurrence claims and control-plane owner leases.

Revision 0008 is forward-only, additive and re-entrant.  It executes only the
reviewed ``SCHEDULER_BATCH`` manifest, rewrites no legacy row, and exposes no
destructive downgrade.  Importing this module performs no database access.
"""
from __future__ import annotations

from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from app.db.production_hardening_migration_plan import (
    SCHEDULER_BATCH,
    ColumnSpec,
    MigrationOperation,
    OperationKind,
    canonical_type_family,
)

revision = "0008_scheduler_claim"
down_revision = "0007_portfolio_risk"

SCHEDULER_MANIFEST_CHECKSUM = (
    "84d9b27d60758ee2655426a89bc870b329253afe606d8fef520949a99ad79b25"
)
branch_labels = None
depends_on = None

_PENDING = "pending"
_SATISFIED = "satisfied_no_op"
_BLOCKED = "blocked"
_ALLOWED_KINDS = frozenset(
    {OperationKind.ADD_COLUMN, OperationKind.CREATE_TABLE, OperationKind.CREATE_INDEX}
)


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
    if family == "integer":
        return sa.Integer()
    if family == "bigint":
        return sa.BigInteger()
    raise RuntimeError(f"unsupported M-SCHEDULER type family: {column.type_family}")


def _runtime_column(column: ColumnSpec) -> sa.Column[Any]:
    default = sa.text(column.server_default) if column.server_default is not None else None
    return sa.Column(
        column.name,
        _runtime_type(column),
        nullable=column.nullable,
        server_default=default,
    )


def _observed_type_family(sql_type: sa.types.TypeEngine[Any]) -> str:
    family = canonical_type_family(type(sql_type).__name__)
    return {
        "smallinteger": "smallint",
        "biginteger": "bigint",
    }.get(family, family)


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
    if expected.precision is not None and getattr(sql_type, "precision", None) != expected.precision:
        return False
    if expected.scale is not None and getattr(sql_type, "scale", None) != expected.scale:
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
    table_exists = operation.table in set(inspector.get_table_names())

    if operation.kind is OperationKind.ADD_COLUMN:
        if not table_exists or operation.column is None:
            return _BLOCKED, "required table is absent or add-column contract is invalid"
        columns = {item["name"]: item for item in inspector.get_columns(operation.table)}
        observed = columns.get(operation.column.name)
        if observed is None:
            return _PENDING, "column is absent"
        if not _column_is_compatible(
            operation.column, observed, dialect_name=bind.dialect.name
        ):
            return _BLOCKED, "column exists with an incompatible definition"
        return _SATISFIED, "compatible column already exists"

    if operation.kind is OperationKind.CREATE_TABLE:
        if not table_exists:
            return _PENDING, "table is absent"
        columns = {item["name"]: item for item in inspector.get_columns(operation.table)}
        missing = [item.name for item in operation.table_columns if item.name not in columns]
        incompatible = [
            item.name
            for item in operation.table_columns
            if item.name in columns
            and not _column_is_compatible(
                item, columns[item.name], dialect_name=bind.dialect.name
            )
        ]
        primary_key = tuple(
            str(item)
            for item in inspector.get_pk_constraint(operation.table).get(
                "constrained_columns", ()
            )
        )
        if missing or incompatible or primary_key != operation.table_primary_key:
            return (
                _BLOCKED,
                "existing table conflicts with reviewed columns or primary key: "
                f"missing={missing}, incompatible={incompatible}, "
                f"primary_key_matches={primary_key == operation.table_primary_key}",
            )
        return _SATISFIED, "compatible table already exists"

    if operation.kind is OperationKind.CREATE_INDEX:
        if operation.index is None:
            return _BLOCKED, "create-index contract is invalid"
        if not table_exists:
            planned_tables = {
                item.table
                for item in SCHEDULER_BATCH.operations
                if item.kind is OperationKind.CREATE_TABLE
            }
            if operation.table in planned_tables:
                return _PENDING, "awaiting planned table creation"
            return _BLOCKED, "required index table is absent"
        indexes = {
            str(item["name"]): item
            for item in inspector.get_indexes(operation.table)
            if item.get("name")
        }
        observed = indexes.get(operation.index.name)
        if observed is None:
            return _PENDING, "index is absent"
        columns = tuple(str(item) for item in (observed.get("column_names") or ()))
        if columns != operation.index.columns or bool(observed.get("unique")) != operation.index.unique:
            return _BLOCKED, "index exists with incompatible columns or uniqueness"
        return _SATISFIED, "compatible index already exists"

    return _BLOCKED, "M-SCHEDULER permits only additive operations"


def _apply_operation(operation: MigrationOperation) -> None:
    if operation.kind is OperationKind.ADD_COLUMN and operation.column is not None:
        op.add_column(operation.table, _runtime_column(operation.column))
        return
    if operation.kind is OperationKind.CREATE_TABLE:
        op.create_table(
            operation.table,
            *[_runtime_column(item) for item in operation.table_columns],
            sa.PrimaryKeyConstraint(*operation.table_primary_key),
        )
        return
    if operation.kind is OperationKind.CREATE_INDEX and operation.index is not None:
        op.create_index(
            operation.index.name,
            operation.table,
            list(operation.index.columns),
            unique=operation.index.unique,
        )
        return
    raise RuntimeError(f"unsupported M-SCHEDULER operation: {operation.operation_id}")


def upgrade() -> None:
    if SCHEDULER_BATCH.checksum != SCHEDULER_MANIFEST_CHECKSUM:
        raise RuntimeError(
            "M-SCHEDULER manifest checksum does not match the reviewed Wave 3 plan"
        )
    if SCHEDULER_BATCH.batch_id != "M-SCHEDULER":
        raise RuntimeError("scheduler manifest has the wrong batch identity")
    if SCHEDULER_BATCH.proposed_revision != revision:
        raise RuntimeError("scheduler manifest is not bound to revision 0008_scheduler_claim")
    if SCHEDULER_BATCH.depends_on != down_revision:
        raise RuntimeError("scheduler manifest dependency does not match down_revision")
    if any(operation.kind not in _ALLOWED_KINDS for operation in SCHEDULER_BATCH.operations):
        raise RuntimeError("scheduler manifest contains a non-additive operation")

    bind = op.get_bind()
    blocked = [
        f"{operation.object_name}: {reason}"
        for operation in SCHEDULER_BATCH.operations
        for status, reason in [_operation_status(bind, operation)]
        if status == _BLOCKED
    ]
    if blocked:
        raise RuntimeError("M-SCHEDULER precondition failed: " + "; ".join(blocked))

    for operation in SCHEDULER_BATCH.operations:
        status, reason = _operation_status(bind, operation)
        if status == _SATISFIED:
            continue
        if status != _PENDING:
            raise RuntimeError(
                f"M-SCHEDULER operation blocked ({operation.object_name}): {reason}"
            )
        _apply_operation(operation)
        post_status, post_reason = _operation_status(bind, operation)
        if post_status != _SATISFIED:
            raise RuntimeError(
                f"M-SCHEDULER postcondition failed ({operation.object_name}): "
                f"{post_reason}"
            )

    incomplete = [
        operation.object_name
        for operation in SCHEDULER_BATCH.operations
        if _operation_status(bind, operation)[0] != _SATISFIED
    ]
    if incomplete:
        raise RuntimeError(f"M-SCHEDULER final verification failed: {incomplete}")


def downgrade() -> None:
    raise RuntimeError(
        "Scheduler-claim migrations are forward-only; rollback the scheduler flag instead"
    )
