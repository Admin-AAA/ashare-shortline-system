"""Add the nullable per-route temperature override through the safe DDL gate."""
from __future__ import annotations

from ..contract import MigrationContext, MigrationOperation, MigrationSafetyError, OperationState, define_migration
from ..preflight import DdlOperation, DdlTarget

_TABLE = "model_route_config"
_COLUMN = "temperature"
_DEFINITION = "FLOAT NULL DEFAULT NULL"
_DATA_TYPES = ("float", "double", "real")


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
        version="V004_add_model_route_temperature",
    )


def _apply(context: MigrationContext) -> None:
    context.require_ddl_preflight(
        DdlTarget(
            migration_version="V004_add_model_route_temperature",
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
    version="V004_add_model_route_temperature",
    description="Add nullable model route temperature override with online DDL preflight",
    operations=(
        MigrationOperation(
            name="add_model_route_temperature",
            checksum_material=(
                "ALTER model_route_config ADD COLUMN temperature FLOAT NULL DEFAULT NULL "
                "ALGORITHM=INSTANT LOCK=NONE; exact shape: float nullable default NULL"
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
