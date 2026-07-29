"""Promote market-data provenance from JSON blobs into queryable evidence."""
from __future__ import annotations

import json
from collections.abc import Callable

from ..contract import MigrationContext, MigrationOperation, OperationState, define_migration
from ..preflight import DdlOperation, DdlTarget


VERSION = "V029_recommendation_source_evidence"
TABLE = "recommendation_runs"


def _column_state(column: str) -> Callable[[MigrationContext], OperationState]:
    def check(context: MigrationContext) -> OperationState:
        return OperationState.SATISFIED if context.has_column(TABLE, column) else OperationState.PENDING
    return check


def _add_column(column: str, definition: str) -> Callable[[MigrationContext], None]:
    def apply(context: MigrationContext) -> None:
        context.require_ddl_preflight(DdlTarget(
            migration_version=VERSION,
            operation=DdlOperation.ADD_COLUMN,
            table=TABLE,
            column=column,
            expected_data_types=("varchar", "text"),
            expected_nullable=True,
            expected_default=None,
            algorithm="INPLACE",
            lock="NONE",
        ))
        context.add_column(TABLE, column, definition, mysql_algorithm="INPLACE", mysql_lock="NONE")
    return apply


def _evidence_state(context: MigrationContext) -> OperationState:
    if not context.has_column(TABLE, "data_as_of") or not context.has_column(TABLE, "source_snapshot_id"):
        return OperationState.PENDING
    if not context.has_column(TABLE, "market_snapshot_json"):
        return OperationState.SATISFIED
    missing = context.count_rows(
        TABLE,
        where_sql=(
            "evaluation_scope = :scope AND (data_as_of IS NULL OR source_snapshot_id IS NULL) "
            "AND market_snapshot_json LIKE :snapshot_marker"
        ),
        params={"scope": "live_forward", "snapshot_marker": "%\"snapshot_id\"%"},
    )
    return OperationState.SATISFIED if missing == 0 else OperationState.PENDING


def _backfill_evidence(context: MigrationContext) -> None:
    if not context.has_column(TABLE, "market_snapshot_json"):
        return
    rows = context.select_rows(
        TABLE,
        ("run_id", "market_snapshot_json"),
        where_sql=(
            "evaluation_scope = :scope AND (data_as_of IS NULL OR source_snapshot_id IS NULL) "
            "AND market_snapshot_json LIKE :snapshot_marker"
        ),
        params={"scope": "live_forward", "snapshot_marker": "%\"snapshot_id\"%"},
    )
    for row in rows:
        try:
            snapshot = json.loads(str(row.get("market_snapshot_json") or "{}"))
        except (TypeError, ValueError):
            continue
        data_as_of = str(snapshot.get("trade_date") or "")
        snapshot_id = str(snapshot.get("snapshot_id") or "")
        if not data_as_of or not snapshot_id:
            continue
        context.update_rows(
            TABLE,
            {"data_as_of": data_as_of, "source_snapshot_id": snapshot_id},
            where_sql="run_id = :run_id",
            where_params={"run_id": row["run_id"]},
        )


def _verify(context: MigrationContext) -> None:
    for column in ("data_as_of", "source_snapshot_id"):
        if not context.has_column_shape(
            TABLE, column, data_types=("varchar", "text"), nullable=True, default=None
        ):
            raise RuntimeError(f"recommendation source evidence column is incomplete: {column}")


operations = []
for _column, _definition in (
    ("data_as_of", "VARCHAR(10) NULL"),
    ("source_snapshot_id", "VARCHAR(100) NULL"),
):
    operations.append(MigrationOperation(
        name=f"add_{_column}",
        checksum_material=f"ALTER recommendation_runs ADD {_column} {_definition}",
        precondition=_column_state(_column),
        apply=_add_column(_column, _definition),
        postcondition=_column_state(_column),
    ))
operations.append(MigrationOperation(
    name="backfill_source_evidence",
    checksum_material="backfill live-forward evidence only from persisted market snapshot JSON",
    precondition=_evidence_state,
    apply=_backfill_evidence,
    postcondition=_evidence_state,
))


migration = define_migration(
    version=VERSION,
    description="Persist recommendation market-data provenance",
    operations=tuple(operations),
    verify=_verify,
)


def migrations():
    return (migration,)
