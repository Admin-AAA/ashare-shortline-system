from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from app.db.alembic_runtime import upgrade_database
from app.db.production_hardening_migration_plan import EVIDENCE_BATCH, OperationKind


_RUN_PROVENANCE_COLUMNS = tuple(
    operation.column.name
    for operation in EVIDENCE_BATCH.operations
    if operation.kind is OperationKind.ADD_COLUMN and operation.column is not None
)


def _legacy_engine(*, evidence_class_sql: str = "evidence_class VARCHAR(40) NULL"):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE recommendation_runs (
                    run_id VARCHAR(60) PRIMARY KEY,
                    evaluation_scope VARCHAR(32) NOT NULL,
                    trade_date VARCHAR(10) NOT NULL,
                    started_at DATETIME NOT NULL,
                    {evidence_class_sql}
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE recommendations (
                    recommendation_id VARCHAR(80) PRIMARY KEY,
                    run_id VARCHAR(60) NOT NULL,
                    recommended_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO recommendation_runs
                    (run_id, evaluation_scope, trade_date, started_at, evidence_class)
                VALUES
                    ('legacy-run', 'historical_replay', '2026-07-31',
                     '2026-08-01 09:30:00.123456', NULL)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO recommendations
                    (recommendation_id, run_id, recommended_at)
                VALUES
                    ('legacy-rec', 'legacy-run', '2026-07-31 16:00:00.654321')
                """
            )
        )
    return engine


def _legacy_facts(engine) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    with engine.connect() as connection:
        runs = list(
            connection.execute(
                text(
                    """
                    SELECT run_id, evaluation_scope, trade_date, started_at
                    FROM recommendation_runs
                    ORDER BY run_id
                    """
                )
            ).tuples()
        )
        recommendations = list(
            connection.execute(
                text(
                    """
                    SELECT recommendation_id, run_id, recommended_at
                    FROM recommendations
                    ORDER BY recommendation_id
                    """
                )
            ).tuples()
        )
    return runs, recommendations


def _schema_signature(engine) -> dict[str, object]:
    inspector = inspect(engine)
    tables = {
        "recommendation_runs",
        "recommendation_evidence_classification_audits",
    }
    return {
        table: {
            "columns": tuple(
                (column["name"], str(column["type"]), bool(column["nullable"]))
                for column in inspector.get_columns(table)
            ),
            "indexes": tuple(
                sorted(
                    (
                        index["name"],
                        tuple(index.get("column_names") or ()),
                        bool(index.get("unique", False)),
                    )
                    for index in inspector.get_indexes(table)
                )
            ),
            "primary_key": tuple(
                inspector.get_pk_constraint(table).get("constrained_columns") or ()
            ),
        }
        for table in tables
    }


def test_evidence_migration_preserves_legacy_rows_and_second_upgrade_is_noop() -> None:
    """**Validates: Requirements 1.2, 1.3, 1.7, 1.8, 9.1, 9.2**

    A partially compatible legacy schema represents a retry boundary: the
    existing nullable evidence_class must be reused, every remaining object must
    be added, and neither legacy scope nor either historical timestamp may move.
    """

    engine = _legacy_engine()
    before_facts = _legacy_facts(engine)

    first = upgrade_database(engine, target_revision="0004_evidence_provenance")
    after_first_signature = _schema_signature(engine)
    second = upgrade_database(engine, target_revision="0004_evidence_provenance")
    after_second_signature = _schema_signature(engine)

    assert first.current_revision == second.current_revision == "0004_evidence_provenance"
    assert first.head_revision == second.head_revision == "0004_evidence_provenance"
    assert first.adopted_existing_schema is True
    assert second.adopted_existing_schema is False
    assert after_first_signature == after_second_signature
    assert _legacy_facts(engine) == before_facts

    inspector = inspect(engine)
    run_columns = {column["name"]: column for column in inspector.get_columns("recommendation_runs")}
    assert set(_RUN_PROVENANCE_COLUMNS) <= set(run_columns)
    assert all(run_columns[name]["nullable"] for name in _RUN_PROVENANCE_COLUMNS)

    run_indexes = {
        index["name"]: tuple(index.get("column_names") or ())
        for index in inspector.get_indexes("recommendation_runs")
    }
    assert run_indexes["ix_rec_runs_evidence_trade_generated"] == (
        "evidence_class",
        "trade_date",
        "actual_generated_at",
    )
    assert run_indexes["ix_rec_runs_reconstruction_batch"] == (
        "reconstruction_batch_id",
    )
    assert run_indexes["ix_rec_runs_evidence_identity_hash"] == (
        "evidence_identity_hash",
    )

    audit_table = "recommendation_evidence_classification_audits"
    audit_columns = {column["name"] for column in inspector.get_columns(audit_table)}
    create_table = next(
        operation
        for operation in EVIDENCE_BATCH.operations
        if operation.kind is OperationKind.CREATE_TABLE
    )
    assert audit_columns == {column.name for column in create_table.table_columns}
    assert tuple(inspector.get_pk_constraint(audit_table)["constrained_columns"]) == (
        "plan_hash",
        "run_id",
    )

    with engine.begin() as connection:
        null_projection = connection.execute(
            text(
                "SELECT "
                + ", ".join(_RUN_PROVENANCE_COLUMNS)
                + " FROM recommendation_runs WHERE run_id = 'legacy-run'"
            )
        ).one()
        assert all(value is None for value in null_projection)
        connection.execute(
            text(
                """
                INSERT INTO recommendation_evidence_classification_audits (
                    plan_hash,
                    run_id,
                    planned_evidence_class,
                    classification_reason,
                    legacy_evaluation_scope,
                    legacy_started_at,
                    legacy_trade_date,
                    legacy_recommended_at_min,
                    legacy_recommended_at_max,
                    planned_at
                ) VALUES (
                    :plan_hash,
                    'legacy-run',
                    'historical_reconstruction',
                    'legacy_historical_scope',
                    'historical_replay',
                    '2026-08-01 09:30:00.123456',
                    '2026-07-31',
                    '2026-07-31 16:00:00.654321',
                    '2026-07-31 16:00:00.654321',
                    :planned_at
                )
                """
            ),
            {
                "plan_hash": "a" * 64,
                "planned_at": datetime(2026, 8, 3, 12, 0, 0),
            },
        )
        audit_counts = connection.execute(
            text(
                """
                SELECT recommendation_count, outcome_count
                FROM recommendation_evidence_classification_audits
                WHERE plan_hash = :plan_hash AND run_id = 'legacy-run'
                """
            ),
            {"plan_hash": "a" * 64},
        ).one()
        assert tuple(audit_counts) == (0, 0)


def test_evidence_migration_blocks_incompatible_existing_schema_before_ddl() -> None:
    """**Validates: Requirements 9.1, 9.2**"""

    engine = _legacy_engine(evidence_class_sql="evidence_class VARCHAR(8) NULL")
    before_facts = _legacy_facts(engine)

    with pytest.raises(RuntimeError, match="evidence_class.*incompatible"):
        upgrade_database(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("recommendation_runs")}
    assert "actual_generated_at" not in columns
    assert _legacy_facts(engine) == before_facts
