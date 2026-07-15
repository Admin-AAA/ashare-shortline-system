"""Adopt a fully verified existing application schema without modifying business tables."""
from __future__ import annotations

from ..contract import (
    MigrationBaselineError,
    MigrationContext,
    MigrationOperation,
    OperationState,
    define_migration,
)

# This inventory is the exact table/column/index surface previously repaired by
# init_db's unversioned helpers. V000 only adopts a schema already at this level;
# it never makes an incomplete deployment look current.
BASELINE_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
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

BASELINE_REQUIRED_INDEXES: dict[str, tuple[str, ...]] = {
    "daily_kbars": ("idx_kbars_td", "idx_kbars_code_td", "idx_kbars_td_ma5"),
    "stock_quotes": ("idx_quotes_td", "idx_quotes_status"),
    "limit_up_board": ("idx_lub_td_limit", "idx_lub_td_ltimes"),
    "concept_sector": ("idx_cs_td",),
    "concept_sector_stock": ("idx_css_code_sc",),
    "capital_flow": ("idx_cf_td_main",),
    "dragon_tiger": ("idx_dt_td_net",),
    "news_announcement": ("idx_na_code_date",),
    "model_route_config": ("idx_mrc_enabled_priority",),
    "margin_data": ("idx_margin_td",),
    "hk_holdings": ("idx_hk_td",),
    "holder_numbers": ("idx_hn_code_ed",),
    "stk_limits": ("idx_sl_code_td",),
    "minute_kbars": ("uq_minute_kbar_code_freq_time", "idx_mk_td", "idx_mk_td_code"),
    "auction_data": ("idx_auction_td",),
    "broker_recommends": ("idx_br_month",),
    "holder_trades": ("idx_ht_code_ann",),
    "institutional_surveys": ("idx_is_code_sd",),
    "share_floats": ("idx_sf_code_fd",),
    "pledge_stats": ("idx_ps_code_ed",),
    "repurchases": ("idx_rep_code_ann",),
    "recommendation_runs": ("idx_runs_mode_trade_date", "idx_runs_status_started"),
    "recommendations": ("idx_recs_run_score", "idx_recs_trade_score", "idx_recs_code_trade"),
    "recommendation_outcomes": ("idx_outcomes_label", "idx_outcomes_entry_label"),
}


def _missing_baseline_requirements(context: MigrationContext) -> tuple[str, ...]:
    missing: list[str] = []
    for table, columns in BASELINE_REQUIRED_COLUMNS.items():
        if not context.has_table(table):
            missing.append(f"table:{table}")
            continue
        for column in columns:
            if not context.has_column(table, column):
                missing.append(f"column:{table}.{column}")
    if context.dialect_name != "mysql":
        return tuple(missing)
    for table, indexes in BASELINE_REQUIRED_INDEXES.items():
        if not context.has_table(table):
            continue
        for index in indexes:
            if not context.has_index(table, index):
                missing.append(f"index:{table}.{index}")
    return tuple(missing)


def _baseline_state(context: MigrationContext) -> OperationState:
    return OperationState.SATISFIED if not _missing_baseline_requirements(context) else OperationState.PENDING


def _reject_incomplete_baseline(context: MigrationContext) -> None:
    missing = _missing_baseline_requirements(context)
    if missing:
        preview = ", ".join(missing[:8])
        suffix = " ..." if len(missing) > 8 else ""
        raise MigrationBaselineError(
            f"cannot adopt incomplete existing schema baseline: {preview}{suffix}",
            version="V000",
        )


def _verify_baseline(context: MigrationContext) -> None:
    _reject_incomplete_baseline(context)


migration = define_migration(
    version="V000",
    description="Adopt verified existing application schema baseline",
    operations=(
        MigrationOperation(
            name="verify_existing_application_schema_inventory",
            checksum_material="requires every legacy init_db table, column, and index target; never creates or deletes business tables",
            precondition=_baseline_state,
            apply=_reject_incomplete_baseline,
            postcondition=_baseline_state,
        ),
    ),
    verify=_verify_baseline,
)
