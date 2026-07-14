"""行情池：向后兼容 re-export 层（任务 5.1/5.2 拆包后）。

原 `market_pool/__init__.py`（~2989 行）混杂了两类职责：
1. 核心报价池 + 日K池 + candidate readiness + universe/snapshot 组装 → 拆到 `core.py`
2. ~15 组辅助数据集（limit_up_board/concept_sector/capital_flow/dragon_tiger/news/
   margin_detail/block_trade/hsgt_flow/hsgt_top10/pledge/broker_recs/
   share_float_upcoming/fina_indicator/auction/holder_number/holder_trade/
   institutional_survey）的 refresh/persist/load 三件套 → 拆到 `auxiliary.py`
   （辅助数据集改用 `AuxiliaryDataset` 描述符 + 通用 upsert 收口重复的持久化样板）

本文件现在只做一件事：把 `core` 和 `auxiliary` 里所有当前对外可见的名字重新导出，
让 `from app.services.market_pool import get_market_universe_from_pool` 之类现有
写法完全不用改也能继续工作（需求 5.2：核心池对外只暴露 universe/snapshot/readiness
一组函数；其余名字是历史上散落在各调用点的内部函数引用，保留导出是为了不破坏现有
调用点，不代表它们是本次重构鼓励的新对外契约）。

`history_maturity.py` 不受本次拆分影响，`from app.services.market_pool.history_maturity
import ...` 路径保持不变。
"""
from __future__ import annotations

from app.services.market_pool import auxiliary as auxiliary
from app.services.market_pool import core as core

# ── core：核心报价池 + 日K池 + readiness + universe/snapshot（对外契约，需求 5.2） ──
from app.services.market_pool.core import (
    MarketDataError,
    NUMERIC_QUOTE_FIELDS,
    STRICT_CONSENSUS_FIELDS,
    DERIVED_RETURN_FIELDS,
    LOCAL_DAILY_BAR_FIELDS,
    STOCK_QUOTE_UPSERT_BATCH_SIZE,
    get_settings,
    enabled_sources,
    logger,
    get_market_universe_from_pool,
    get_market_universe_from_pool_with_realtime,
    get_market_snapshot_from_pool,
    refresh_quote_pool,
    local_pool_recommendation_readiness,
    enrich_quote_with_local_metrics,
    # 以下是内部实现细节，但被其它模块（data_sources/data_gap_remediation_service/
    # single_stock_analysis/stock_backtest_service/stock_matrix_service/scripts 等）
    # 直接 import 使用，保留导出以维持现有调用点不失效。
    _apply_local_quote_metrics,
    _backfill_latest_trade_date_bars,
    _bars_cover_quote_date,
    _cached_quotes_cover_latest_quote_date,
    _date_window_days,
    _dedupe_latest_quote_rows,
    _execution_recall_count,
    _incremental_kline_start,
    _load_bars_from_pool,
    _load_cached_bars,
    _load_cached_bars_batch,
    _load_cached_bars_summary,
    _load_cached_quotes,
    _load_or_refresh_bars,
    _load_or_refresh_quotes,
    _load_quotes_from_pool,
    _merge_quality,
    _merge_quote_rows,
    _persist_kbars,
    _persist_stock_quotes,
    _quote_meta,
    _quote_metrics_from_bars,
    _quote_row_recency_key,
    _readiness_example,
    _record_source_health,
    _refresh_bars_for_quote,
    _refresh_quote_pool,
    _required_bar_date_for_quote,
    _resolve_snapshot_trade_date,
    _summarize_kline_skip_reasons,
    _quote_sources,
    _kline_sources,
    _fetch_quotes,
    _fetch_kline,
    # core.py 从 market_data 转手导入的候选召回/打分/快照构建函数，原
    # `market_pool/__init__.py` 是模块级名字，其它调用点（data_gap_remediation_service
    # 等）一直经 `from app.services.market_pool import _candidate_recall_plan` 获取，
    # 保留导出维持不失效。
    _candidate_recall_plan,
    _candidate_filter_summary,
    _eligible_market_quotes,
    _select_recall_candidates,
    _exchange_from_code,
    _fetch_tushare_realtime_frame,
    _format_yyyymmdd,
    _latest_trade_date_from_quotes,
    _normalize_code,
    _num,
    _row_value,
    _build_quote_snapshot,
    _build_snapshot,
    _build_stock_from_bars,
    _apply_relative_features,
)
from app.engine.trading_calendar import (
    latest_allowed_bar_date as _latest_allowed_bar_date,
    latest_allowed_quote_date as _latest_allowed_quote_date,
    normalize_trade_date as _normalize_trade_date,
    previous_weekday as _previous_weekday,
)

# ── auxiliary：~15 组辅助数据集（AuxiliaryDataset 描述符驱动，需求 5.1） ──
from app.services.market_pool.auxiliary import (
    AuxiliaryDataset,
    AUXILIARY_DATASETS,
    NewsRefreshResult,
    load_auxiliary_for_stocks,
    inject_limit_up_flags,
    inject_dragon_tiger_flags,
    refresh_limit_up_board,
    refresh_concept_sectors,
    refresh_dragon_tiger,
    refresh_capital_flows_for_codes,
    refresh_news_for_codes,
    refresh_margin_detail,
    refresh_block_trade,
    refresh_hsgt_flow,
    refresh_hsgt_top10,
    _load_auction_for_stocks,
    _load_block_trade_for_stocks,
    _load_broker_recs_for_stocks,
    _load_cached_broken_limit_up,
    _load_cached_capital_flows,
    _load_cached_concept_sectors,
    _load_cached_dragon_tigers,
    _load_cached_limit_up_board,
    _load_cached_news,
    _load_concept_sectors_for_stocks,
    _load_fina_indicator_for_stocks,
    _load_holder_number_for_stocks,
    _load_holder_trade_for_stocks,
    _load_hsgt_flow,
    _load_hsgt_top10_for_stocks,
    _load_margin_detail_for_stocks,
    _load_pledge_for_stocks,
    _load_share_float_upcoming,
    _load_survey_for_stocks,
    _persist_block_trade,
    _persist_concept_sector_stocks,
    _persist_fina_indicator,
    _persist_hsgt_flow,
    _persist_hsgt_top10,
    _persist_margin_detail,
    _persist_normalized_capital_flow,
    _persist_normalized_concept_sector,
    _persist_normalized_dragon_tiger,
    _persist_normalized_limit_up_board,
    _persist_normalized_news,
    _refresh_aux_data,
    _upsert_by_key,
    _auxiliary_coverage_for_stocks,
)

__all__ = [
    "core",
    "auxiliary",
    # core 对外契约
    "get_market_universe_from_pool",
    "get_market_universe_from_pool_with_realtime",
    "get_market_snapshot_from_pool",
    "refresh_quote_pool",
    "local_pool_recommendation_readiness",
    "enrich_quote_with_local_metrics",
    # auxiliary 对外契约
    "AuxiliaryDataset",
    "AUXILIARY_DATASETS",
    "NewsRefreshResult",
    "load_auxiliary_for_stocks",
    "inject_limit_up_flags",
    "inject_dragon_tiger_flags",
    "refresh_limit_up_board",
    "refresh_concept_sectors",
    "refresh_dragon_tiger",
    "refresh_capital_flows_for_codes",
    "refresh_news_for_codes",
    "refresh_margin_detail",
    "refresh_block_trade",
    "refresh_hsgt_flow",
    "refresh_hsgt_top10",
    "MarketDataError",
]
