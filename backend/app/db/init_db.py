"""
数据库初始化 — v2 Schema。

迁移策略：
- Base.metadata.create_all 自动创建新表
- _migrate_v2 删除旧的 raw_* / unified_quotes / daily_bars / daily_indicators 表
- 保留 operational 表（recommendations 等）不动
"""

from sqlalchemy import inspect, text

from app.db.session import Base, engine
from app.models import tables  # noqa: F401


def init_db() -> None:
    _migrate_v2()
    Base.metadata.create_all(bind=engine)
    _ensure_indexes()


def _migrate_v2() -> None:
    """删除废弃的旧表（已迁移完毕，仅删除残留旧表）。"""
    tables_to_drop = [
        "raw_quotes",
        "raw_daily_bars",
        "raw_limit_up_board",
        "raw_concept_sector",
        "raw_capital_flow",
        "raw_dragon_tiger",
        "raw_news_announcement",
        "unified_quotes",
        "daily_bars",
        "daily_indicators",
    ]
    with engine.begin() as connection:
        inspector = inspect(connection)
        existing = set(inspector.get_table_names())
        for t in tables_to_drop:
            if t in existing:
                # 先删依赖旧表索引
                connection.execute(text(f"DROP TABLE IF EXISTS `{t}`"))


def _ensure_indexes() -> None:
    """创建新表的性能索引。"""
    with engine.begin() as connection:
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())

        if "daily_kbars" in existing_tables:
            _ensure_index(connection, inspector, "daily_kbars", "idx_kbars_td", "trade_date")
            _ensure_index(connection, inspector, "daily_kbars", "idx_kbars_code_td", "code, trade_date")
            _ensure_index(connection, inspector, "daily_kbars", "idx_kbars_td_ma5", "trade_date, ma5")

        if "stock_quotes" in existing_tables:
            _ensure_index(connection, inspector, "stock_quotes", "idx_quotes_td", "trade_date")
            _ensure_index(connection, inspector, "stock_quotes", "idx_quotes_status", "freshness_status, snapped_at")

        if "limit_up_board" in existing_tables:
            _ensure_index(connection, inspector, "limit_up_board", "idx_lub_td_limit", "trade_date, limit_type")
            _ensure_index(connection, inspector, "limit_up_board", "idx_lub_td_ltimes", "trade_date, limit_times")

        if "concept_sector" in existing_tables:
            _ensure_index(connection, inspector, "concept_sector", "idx_cs_td", "trade_date")

        if "concept_sector_stock" in existing_tables:
            _ensure_index(connection, inspector, "concept_sector_stock", "idx_css_code_sc", "code, sector_code")

        if "capital_flow" in existing_tables:
            _ensure_index(connection, inspector, "capital_flow", "idx_cf_td_main", "trade_date, main_net_inflow_yi")

        if "dragon_tiger" in existing_tables:
            _ensure_index(connection, inspector, "dragon_tiger", "idx_dt_td_net", "trade_date, net_amount_yi")

        if "news_announcement" in existing_tables:
            _ensure_index(connection, inspector, "news_announcement", "idx_na_code_date", "code, announce_date")

        if "margin_data" in existing_tables:
            _ensure_index(connection, inspector, "margin_data", "idx_margin_td", "trade_date")

        if "hk_holdings" in existing_tables:
            _ensure_index(connection, inspector, "hk_holdings", "idx_hk_td", "trade_date")

        if "holder_numbers" in existing_tables:
            _ensure_index(connection, inspector, "holder_numbers", "idx_hn_code_ed", "code, end_date")

        if "stk_limits" in existing_tables:
            _ensure_index(connection, inspector, "stk_limits", "idx_sl_code_td", "code, trade_date")

        if "minute_kbars" in existing_tables:
            _ensure_index(connection, inspector, "minute_kbars", "idx_mk_code_time", "code, trade_time")
            _ensure_index(connection, inspector, "minute_kbars", "idx_mk_td", "trade_date")

        if "auction_data" in existing_tables:
            _ensure_index(connection, inspector, "auction_data", "idx_auction_td", "trade_date")

        if "broker_recommends" in existing_tables:
            _ensure_index(connection, inspector, "broker_recommends", "idx_br_month", "month")

        if "holder_trades" in existing_tables:
            _ensure_index(connection, inspector, "holder_trades", "idx_ht_code_ann", "code, ann_date")

        if "institutional_surveys" in existing_tables:
            _ensure_index(connection, inspector, "institutional_surveys", "idx_is_code_sd", "code, surv_date")

        if "share_floats" in existing_tables:
            _ensure_index(connection, inspector, "share_floats", "idx_sf_code_fd", "code, float_date")

        if "pledge_stats" in existing_tables:
            _ensure_index(connection, inspector, "pledge_stats", "idx_ps_code_ed", "code, end_date")

        if "repurchases" in existing_tables:
            _ensure_index(connection, inspector, "repurchases", "idx_rep_code_ann", "code, ann_date")

        # 兼容旧表索引（如果用户之前没重建数据库）
        if "recommendation_runs" in existing_tables:
            run_cols = {c["name"] for c in inspector.get_columns("recommendation_runs")}
            _ensure_column(connection, run_cols, "recommendation_runs", "heartbeat_at", "DATETIME")
            _ensure_column(connection, run_cols, "recommendation_runs", "progress_phase", "VARCHAR(80)")
            _ensure_column(connection, run_cols, "recommendation_runs", "progress_current", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, run_cols, "recommendation_runs", "progress_total", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, run_cols, "recommendation_runs", "progress_message", "TEXT")
            _ensure_column(connection, run_cols, "recommendation_runs", "interrupted_at", "DATETIME")
            _ensure_column(connection, run_cols, "recommendation_runs", "checkpoint_phase", "VARCHAR(80)")

        if "scheduled_task_runs" in existing_tables:
            tr_cols = {c["name"] for c in inspector.get_columns("scheduled_task_runs")}
            _ensure_column(connection, tr_cols, "scheduled_task_runs", "summary", "TEXT")
            _ensure_column(connection, tr_cols, "scheduled_task_runs", "payload_json", "TEXT")
            _ensure_column(connection, tr_cols, "scheduled_task_runs", "result_json", "TEXT")

        if "presets" in existing_tables:
            p_cols = {c["name"] for c in inspector.get_columns("presets")}
            _ensure_column(connection, p_cols, "presets", "data_source", "VARCHAR(20) NOT NULL DEFAULT 'local_pool'")

        if "recommendation_outcomes" in existing_tables:
            o_cols = {c["name"] for c in inspector.get_columns("recommendation_outcomes")}
            for col, defn in [
                ("exit_date", "VARCHAR(10)"),
                ("exit_price", "FLOAT"),
                ("executed_return", "FLOAT NOT NULL DEFAULT 0"),
                ("weighted_return", "FLOAT NOT NULL DEFAULT 0"),
                ("position_size", "FLOAT NOT NULL DEFAULT 1"),
                ("holding_days", "INTEGER NOT NULL DEFAULT 0"),
                ("entry_slippage_bps", "FLOAT NOT NULL DEFAULT 0"),
                ("exit_slippage_bps", "FLOAT NOT NULL DEFAULT 0"),
                ("stop_loss_price", "FLOAT"),
                ("take_profit_price", "FLOAT"),
                ("exit_reason", "VARCHAR(40)"),
                ("achievable_return", "FLOAT NOT NULL DEFAULT 0"),
                ("outcome_label_legacy", "VARCHAR(30) NOT NULL DEFAULT 'pending'"),
            ]:
                _ensure_column(connection, o_cols, "recommendation_outcomes", col, defn)


def _ensure_index(connection, inspector, table: str, index_name: str, columns: str) -> None:
    existing = {idx["name"] for idx in inspector.get_indexes(table)}
    if index_name not in existing:
        connection.execute(text(f"CREATE INDEX {index_name} ON {table}({columns})"))


def _ensure_column(connection, existing_columns: set[str], table: str, column: str, definition: str) -> None:
    if column not in existing_columns:
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
        existing_columns.add(column)
