"""Add prompt-context audit summary column to LLM call logs.

Revision 0009 is additive, forward-only, and re-entrant.  It applies only the
reviewed ``M-LLM`` add-column operation; it never backfills, deletes, renames or
rewrites existing rows.  Importing this module performs no database access.
"""
from __future__ import annotations

from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from app.db.production_hardening_migration_plan import (
    LLM_BATCH,
    ColumnSpec,
    MigrationOperation,
    OperationKind,
    canonical_type_family,
)


revision = "0009_llm_context_audit"
down_revision = "0008_scheduler_claim"
branch_labels = None
depends_on = None

LLM_MANIFEST_CHECKSUM = (
    "7e89b8678fd443c2ea6f8260b27a144345a974be64996994bbeb52dd4718aa3b"
)

_PENDING = "pending"
_SATISFIED = "satisfied_no_op"
_BLOCKED = "blocked"


def _runtime_type(column: ColumnSpec) -> sa.types.TypeEngine[Any]:
    family = canonical_type_family(column.type_family)
    if family == "longtext":
        return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")
    raise RuntimeError(f"unsupported M-LLM type family: {column.type_family}")


def _runtime_column(column: ColumnSpec) -> sa.Column[Any]:
    return sa.Column(
        column.name,
        _runtime_type(column),
        nullable=column.nullable,
    )


def _observed_type_family(sql_type: sa.types.TypeEngine[Any]) -> str:
    return canonical_type_family(type(sql_type).__name__)


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
    else:
        type_matches = observed_family == expected_family
    if not type_matches or bool(observed.get("nullable", True)) != expected.nullable:
        return False
    return True


def _operation_status(
    bind: sa.engine.Connection,
    operation: MigrationOperation,
) -> tuple[str, str]:
    inspector = sa.inspect(bind)
    if operation.table not in set(inspector.get_table_names()):
        return _BLOCKED, "required existing api_call_logs table is absent"

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

    return _BLOCKED, "M-LLM permits only the additive context audit column"


def _apply_operation(operation: MigrationOperation) -> None:
    if operation.kind is OperationKind.ADD_COLUMN and operation.column is not None:
        op.add_column(operation.table, _runtime_column(operation.column))
        return
    raise RuntimeError(f"unsupported M-LLM operation: {operation.operation_id}")


def upgrade() -> None:
    if LLM_BATCH.checksum != LLM_MANIFEST_CHECKSUM:
        raise RuntimeError(
            "M-LLM manifest checksum does not match the reviewed R-LLM-4 plan"
        )
    if LLM_BATCH.batch_id != "M-LLM" or LLM_BATCH.proposed_revision != revision:
        raise RuntimeError("LLM manifest is not bound to revision 0009_llm_context_audit")
    if LLM_BATCH.depends_on != down_revision:
        raise RuntimeError("LLM manifest dependency does not match Alembic down_revision")

    bind = op.get_bind()
    blocked = [
        f"{operation.object_name}: {reason}"
        for operation in LLM_BATCH.operations
        for status, reason in [_operation_status(bind, operation)]
        if status == _BLOCKED
    ]
    if blocked:
        raise RuntimeError("M-LLM precondition failed: " + "; ".join(blocked))

    for operation in LLM_BATCH.operations:
        status, reason = _operation_status(bind, operation)
        if status == _SATISFIED:
            continue
        if status != _PENDING:
            raise RuntimeError(
                f"M-LLM operation blocked ({operation.object_name}): {reason}"
            )
        _apply_operation(operation)
        post_status, post_reason = _operation_status(bind, operation)
        if post_status != _SATISFIED:
            raise RuntimeError(
                f"M-LLM postcondition failed ({operation.object_name}): {post_reason}"
            )

    incomplete = [
        operation.object_name
        for operation in LLM_BATCH.operations
        if _operation_status(bind, operation)[0] != _SATISFIED
    ]
    if incomplete:
        raise RuntimeError(f"M-LLM final verification failed: {incomplete}")


def downgrade() -> None:
    raise RuntimeError(
        "LLM context-audit migrations are forward-only; roll back read-path flags instead"
    )
