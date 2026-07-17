"""Create immutable, self-contained strategy artifact snapshots."""
from __future__ import annotations

from ..contract import MigrationContext, MigrationOperation, OperationState, define_migration


VERSION = "V025_strategy_version_snapshots"
TABLE = "strategy_version_snapshots"


def _state(context: MigrationContext) -> OperationState:
    if not context.has_table(TABLE):
        return OperationState.PENDING
    required = ("version_id", "strategy_id", "snapshot_json", "content_hash", "created_at")
    if all(context.has_column(TABLE, column) for column in required):
        return OperationState.SATISFIED
    return OperationState.PENDING


def _apply(context: MigrationContext) -> None:
    context.create_table(
        TABLE,
        (
            ("version_id", "VARCHAR(100) NOT NULL"),
            ("strategy_id", "VARCHAR(80) NOT NULL"),
            ("snapshot_json", "LONGTEXT NOT NULL"),
            ("content_hash", "CHAR(64) NOT NULL"),
            ("created_at", "DATETIME(6) NOT NULL"),
        ),
        constraints=("CONSTRAINT pk_strategy_version_snapshots PRIMARY KEY (version_id)",),
    )
    context.create_index(
        TABLE,
        "idx_strategy_version_snapshots_strategy_created",
        ("strategy_id", "created_at"),
        unique=False,
    )


def _verify(context: MigrationContext) -> None:
    if _state(context) is not OperationState.SATISFIED:
        raise RuntimeError("strategy version snapshot schema is incomplete")
    if context.primary_key_columns(TABLE) != ("version_id",):
        raise RuntimeError("strategy version snapshot primary key is incompatible")
    if not context.has_index_shape(
        TABLE,
        "idx_strategy_version_snapshots_strategy_created",
        columns=("strategy_id", "created_at"),
        unique=False,
    ):
        raise RuntimeError("strategy version snapshot index is missing or incompatible")


migration = define_migration(
    version=VERSION,
    description="Create immutable strategy version artifact snapshots",
    operations=(
        MigrationOperation(
            name="create_strategy_version_snapshots",
            checksum_material=(
                "CREATE strategy_version_snapshots(version_id PK, strategy_id, snapshot_json, "
                "content_hash, created_at); index(strategy_id, created_at)"
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
