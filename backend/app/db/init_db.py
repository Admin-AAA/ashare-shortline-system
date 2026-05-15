from sqlalchemy import inspect, text

from app.db.session import Base, engine
from app.models import tables  # noqa: F401


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_schema()


def _migrate_schema() -> None:
    with engine.begin() as connection:
        inspector = inspect(connection)
        table_names = set(inspector.get_table_names())
        if "recommendation_runs" in table_names:
            run_columns = {column["name"] for column in inspector.get_columns("recommendation_runs")}
            _ensure_column(connection, run_columns, "recommendation_runs", "heartbeat_at", "DATETIME")
            _ensure_column(connection, run_columns, "recommendation_runs", "progress_phase", "VARCHAR(80)")
            _ensure_column(connection, run_columns, "recommendation_runs", "progress_current", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, run_columns, "recommendation_runs", "progress_total", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, run_columns, "recommendation_runs", "progress_message", "TEXT")
            _ensure_column(connection, run_columns, "recommendation_runs", "interrupted_at", "DATETIME")

        if engine.dialect.name != "sqlite":
            return

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
        if "daily_bars" in table_names:
            _ensure_index(connection, inspector, "daily_bars", "idx_daily_bars_td_code", "trade_date, code")
            _ensure_index(connection, inspector, "daily_bars", "idx_daily_bars_td_ma5", "trade_date, ma5")
            _drop_index_if_exists(connection, inspector, "daily_bars", "ix_daily_bars_trade_date")
        if "daily_indicators" in table_names:
            _ensure_index(connection, inspector, "daily_indicators", "idx_daily_indicators_td", "trade_date")
        # Drop redundant single-column index that confuses the query planner

        # Auxiliary data indexes
        if "limit_up_board" in table_names:
            _ensure_index(connection, inspector, "limit_up_board", "idx_lub_td_cdays", "trade_date, consecutive_days")
        if "concept_sector" in table_names:
            _ensure_index(connection, inspector, "concept_sector", "idx_cs_td_heat", "trade_date, heat_rank")
        if "capital_flow" in table_names:
            _ensure_index(connection, inspector, "capital_flow", "idx_cf_td_main", "trade_date, main_net_inflow_yi")
        if "dragon_tiger" in table_names:
            _ensure_index(connection, inspector, "dragon_tiger", "idx_dt_td_net", "trade_date, net_amount_yi")
        if "news_announcement" in table_names:
            _ensure_index(connection, inspector, "news_announcement", "idx_na_code_date", "code, announce_date")
        if "concept_sector_stock" in table_names:
            _ensure_index(connection, inspector, "concept_sector_stock", "idx_css_code", "code, sector_code")


def _ensure_index(connection, inspector, table: str, index_name: str, columns: str) -> None:
    existing = {idx["name"] for idx in inspector.get_indexes(table)}
    if index_name not in existing:
        connection.execute(text(f"CREATE INDEX {index_name} ON {table}({columns})"))


def _ensure_column(connection, existing_columns: set[str], table: str, column: str, definition: str) -> None:
    if column not in existing_columns:
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
        existing_columns.add(column)


def _drop_index_if_exists(connection, inspector, table: str, index_name: str) -> None:
    existing = {idx["name"] for idx in inspector.get_indexes(table)}
    if index_name in existing:
        connection.execute(text(f"DROP INDEX {index_name}"))
