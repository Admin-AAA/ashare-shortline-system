"""Persist whether a run is forward evidence or a replay/research artifact."""
from __future__ import annotations

from ..contract import MigrationContext, MigrationOperation, OperationState, define_migration
from ..preflight import DdlOperation, DdlTarget


VERSION = "V028_recommendation_evaluation_scope"
TABLE = "recommendation_runs"
COLUMN = "evaluation_scope"


def _column_state(context: MigrationContext) -> OperationState:
    return OperationState.SATISFIED if context.has_column(TABLE, COLUMN) else OperationState.PENDING


def _add_column(context: MigrationContext) -> None:
    context.require_ddl_preflight(
        DdlTarget(
            migration_version=VERSION,
            operation=DdlOperation.ADD_COLUMN,
            table=TABLE,
            column=COLUMN,
            expected_data_types=("varchar", "text"),
            expected_nullable=False,
            expected_default="historical_replay",
            algorithm="INPLACE",
            lock="NONE",
        )
    )
    context.add_column(
        TABLE,
        COLUMN,
        "VARCHAR(32) NOT NULL DEFAULT 'historical_replay'",
        mysql_algorithm="INPLACE",
        mysql_lock="NONE",
    )


def _forward_predicate(context: MigrationContext) -> str:
    if context.dialect_name == "mysql":
        return (
            "run_mode = :mode AND status = :status "
            "AND DATE(DATE_ADD(started_at, INTERVAL 8 HOUR)) = trade_date "
            "AND evaluation_scope <> :scope"
        )
    return (
        "run_mode = :mode AND status = :status "
        "AND DATE(DATETIME(started_at, '+8 hours')) = trade_date "
        "AND evaluation_scope <> :scope"
    )


def _scope_state(context: MigrationContext, *, mode: str, scope: str) -> OperationState:
    if not context.has_column(TABLE, COLUMN):
        return OperationState.PENDING
    if scope == "live_forward":
        predicate = _forward_predicate(context)
        params = {"mode": mode, "status": "completed", "scope": scope}
    else:
        predicate = "run_mode = :mode AND evaluation_scope <> :scope"
        params = {"mode": mode, "scope": scope}
    return (
        OperationState.SATISFIED
        if context.count_rows(TABLE, where_sql=predicate, params=params) == 0
        else OperationState.PENDING
    )


def _backfill_scope(context: MigrationContext, *, mode: str, scope: str) -> None:
    if scope == "live_forward":
        predicate = _forward_predicate(context)
        params = {"mode": mode, "status": "completed", "scope": scope}
    else:
        predicate = "run_mode = :mode AND evaluation_scope <> :scope"
        params = {"mode": mode, "scope": scope}
    context.update_rows(
        TABLE,
        {COLUMN: scope},
        where_sql=predicate,
        where_params=params,
    )


def _verify(context: MigrationContext) -> None:
    if not context.has_column_shape(
        TABLE,
        COLUMN,
        data_types=("varchar", "text"),
        nullable=False,
        default="historical_replay",
    ):
        raise RuntimeError("recommendation run evaluation scope schema is incomplete")


operations = [
    MigrationOperation(
        name="add_evaluation_scope",
        checksum_material=(
            "ALTER recommendation_runs ADD evaluation_scope VARCHAR(32) "
            "NOT NULL DEFAULT historical_replay"
        ),
        precondition=_column_state,
        apply=_add_column,
        postcondition=_column_state,
    )
]
for _mode, _scope in (
    ("formal", "live_forward"),
    ("experiment", "experiment"),
    ("backtest", "backtest"),
):
    operations.append(
        MigrationOperation(
            name=f"backfill_{_scope}",
            checksum_material=f"classify {_mode} recommendation runs as {_scope}",
            precondition=lambda context, mode=_mode, scope=_scope: _scope_state(
                context, mode=mode, scope=scope
            ),
            apply=lambda context, mode=_mode, scope=_scope: _backfill_scope(
                context, mode=mode, scope=scope
            ),
            postcondition=lambda context, mode=_mode, scope=_scope: _scope_state(
                context, mode=mode, scope=scope
            ),
        )
    )


migration = define_migration(
    version=VERSION,
    description="Classify recommendation runs by evaluation provenance",
    operations=tuple(operations),
    verify=_verify,
)


def migrations():
    return (migration,)
