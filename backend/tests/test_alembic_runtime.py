from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from app.db.alembic_runtime import get_alembic_health, upgrade_database


def test_upgrade_database_creates_schema_and_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")

    first = upgrade_database(engine)
    second = upgrade_database(engine)

    tables = set(inspect(engine).get_table_names())
    assert "stocks" in tables
    assert "recommendation_runs" in tables
    assert "alembic_version" in tables
    assert first.current_revision == second.current_revision
    assert first.current_revision == "0002_remove_legacy_ledger"
    assert "schema_migrations" not in tables
    assert get_alembic_health().as_dict() == {
        "status": "ok",
        "current_revision": "0002_remove_legacy_ledger",
        "head_revision": "0002_remove_legacy_ledger",
        "error_code": None,
    }


def test_upgrade_database_adopts_an_existing_demo_schema() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE demo_owned_table (id INTEGER PRIMARY KEY)"))

    result = upgrade_database(engine)

    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002_remove_legacy_ledger"
        assert connection.scalar(text("SELECT COUNT(*) FROM demo_owned_table")) == 0
    assert result.current_revision == "0002_remove_legacy_ledger"
