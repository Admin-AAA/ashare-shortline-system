"""Add an append-only outcome calculation ledger and current-version pointer."""
from __future__ import annotations

from ..contract import MigrationContext, MigrationOperation, OperationState, define_migration
from ..preflight import DdlOperation, DdlTarget


VERSION = "V030_outcome_version_ledger"


def _column_state(column: str):
    return lambda context: (
        OperationState.SATISFIED
        if context.has_column("recommendation_outcomes", column)
        else OperationState.PENDING
    )


def _add_column(column: str) -> callable:
    def apply(context: MigrationContext) -> None:
        context.require_ddl_preflight(DdlTarget(
            migration_version=VERSION,
            operation=DdlOperation.ADD_COLUMN,
            table="recommendation_outcomes",
            column=column,
            expected_data_types=("varchar", "text"),
            expected_nullable=True,
            expected_default=None,
            algorithm="INPLACE",
            lock="NONE",
        ))
        context.add_column(
            "recommendation_outcomes", column, "VARCHAR(80) NULL",
            mysql_algorithm="INPLACE", mysql_lock="NONE",
        )
    return apply


def _table_state(context: MigrationContext) -> OperationState:
    return OperationState.SATISFIED if context.has_table("recommendation_outcome_versions") else OperationState.PENDING


def _create_table(context: MigrationContext) -> None:
    context.create_table("recommendation_outcome_versions", (
        ("version_id", "VARCHAR(80) NOT NULL PRIMARY KEY"),
        ("recommendation_id", "VARCHAR(80) NOT NULL"),
        ("calculation_version", "VARCHAR(80) NOT NULL"),
        ("entry_policy_id", "VARCHAR(80) NOT NULL"),
        ("horizon_days", "INTEGER NOT NULL"),
        ("label_source", "VARCHAR(20) NOT NULL"),
        ("result_json", "LONGTEXT NOT NULL"),
        ("computed_at", "DATETIME NOT NULL"),
    ))


def _verify(context: MigrationContext) -> None:
    if not context.has_table("recommendation_outcome_versions"):
        raise RuntimeError("outcome version ledger is missing")
    for column in ("calculation_version_id", "entry_policy_id"):
        if not context.has_column_shape(
            "recommendation_outcomes", column,
            data_types=("varchar", "text"), nullable=True, default=None,
        ):
            raise RuntimeError(f"outcome version pointer is incomplete: {column}")


operations = [MigrationOperation(
    name="create_outcome_version_ledger",
    checksum_material="CREATE recommendation_outcome_versions append-only ledger",
    precondition=_table_state,
    apply=_create_table,
    postcondition=_table_state,
)]
for _column in ("calculation_version_id", "entry_policy_id"):
    operations.append(MigrationOperation(
        name=f"add_{_column}",
        checksum_material=f"ALTER recommendation_outcomes ADD {_column} VARCHAR(80) NULL",
        precondition=_column_state(_column),
        apply=_add_column(_column),
        postcondition=_column_state(_column),
    ))

migration = define_migration(
    version=VERSION,
    description="Version outcome calculations without destructive overwrite",
    operations=tuple(operations),
    verify=_verify,
)


def migrations():
    return (migration,)
