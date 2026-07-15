"""Focused contract coverage for strict online-DDL preflight and schema signatures."""
from __future__ import annotations

from sqlalchemy import create_engine, text

from app.db.migrations.contract import MigrationContext
from app.db.migrations.preflight import DdlOperation, DdlPreflight, DdlTarget, PreflightStatus


def _target() -> DdlTarget:
    return DdlTarget(
        migration_version="V003_add_model_route_max_tokens",
        operation=DdlOperation.ADD_COLUMN,
        table="model_route_config",
        column="max_tokens",
        expected_data_types=("int", "integer"),
        expected_nullable=True,
        expected_default=None,
    )


def test_column_signature_distinguishes_same_name_with_wrong_shape() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE model_route_config (max_tokens TEXT NOT NULL DEFAULT '0')"))
        context = MigrationContext(connection)

        signature = context.column_signature("model_route_config", "max_tokens")

        assert signature is not None
        assert signature.data_type == "text"
        assert not context.has_column_shape(
            "model_route_config",
            "max_tokens",
            data_types=("int", "integer"),
            nullable=True,
            default=None,
        )


def test_index_signature_preserves_column_order_and_uniqueness() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE signature_fixture (id INTEGER, code TEXT, trade_date TEXT)"))
        connection.execute(text("CREATE UNIQUE INDEX uq_signature_fixture ON signature_fixture (code, trade_date)"))
        context = MigrationContext(connection)

        signature = context.index_signature("signature_fixture", "uq_signature_fixture")

        assert signature is not None
        assert signature.columns == ("code", "trade_date")
        assert signature.unique is True
        assert context.has_index_shape(
            "signature_fixture",
            "uq_signature_fixture",
            columns=("code", "trade_date"),
            unique=True,
        )
        assert not context.has_index_shape(
            "signature_fixture",
            "uq_signature_fixture",
            columns=("trade_date", "code"),
            unique=True,
        )


def test_sqlite_preflight_is_safe_and_never_attempts_online_mysql_ddl() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        report = DdlPreflight(connection).run(_target())

    assert report.is_safe
    assert report.checks[0].status is PreflightStatus.NOT_APPLICABLE
    assert report.as_dict()["safe"] is True
