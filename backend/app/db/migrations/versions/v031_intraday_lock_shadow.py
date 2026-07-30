"""Intraday lock-profit shadow monitoring: monitor tasks + lock signals.

影子锁利链路(只记录不交易): 盘中轮询精选持仓的 5min 数据, 触及锁利目标记录信号,
盘后回填触发后走势并与实际出场对比, 量化"若当时锁利能多赚多少"。
"""
from __future__ import annotations

from ..contract import MigrationContext, MigrationOperation, OperationState, define_migration


VERSION = "V031_intraday_lock_shadow"
MONITOR_TABLE = "intraday_lock_monitors"
SIGNAL_TABLE = "intraday_lock_signals"


def _monitor_state(context: MigrationContext) -> OperationState:
    if not context.has_table(MONITOR_TABLE):
        return OperationState.PENDING
    required = (
        "monitor_id", "recommendation_id", "code", "trade_date", "entry_date",
        "entry_price", "target_pct", "lock_ratio", "status", "created_at", "updated_at",
    )
    return (
        OperationState.SATISFIED
        if all(context.has_column(MONITOR_TABLE, c) for c in required)
        else OperationState.PENDING
    )


def _signal_state(context: MigrationContext) -> OperationState:
    if not context.has_table(SIGNAL_TABLE):
        return OperationState.PENDING
    required = (
        "signal_id", "monitor_id", "code", "trade_date", "target_pct",
        "trigger_time", "trigger_price", "status", "created_at", "updated_at",
    )
    return (
        OperationState.SATISFIED
        if all(context.has_column(SIGNAL_TABLE, c) for c in required)
        else OperationState.PENDING
    )


def _apply_monitors(context: MigrationContext) -> None:
    context.create_table(
        MONITOR_TABLE,
        (
            ("monitor_id", "VARCHAR(80) NOT NULL"),
            ("recommendation_id", "VARCHAR(80) NOT NULL"),
            ("code", "VARCHAR(6) NOT NULL"),
            ("trade_date", "VARCHAR(10) NOT NULL"),
            ("entry_date", "VARCHAR(10) NOT NULL"),
            ("entry_price", "DOUBLE NOT NULL"),
            ("target_pct", "DOUBLE NOT NULL"),
            ("lock_ratio", "DOUBLE NOT NULL DEFAULT 0.5"),
            ("status", "VARCHAR(20) NOT NULL DEFAULT 'monitoring'"),
            ("triggered_at", "DATETIME(6) NULL"),
            ("created_at", "DATETIME(6) NOT NULL"),
            ("updated_at", "DATETIME(6) NOT NULL"),
        ),
        constraints=(
            "CONSTRAINT pk_intraday_lock_monitors PRIMARY KEY (monitor_id)",
            "KEY idx_ilm_status_date (status, trade_date)",
            "KEY idx_ilm_recommendation (recommendation_id)",
        ),
    )


def _apply_signals(context: MigrationContext) -> None:
    context.create_table(
        SIGNAL_TABLE,
        (
            ("signal_id", "VARCHAR(80) NOT NULL"),
            ("monitor_id", "VARCHAR(80) NOT NULL"),
            ("code", "VARCHAR(6) NOT NULL"),
            ("trade_date", "VARCHAR(10) NOT NULL"),
            ("target_pct", "DOUBLE NOT NULL"),
            ("lock_ratio", "DOUBLE NOT NULL DEFAULT 0.5"),
            ("trigger_time", "VARCHAR(40) NOT NULL"),
            ("trigger_price", "DOUBLE NOT NULL"),
            ("post_trigger_max_pct", "DOUBLE NULL"),
            ("post_trigger_min_pct", "DOUBLE NULL"),
            ("actual_exit_price", "DOUBLE NULL"),
            ("actual_exit_return", "DOUBLE NULL"),
            ("lock_delta_pct", "DOUBLE NULL"),
            ("status", "VARCHAR(20) NOT NULL DEFAULT 'open'"),
            ("created_at", "DATETIME(6) NOT NULL"),
            ("updated_at", "DATETIME(6) NOT NULL"),
        ),
        constraints=(
            "CONSTRAINT pk_intraday_lock_signals PRIMARY KEY (signal_id)",
            "KEY idx_ils_monitor (monitor_id)",
            "KEY idx_ils_date_status (trade_date, status)",
        ),
    )


def _verify(context: MigrationContext) -> None:
    if _monitor_state(context) is not OperationState.SATISFIED:
        raise RuntimeError("intraday_lock_monitors schema is incomplete")
    if _signal_state(context) is not OperationState.SATISFIED:
        raise RuntimeError("intraday_lock_signals schema is incomplete")
    if context.primary_key_columns(MONITOR_TABLE) != ("monitor_id",):
        raise RuntimeError("intraday_lock_monitors primary key is incompatible")
    if context.primary_key_columns(SIGNAL_TABLE) != ("signal_id",):
        raise RuntimeError("intraday_lock_signals primary key is incompatible")


migration = define_migration(
    version=VERSION,
    description="Intraday lock-profit shadow monitoring tables",
    operations=(
        MigrationOperation(
            name="create_intraday_lock_monitors",
            checksum_material=(
                "CREATE intraday_lock_monitors(monitor_id PK, recommendation_id, code, "
                "trade_date, entry, target_pct, lock_ratio, status, timestamps)"
            ),
            precondition=_monitor_state,
            apply=_apply_monitors,
            postcondition=_monitor_state,
        ),
        MigrationOperation(
            name="create_intraday_lock_signals",
            checksum_material=(
                "CREATE intraday_lock_signals(signal_id PK, monitor_id, code, trade_date, "
                "target_pct, trigger, post_trigger, actual_exit, lock_delta, status, timestamps)"
            ),
            precondition=_signal_state,
            apply=_apply_signals,
            postcondition=_signal_state,
        ),
    ),
    verify=_verify,
)


def migrations():
    return (migration,)
