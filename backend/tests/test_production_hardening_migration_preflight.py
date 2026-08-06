from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text

from app.db.production_hardening_migration_plan import (
    CURRENT_ALEMBIC_HEAD,
    EVIDENCE_BATCH,
    MIGRATION_BATCHES,
    ColumnState,
    IndexState,
    OperationKind,
    OperationStatus,
    SchemaInventory,
    TableState,
    evaluate_operation,
    manifest_payload,
    validate_manifest,
)
from app.db.production_hardening_preflight import (
    LegacyRunFacts,
    assert_read_only_statement,
    classify_legacy_run,
    collect_classification_plan,
    collect_schema_inventory,
)


def _base_inventory() -> SchemaInventory:
    return SchemaInventory(
        tables={
            name: TableState(columns={}, indexes={})
            for name in (
                "recommendation_runs",
                "recommendation_outcomes",
                "recommendation_outcome_versions",
                "backtest_holdout_claims",
                "scheduled_tasks",
                "scheduled_task_runs",
                "api_call_logs",
            )
        }
    )


def _column_state(column) -> ColumnState:
    return ColumnState(
        type_family=column.type_family,
        nullable=column.nullable,
        length=column.length,
        precision=column.precision,
        scale=column.scale,
        fsp=column.fsp,
    )


def _simulate_applied(inventory: SchemaInventory, operation) -> SchemaInventory:
    tables = dict(inventory.tables)
    existing = tables.get(operation.table, TableState(columns={}, indexes={}))
    columns = dict(existing.columns)
    indexes = dict(existing.indexes)
    if operation.kind is OperationKind.CREATE_TABLE:
        columns = {column.name: _column_state(column) for column in operation.table_columns}
        indexes = {
            "PRIMARY": IndexState(
                columns=operation.table_primary_key,
                unique=True,
            )
        }
    elif operation.kind is OperationKind.ADD_COLUMN:
        assert operation.column is not None
        columns[operation.column.name] = _column_state(operation.column)
    elif operation.kind is OperationKind.CREATE_INDEX:
        assert operation.index is not None
        indexes[operation.index.name] = IndexState(
            columns=operation.index.columns,
            unique=operation.index.unique,
        )
    tables[operation.table] = TableState(columns=columns, indexes=indexes)
    return SchemaInventory(tables=tables)


def _planned_tables() -> frozenset[str]:
    return frozenset(
        operation.table
        for batch in MIGRATION_BATCHES
        for operation in batch.operations
        if operation.kind is OperationKind.CREATE_TABLE
    )


def test_manifest_is_linear_additive_and_checksum_bound() -> None:
    validate_manifest()
    payload = manifest_payload()

    assert payload["current_alembic_head"] == CURRENT_ALEMBIC_HEAD
    assert payload["migration_owner"] == "alembic"
    assert payload["execution_mode"] == "plan_and_read_only_preflight"
    assert payload["batch_order"] == [
        "M-EVIDENCE",
        "M-OUTCOME",
        "M-HOLDOUT",
        "M-RISK",
        "M-SCHEDULER",
        "M-LLM",
    ]
    assert {
        operation.kind
        for batch in MIGRATION_BATCHES
        for operation in batch.operations
    } <= {
        OperationKind.CREATE_TABLE,
        OperationKind.ADD_COLUMN,
        OperationKind.CREATE_INDEX,
    }
    assert EVIDENCE_BATCH.classification_mode == "read_only_plan_only"
    assert all(
        operation.contract()["precondition"]
        and operation.contract()["postcondition"]
        and operation.contract()["no_op_verify"]
        and operation.contract()["failure_reentry"]
        for batch in MIGRATION_BATCHES
        for operation in batch.operations
    )

    changed = replace(EVIDENCE_BATCH, purpose=EVIDENCE_BATCH.purpose + " changed")
    assert changed.checksum != EVIDENCE_BATCH.checksum
    assert manifest_payload()["manifest_checksum"] == payload["manifest_checksum"]


def test_every_operation_failure_boundary_is_reentrant_and_second_pass_is_noop() -> None:
    """**Validates: Requirements 9.1, 9.2**

    Across every possible operation boundary, completed objects remain no-ops,
    unfinished objects remain pending, and a complete retry becomes all no-op.
    """

    inventory = _base_inventory()
    planned_tables = _planned_tables()
    for batch in MIGRATION_BATCHES:
        starting_inventory = inventory
        for failure_after in range(len(batch.operations) + 1):
            partial = starting_inventory
            for operation in batch.operations[:failure_after]:
                partial = _simulate_applied(partial, operation)

            evaluations = [
                evaluate_operation(operation, partial, planned_tables=planned_tables)
                for operation in batch.operations
            ]
            assert all(
                evaluation.status is OperationStatus.SATISFIED_NO_OP
                for evaluation in evaluations[:failure_after]
            )
            assert all(
                evaluation.status is OperationStatus.PENDING
                for evaluation in evaluations[failure_after:]
            )

            retried = partial
            for operation in batch.operations[failure_after:]:
                retried = _simulate_applied(retried, operation)
            assert all(
                evaluate_operation(
                    operation,
                    retried,
                    planned_tables=planned_tables,
                ).status
                is OperationStatus.SATISFIED_NO_OP
                for operation in batch.operations
            )

        for operation in batch.operations:
            inventory = _simulate_applied(inventory, operation)


def test_existing_incompatible_column_blocks_instead_of_rewriting() -> None:
    operation = next(
        item
        for item in EVIDENCE_BATCH.operations
        if item.kind is OperationKind.ADD_COLUMN and item.column.name == "evidence_class"
    )
    inventory = _base_inventory()
    tables = dict(inventory.tables)
    tables["recommendation_runs"] = TableState(
        columns={
            "evidence_class": ColumnState(
                type_family="varchar",
                length=8,
                nullable=False,
            )
        },
        indexes={},
    )

    evaluation = evaluate_operation(
        operation,
        SchemaInventory(tables=tables),
        planned_tables=_planned_tables(),
    )

    assert evaluation.status is OperationStatus.BLOCKED
    assert "incompatible" in evaluation.reason


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT version_num FROM alembic_version",
        "SHOW VARIABLES LIKE 'version'",
        "WITH values_cte AS (SELECT 1 AS value) SELECT value FROM values_cte",
        "SET TRANSACTION READ ONLY",
    ],
)
def test_read_only_guard_accepts_only_preflight_statements(statement: str) -> None:
    assert_read_only_statement(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE recommendation_runs SET status = 'done'",
        "DELETE FROM recommendations",
        "ALTER TABLE recommendation_runs ADD COLUMN unsafe INT",
        "DROP TABLE recommendations",
        "TRUNCATE TABLE recommendation_outcomes",
        "SELECT 1; SELECT 2",
    ],
)
def test_read_only_guard_rejects_mutating_or_multi_statements(statement: str) -> None:
    with pytest.raises(ValueError, match="read-only|multiple"):
        assert_read_only_statement(statement)


def test_legacy_classification_is_conservative_and_never_promotes_live() -> None:
    examples = [
        (
            LegacyRunFacts(
                "research",
                "backtest",
                "live_forward",
                "anything",
                "2026-08-01",
                datetime(2026, 8, 1, 9),
            ),
            ("research_backtest", "legacy_research_run_mode"),
        ),
        (
            LegacyRunFacts(
                "explicit-history",
                "formal",
                "historical_replay",
                "anything",
                "2026-08-01",
                datetime(2026, 8, 1, 9),
            ),
            ("historical_reconstruction", "legacy_historical_scope"),
        ),
        (
            LegacyRunFacts(
                "disguised-replay",
                "formal",
                "live_forward",
                "REPLAY authorized",
                "2026-08-01",
                datetime(2026, 8, 1, 9),
            ),
            ("historical_reconstruction", "known_replay_source"),
        ),
        (
            LegacyRunFacts(
                "late",
                "formal",
                "live_forward",
                "formal",
                "2026-08-01",
                datetime(2026, 8, 2, 9),
            ),
            ("historical_reconstruction", "generated_after_signal_date"),
        ),
        (
            LegacyRunFacts(
                "same-day-legacy",
                "formal",
                "live_forward",
                "formal",
                "2026-08-01",
                datetime(2026, 8, 1, 9),
            ),
            ("unverified", "insufficient_immutable_provenance"),
        ),
    ]

    results = [classify_legacy_run(facts) for facts, _ in examples]

    assert results == [expected for _, expected in examples]
    assert all(evidence_class != "live_forward" for evidence_class, _ in results)


def test_classification_plan_reads_only_and_preserves_all_source_rows() -> None:
    """**Validates: Requirements 9.2, 9.3**"""

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE recommendation_runs (
                    run_id VARCHAR(60) PRIMARY KEY,
                    run_mode VARCHAR(20),
                    evaluation_scope VARCHAR(32),
                    run_name VARCHAR(120),
                    trade_date VARCHAR(10),
                    started_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE recommendations (
                    recommendation_id VARCHAR(80) PRIMARY KEY,
                    run_id VARCHAR(60)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE recommendation_outcomes (
                    recommendation_id VARCHAR(80) PRIMARY KEY
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO recommendation_runs
                    (run_id, run_mode, evaluation_scope, run_name, trade_date, started_at)
                VALUES
                    ('run-history', 'formal', 'historical_replay', 'replay', '2026-08-01', '2026-08-02 09:00:00'),
                    ('run-unknown', 'formal', 'live_forward', 'formal', '2026-08-02', '2026-08-02 09:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO recommendations (recommendation_id, run_id)
                VALUES ('rec-1', 'run-history'), ('rec-2', 'run-unknown')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO recommendation_outcomes (recommendation_id)
                VALUES ('rec-1'), ('rec-2')
                """
            )
        )

    with engine.connect() as connection:
        inventory = collect_schema_inventory(connection)
        before = {
            table: connection.scalar(text(f"SELECT COUNT(*) FROM {table}"))
            for table in (
                "recommendation_runs",
                "recommendations",
                "recommendation_outcomes",
            )
        }
        plan = collect_classification_plan(connection, inventory)
        after = {
            table: connection.scalar(text(f"SELECT COUNT(*) FROM {table}"))
            for table in before
        }

    assert before == after == {
        "recommendation_runs": 2,
        "recommendations": 2,
        "recommendation_outcomes": 2,
    }
    assert plan["status"] == "read_only_plan_generated"
    assert plan["write_authorized"] is False
    assert plan["write_operations_executed"] == 0
    assert plan["live_forward_assignments"] == 0
    assert plan["invariants"] == {
        "grouped_runs_match_total": True,
        "no_legacy_row_promoted_to_live": True,
        "source_rows_not_modified": True,
    }
    assert plan["totals"]["orphan_recommendations"] == 0
    assert plan["totals"]["orphan_outcomes"] == 0
