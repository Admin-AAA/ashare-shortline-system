"""Persist latest realtime execution decision and first buy confirmation."""
from __future__ import annotations

from ..contract import MigrationContext, MigrationOperation, OperationState, define_migration


VERSION = "V026_recommendation_execution_states"
TABLE = "recommendation_execution_states"


def _state(context: MigrationContext) -> OperationState:
    if not context.has_table(TABLE):
        return OperationState.PENDING
    required = (
        "recommendation_id",
        "latest_decision_code",
        "latest_checked_at",
        "latest_condition_json",
        "ever_buy_confirmed",
        "first_buy_confirmed_at",
        "created_at",
        "updated_at",
    )
    return (
        OperationState.SATISFIED
        if all(context.has_column(TABLE, column) for column in required)
        else OperationState.PENDING
    )


def _apply(context: MigrationContext) -> None:
    context.create_table(
        TABLE,
        (
            ("recommendation_id", "VARCHAR(80) NOT NULL"),
            ("latest_decision_code", "VARCHAR(20) NOT NULL"),
            ("latest_decision_label", "VARCHAR(80) NOT NULL DEFAULT ''"),
            ("latest_reason", "TEXT NULL"),
            ("latest_checked_at", "DATETIME(6) NOT NULL"),
            ("latest_quote_time", "VARCHAR(40) NULL"),
            ("latest_data_as_of", "VARCHAR(10) NULL"),
            ("latest_price", "DOUBLE NULL"),
            ("latest_strategy_id", "VARCHAR(80) NULL"),
            ("latest_setup_match", "VARCHAR(120) NULL"),
            ("latest_condition_json", "LONGTEXT NOT NULL"),
            ("ever_buy_confirmed", "BOOLEAN NOT NULL DEFAULT 0"),
            ("first_buy_confirmed_at", "DATETIME(6) NULL"),
            ("first_buy_confirmed_price", "DOUBLE NULL"),
            ("created_at", "DATETIME(6) NOT NULL"),
            ("updated_at", "DATETIME(6) NOT NULL"),
        ),
        constraints=("CONSTRAINT pk_recommendation_execution_states PRIMARY KEY (recommendation_id)",),
    )


def _verify(context: MigrationContext) -> None:
    if _state(context) is not OperationState.SATISFIED:
        raise RuntimeError("recommendation execution state schema is incomplete")
    if context.primary_key_columns(TABLE) != ("recommendation_id",):
        raise RuntimeError("recommendation execution state primary key is incompatible")


migration = define_migration(
    version=VERSION,
    description="Persist recommendation execution confirmation state",
    operations=(
        MigrationOperation(
            name="create_recommendation_execution_states",
            checksum_material=(
                "CREATE recommendation_execution_states(recommendation_id PK, latest decision, "
                "latest condition, ever buy, first buy, timestamps)"
            ),
            precondition=_state,
            apply=_apply,
            postcondition=_state,
        ),
    ),
    verify=_verify,
)


def migrations():
    return (migration,)
