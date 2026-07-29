"""Record recommendations cancelled before any confirmed buy."""
from __future__ import annotations

from ..contract import MigrationContext, MigrationOperation, OperationState, define_migration
from ..preflight import DdlOperation, DdlTarget


VERSION = "V027_cancelled_before_entry"
TABLE = "recommendation_execution_states"
_COLUMNS = (
    ("cancelled_before_entry", "BOOLEAN NOT NULL DEFAULT 0", ("boolean", "bool", "tinyint"), False, "0"),
    ("first_cancelled_at", "DATETIME(6) NULL", ("datetime", "timestamp"), True, None),
    ("first_cancelled_price", "DOUBLE NULL", ("double", "float", "real"), True, None),
    ("first_cancel_reason", "TEXT NULL", ("text",), True, None),
)


def _column_state(context: MigrationContext, column: str) -> OperationState:
    return OperationState.SATISFIED if context.has_column(TABLE, column) else OperationState.PENDING


def _add_column(
    context: MigrationContext,
    column: str,
    definition: str,
    data_types: tuple[str, ...],
    nullable: bool,
    default: str | None,
) -> None:
    context.require_ddl_preflight(
        DdlTarget(
            migration_version=VERSION,
            operation=DdlOperation.ADD_COLUMN,
            table=TABLE,
            column=column,
            expected_data_types=data_types,
            expected_nullable=nullable,
            expected_default=default,
            algorithm="INPLACE",
            lock="NONE",
        )
    )
    context.add_column(TABLE, column, definition, mysql_algorithm="INPLACE", mysql_lock="NONE")


def _backfill_state(context: MigrationContext) -> OperationState:
    if any(not context.has_column(TABLE, column) for column, *_shape in _COLUMNS):
        return OperationState.PENDING
    remaining = context.count_rows(
        TABLE,
        where_sql="latest_decision_code = :decision AND ever_buy_confirmed = :ever_buy AND cancelled_before_entry = :cancelled",
        params={"decision": "cancel", "ever_buy": 0, "cancelled": 0},
    )
    return OperationState.SATISFIED if remaining == 0 else OperationState.PENDING


def _backfill(context: MigrationContext) -> None:
    context.update_rows(
        TABLE,
        {"cancelled_before_entry": 1},
        where_sql="latest_decision_code = :decision AND ever_buy_confirmed = :ever_buy AND cancelled_before_entry = :cancelled",
        where_params={"decision": "cancel", "ever_buy": 0, "cancelled": 0},
    )


def _verify(context: MigrationContext) -> None:
    missing = [column for column, *_shape in _COLUMNS if not context.has_column(TABLE, column)]
    if missing:
        raise RuntimeError(f"cancelled-before-entry schema is incomplete: {missing}")


operations = [
    MigrationOperation(
        name=f"add_{column}",
        checksum_material=f"ALTER {TABLE} ADD COLUMN {column} {definition} ALGORITHM=INPLACE LOCK=NONE",
        precondition=lambda context, column=column: _column_state(context, column),
        apply=lambda context, column=column, definition=definition, data_types=data_types, nullable=nullable, default=default: _add_column(
            context, column, definition, data_types, nullable, default
        ),
        postcondition=lambda context, column=column: _column_state(context, column),
    )
    for column, definition, data_types, nullable, default in _COLUMNS
]
operations.append(
    MigrationOperation(
        name="backfill_existing_cancelled_before_entry",
        checksum_material=(
            "UPDATE recommendation_execution_states SET cancelled_before_entry=1 "
            "WHERE latest_decision_code=cancel AND ever_buy_confirmed=0 AND cancelled_before_entry=0"
        ),
        precondition=_backfill_state,
        apply=_backfill,
        postcondition=_backfill_state,
    )
)

migration = define_migration(
    version=VERSION,
    description="Persist cancellation facts before any confirmed buy",
    operations=tuple(operations),
    verify=_verify,
)


def migrations():
    return (migration,)
