"""Add the nullable per-route maximum-token override through the safe DDL gate."""
from __future__ import annotations

from ..contract import MigrationContext, MigrationOperation, MigrationSafetyError, OperationState, define_migration
from ..preflight import DdlOperation, DdlTarget

_TABLE = "model_route_config"
_COLUMN = "max_tokens"
_DEFINITION = "INTEGER NULL DEFAULT NULL"
_DATA_TYPES = ("int", "integer")


def _state(context: MigrationContext) -> OperationState:
    signature = context.column_signature(_TABLE, _COLUMN)
    if signature is None:
        return OperationState.PENDING
    if context.has_column_shape(
        _TABLE,
        _COLUMN,
        data_types=_DATA_TYPES,
        nullable=True,
        default=None,
    ):
        return OperationState.SATISFIED
    raise MigrationSafetyError(
        f"column shape mismatch for {_TABLE}.{_COLUMN}: {signature.type_expression}",
        version="V003_add_model_route_max_tokens",
    )


def _apply(context: MigrationContext) -> None:
    context.require_ddl_preflight(
        DdlTarget(
            migration_version="V003_add_model_route_max_tokens",
            operation=DdlOperation.ADD_COLUMN,
            table=_TABLE,
            column=_COLUMN,
            expected_data_types=_DATA_TYPES,
            expected_nullable=True,
            expected_default=None,
        )
    )
    context.add_column(_TABLE, _COLUMN, _DEFINITION, mysql_algorithm="INSTANT", mysql_lock="NONE")


migration = define_migration(
    version="V003_add_model_route_max_tokens",
    description="Add nullable model route max_tokens override with online DDL preflight",
    operations=(
        MigrationOperation(
            name="add_model_route_max_tokens",
            checksum_material=(
                "ALTER model_route_config ADD COLUMN max_tokens INTEGER NULL DEFAULT NULL "
                "ALGORITHM=INSTANT LOCK=NONE; exact shape: integer nullable default NULL"
            ),
            precondition=_state,
            apply=_apply,
            postcondition=_state,
        ),
    ),
    verify=lambda context: _state(context),
)


def migrations():
    return (migration,)
