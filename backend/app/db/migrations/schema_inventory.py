"""Auditable inventory of the schema surface formerly owned by ``init_db``.

This module is deliberately data-only.  It is the source of truth for the
legacy ``_ensure_column``, ``_ensure_column_type``, ``_ensure_index`` and
``_migrate_minute_kbars`` surface.  A target version is recorded for every
item, including retired objects that must never be dropped by the runner.

The old ``_migrate_v2`` table-drop list is kept separately as a forbidden
inventory.  It is documentation/evidence only and is not an executable
migration operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


LEGACY_BASELINE_VERSION = "V000"


@dataclass(frozen=True)
class SchemaInventoryItem:
    """One exact object or bounded data action from the old init path."""

    helper: str
    target_version: str
    table: str
    name: str
    kind: str
    definition: str | None = None
    columns: tuple[str, ...] = ()
    natural_key: tuple[str, ...] = ()
    notes: str = ""
    destructive: bool = False

    def __post_init__(self) -> None:
        if self.kind not in {"column", "column_type", "column_default", "index", "unique", "data_backfill"}:
            raise ValueError(f"unsupported schema inventory kind: {self.kind}")
        if not self.helper or not self.target_version or not self.table or not self.name:
            raise ValueError("schema inventory items require helper, version, table and name")
        if self.kind in {"index", "unique"} and not self.columns:
            raise ValueError(f"{self.kind} inventory item requires ordered columns: {self.table}.{self.name}")
        if self.kind == "data_backfill" and self.destructive:
            raise ValueError("destructive data backfills are not allowed in the inventory")



def _column(helper: str, table: str, name: str, definition: str, *, notes: str = "") -> SchemaInventoryItem:
    return SchemaInventoryItem(helper, LEGACY_BASELINE_VERSION, table, name, "column", definition=definition, notes=notes)



def _column_type(table: str, name: str, definition: str, *, notes: str = "") -> SchemaInventoryItem:
    return SchemaInventoryItem(
        "_ensure_column_type", LEGACY_BASELINE_VERSION, table, name, "column_type", definition=definition, notes=notes
    )



def _column_default(table: str, name: str, default: str, *, notes: str = "") -> SchemaInventoryItem:
    return SchemaInventoryItem(
        "_ensure_column_default", LEGACY_BASELINE_VERSION, table, name, "column_default", definition=default, notes=notes
    )



def _index(table: str, name: str, *columns: str, unique: bool = False, notes: str = "") -> SchemaInventoryItem:
    return SchemaInventoryItem(
        "_ensure_index",
        LEGACY_BASELINE_VERSION,
        table,
        name,
        "unique" if unique else "index",
        columns=tuple(columns),
        natural_key=tuple(columns) if unique else (),
        notes=notes,
    )


# Columns that V000 must observe even though the old helper did not add them
# (primary/business columns are the context required to adopt the old helper
# surface safely).
_BASELINE_CORE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "daily_kbars": ("code", "trade_date", "ma5"),
    "stock_quotes": ("trade_date", "freshness_status", "snapped_at"),
    "limit_up_board": ("trade_date", "limit_type", "limit_times"),
    "concept_sector": ("trade_date",),
    "concept_sector_stock": ("code", "sector_code"),
    "capital_flow": ("trade_date", "main_net_inflow_yi"),
    "dragon_tiger": ("trade_date", "net_amount_yi"),
    "news_announcement": ("code", "announce_date"),
    "model_route_config": ("enabled", "priority"),
    "margin_data": ("trade_date",),
    "hk_holdings": ("trade_date",),
    "holder_numbers": ("code", "end_date"),
    "stk_limits": ("code", "trade_date"),
    "minute_kbars": ("code", "freq", "trade_date", "trade_time"),
    "auction_data": ("trade_date",),
    "broker_recommends": ("month",),
    "holder_trades": ("code", "ann_date"),
    "institutional_surveys": ("code", "surv_date"),
    "share_floats": ("code", "float_date"),
    "pledge_stats": ("code", "end_date"),
    "repurchases": ("code", "ann_date"),
    "recommendation_runs": (
        "run_mode", "trade_date", "status", "started_at", "score_line",
        "heartbeat_at", "progress_phase", "progress_current", "progress_total",
        "progress_message", "interrupted_at", "checkpoint_phase", "filter_audit_json",
    ),
    "scheduled_task_runs": ("summary", "payload_json", "result_json"),
    "presets": ("run_mode", "score_line", "data_source", "skip_aux_refresh"),
    "recommendations": ("run_id", "trade_date", "final_score", "feature_snapshot_json"),
    "api_call_logs": ("prompt_tokens", "completion_tokens", "cache_hit_tokens"),
    "recommendation_outcomes": (
        "entry_triggered", "outcome_label", "exit_date", "exit_price", "executed_return",
        "weighted_return", "position_size", "holding_days", "entry_slippage_bps",
        "exit_slippage_bps", "stop_loss_price", "take_profit_price", "exit_reason",
        "achievable_return", "outcome_label_legacy",
    ),
}


def _baseline_core_items() -> tuple[SchemaInventoryItem, ...]:
    existing = {(item.table, item.name) for item in _LEGACY_HELPER_INVENTORY}
    return tuple(
        SchemaInventoryItem(
            "V000_baseline_inventory",
            LEGACY_BASELINE_VERSION,
            table,
            column,
            "column",
            notes="Required existing business column for safe baseline adoption; not created by V000.",
        )
        for table, columns in _BASELINE_CORE_COLUMNS.items()
        for column in columns
        if (table, column) not in existing
    )


# This is intentionally explicit rather than inferred from ORM metadata.  It
# records exactly what the old helper calls attempted to add or alter.
_LEGACY_HELPER_INVENTORY: tuple[SchemaInventoryItem, ...] = (
    # _ensure_column calls
    _column("_ensure_column", "recommendation_runs", "score_line", "FLOAT NOT NULL DEFAULT 72"),
    _column("_ensure_column", "recommendation_runs", "heartbeat_at", "DATETIME"),
    _column("_ensure_column", "recommendation_runs", "progress_phase", "VARCHAR(80)"),
    _column("_ensure_column", "recommendation_runs", "progress_current", "INTEGER NOT NULL DEFAULT 0"),
    _column("_ensure_column", "recommendation_runs", "progress_total", "INTEGER NOT NULL DEFAULT 0"),
    _column("_ensure_column", "recommendation_runs", "progress_message", "TEXT"),
    _column("_ensure_column", "recommendation_runs", "interrupted_at", "DATETIME"),
    _column("_ensure_column", "recommendation_runs", "checkpoint_phase", "VARCHAR(80)"),
    _column("_ensure_column", "recommendation_runs", "filter_audit_json", "LONGTEXT"),
    _column("_ensure_column", "scheduled_task_runs", "summary", "TEXT"),
    _column("_ensure_column", "scheduled_task_runs", "payload_json", "TEXT"),
    _column("_ensure_column", "scheduled_task_runs", "result_json", "TEXT"),
    _column("_ensure_column", "presets", "score_line", "FLOAT NOT NULL DEFAULT 72"),
    _column(
        "_ensure_column",
        "presets",
        "data_source",
        "VARCHAR(20) NOT NULL DEFAULT 'local_pool_preferred'",
        notes="The old path also attempted a default-only repair; see column_default item.",
    ),
    _column("_ensure_column", "presets", "skip_aux_refresh", "BOOLEAN NOT NULL DEFAULT 0"),
    _column("_ensure_column", "api_call_logs", "prompt_tokens", "INTEGER"),
    _column("_ensure_column", "api_call_logs", "completion_tokens", "INTEGER"),
    _column("_ensure_column", "api_call_logs", "cache_hit_tokens", "INTEGER"),
    _column("_ensure_column", "recommendation_outcomes", "exit_date", "VARCHAR(10)"),
    _column("_ensure_column", "recommendation_outcomes", "exit_price", "FLOAT"),
    _column("_ensure_column", "recommendation_outcomes", "executed_return", "FLOAT NOT NULL DEFAULT 0"),
    _column("_ensure_column", "recommendation_outcomes", "weighted_return", "FLOAT NOT NULL DEFAULT 0"),
    _column("_ensure_column", "recommendation_outcomes", "position_size", "FLOAT NOT NULL DEFAULT 1"),
    _column("_ensure_column", "recommendation_outcomes", "holding_days", "INTEGER NOT NULL DEFAULT 0"),
    _column("_ensure_column", "recommendation_outcomes", "entry_slippage_bps", "FLOAT NOT NULL DEFAULT 0"),
    _column("_ensure_column", "recommendation_outcomes", "exit_slippage_bps", "FLOAT NOT NULL DEFAULT 0"),
    _column("_ensure_column", "recommendation_outcomes", "stop_loss_price", "FLOAT"),
    _column("_ensure_column", "recommendation_outcomes", "take_profit_price", "FLOAT"),
    _column("_ensure_column", "recommendation_outcomes", "exit_reason", "VARCHAR(40)"),
    _column("_ensure_column", "recommendation_outcomes", "achievable_return", "FLOAT NOT NULL DEFAULT 0"),
    _column(
        "_ensure_column",
        "recommendation_outcomes",
        "outcome_label_legacy",
        "VARCHAR(30) NOT NULL DEFAULT 'pending'",
    ),
    # _ensure_column_type and _ensure_column_default calls
    _column_type(
        "recommendations",
        "feature_snapshot_json",
        "LONGTEXT NOT NULL",
        notes="Only a compatible widening/type correction is permitted; no data is dropped.",
    ),
    _column_default("presets", "data_source", "local_pool_preferred"),
    # _ensure_index calls, preserving the exact ordered column list.
    _index("daily_kbars", "idx_kbars_td", "trade_date"),
    _index("daily_kbars", "idx_kbars_code_td", "code", "trade_date"),
    _index("daily_kbars", "idx_kbars_td_ma5", "trade_date", "ma5"),
    _index("stock_quotes", "idx_quotes_td", "trade_date"),
    _index("stock_quotes", "idx_quotes_status", "freshness_status", "snapped_at"),
    _index("limit_up_board", "idx_lub_td_limit", "trade_date", "limit_type"),
    _index("limit_up_board", "idx_lub_td_ltimes", "trade_date", "limit_times"),
    _index("concept_sector", "idx_cs_td", "trade_date"),
    _index("concept_sector_stock", "idx_css_code_sc", "code", "sector_code"),
    _index("capital_flow", "idx_cf_td_main", "trade_date", "main_net_inflow_yi"),
    _index("dragon_tiger", "idx_dt_td_net", "trade_date", "net_amount_yi"),
    _index("news_announcement", "idx_na_code_date", "code", "announce_date"),
    _index("model_route_config", "idx_mrc_enabled_priority", "enabled", "priority"),
    _index("margin_data", "idx_margin_td", "trade_date"),
    _index("hk_holdings", "idx_hk_td", "trade_date"),
    _index("holder_numbers", "idx_hn_code_ed", "code", "end_date"),
    _index("stk_limits", "idx_sl_code_td", "code", "trade_date"),
    _index("minute_kbars", "idx_mk_td", "trade_date"),
    _index("minute_kbars", "idx_mk_td_code", "trade_date", "code"),
    _index("auction_data", "idx_auction_td", "trade_date"),
    _index("broker_recommends", "idx_br_month", "month"),
    _index("holder_trades", "idx_ht_code_ann", "code", "ann_date"),
    _index("institutional_surveys", "idx_is_code_sd", "code", "surv_date"),
    _index("share_floats", "idx_sf_code_fd", "code", "float_date"),
    _index("pledge_stats", "idx_ps_code_ed", "code", "end_date"),
    _index("repurchases", "idx_rep_code_ann", "code", "ann_date"),
    _index("recommendation_runs", "idx_runs_mode_trade_date", "run_mode", "trade_date"),
    _index("recommendation_runs", "idx_runs_status_started", "status", "started_at"),
    _index("recommendations", "idx_recs_run_score", "run_id", "final_score"),
    _index("recommendations", "idx_recs_trade_score", "trade_date", "final_score"),
    _index("recommendations", "idx_recs_code_trade", "code", "trade_date"),
    _index("recommendation_outcomes", "idx_outcomes_label", "outcome_label"),
    _index("recommendation_outcomes", "idx_outcomes_entry_label", "entry_triggered", "outcome_label"),
    # _migrate_minute_kbars calls.  The old unique index is observed only and
    # intentionally has no executable DROP operation in the forward-only plan.
    _column(
        "_migrate_minute_kbars",
        "minute_kbars",
        "freq",
        "VARCHAR(5) DEFAULT '1min'",
        notes="Legacy frequency expansion; baseline target is V000 and never removes rows.",
    ),
    SchemaInventoryItem(
        "_migrate_minute_kbars",
        LEGACY_BASELINE_VERSION,
        "minute_kbars",
        "uq_minute_kbar_code_freq_time",
        "unique",
        columns=("code", "freq", "trade_time"),
        natural_key=("code", "freq", "trade_time"),
        notes="Forward-only target; duplicate natural keys must block before creation.",
    ),
    SchemaInventoryItem(
        "_migrate_minute_kbars",
        "FORBIDDEN_NOT_MANAGED",
        "minute_kbars",
        "uq_minute_kbar_code_time",
        "unique",
        columns=("code", "trade_time"),
        natural_key=("code", "freq", "trade_time"),
        notes="Retired legacy index is reported in inventory only; the runner never drops it.",
    ),
    # The old one-time presets data rewrite is recorded so it cannot silently
    # disappear, but it is not replayed by V000 or any DDL migration.
    SchemaInventoryItem(
        "_ensure_column_default",
        LEGACY_BASELINE_VERSION,
        "presets",
        "experiment_skip_aux_refresh_backfill",
        "data_backfill",
        definition="skip_aux_refresh=1 WHERE run_mode='experiment' AND skip_aux_refresh=0",
        notes="Requires an explicit, separately reviewed data migration; not run by schema startup.",
    ),
)


LEGACY_SCHEMA_INVENTORY: tuple[SchemaInventoryItem, ...] = _LEGACY_HELPER_INVENTORY + _baseline_core_items()


# Tables explicitly dropped by the old _migrate_v2.  This list is a negative
# safety assertion, never an operation supplied to MigrationContext.
FORBIDDEN_DESTRUCTIVE_TABLES: tuple[str, ...] = (
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
)


def _group_columns() -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for item in LEGACY_SCHEMA_INVENTORY:
        if item.kind in {"column", "column_type", "column_default"} and item.name not in grouped.get(item.table, ()):
            grouped.setdefault(item.table, []).append(item.name)
    return {table: tuple(columns) for table, columns in grouped.items()}



def _group_indexes() -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for item in LEGACY_SCHEMA_INVENTORY:
        if item.kind in {"index", "unique"} and item.target_version != "FORBIDDEN_NOT_MANAGED":
            grouped.setdefault(item.table, []).append(item.name)
    return {table: tuple(indexes) for table, indexes in grouped.items()}


BASELINE_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = MappingProxyType(_group_columns())
BASELINE_REQUIRED_INDEXES: Mapping[str, tuple[str, ...]] = MappingProxyType(_group_indexes())


__all__ = [
    "BASELINE_REQUIRED_COLUMNS",
    "BASELINE_REQUIRED_INDEXES",
    "FORBIDDEN_DESTRUCTIVE_TABLES",
    "LEGACY_BASELINE_VERSION",
    "LEGACY_SCHEMA_INVENTORY",
    "SchemaInventoryItem",
]
