"""Forward-only schema for isolated P0 research backtests.

The tables in this migration are intentionally independent from legacy
``backtest_runs``, ``recommendations`` and ``recommendation_outcomes``.  They
store immutable source artifacts, concurrent request claims, attempts, and
experiment evidence without deleting or mutating production recommendation data.
"""
from __future__ import annotations

from collections.abc import Iterable

from ..contract import Migration, MigrationContext, MigrationOperation, OperationState, define_migration


_RESEARCH_SCHEMA: dict[str, tuple[tuple[tuple[str, str], ...], tuple[str, ...], tuple[tuple[str, tuple[str, ...], bool], ...]]] = {
    "backtest_data_snapshots": (
        (
            ("snapshot_id", "VARCHAR(80) PRIMARY KEY"),
            ("snapshot_version", "INTEGER NOT NULL"),
            ("manifest_hash", "CHAR(64) NOT NULL"),
            ("partition_manifest_json", "LONGTEXT NOT NULL"),
            ("coverage_json", "LONGTEXT NOT NULL"),
            ("missing_requirements_json", "LONGTEXT NOT NULL"),
            ("created_at", "DATETIME(6) NOT NULL"),
        ),
        ("CONSTRAINT uq_backtest_snapshot_manifest UNIQUE (manifest_hash)",),
        (("idx_bts_created", ("created_at",), False),),
    ),
    "backtest_input_artifacts": (
        (
            ("artifact_id", "VARCHAR(100) PRIMARY KEY"),
            ("snapshot_id", "VARCHAR(80) NOT NULL"),
            ("signal_date", "VARCHAR(10) NOT NULL"),
            ("artifact_scope", "VARCHAR(24) NOT NULL"),
            ("artifact_type", "VARCHAR(48) NOT NULL"),
            ("schema_version", "INTEGER NOT NULL"),
            ("row_count", "INTEGER NOT NULL"),
            ("content_hash", "CHAR(64) NOT NULL"),
            ("content_encoding", "VARCHAR(24) NOT NULL"),
            ("payload", "LONGBLOB NOT NULL"),
            ("created_at", "DATETIME(6) NOT NULL"),
        ),
        (
            "CONSTRAINT uq_backtest_artifact UNIQUE (snapshot_id, signal_date, artifact_scope, artifact_type)",
        ),
        (
            ("idx_bta_snapshot", ("snapshot_id",), False),
            ("idx_bta_signal_date", ("signal_date",), False),
        ),
    ),
    "backtest_request_claims": (
        (
            ("request_fingerprint", "CHAR(64) PRIMARY KEY"),
            ("active_backtest_id", "VARCHAR(80) NULL"),
            ("completed_backtest_id", "VARCHAR(80) NULL"),
            ("next_attempt_no", "INTEGER NOT NULL"),
            ("updated_at", "DATETIME(6) NOT NULL"),
        ),
        (),
        (
            ("idx_btrc_active", ("active_backtest_id",), False),
            ("idx_btrc_completed", ("completed_backtest_id",), False),
        ),
    ),
    "backtest_runs_v2": (
        (
            ("backtest_id", "VARCHAR(80) PRIMARY KEY"),
            ("experiment_id", "VARCHAR(80) NULL"),
            ("parent_backtest_id", "VARCHAR(80) NULL"),
            ("request_fingerprint", "CHAR(64) NOT NULL"),
            ("attempt_no", "INTEGER NOT NULL"),
            ("snapshot_id", "VARCHAR(80) NOT NULL"),
            ("strategy_id", "VARCHAR(80) NOT NULL"),
            ("strategy_version", "VARCHAR(160) NOT NULL"),
            ("request_schema_version", "INTEGER NOT NULL"),
            ("request_json", "LONGTEXT NOT NULL"),
            ("execution_version", "VARCHAR(128) NOT NULL"),
            ("execution_policy_fingerprint", "CHAR(64) NOT NULL"),
            ("signal_date", "VARCHAR(10) NULL"),
            ("date_from", "VARCHAR(10) NULL"),
            ("date_to", "VARCHAR(10) NULL"),
            ("status", "VARCHAR(40) NOT NULL"),
            ("result_json", "LONGTEXT NOT NULL"),
            ("started_at", "DATETIME(6) NULL"),
            ("finished_at", "DATETIME(6) NULL"),
            ("created_at", "DATETIME(6) NOT NULL"),
        ),
        ("CONSTRAINT uq_backtest_request_attempt UNIQUE (request_fingerprint, attempt_no)",),
        (
            ("idx_btrv2_experiment", ("experiment_id",), False),
            ("idx_btrv2_parent", ("parent_backtest_id",), False),
            ("idx_btrv2_request", ("request_fingerprint",), False),
            ("idx_btrv2_snapshot", ("snapshot_id",), False),
            ("idx_btrv2_strategy", ("strategy_id",), False),
            ("idx_btrv2_policy", ("execution_policy_fingerprint",), False),
            ("idx_btrv2_signal", ("signal_date",), False),
            ("idx_btrv2_status", ("status",), False),
            ("idx_btrv2_created", ("created_at",), False),
        ),
    ),
    "backtest_candidates": (
        (
            ("candidate_id", "VARCHAR(100) PRIMARY KEY"),
            ("backtest_id", "VARCHAR(80) NOT NULL"),
            ("signal_date", "VARCHAR(10) NOT NULL"),
            ("code", "VARCHAR(6) NOT NULL"),
            ("decision_status", "VARCHAR(32) NOT NULL"),
            ("signal_json", "LONGTEXT NULL"),
            ("audit_json", "LONGTEXT NOT NULL"),
            ("input_artifact_id", "VARCHAR(100) NOT NULL"),
        ),
        ("CONSTRAINT uq_backtest_candidate UNIQUE (backtest_id, signal_date, code)",),
        (
            ("idx_btc_backtest", ("backtest_id",), False),
            ("idx_btc_signal", ("signal_date",), False),
            ("idx_btc_code", ("code",), False),
            ("idx_btc_artifact", ("input_artifact_id",), False),
        ),
    ),
    "backtest_trades": (
        (
            ("trade_id", "VARCHAR(100) PRIMARY KEY"),
            ("backtest_id", "VARCHAR(80) NOT NULL"),
            ("signal_date", "VARCHAR(10) NOT NULL"),
            ("code", "VARCHAR(6) NOT NULL"),
            ("entry_date", "VARCHAR(10) NULL"),
            ("exit_date", "VARCHAR(10) NULL"),
            ("outcome_label", "VARCHAR(32) NOT NULL"),
            ("trade_json", "LONGTEXT NOT NULL"),
        ),
        ("CONSTRAINT uq_backtest_trade UNIQUE (backtest_id, signal_date, code)",),
        (
            ("idx_btt_backtest", ("backtest_id",), False),
            ("idx_btt_signal", ("signal_date",), False),
            ("idx_btt_code", ("code",), False),
        ),
    ),
    "backtest_failure_attributions": (
        (
            ("attribution_id", "VARCHAR(100) PRIMARY KEY"),
            ("backtest_id", "VARCHAR(80) NOT NULL"),
            ("trade_id", "VARCHAR(100) NOT NULL"),
            ("reason_code", "VARCHAR(48) NOT NULL"),
            ("rule_or_factor_reference", "VARCHAR(160) NULL"),
            ("evidence_references_json", "LONGTEXT NOT NULL"),
            ("confidence", "DOUBLE NOT NULL"),
            ("is_unknown", "BOOLEAN NOT NULL"),
            ("created_at", "DATETIME(6) NOT NULL"),
        ),
        ("CONSTRAINT uq_backtest_trade_attribution UNIQUE (trade_id, reason_code)",),
        (
            ("idx_btfa_backtest", ("backtest_id",), False),
            ("idx_btfa_trade", ("trade_id",), False),
            ("idx_btfa_reason", ("reason_code",), False),
        ),
    ),
    "backtest_experiments": (
        (
            ("experiment_id", "VARCHAR(80) PRIMARY KEY"),
            ("hypothesis", "TEXT NOT NULL"),
            ("baseline_backtest_id", "VARCHAR(80) NOT NULL"),
            ("snapshot_id", "VARCHAR(80) NOT NULL"),
            ("execution_policy_fingerprint", "CHAR(64) NOT NULL"),
            ("split_hash", "CHAR(64) NOT NULL"),
            ("split_json", "LONGTEXT NOT NULL"),
            ("candidate_strategy_id", "VARCHAR(80) NOT NULL"),
            ("candidate_strategy_version", "VARCHAR(160) NOT NULL"),
            ("frozen_candidate_fingerprint", "CHAR(64) NOT NULL"),
            ("acceptance_json", "LONGTEXT NOT NULL"),
            ("status", "VARCHAR(32) NOT NULL"),
            ("conclusion_json", "LONGTEXT NULL"),
            ("created_at", "DATETIME(6) NOT NULL"),
            ("frozen_at", "DATETIME(6) NULL"),
            ("finished_at", "DATETIME(6) NULL"),
        ),
        (),
        (
            ("idx_bte_baseline", ("baseline_backtest_id",), False),
            ("idx_bte_snapshot", ("snapshot_id",), False),
            ("idx_bte_policy", ("execution_policy_fingerprint",), False),
            ("idx_bte_candidate", ("candidate_strategy_id",), False),
            ("idx_bte_status", ("status",), False),
        ),
    ),
}


def _required_columns(table: str) -> tuple[str, ...]:
    return tuple(column for column, _definition in _RESEARCH_SCHEMA[table][0])


def _required_indexes(table: str) -> tuple[str, ...]:
    return tuple(name for name, _columns, _unique in _RESEARCH_SCHEMA[table][2])


def _table_state(context: MigrationContext, table: str) -> OperationState:
    if not context.has_table(table):
        return OperationState.PENDING
    columns = _required_columns(table)
    indexes = _required_indexes(table)
    if all(context.has_column(table, column) for column in columns) and all(
        context.has_index(table, index) for index in indexes
    ):
        return OperationState.SATISFIED
    return OperationState.PENDING


def _apply_table(context: MigrationContext, table: str) -> None:
    columns, constraints, indexes = _RESEARCH_SCHEMA[table]
    context.create_table(table, columns, constraints=constraints)
    for index_name, index_columns, unique in indexes:
        context.create_index(table, index_name, index_columns, unique=unique)


def _operation(table: str) -> MigrationOperation:
    columns, constraints, indexes = _RESEARCH_SCHEMA[table]
    checksum_material = {
        "table": table,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "purpose": "isolated immutable P0 research-backtest persistence; no legacy recommendation table mutation",
    }
    return MigrationOperation(
        name=f"create_{table}",
        checksum_material=repr(checksum_material),
        precondition=lambda context, table=table: _table_state(context, table),
        apply=lambda context, table=table: _apply_table(context, table),
        postcondition=lambda context, table=table: _table_state(context, table),
    )


def _verify(context: MigrationContext) -> None:
    incomplete = [table for table in _RESEARCH_SCHEMA if _table_state(context, table) is not OperationState.SATISFIED]
    if incomplete:
        raise RuntimeError(f"P0 research schema is incomplete: {', '.join(incomplete)}")


migration = define_migration(
    version="V001_p0_research",
    description="Create isolated immutable P0 research backtest and experiment tables",
    operations=tuple(_operation(table) for table in _RESEARCH_SCHEMA),
    verify=_verify,
)


def migrations() -> tuple[Migration, ...]:
    """Return the immutable P0 research schema version."""
    return (migration,)
