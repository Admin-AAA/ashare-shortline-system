"""索引契约收口：补齐 ORM 声明但库里缺失的索引，删除被更宽索引完全覆盖的冗余索引。

背景：生产库索引由多轮一次性脚本累积，与 ORM 声明长期不同步。结果是
`alembic upgrade head` 建出的新库缺少生产依赖的覆盖索引（例如
`daily_kbars (trade_date, ma5)`、`recommendations (run_id, final_score)`），
同时生产库堆着 41 个只被唯一键或更宽索引前缀完全覆盖的单列索引
（实测 minute_kbars 1011MB、stk_limits 709MB、daily_kbars 159MB 等）。

本修订只做两件事，全部是索引级变更，不动任何数据行：

1. 创建 ORM 已声明但生产库缺失的 9 个索引；
2. 删除列序列被另一个索引（唯一键或更宽索引）完全覆盖的 41 个非唯一索引。

删除前逐个复核覆盖者仍然存在；找不到覆盖者就跳过该索引，绝不让某个查询失去索引。
`recommendation_runs.ix_recommendation_runs_updated_at` 与
`recommendations.ix_recommendations_updated_at` 由修订 0028 建立并被
`app/services/data_version.py` 用 FORCE INDEX 指名使用，不在删除范围内。
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0032_index_contract_reconcile"
down_revision = "0031_share_float_ratio_index"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

# (表, 索引名, 列序列, 是否唯一)
CREATE_INDEXES = (
    ("data_source_attempts", "ix_data_source_attempts_outcome", ("outcome",), False),
    ("data_source_request_traces", "ix_data_source_request_traces_started_at", ("started_at",), False),
    ("intraday_lock_monitors", "ix_intraday_lock_monitors_trade_date", ("trade_date",), False),
    ("intraday_lock_signals", "ix_intraday_lock_signals_status", ("status",), False),
    ("recommendation_outcome_versions", "ix_recommendation_outcome_versions_computed_at", ("computed_at",), False),
    ("recommendation_outcomes", "ix_recommendation_outcomes_calculation_version_id", ("calculation_version_id",), False),
    ("recommendation_reconstruction_batches", "ix_recommendation_reconstruction_batches_window_start", ("window_start",), False),
    ("recommendation_reconstruction_batches", "ix_recommendation_reconstruction_batches_window_end", ("window_end",), False),
    ("recommendation_runs", "ix_recommendation_runs_data_as_of", ("data_as_of",), False),
)

# 每个待删索引都必须有覆盖者；覆盖者自身也在删除集合里时不允许删除。
DROP_INDEXES = (
    ("backtest_candidates", "idx_btc_backtest", ("backtest_id",)),
    ("backtest_failure_attributions", "idx_btfa_trade", ("trade_id",)),
    ("backtest_input_artifacts", "idx_bta_snapshot", ("snapshot_id",)),
    ("backtest_runs_v2", "idx_btrv2_request", ("request_fingerprint",)),
    ("backtest_trades", "idx_btt_backtest", ("backtest_id",)),
    ("broker_recommends", "ix_broker_recommends_code", ("code",)),
    ("broker_recommends", "ix_broker_recommends_month", ("month",)),
    ("capital_flow", "ix_capital_flow_trade_date", ("trade_date",)),
    ("concept_sector", "ix_concept_sector_sector_code", ("sector_code",)),
    ("concept_sector", "ix_concept_sector_trade_date", ("trade_date",)),
    ("concept_sector_stock", "ix_concept_sector_stock_code", ("code",)),
    ("concept_sector_stock", "ix_concept_sector_stock_sector_code", ("sector_code",)),
    ("daily_kbars", "ix_daily_kbars_trade_date", ("trade_date",)),
    ("dragon_tiger", "ix_dragon_tiger_code", ("code",)),
    ("dragon_tiger", "ix_dragon_tiger_trade_date", ("trade_date",)),
    ("hk_holdings", "idx_hk_td", ("trade_date",)),
    ("hk_holdings", "ix_hk_holdings_code", ("code",)),
    ("holder_numbers", "idx_hn_code_ed", ("code", "end_date")),
    ("holder_numbers", "ix_holder_numbers_code", ("code",)),
    ("holder_trades", "ix_holder_trades_code", ("code",)),
    ("hsgt_flows", "ix_hsgt_flows_trade_date", ("trade_date",)),
    ("hsgt_top10", "ix_hsgt_top10_code", ("code",)),
    ("index_daily", "ix_index_daily_ts_code", ("ts_code",)),
    ("institutional_surveys", "ix_institutional_surveys_code", ("code",)),
    ("limit_up_board", "ix_limit_up_board_trade_date", ("trade_date",)),
    ("margin_detail", "ix_margin_detail_code", ("code",)),
    ("minute_kbars", "ix_minute_kbars_trade_date", ("trade_date",)),
    ("model_route_config", "ix_model_route_config_enabled", ("enabled",)),
    ("news_announcement", "ix_news_announcement_code", ("code",)),
    ("pledge_stats", "idx_ps_code_ed", ("code", "end_date")),
    ("pledge_stats", "ix_pledge_stats_code", ("code",)),
    ("preset_skills", "ix_preset_skills_preset_id", ("preset_id",)),
    ("recommendations", "ix_recommendations_code", ("code",)),
    ("recommendations", "ix_recommendations_run_id", ("run_id",)),
    ("recommendations", "ix_recommendations_trade_date", ("trade_date",)),
    ("repurchases", "ix_repurchases_code", ("code",)),
    ("review_notes", "ix_review_notes_recommendation_id", ("recommendation_id",)),
    ("share_floats", "ix_share_floats_code", ("code",)),
    ("stk_limits", "idx_sl_code_td", ("code", "trade_date")),
    ("stk_limits", "ix_stk_limits_code", ("code",)),
    ("stock_fina_indicator", "ix_stock_fina_indicator_code", ("code",)),
    ("stock_metadata_history", "ix_stock_metadata_history_code", ("code",)),
    ("watch_items", "ix_watch_items_source_recommendation_id", ("source_recommendation_id",)),
)


def _index_columns(bind: sa.engine.Connection) -> dict[tuple[str, str], tuple[tuple[str, ...], bool]]:
    """库里全部二级索引：{(表, 索引名): (列序列, 是否唯一)}。"""
    rows = bind.execute(sa.text(
        "SELECT table_name, index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS cols, "
        "MAX(non_unique) AS non_unique FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND index_name <> 'PRIMARY' "
        "GROUP BY table_name, index_name"
    ))
    out: dict[tuple[str, str], tuple[tuple[str, ...], bool]] = {}
    for table_name, index_name, cols, non_unique in rows:
        out[(str(table_name), str(index_name))] = (tuple(str(cols).split(",")), int(non_unique) == 0)
    return out


def _covered_by(
    observed: dict[tuple[str, str], tuple[tuple[str, ...], bool]],
    table: str,
    name: str,
    columns: tuple[str, ...],
) -> str | None:
    """返回列序列以 columns 为前缀、且自身不在删除集合里的索引名。"""
    for (other_table, other_name), (other_cols, _unique) in observed.items():
        if other_table != table or other_name == name:
            continue
        if (table, other_name) in _DROP_PAIRS:
            continue
        if len(other_cols) >= len(columns) and other_cols[: len(columns)] == columns:
            return other_name
    return None


_DROP_PAIRS = frozenset((table, name) for table, name, _ in DROP_INDEXES)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.execute("SET SESSION lock_wait_timeout = 60")

    observed = _index_columns(bind)
    created = 0
    for table, name, columns, unique in CREATE_INDEXES:
        if (table, name) in observed:
            existing_cols, existing_unique = observed[(table, name)]
            if existing_cols != columns or existing_unique != unique:
                raise RuntimeError(
                    f"index {name} on {table} conflicts with revision 0032: "
                    f"columns={existing_cols}, unique={existing_unique}"
                )
            continue
        keyword = "UNIQUE INDEX" if unique else "INDEX"
        column_sql = ", ".join(f"`{column}`" for column in columns)
        op.execute(
            f"ALTER TABLE `{table}` ADD {keyword} `{name}` ({column_sql}), "
            "ALGORITHM=INPLACE, LOCK=NONE"
        )
        observed[(table, name)] = (columns, unique)
        created += 1
        log.info("created index %s.%s%s", table, name, columns)

    dropped = 0
    for table, name, columns in DROP_INDEXES:
        if (table, name) not in observed:
            continue
        existing_cols, existing_unique = observed[(table, name)]
        if existing_unique:
            raise RuntimeError(f"index {name} on {table} is unique; revision 0032 only drops redundant non-unique indexes")
        if existing_cols != columns:
            raise RuntimeError(
                f"index {name} on {table} has columns {existing_cols}, expected {columns}"
            )
        cover = _covered_by(observed, table, name, columns)
        if cover is None:
            log.warning("skipped dropping %s.%s: no surviving covering index", table, name)
            continue
        op.execute(f"ALTER TABLE `{table}` DROP INDEX `{name}`, ALGORITHM=INPLACE, LOCK=NONE")
        del observed[(table, name)]
        dropped += 1
        log.info("dropped redundant index %s.%s (covered by %s)", table, name, cover)

    print(f"revision 0032: created {created} indexes, dropped {dropped} redundant indexes")


def downgrade() -> None:
    raise RuntimeError("索引契约修订是前向修订；被删除的冗余索引不需要恢复")
