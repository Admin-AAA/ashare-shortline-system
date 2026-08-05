"""Create versioned portfolio-risk policy/state/decision/reservation facts.

Revision 0007 is forward-only, additive and re-entrant.  It executes only the
reviewed ``RISK_BATCH`` manifest.  It performs no data rewrite and has no DROP,
TRUNCATE, DELETE, rename or downgrade path.  Merely importing this module does
not connect to or mutate a database.
"""
from __future__ import annotations

from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from app.db.production_hardening_migration_plan import (
    RISK_BATCH,
    ColumnSpec,
    MigrationOperation,
    OperationKind,
    canonical_type_family,
)

revision = "0007_portfolio_risk"
down_revision = "0006_holdout_identity"
branch_labels = None
depends_on = None

RISK_MANIFEST_CHECKSUM = "9134301274f288057d7288e2a9fb0d4eb3cd4877413327be71acacd0ba590595"
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
    if family == "integer":
        return sa.Integer()
    if family == "bigint":
        return sa.BigInteger()
    if family == "tinyint":
        return sa.SmallInteger().with_variant(mysql.TINYINT(), "mysql")
    if family == "decimal":
        return sa.Numeric(precision=column.precision, scale=column.scale)
    if family == "longtext":
        return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")
    raise RuntimeError(f"unsupported M-RISK type family: {column.type_family}")


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
        "tinyinteger": "tinyint",
        "numeric": "decimal",
    }.get(family, family)


def _column_is_compatible(
    expected: ColumnSpec,
    observed: dict[str, Any],
    *,
    dialect_name: str,
) -> bool:
    sql_type = observed["type"]
    expected_family = canonical_type_family(expected.type_family)
    observed_family = _observed_type_family(sql_type)
    if dialect_name == "sqlite" and expected_family == "longtext":
        type_matches = observed_family == "text"
    elif dialect_name == "sqlite" and expected_family == "tinyint":
        type_matches = observed_family in {"smallint", "integer", "boolean"}
    else:
        type_matches = observed_family == expected_family
    if not type_matches or bool(observed.get("nullable", True)) != expected.nullable:
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
    tables = set(inspector.get_table_names())
    table_exists = operation.table in tables

    if operation.kind is OperationKind.CREATE_TABLE:
        if not table_exists:
            return _PENDING, "table is absent"
        columns = {item["name"]: item for item in inspector.get_columns(operation.table)}
        missing = [item.name for item in operation.table_columns if item.name not in columns]
        incompatible = [
            item.name
            for item in operation.table_columns
            if item.name in columns
            and not _column_is_compatible(item, columns[item.name], dialect_name=bind.dialect.name)
        ]
        primary_key = tuple(
            str(item)
            for item in inspector.get_pk_constraint(operation.table).get("constrained_columns", ())
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
            planned = {
                item.table for item in RISK_BATCH.operations
                if item.kind is OperationKind.CREATE_TABLE
            }
            if operation.table in planned:
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

    return _BLOCKED, "M-RISK permits only reviewed create-table/create-index operations"


def _apply_operation(operation: MigrationOperation) -> None:
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
    raise RuntimeError(f"unsupported M-RISK operation: {operation.operation_id}")


def upgrade() -> None:
    if RISK_BATCH.checksum != RISK_MANIFEST_CHECKSUM:
        raise RuntimeError("M-RISK manifest checksum does not match the reviewed Wave 3 plan")
    if RISK_BATCH.batch_id != "M-RISK" or RISK_BATCH.proposed_revision != revision:
        raise RuntimeError("M-RISK manifest is not bound to revision 0007_portfolio_risk")
    if RISK_BATCH.depends_on != down_revision:
        raise RuntimeError("M-RISK dependency does not match Alembic down_revision")
    if any(
        operation.kind not in {OperationKind.CREATE_TABLE, OperationKind.CREATE_INDEX}
        for operation in RISK_BATCH.operations
    ):
        raise RuntimeError("M-RISK manifest contains a non-additive operation")

    bind = op.get_bind()
    blocked = [
        f"{operation.object_name}: {reason}"
        for operation in RISK_BATCH.operations
        for status, reason in [_operation_status(bind, operation)]
        if status == _BLOCKED
    ]
    if blocked:
        raise RuntimeError("M-RISK precondition failed: " + "; ".join(blocked))

    for operation in RISK_BATCH.operations:
        status, reason = _operation_status(bind, operation)
        if status == _SATISFIED:
            continue
        if status != _PENDING:
            raise RuntimeError(f"M-RISK operation blocked ({operation.object_name}): {reason}")
        _apply_operation(operation)
        post_status, post_reason = _operation_status(bind, operation)
        if post_status != _SATISFIED:
            raise RuntimeError(
                f"M-RISK postcondition failed ({operation.object_name}): {post_reason}"
            )

    incomplete = [
        operation.object_name
        for operation in RISK_BATCH.operations
        if _operation_status(bind, operation)[0] != _SATISFIED
    ]
    if incomplete:
        raise RuntimeError(f"M-RISK final verification failed: {incomplete}")


def downgrade() -> None:
    raise RuntimeError(
        "Portfolio-risk migrations are forward-only; rollback the read/enforcement flag instead"
    )
