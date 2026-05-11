from sqlalchemy import inspect, text

from app.db.session import Base, engine
from app.models import tables  # noqa: F401


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_schema()


def _migrate_sqlite_schema() -> None:
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as connection:
        inspector = inspect(connection)
        table_names = set(inspector.get_table_names())
        if "presets" in table_names:
            preset_columns = {column["name"] for column in inspector.get_columns("presets")}
            if "data_source" not in preset_columns:
                connection.execute(
                    text("ALTER TABLE presets ADD COLUMN data_source VARCHAR(20) NOT NULL DEFAULT 'local_pool'")
                )

        if "recommendation_outcomes" in table_names:
            outcome_columns = {column["name"] for column in inspector.get_columns("recommendation_outcomes")}
            if "achievable_return" not in outcome_columns:
                connection.execute(
                    text("ALTER TABLE recommendation_outcomes ADD COLUMN achievable_return FLOAT NOT NULL DEFAULT 0")
                )
            if "outcome_label_legacy" not in outcome_columns:
                connection.execute(
                    text("ALTER TABLE recommendation_outcomes ADD COLUMN outcome_label_legacy VARCHAR(30) NOT NULL DEFAULT 'pending'")
                )

        # Performance indexes for local pool status queries
        _ensure_index(connection, inspector, "daily_bars", "idx_daily_bars_td_code", "trade_date, code")
        _ensure_index(connection, inspector, "daily_bars", "idx_daily_bars_td_ma5", "trade_date, ma5")
        _ensure_index(connection, inspector, "daily_indicators", "idx_daily_indicators_td", "trade_date")
        # Drop redundant single-column index that confuses the query planner
        _drop_index_if_exists(connection, inspector, "daily_bars", "ix_daily_bars_trade_date")


def _ensure_index(connection, inspector, table: str, index_name: str, columns: str) -> None:
    existing = {idx["name"] for idx in inspector.get_indexes(table)}
    if index_name not in existing:
        connection.execute(text(f"CREATE INDEX {index_name} ON {table}({columns})"))


def _drop_index_if_exists(connection, inspector, table: str, index_name: str) -> None:
    existing = {idx["name"] for idx in inspector.get_indexes(table)}
    if index_name in existing:
        connection.execute(text(f"DROP INDEX {index_name}"))
