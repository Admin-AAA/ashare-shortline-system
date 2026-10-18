from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
import pytest

from app.db.alembic_runtime import get_alembic_health, upgrade_database


HEAD_REVISION = "0021_trade_calendar"


def test_upgrade_database_creates_schema_and_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")

    first = upgrade_database(engine)
    second = upgrade_database(engine)

    tables = set(inspect(engine).get_table_names())
    assert "stocks" in tables
    assert "recommendation_runs" in tables
    assert "recommendation_reconstruction_batches" in tables
    assert "recommendation_collection_seals" in tables
    assert "alembic_version" in tables
    assert first.current_revision == second.current_revision
    assert first.current_revision == HEAD_REVISION
    assert "schema_migrations" not in tables
    minute_indexes = {
        index["name"]: index for index in inspect(engine).get_indexes("minute_kbars")
    }
    assert tuple(minute_indexes["idx_mk_td_code"]["column_names"]) == (
        "trade_date",
        "code",
    )
    assert not bool(minute_indexes["idx_mk_td_code"]["unique"])
    run_indexes = {
        index["name"]: index for index in inspect(engine).get_indexes("recommendation_runs")
    }
    assert bool(run_indexes["uq_rec_runs_reconstruction_batch_date"]["unique"])
    assert tuple(
        run_indexes["uq_rec_runs_reconstruction_batch_date"]["column_names"]
    ) == ("reconstruction_batch_id", "trade_date")
    seal_inspector = inspect(engine)
    assert tuple(
        seal_inspector.get_pk_constraint("recommendation_collection_seals")[
            "constrained_columns"
        ]
    ) == ("run_id",)
    seal_indexes = {
        index["name"]: index
        for index in seal_inspector.get_indexes("recommendation_collection_seals")
    }
    seal_key_index = seal_indexes["uq_rec_collection_seals_side_effect_key"]
    assert tuple(seal_key_index["column_names"]) == ("checkpoint_side_effect_key",)
    assert bool(seal_key_index["unique"])
    assert get_alembic_health().as_dict() == {
        "status": "ok",
        "current_revision": HEAD_REVISION,
        "head_revision": HEAD_REVISION,
        "error_code": None,
    }


def test_upgrade_database_adopts_an_existing_demo_schema() -> None:
    from app.db.session import Base
    from app.models.tables import RecommendationRun

    engine = create_engine("sqlite:///:memory:")
    assert RecommendationRun.__tablename__ == "recommendation_runs"
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE demo_owned_table (id INTEGER PRIMARY KEY)"))

    result = upgrade_database(engine)

    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            HEAD_REVISION
        )
        assert connection.scalar(text("SELECT COUNT(*) FROM demo_owned_table")) == 0
    assert result.current_revision == HEAD_REVISION


def test_upgrade_restores_missing_minute_composite_index_from_0011() -> None:
    engine = create_engine("sqlite:///:memory:")
    upgrade_database(engine, target_revision="0011_reconstruction_batches")
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX idx_mk_td_code"))

    result = upgrade_database(engine)

    indexes = {
        index["name"]: index for index in inspect(engine).get_indexes("minute_kbars")
    }
    assert tuple(indexes["idx_mk_td_code"]["column_names"]) == (
        "trade_date",
        "code",
    )
    assert not bool(indexes["idx_mk_td_code"]["unique"])
    assert result.current_revision == HEAD_REVISION


def test_upgrade_blocks_incompatible_minute_composite_index() -> None:
    engine = create_engine("sqlite:///:memory:")
    upgrade_database(engine, target_revision="0011_reconstruction_batches")
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX idx_mk_td_code"))
        connection.execute(
            text("CREATE INDEX idx_mk_td_code ON minute_kbars (code, trade_date)")
        )

    with pytest.raises(RuntimeError, match="conflicts with revision 0012"):
        upgrade_database(engine)

    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0011_reconstruction_batches"
        )


def test_upgrade_to_0013_widens_version_num_for_long_revision_id() -> None:
    """0013's revision id (36 chars) exceeds the default varchar(32) alembic_version.

    The migration must widen the column before Alembic records its own id; this
    holds on MySQL, where the length is enforced, and is a no-op on SQLite.
    """
    engine = create_engine("sqlite:///:memory:")
    upgrade_database(engine, target_revision="0012_minute_kbar_composite_index")
    with engine.connect() as connection:
        before = inspect(engine).get_columns("alembic_version")
        assert before[0]["name"] == "version_num"
        assert getattr(before[0]["type"], "length", None) == 32

    result = upgrade_database(engine, target_revision="0013_recommendation_collection_seals")

    assert result.current_revision == "0013_recommendation_collection_seals"
    assert len(result.current_revision) > 32
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0013_recommendation_collection_seals"
        )
