from __future__ import annotations

import logging
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.tables import (
    BlockTrade,
    CapitalFlow,
    ConceptSector,
    ConceptSectorStock,
    DailyKbar,
    DataSourceHealth,
    DragonTiger,
    HolderNumber,
    HolderTrade,
    HsgtFlow,
    HsgtTop10,
    InstitutionalSurvey,
    LimitUpBoard,
    MarginDetail,
    MarketSnapshot,
    NewsAnnouncement,
    Stock,
    StockQuote,
)
from app.services.data_sources import enabled_sources
from app.services.market_data import (
    MarketDataError,
    _apply_relative_features,
    _build_quote_snapshot,
    _build_snapshot,
    _build_stock_from_bars,
    _candidate_recall_plan,
    _candidate_filter_summary,
    _eligible_market_quotes,
    _exchange_from_code,
    _fetch_tushare_realtime_frame,
    _format_yyyymmdd,
    _latest_allowed_quote_date,
    _latest_trade_date_from_quotes,
    _normalize_code,
    _num,
    _row_value,
    _select_recall_candidates,
)
from app.services import market_pool_adapters as pool_adapters

_quote_sources = pool_adapters.quote_sources
_kline_sources = pool_adapters.kline_sources
_fetch_quotes = pool_adapters.fetch_quote_rows
_fetch_kline = pool_adapters.fetch_kline_rows
_limit_up_sources = pool_adapters.limit_up_sources
_concept_sector_sources = pool_adapters.concept_sector_sources
_capital_flow_sources = pool_adapters.capital_flow_sources
_dragon_tiger_sources = pool_adapters.dragon_tiger_sources
_news_sources = pool_adapters.news_sources
_fetch_limit_up_board = pool_adapters.fetch_limit_up_board_rows
_fetch_concept_sectors = pool_adapters.fetch_concept_sector_rows
_fetch_capital_flow = pool_adapters.fetch_capital_flow_rows
_fetch_capital_flow_batch = pool_adapters.fetch_capital_flow_batch
_fetch_dragon_tiger = pool_adapters.fetch_dragon_tiger_rows
_fetch_news = pool_adapters.fetch_news_rows


NUMERIC_QUOTE_FIELDS = [
    "price",
    "pct",
    "volume",
    "amount_yi",
    "turnover_rate",
    "pe_ttm",
    "total_mv_yi",
    "free_mv_yi",
    "chg60",
    "ytd_chg",
    "main_net_inflow_yi",
    "high",
    "low",
    "open",
    "preclose",
]

STRICT_CONSENSUS_FIELDS = {"price": 0.018, "pct": 0.08}
DERIVED_RETURN_FIELDS = {"chg60", "ytd_chg"}
LOCAL_DAILY_BAR_FIELDS = DERIVED_RETURN_FIELDS | {"ma5", "ma10", "ma20"}
STOCK_QUOTE_UPSERT_BATCH_SIZE = 500


def _normalize_trade_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return raw[:10]




def _required_bar_date_for_quote(quote: dict[str, Any]) -> str:
    quote_date = _normalize_trade_date(quote.get("trade_date") or date.today().isoformat())
    latest_quote_date = _latest_allowed_quote_date()
    if quote_date >= latest_quote_date:
        return _latest_allowed_bar_date(_previous_weekday(date.fromisoformat(latest_quote_date)))
    return _latest_allowed_bar_date(date.fromisoformat(quote_date))



def _date_window_days(ttl_seconds: int | None, minimum_days: int = 1) -> int:
    if not ttl_seconds:
        return minimum_days
    return max(minimum_days, int((int(ttl_seconds) + 86399) // 86400))


def _resolve_snapshot_trade_date(db: Session, trade_date: str | None = None) -> str:
    target = _normalize_trade_date(trade_date)
    if target:
        return target

    latest_snapshot = db.scalar(select(MarketSnapshot.trade_date).order_by(MarketSnapshot.trade_date.desc()).limit(1))
    if latest_snapshot:
        return _normalize_trade_date(latest_snapshot)

    latest_quote = db.scalar(
        select(func.max(StockQuote.trade_date)).where(StockQuote.freshness_status != "inactive")
    )
    if latest_quote:
        return _normalize_trade_date(latest_quote)

    latest_bar = db.scalar(select(func.max(DailyKbar.trade_date)))
    if latest_bar:
        return _normalize_trade_date(latest_bar)

    return date.today().isoformat()


def get_market_universe_from_pool(db: Session, refresh: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = get_settings()
    quotes, quote_meta = _load_or_refresh_quotes(db, settings) if refresh else _load_quotes_from_pool(db, settings, skip_metrics=True)
    total_quote_count = len(quotes)
    eligible_quotes = _eligible_market_quotes(quotes)
    filter_summary = _candidate_filter_summary(quotes)
    recall_plan = _candidate_recall_plan(total_quote_count, len(eligible_quotes), settings)
    execution_recall_count = _execution_recall_count(recall_plan, len(eligible_quotes))
    candidates = _select_recall_candidates(eligible_quotes, execution_recall_count)
    if not candidates:
        raise MarketDataError("统一行情池没有可用候选股票，已停止运行。")

    candidate_codes = [quote["code"] for quote in candidates]
    latest_quote_trade_date = _latest_trade_date_from_quotes(candidates)
    latest_allowed_bar_date = _latest_allowed_bar_date()
    latest_backfill_count = 0
    if refresh and candidate_codes and latest_quote_trade_date:
        backfill_target_trade_date = _required_bar_date_for_quote({"trade_date": latest_quote_trade_date})
        bar_summaries = _load_cached_bars_summary(db, candidate_codes, 30)
        missing_latest_trade_date_quotes = []
        for quote in candidates:
            code = str(quote.get("code") or "")
            quote_trade_date = _normalize_trade_date(quote.get("trade_date"))
            if not code or quote_trade_date != backfill_target_trade_date:
                continue
            latest_bar_date = _normalize_trade_date(bar_summaries.get(code, {}).get("latest_trade_date"))
            if latest_bar_date < backfill_target_trade_date:
                missing_latest_trade_date_quotes.append(quote)
        if missing_latest_trade_date_quotes:
            latest_backfill_count = _backfill_latest_trade_date_bars(
                db,
                missing_latest_trade_date_quotes,
                settings,
                backfill_target_trade_date,
            )

    stocks: list[dict[str, Any]] = []
    kline_errors: dict[str, list[str]] = {}
    kline_usage: Counter[str] = Counter()

    # ── 批量预加载日K（非刷新模式），避免 N 次逐个查询 ──
    if not refresh:
        bars_batch = _load_cached_bars_batch(db, candidate_codes, settings.market_kline_days)
    else:
        bars_batch = None

    for quote in candidates:
        if len(stocks) >= recall_plan["scan_target_count"]:
            break
        if refresh:
            bars, kline_trace, errors = _load_or_refresh_bars(db, quote, settings)
        else:
            cached = bars_batch.get(quote["code"], []) if bars_batch else []
            if len(cached) < 30:
                bars, kline_trace, errors = cached, {"selected_source": "daily_bars_cache", "freshness_status": "missing"}, [
                    f"daily_bars_cache: 本地日K不足30条，仅 {len(cached)} 条"
                ]
            elif not _bars_cover_quote_date(cached, quote):
                bars, kline_trace, errors = cached, {"selected_source": "daily_bars_cache", "freshness_status": "stale_cache"}, [
                    "daily_bars_cache: 本地日K未覆盖报价日期"
                ]
            else:
                bars, kline_trace, errors = cached, {"selected_source": "daily_bars_cache", "freshness_status": "local_pool_readonly"}, []
        if errors:
            kline_errors[quote["code"]] = errors
            continue
        selected_source = str(kline_trace.get("selected_source") or "daily_bars_cache")
        stock = _build_stock_from_bars(
            quote,
            bars,
            quote_source="unified_pool",
            kline_source=selected_source,
        )
        stock["quote_source"] = "unified_pool"
        stock["quote_time"] = quote.get("fetched_at") or stock.get("quote_time")
        stock["data_quality"] = _merge_quality(stock.get("data_quality"), quote, kline_trace)
        stock["source_trace"] = {
            "data_source": "local_unified_market_pool",
            "quote_source": "unified_pool",
            "quote_raw_sources": quote.get("raw_sources", []),
            "quote_source_count": quote.get("source_count", 0),
            "quote_quality_score": quote.get("quality_score", 0),
            "quote_conflicts": quote.get("conflicts", []),
            "quote_cache_status": quote.get("freshness_status", "unknown"),
            "kline_source": selected_source,
            "kline_cache_status": kline_trace.get("freshness_status", "unknown"),
        }
        stocks.append(stock)
        kline_usage[selected_source] += 1

    if not stocks:
        detail = "; ".join(f"{code}: {' | '.join(errors[:2])}" for code, errors in list(kline_errors.items())[:8])
        raise MarketDataError(f"统一行情池无法取得候选日K，已停止运行。{detail}")

    quote_snapshot = _build_quote_snapshot(
        eligible_quotes,
        data_source="local_unified_market_pool",
        quote_source="unified_pool",
        quote_errors=quote_meta.get("errors", []),
    )
    _apply_relative_features(stocks)
    snapshot = _build_snapshot(
        stocks=stocks,
        quote_source="unified_pool",
        quote_errors=quote_meta.get("errors", []),
        kline_sources=_kline_sources(settings),
        kline_usage=kline_usage,
        kline_errors=kline_errors,
        quote_snapshot=quote_snapshot,
    )
    snapshot["breadth"].update(
        {
            "data_source": "local_unified_market_pool",
            "quote_pool_status": quote_meta.get("status"),
            "quote_source_count": quote_meta.get("source_count"),
            "quote_sources": quote_meta.get("sources", []),
            "quote_cache_age_seconds": quote_meta.get("cache_age_seconds"),
            "quote_pool_min_source_count": settings.market_pool_min_source_count,
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "full_market_mode": recall_plan["full_market_mode"],
            "unified_quote_total": total_quote_count,
            "base_filter_eligible_count": len(eligible_quotes),
            "candidate_filter": filter_summary,
            "actual_scan_count": len(stocks),
            "kline_missing_count": len(kline_errors),
            "kline_skip_reasons": _summarize_kline_skip_reasons(kline_errors),
            "candidate_limit_config": {
                "market_candidate_count": recall_plan["explicit_scan_limit"],
                "lookahead_multiplier": recall_plan["lookahead_multiplier"],
                "lookahead_count": recall_plan["broad_recall_count"],
                "target_scan_count": recall_plan["scan_target_count"],
                "broad_recall_count": execution_recall_count,
                "configured_broad_recall_count": recall_plan["broad_recall_count"],
                "broad_recall_limited": execution_recall_count < len(eligible_quotes),
                "recall_strategy": "multi_bucket_quote_recall",
            },
            "latest_quote_trade_date": latest_quote_trade_date,
            "latest_allowed_bar_date": latest_allowed_bar_date,
            "latest_trade_date_backfill_count": latest_backfill_count,
        }
    )
    snapshot["snapshot_id"] = f"snapshot_pool_{snapshot['trade_date']}_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%H%M%S')}"
    return stocks, snapshot


def _build_realtime_bar(realtime_row: dict[str, Any], prev_close: float, code: str) -> dict[str, Any]:
    """将 realtime_quote 返回的实时行情拼接为一条日K。"""
    price = _num(_row_value(realtime_row, "PRICE", "price"))
    return {
        "code": code,
        "trade_date": _format_yyyymmdd(_row_value(realtime_row, "DATE", "date")),
        "open": _num(_row_value(realtime_row, "OPEN", "open")),
        "high": _num(_row_value(realtime_row, "HIGH", "high")),
        "low": _num(_row_value(realtime_row, "LOW", "low")),
        "close": price,
        "volume": _num(_row_value(realtime_row, "VOLUME", "volume", "volumn")),
        "amount": _num(_row_value(realtime_row, "AMOUNT", "amount")),
        "pct_chg": round((price - prev_close) / prev_close * 100, 4) if prev_close > 0 else 0.0,
        "turnover_rate": 0.0,
        "source": "realtime_quote",
    }


def get_market_universe_from_pool_with_realtime(db: Session) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """盘中实时模式：本地池历史K线 + realtime_quote 今日实时快照拼接。

    流程：
    1. 从本地 stock_quotes 加载缓存行情（含市值/PE/换手率）
    2. 提取候选股票，分批拉取 realtime_quote 实时快照
    3. 合并 quote：实时 price/high/low/volume/amount + 缓存市值/PE
    4. 从 daily_kbars 加载历史K线，拼接今日实时K线
    5. 调用 _build_stock_from_bars 构建完整 Stock 对象
    """
    settings = get_settings()
    quotes, quote_meta = _load_quotes_from_pool(db, settings, skip_metrics=True)
    total_quote_count = len(quotes)
    eligible_quotes = _eligible_market_quotes(quotes)
    filter_summary = _candidate_filter_summary(quotes)
    recall_plan = _candidate_recall_plan(total_quote_count, len(eligible_quotes), settings)
    execution_recall_count = _execution_recall_count(recall_plan, len(eligible_quotes))
    candidates = _select_recall_candidates(eligible_quotes, execution_recall_count)
    if not candidates:
        raise MarketDataError("统一行情池没有可用候选股票，已停止运行。")

    candidate_codes = [quote["code"] for quote in candidates]
    ts_codes = [f"{code}.{_exchange_from_code(code)}" for code in candidate_codes]

    # 分批拉取 realtime_quote（sina 源单次最多 50 只）
    realtime_map: dict[str, dict[str, Any]] = {}
    chunk_size = 50
    for start in range(0, len(ts_codes), chunk_size):
        chunk = ts_codes[start:start + chunk_size]
        try:
            frame = _fetch_tushare_realtime_frame(chunk, settings)
            if frame is None or frame.empty:
                continue
            for _, row in frame.iterrows():
                item = dict(row)
                code = _normalize_code(str(_row_value(item, "TS_CODE", "ts_code", "code") or "").split(".")[0])
                if code:
                    realtime_map[code] = item
        except Exception as exc:
            logger.warning("realtime_quote 批次 %d-%d 失败: %s", start, start + chunk_size, exc)

    if not realtime_map:
        raise MarketDataError("盘中实时行情拉取失败，realtime_quote 未返回任何数据。")

    # 合并 quote：实时 price/high/low/volume/amount + 缓存市值/PE
    merged_quotes: list[dict[str, Any]] = []
    for quote in candidates:
        code = quote["code"]
        rt = realtime_map.get(code)
        if not rt:
            continue
        price = _num(_row_value(rt, "PRICE", "price"))
        if price <= 0:
            continue
        preclose = _num(_row_value(rt, "PRE_CLOSE", "pre_close", "preclose")) or _num(quote.get("preclose"))
        merged = dict(quote)
        merged["price"] = price
        merged["pct"] = round((price - preclose) / preclose * 100, 4) if preclose > 0 else 0.0
        merged["high"] = _num(_row_value(rt, "HIGH", "high"))
        merged["low"] = _num(_row_value(rt, "LOW", "low"))
        merged["open"] = _num(_row_value(rt, "OPEN", "open"))
        merged["preclose"] = preclose
        merged["volume"] = _num(_row_value(rt, "VOLUME", "volume", "volumn"))
        merged["amount_yi"] = _num(_row_value(rt, "AMOUNT", "amount")) / 100000000
        free_mv_yi = _num(quote.get("free_mv_yi"))
        if free_mv_yi > 0 and merged["amount_yi"] > 0:
            merged["turnover_rate"] = round(merged["amount_yi"] / free_mv_yi * 100, 4)
        merged["trade_date"] = _format_yyyymmdd(_row_value(rt, "DATE", "date"))
        merged["fetched_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        merged_quotes.append(merged)

    if not merged_quotes:
        raise MarketDataError("盘中实时行情合并失败，无有效候选。")

    # 批量预加载历史K线
    stocks: list[dict[str, Any]] = []
    kline_errors: dict[str, list[str]] = {}
    kline_usage: Counter[str] = Counter()
    bars_batch = _load_cached_bars_batch(db, candidate_codes, settings.market_kline_days)

    for quote in merged_quotes:
        if len(stocks) >= recall_plan["scan_target_count"]:
            break
        code = quote["code"]
        bars = list(bars_batch.get(code, []))
        if len(bars) < 30:
            kline_errors[code] = [f"daily_bars_cache: 本地日K不足30条，仅 {len(bars)} 条"]
            continue

        # 拼接今日实时K线
        prev_close = bars[-1]["close"] if bars else _num(quote.get("preclose"))
        rt_bar = _build_realtime_bar(realtime_map[code], prev_close, code)
        # 如果今日实时K线日期与最后一条历史K线相同，替换最后一条
        if bars and bars[-1]["trade_date"] == rt_bar["trade_date"]:
            bars = bars[:-1]
        bars.append(rt_bar)

        selected_source = "daily_bars_cache+realtime"
        stock = _build_stock_from_bars(
            quote,
            bars,
            quote_source="unified_pool_realtime",
            kline_source=selected_source,
        )
        stock["quote_source"] = "unified_pool_realtime"
        stock["quote_time"] = quote.get("fetched_at") or stock.get("quote_time")
        stock["data_quality"] = _merge_quality(stock.get("data_quality"), quote, {"selected_source": selected_source, "freshness_status": "realtime"})
        stock["source_trace"] = {
            "data_source": "local_pool_realtime",
            "quote_source": "unified_pool_realtime",
            "quote_raw_sources": ["tushare_realtime"],
            "quote_source_count": 1,
            "quote_quality_score": quote.get("quality_score", 0),
            "quote_conflicts": [],
            "quote_cache_status": "realtime",
            "kline_source": selected_source,
            "kline_cache_status": "local_pool+realtime_bar",
        }
        stocks.append(stock)
        kline_usage[selected_source] += 1

    if not stocks:
        detail = "; ".join(f"{code}: {' | '.join(errors[:2])}" for code, errors in list(kline_errors.items())[:8])
        raise MarketDataError(f"盘中实时行情池无法取得候选日K，已停止运行。{detail}")

    quote_snapshot = _build_quote_snapshot(
        merged_quotes,
        data_source="local_pool_realtime",
        quote_source="unified_pool_realtime",
        quote_errors=quote_meta.get("errors", []),
    )
    _apply_relative_features(stocks)
    snapshot = _build_snapshot(
        stocks=stocks,
        quote_source="unified_pool_realtime",
        quote_errors=quote_meta.get("errors", []),
        kline_sources=_kline_sources(settings),
        kline_usage=kline_usage,
        kline_errors=kline_errors,
        quote_snapshot=quote_snapshot,
    )
    snapshot["breadth"].update(
        {
            "data_source": "local_pool_realtime",
            "quote_pool_status": quote_meta.get("status"),
            "realtime_quote_count": len(realtime_map),
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "full_market_mode": recall_plan["full_market_mode"],
            "unified_quote_total": total_quote_count,
            "base_filter_eligible_count": len(eligible_quotes),
            "candidate_filter": filter_summary,
            "actual_scan_count": len(stocks),
            "kline_missing_count": len(kline_errors),
            "kline_skip_reasons": _summarize_kline_skip_reasons(kline_errors),
            "candidate_limit_config": {
                "market_candidate_count": recall_plan["explicit_scan_limit"],
                "lookahead_multiplier": recall_plan["lookahead_multiplier"],
                "lookahead_count": recall_plan["broad_recall_count"],
                "target_scan_count": recall_plan["scan_target_count"],
                "broad_recall_count": execution_recall_count,
                "configured_broad_recall_count": recall_plan["broad_recall_count"],
                "broad_recall_limited": execution_recall_count < len(eligible_quotes),
                "recall_strategy": "multi_bucket_quote_recall",
            },
        }
    )
    snapshot["snapshot_id"] = f"snapshot_pool_rt_{snapshot['trade_date']}_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%H%M%S')}"
    return stocks, snapshot


def get_market_snapshot_from_pool(db: Session, refresh: bool = False) -> dict[str, Any]:
    settings = get_settings()
    quotes, quote_meta = _load_or_refresh_quotes(db, settings) if refresh else _load_quotes_from_pool(db, settings, skip_metrics=True)
    total_quote_count = len(quotes)
    eligible_quotes = _eligible_market_quotes(quotes)
    filter_summary = _candidate_filter_summary(quotes)
    if not eligible_quotes:
        raise MarketDataError("统一行情池没有可用市场快照。")
    snapshot = _build_quote_snapshot(
        eligible_quotes,
        data_source="local_unified_market_pool",
        quote_source="unified_pool",
        quote_errors=quote_meta.get("errors", []),
    )
    snapshot["breadth"].update(
        {
            "quote_pool_status": quote_meta.get("status"),
            "quote_source_count": quote_meta.get("source_count"),
            "quote_sources": quote_meta.get("sources", []),
            "quote_cache_age_seconds": quote_meta.get("cache_age_seconds"),
            "unified_quote_total": total_quote_count,
            "base_filter_eligible_count": len(eligible_quotes),
            "candidate_filter": filter_summary,
            "actual_scan_count": len(eligible_quotes),
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }
    )
    snapshot["snapshot_id"] = f"snapshot_pool_{snapshot['trade_date']}"
    return snapshot


def refresh_quote_pool(db: Session) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _refresh_quote_pool(db, get_settings())


def local_pool_recommendation_readiness(
    db: Session,
    cached_quotes: list[dict[str, Any]] | None = None,
    cached_bar_summaries: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    configured_full_market_mode = int(getattr(settings, "market_candidate_count", 0) or 0) <= 0
    min_bar_count = 30
    messages: list[str] = []
    examples: list[dict[str, Any]] = []

    active_sources = enabled_sources(db, "quote", _quote_sources(settings))
    quotes = (
        cached_quotes
        if cached_quotes is not None
        else _load_cached_quotes(
            db,
            allowed_sources=set(active_sources) if active_sources else None,
            skip_metrics=True,
        )
    )
    using_cache_without_active_sources = not active_sources and bool(quotes)
    if not active_sources and not quotes:
        return {
            "status": "blocked",
            "can_run_local_recommendation": False,
            "has_missing_data": True,
            "full_market_mode": configured_full_market_mode,
            "required_candidate_count": 0,
            "quote": {
                "available_count": 0,
                "candidate_count": 0,
                "latest_trade_date": None,
                "active_sources": [],
            },
            "kline": {
                "checked_count": 0,
                "ready_count": 0,
                "missing_count": 0,
                "stale_count": 0,
                "min_bars": min_bar_count,
                "examples": [],
            },
            "messages": ["所有报价数据源都已关闭，本地池无法确认可用报价。"],
        }

    quote_trade_dates = [str(item.get("trade_date") or "") for item in quotes if item.get("trade_date")]
    eligible_quotes = _eligible_market_quotes(quotes) if quotes else []
    filter_summary = _candidate_filter_summary(quotes) if quotes else {
        "total_count": 0,
        "base_eligible_count": 0,
        "excluded_count": 0,
        "high_liquidity_count": 0,
        "low_liquidity_count": 0,
        "exclusion_counts": {},
        "high_liquidity_threshold": {"amount_yi": 0.5, "turnover_rate": 0.1},
    }
    recall_plan = _candidate_recall_plan(len(quotes), len(eligible_quotes), settings)
    execution_recall_count = _execution_recall_count(recall_plan, len(eligible_quotes))
    candidates = _select_recall_candidates(eligible_quotes, execution_recall_count)
    effective_required_count = min(len(candidates), recall_plan["scan_target_count"])

    # Bar summaries：优先用传入缓存，避免重复 GROUP BY 查询
    candidate_codes = [str(item.get("code") or "") for item in candidates]
    if cached_bar_summaries is not None:
        bar_summaries = {code: cached_bar_summaries[code] for code in candidate_codes if code in cached_bar_summaries}
    else:
        bar_summaries = _load_cached_bars_summary(db, candidate_codes, min_bar_count) if candidate_codes else {}

    ready_count = 0
    missing_count = 0
    short_history_count = 0
    immature_history_count = 0
    stale_count = 0
    checked_count = 0
    maturity_map = _load_history_maturity_map(db, candidate_codes) if candidate_codes else {}
    for quote in candidates:
        checked_count += 1
        code = str(quote.get("code") or "")
        summary = bar_summaries.get(code, {})
        bar_count = int(summary.get("bar_count") or 0)
        latest_bar_date = _normalize_trade_date(summary.get("latest_trade_date"))
        quote_date = _normalize_trade_date(quote.get("trade_date") or date.today().isoformat())
        required_bar_date = _required_bar_date_for_quote(quote)
        natural_short_history = False
        if bar_count < min_bar_count:
            if bar_count <= 0:
                missing_count += 1
            else:
                short_history_count += 1
                maturity = maturity_map.get(code, {})
                natural_short_history = _is_natural_short_history(
                    bar_count=bar_count,
                    latest_bar_date=latest_bar_date,
                    quote_date=required_bar_date,
                    first_bar_date=_normalize_trade_date(summary.get("first_trade_date") or maturity.get("first_bar_date")),
                    list_date=_normalize_trade_date(summary.get("list_date") or maturity.get("list_date")),
                )
                if natural_short_history:
                    immature_history_count += 1
            if len(examples) < 8:
                examples.append(
                    _readiness_example(
                        quote,
                        "上市未满30根日K" if natural_short_history else "本地日K不足30条",
                        bar_count,
                        latest_bar_date,
                        quote_date,
                        required_bar_date,
                    )
                )
            continue
        if not latest_bar_date or latest_bar_date < required_bar_date:
            stale_count += 1
            if len(examples) < 8:
                examples.append(
                    _readiness_example(
                        quote,
                        "本地日K未覆盖报价日期",
                        bar_count,
                        latest_bar_date,
                        quote_date,
                        required_bar_date,
                    )
                )
            continue
        ready_count += 1

    can_run = (
        len(quotes) > 0 if recall_plan["full_market_mode"] else len(quotes) >= recall_plan["scan_target_count"]
    ) and ready_count > 0
    has_missing_data = (
        not candidates
        or ready_count < effective_required_count
        or (
            not recall_plan["full_market_mode"]
            and (
                len(quotes) < recall_plan["scan_target_count"]
                or len(candidates) < recall_plan["scan_target_count"]
            )
        )
    )
    if recall_plan["full_market_mode"] and not quotes:
        status = "blocked"
        messages.append("本地统一报价为空，全市场模式无法运行推荐。")
    elif not recall_plan["full_market_mode"] and len(quotes) < recall_plan["scan_target_count"]:
        status = "blocked"
        messages.append(
            f"本地统一报价不足，当前 {len(quotes)} 条，至少需要 {recall_plan['scan_target_count']} 条。"
        )
    elif not candidates:
        status = "blocked"
        messages.append("本地报价存在，但没有股票通过候选过滤条件。")
    elif ready_count == 0:
        status = "blocked"
        messages.append("候选股的本地日K全部缺失或未覆盖报价日期。")
    elif ready_count < effective_required_count:
        status = "warning"
        if recall_plan["full_market_mode"]:
            messages.append(
                f"全市场模式可运行本地推荐，将扫描 {len(candidates)} 只候选；"
                f"当前日K覆盖完整 {ready_count} 只，目标扫描 {recall_plan['scan_target_count']} 只。"
            )
        else:
            messages.append(
                f"可运行本地推荐，但宽召回 {len(candidates)} 只里只有 {ready_count} 只日K覆盖完整，"
                f"目标扫描数为 {recall_plan['scan_target_count']}。"
            )
    else:
        status = "ready"
        if recall_plan["full_market_mode"]:
            messages.append(f"全市场模式可直接运行推荐，{ready_count} / {len(candidates)} 只候选股报价与日K覆盖完整。")
        else:
            messages.append(
                f"本地池可直接运行推荐，宽召回 {len(candidates)} 只中已有 {ready_count} 只候选股报价与日K覆盖完整。"
            )
    if using_cache_without_active_sources:
        messages.append("报价源开关当前全部关闭，本地池将仅基于已落库缓存运行；如需刷新行情请重新启用报价源。")

    if missing_count or stale_count:
        gap_parts = []
        if missing_count:
            gap_parts.append(f"日K缺失 {missing_count} 只")
        remediable_short_history = max(0, short_history_count - immature_history_count)
        if remediable_short_history:
            gap_parts.append(f"短历史 {remediable_short_history} 只")
        if stale_count:
            gap_parts.append(f"日K过期 {stale_count} 只")
        messages.append(f"当前存在数据缺口：{'，'.join(gap_parts)}。")
    elif short_history_count > immature_history_count:
        messages.append(f"当前存在数据缺口：短历史 {short_history_count - immature_history_count} 只。")
    if immature_history_count:
        messages.append(f"另有 {immature_history_count} 只候选属于上市未满30根日K，按成熟度风险处理。")

    return {
        "status": status,
        "can_run_local_recommendation": can_run,
        "has_missing_data": has_missing_data,
        "full_market_mode": recall_plan["full_market_mode"],
        "required_candidate_count": effective_required_count,
        "quote": {
            "available_count": len(quotes),
            "candidate_count": len(candidates),
            "base_filter_eligible_count": len(eligible_quotes),
            "unified_quote_total": len(quotes),
            "filter_summary": filter_summary,
            "latest_trade_date": max(quote_trade_dates) if quote_trade_dates else None,
            "active_sources": active_sources,
            "using_cache_without_active_sources": using_cache_without_active_sources,
        },
        "kline": {
            "checked_count": checked_count,
            "ready_count": ready_count,
            "missing_count": missing_count,
            "short_history_count": short_history_count,
            "immature_history_count": immature_history_count,
            "stale_count": stale_count,
            "min_bars": min_bar_count,
            "coverage_count": ready_count,
            "coverage_rate": round(ready_count / max(1, len(candidates)), 4) if candidates else 0.0,
            "latest_trade_date": max(
                [str(summary.get("latest_trade_date") or "") for summary in bar_summaries.values() if summary.get("latest_trade_date")],
                default=None,
            ),
            "example_count": len(examples),
            "example_limit": 8,
            "examples": examples,
        },
        "scan": {
            "configured_candidate_count": recall_plan["explicit_scan_limit"],
            "lookahead_multiplier": recall_plan["lookahead_multiplier"],
            "lookahead_count": recall_plan["broad_recall_count"],
            "data_coverage_lookahead_count": execution_recall_count,
            "actual_scan_count": min(ready_count, recall_plan["scan_target_count"]),
            "skill_input_count": min(ready_count, recall_plan["scan_target_count"]),
            "agent_review_input_count": min(ready_count, recall_plan["scan_target_count"]),
            "scan_limited": execution_recall_count < len(eligible_quotes) or (
                not recall_plan["full_market_mode"] and len(candidates) > recall_plan["scan_target_count"]
            ),
            "demo_limited": (not recall_plan["full_market_mode"]) and len(eligible_quotes) > recall_plan["scan_target_count"],
        },
        "messages": messages,
    }


def _execution_recall_count(recall_plan: dict[str, Any], eligible_count: int) -> int:
    broad_count = max(0, int(recall_plan.get("broad_recall_count") or 0))
    if not bool(recall_plan.get("full_market_mode")) or not bool(recall_plan.get("broad_recall_limited")):
        return min(eligible_count, broad_count)
    target_count = max(1, int(recall_plan.get("scan_target_count") or broad_count or 1))
    return min(eligible_count, max(broad_count, target_count * 3))


def _load_quotes_from_pool(db: Session, settings: Any, skip_metrics: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    active_sources = enabled_sources(db, "quote", _quote_sources(settings))
    quotes = _load_cached_quotes(
        db,
        allowed_sources=set(active_sources) if active_sources else None,
        skip_metrics=skip_metrics,
    )
    if not active_sources and not quotes:
        raise MarketDataError("所有报价数据源都已关闭，且本地数据池没有可用缓存。请至少启用一个报价源。")
    required_count = max(0, int(settings.market_candidate_count))
    if required_count > 0 and len(quotes) < required_count:
        raise MarketDataError(
            f"本地数据池可用报价不足，当前 {len(quotes)} 条，至少需要 {required_count} 条。"
            "请先显式刷新本地数据池，或切换为实时数据。"
        )
    status = "local_pool_readonly" if active_sources else "local_pool_readonly_source_disabled"
    errors = [] if active_sources else ["所有报价数据源关闭，当前仅使用本地缓存。"]
    return quotes, _quote_meta(status, quotes, errors=errors)


def _load_or_refresh_quotes(db: Session, settings: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    configured_sources = _quote_sources(settings)
    active_sources = enabled_sources(db, "quote", configured_sources)
    if not active_sources:
        cached = _load_cached_quotes(db)
        if cached:
            return cached, _quote_meta(
                "local_pool_readonly_source_disabled",
                cached,
                errors=["所有报价数据源关闭，无法刷新行情；当前仅使用本地缓存。"],
            )
        raise MarketDataError("所有报价数据源都已关闭，且本地数据池没有可用缓存。请至少启用一个报价源。")
    allowed_sources = set(active_sources)
    cached = _load_cached_quotes(
        db,
        max_age=timedelta(seconds=settings.market_quote_cache_ttl_seconds),
        allowed_sources=allowed_sources,
    )
    required_count = max(0, int(settings.market_candidate_count))
    if (
        len(cached) > 0
        and (required_count == 0 or len(cached) >= required_count)
        and _cached_quotes_cover_latest_quote_date(cached)
    ):
        return cached, _quote_meta("fresh_cache", cached)
    return _refresh_quote_pool(db, settings, active_sources=active_sources)


def _cached_quotes_cover_latest_quote_date(quotes: list[dict[str, Any]]) -> bool:
    latest_quote_date = _latest_allowed_quote_date()
    latest_cached_date = _latest_trade_date_from_quotes(quotes)
    return bool(latest_cached_date and latest_cached_date >= latest_quote_date)


def _refresh_quote_pool(
    db: Session,
    settings: Any,
    active_sources: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    errors: list[str] = []
    sources = active_sources if active_sources is not None else enabled_sources(db, "quote", _quote_sources(settings))
    if not sources:
        raise MarketDataError("所有报价数据源都已关闭，请至少启用一个报价源。")
    for source in sources:
        start = time.perf_counter()
        try:
            rows = _fetch_quotes(source, settings)
            latency_ms = (time.perf_counter() - start) * 1000
            if not rows:
                _record_source_health(db, source, "quote", "empty", latency_ms=latency_ms)
                errors.append(f"{source}: empty result")
                continue
            trade_date = _latest_trade_date_from_quotes(rows)
            _record_source_health(db, source, "quote", "success", latency_ms=latency_ms)
            for row in rows:
                code = str(row.get("code") or "")
                if code:
                    grouped[code].append(row)
        except Exception as exc:  # noqa: BLE001 - errors become run evidence
            latency_ms = (time.perf_counter() - start) * 1000
            _record_source_health(db, source, "quote", "failure", error=str(exc), latency_ms=latency_ms)
            errors.append(f"{source}: {exc}")

    if not grouped:
        db.commit()
        # 所有报价源都失败时，用旧缓存兜底（T12：preclose_overnight 因 Tushare 超时失败，
        # formal run 跑不通 = 系统对用户等于没在跑。旧缓存虽非最新但有数据，比 0 推荐好）
        try:
            cached = _load_cached_quotes(db, skip_metrics=True)
            if cached:
                logger.warning(
                    "行情池刷新全失败，用旧缓存兜底（%d 条）。错误: %s",
                    len(cached),
                    "；".join(errors),
                )
                return cached, _quote_meta("stale_cache_fallback", cached, errors=errors)
        except Exception:
            pass
        raise MarketDataError("统一行情池刷新失败，所有报价源不可用。" + "；".join(errors))

    merged = [_merge_quote_rows(code, rows, sources, settings) for code, rows in grouped.items()]
    _enrich_industries(settings, merged)
    _persist_stock_quotes(db, merged)
    db.commit()
    return merged, _quote_meta("refreshed", merged, errors=errors)


def _load_cached_quotes(
    db: Session,
    max_age: timedelta | None = None,
    allowed_sources: set[str] | None = None,
    skip_metrics: bool = False,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - max_age if max_age is not None else None
    latest_allowed_date = _latest_allowed_quote_date()
    stmt = (
        select(StockQuote)
        .where(
            StockQuote.freshness_status != "inactive",
            StockQuote.trade_date <= latest_allowed_date,
        )
        .order_by(StockQuote.quality_score.desc(), StockQuote.snapped_at.desc())
    )
    if cutoff is not None:
        stmt = stmt.where(StockQuote.snapped_at >= cutoff)
    rows = _dedupe_latest_quote_rows(db.scalars(stmt).all())
    local_metrics = {} if skip_metrics else _quote_metrics_from_bars(db, [row.code for row in rows])
    quotes = []
    for row in rows:
        quote = {
            "code": row.code,
            "name": row.name,
            "trade_date": row.trade_date,
            "price": float(row.price or 0),
            "pct": float(row.pct or 0),
            "volume": float(row.volume or 0),
            "amount_yi": float(row.amount_yi or 0),
            "turnover_rate": float(row.turnover_rate or 0),
            "pe_ttm": float(row.pe_ttm or 0) if row.pe_ttm else None,
            "total_mv_yi": float(row.total_mv_yi or 0) if row.total_mv_yi else None,
            "free_mv_yi": float(row.free_mv_yi or 0) if row.free_mv_yi else None,
            "chg60": float(row.chg60 or 0) if row.chg60 else None,
            "high": float(row.high or 0) if row.high else None,
            "low": float(row.low or 0) if row.low else None,
            "open": float(row.open or 0) if row.open else None,
            "preclose": float(row.preclose or 0) if row.preclose else None,
            "industry": row.industry or "",
            "source_count": row.source_count,
            "quality_score": row.quality_score,
            "freshness_status": row.freshness_status if cutoff is None or (row.snapped_at and row.snapped_at >= cutoff) else "stale_cache",
            "fetched_at": row.snapped_at.isoformat() if row.snapped_at else "",
            "raw_sources": [row.source] if row.source else [],
            "conflicts": [],
            "exchange": "SH" if (row.code or "").startswith(("6", "9")) else "SZ",
        }
        if not skip_metrics:
            _apply_local_quote_metrics(quote, local_metrics.get(row.code, {}))
        if allowed_sources is not None and not set(quote["raw_sources"]).issubset(allowed_sources):
            continue
        quotes.append(quote)
    return quotes


def _dedupe_latest_quote_rows(rows: list[Any]) -> list[Any]:
    latest_by_code: dict[str, Any] = {}
    for row in rows:
        code = str(getattr(row, "code", "") or "")
        if not code:
            continue
        current = latest_by_code.get(code)
        if current is None or _quote_row_recency_key(row) > _quote_row_recency_key(current):
            latest_by_code[code] = row
    return sorted(
        latest_by_code.values(),
        key=lambda row: (
            float(getattr(row, "quality_score", 0) or 0),
            str(getattr(row, "snapped_at", "") or ""),
            _normalize_trade_date(getattr(row, "trade_date", None)),
        ),
        reverse=True,
    )


def _quote_row_recency_key(row: Any) -> tuple[str, str, float]:
    return (
        _normalize_trade_date(getattr(row, "trade_date", None)),
        str(getattr(row, "snapped_at", "") or ""),
        float(getattr(row, "quality_score", 0) or 0),
    )


def enrich_quote_with_local_metrics(db: Session, code: str, quote: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(quote)
    _apply_local_quote_metrics(enriched, _quote_metrics_from_bars(db, [code]).get(code, {}))
    return enriched


def _quote_metrics_from_bars(db: Session, codes: list[str]) -> dict[str, dict[str, float]]:
    unique_codes = sorted({str(code) for code in codes if code})
    if not unique_codes:
        return {}

    latest_allowed_date = _latest_allowed_bar_date()
    year_start = date.fromisoformat(latest_allowed_date).replace(month=1, day=1).isoformat()
    # 只查询需要的列 + 限制日期范围（避免加载全年数据，只需最近 ~150 交易日）
    cutoff_date = (date.fromisoformat(latest_allowed_date) - timedelta(days=220)).isoformat()
    stmt = (
        select(
            DailyKbar.code,
            DailyKbar.trade_date,
            DailyKbar.close,
            DailyKbar.ma5,
            DailyKbar.ma10,
            DailyKbar.ma20,
        )
        .where(
            DailyKbar.code.in_(unique_codes),
            DailyKbar.trade_date >= cutoff_date,
            DailyKbar.trade_date <= latest_allowed_date,
        )
        .order_by(DailyKbar.code, DailyKbar.trade_date.desc())
    )
    rows = db.execute(stmt).all()

    bars_by_code: dict[str, list[tuple]] = defaultdict(list)
    for row in rows:
        bars_by_code[str(row[0])].append(row)

    result: dict[str, dict[str, float]] = {}
    for code, bars in bars_by_code.items():
        if not bars:
            continue
        latest_close = float(bars[0][2] or 0)
        if latest_close <= 0:
            continue

        latest_bar = bars[0]
        values: dict[str, float] = {}
        for idx, field in enumerate(("ma5", "ma10", "ma20"), start=3):
            value = latest_bar[idx]
            if value is not None:
                values[field] = round(float(value), 3)

        if len(bars) >= 60:
            base_close = float(bars[59][2] or 0)
            if base_close:
                values["chg60"] = round((latest_close - base_close) / base_close * 100, 4)

        ytd_base = next((bar for bar in bars if str(bar[1]) < year_start), None)
        if ytd_base is not None:
            base_close = float(ytd_base[2] or 0)
            if base_close:
                values["ytd_chg"] = round((latest_close - base_close) / base_close * 100, 4)

        if values:
            result[code] = values
    return result


def _apply_local_quote_metrics(quote: dict[str, Any], metrics: dict[str, float]) -> None:
    source_trace = quote.setdefault("source_trace", {})
    if isinstance(source_trace, dict):
        field_sources = source_trace.setdefault("field_sources", {})
    else:
        field_sources = {}
    for field in LOCAL_DAILY_BAR_FIELDS:
        value = metrics.get(field)
        has_source_value = isinstance(field_sources, dict) and bool(field_sources.get(field))
        if value is None:
            if quote.get(field) in {0, 0.0} and not has_source_value:
                quote[field] = None
            continue
        if quote.get(field) in {None, "", "-", 0, 0.0}:
            quote[field] = value
            if isinstance(field_sources, dict):
                field_sources[field] = ["daily_bars_cache"]


def _load_or_refresh_bars(
    db: Session,
    quote: dict[str, Any],
    settings: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    cached = _load_cached_bars(db, quote["code"], settings.market_kline_days)
    if len(cached) >= 30 and _bars_cover_quote_date(cached, quote):
        return cached, {"selected_source": "daily_bars_cache", "freshness_status": "fresh_cache"}, []
    bars, source, errors = _refresh_bars_for_quote(db, quote, settings)
    if bars:
        _persist_kbars(db, bars)
        db.commit()
        merged = _load_cached_bars(db, quote["code"], settings.market_kline_days)
        if len(merged) >= 30 and _bars_cover_quote_date(merged, quote):
            return merged, {"selected_source": source, "freshness_status": "refreshed"}, errors
        if len(merged) < 30:
            errors.append(f"{source}: 合并后日K不足30条，仅 {len(merged)} 条")
        elif not _bars_cover_quote_date(merged, quote):
            errors.append(f"{source}: 合并后日K未覆盖报价日期")
    return [], {"selected_source": source or "", "freshness_status": "missing"}, errors


def _load_bars_from_pool(
    db: Session,
    quote: dict[str, Any],
    settings: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    cached = _load_cached_bars(db, quote["code"], settings.market_kline_days)
    if len(cached) < 30:
        return cached, {"selected_source": "daily_bars_cache", "freshness_status": "missing"}, [
            f"daily_bars_cache: 本地日K不足30条，仅 {len(cached)} 条"
        ]
    if not _bars_cover_quote_date(cached, quote):
        return cached, {"selected_source": "daily_bars_cache", "freshness_status": "stale_cache"}, [
            "daily_bars_cache: 本地日K未覆盖报价日期"
        ]
    return cached, {"selected_source": "daily_bars_cache", "freshness_status": "local_pool_readonly"}, []


def _refresh_bars_for_quote(
    db: Session,
    quote: dict[str, Any],
    settings: Any,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    errors: list[str] = []
    sources = enabled_sources(db, "kline", _kline_sources(settings))
    if not sources:
        return [], "", ["kline: 所有日K数据源都已关闭"]

    # 增量拉取：查本地已存数据的最新日期，只拉缺失的天数
    start_date = _incremental_kline_start(db, quote["code"], settings)
    incremental_mode = start_date is not None

    for source in sources:
        start = time.perf_counter()
        try:
            bars = _fetch_kline(source, quote, settings, start_date=start_date)
            latency_ms = (time.perf_counter() - start) * 1000
            if len(bars) < 30 and not incremental_mode:
                _record_source_health(db, source, "kline", "empty", latency_ms=latency_ms)
                errors.append(f"{source}: 日K不足30条")
                continue
            _record_source_health(db, source, "kline", "success", latency_ms=latency_ms)
            return bars[-settings.market_kline_days :] if not incremental_mode else bars, source, errors
        except Exception as exc:  # noqa: BLE001 - returned as run evidence
            latency_ms = (time.perf_counter() - start) * 1000
            message = f"{source}: {exc}"
            errors.append(message)
            _record_source_health(db, source, "kline", "failure", error=str(exc), latency_ms=latency_ms)
    return [], "", errors


def _incremental_kline_start(db: Session, code: str, settings: Any) -> str | None:
    """查询本地已有日K的最新交易日，返回增量拉取的起始日期 (YYYYMMDD)。

    已有数据时，从最新日期 - buffer 天开始拉取（覆盖可能的复权修正）。
    无数据时返回 None（通知调用方按全量拉取）。
    """
    existing_count, existing_max_date = db.execute(
        select(func.count(), func.max(DailyKbar.trade_date)).where(DailyKbar.code == code)
    ).one()
    if int(existing_count or 0) < 30:
        return None
    if not existing_max_date:
        return None
    buffer_days = getattr(settings, "market_kline_incremental_buffer", 10)
    try:
        latest = datetime.strptime(_normalize_trade_date(existing_max_date), "%Y-%m-%d").date()
        start = latest - timedelta(days=buffer_days)
        return start.strftime("%Y%m%d")
    except ValueError:
        return None


def _readiness_example(
    quote: dict[str, Any],
    reason: str,
    bar_count: int,
    latest_bar_date: str | None,
    quote_date: str,
    required_bar_date: str | None = None,
) -> dict[str, Any]:
    return {
        "code": quote.get("code"),
        "name": quote.get("name"),
        "reason": reason,
        "bar_count": bar_count,
        "latest_bar_date": latest_bar_date,
        "quote_date": quote_date,
        "required_bar_date": required_bar_date or quote_date,
    }


def _backfill_latest_trade_date_bars(
    db: Session,
    quotes: list[dict[str, Any]],
    settings: Any,
    target_trade_date: str,
) -> int:
    from app.services.indicator_computer import compute_daily_bar_indicators, compute_daily_indicators

    target_iso = _normalize_trade_date(target_trade_date)
    if not target_iso or not quotes:
        return 0

    quote_bars_by_code: dict[str, dict[str, Any]] = {}
    for item in quotes:
        code = str(item.get("code") or "")
        quote_trade_date = _normalize_trade_date(item.get("trade_date"))
        price = _num(item.get("price"))
        if not code or quote_trade_date != target_iso or price <= 0:
            continue

        open_price = _num(item.get("open"))
        if open_price <= 0:
            open_price = price
        high_price = _num(item.get("high"))
        if high_price <= 0:
            high_price = max(open_price, price)
        low_price = _num(item.get("low"))
        if low_price <= 0:
            low_price = min(open_price, price)

        quote_bars_by_code[code] = {
            "code": code,
            "trade_date": target_iso,
            "open": max(open_price, 0.0),
            "high": max(high_price, open_price, price),
            "low": min(low_price, open_price, price),
            "close": price,
            "volume": _num(item.get("volume")),
            "amount": _num(item.get("amount_yi")) * 100000000,
            "pct_chg": _num(item.get("pct")),
            "turnover_rate": _num(item.get("turnover_rate")),
            "source": "tushare",
        }

    codes = sorted(quote_bars_by_code)
    if not codes:
        return 0

    historical = db.scalars(
        select(DailyKbar)
        .where(
            DailyKbar.code.in_(codes),
            DailyKbar.trade_date < target_iso,
        )
        .order_by(DailyKbar.code, DailyKbar.trade_date.asc())
    ).all()

    hist_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in historical:
        hist_by_code[row.code].append(
            {
                "code": row.code,
                "trade_date": _normalize_trade_date(row.trade_date),
                "open": float(row.open or 0),
                "high": float(row.high or 0),
                "low": float(row.low or 0),
                "close": float(row.close or 0),
                "volume": float(row.volume or 0),
                "amount": float(row.amount or 0),
                "pct_chg": float(row.pct_chg or 0),
                "turnover_rate": float(row.turnover_rate or 0),
            }
        )

    enriched_bars: list[dict[str, Any]] = []
    for code in codes:
        bar = quote_bars_by_code[code]
        code = bar["code"]
        combined = list(hist_by_code.get(code, []))
        combined.append(dict(bar))
        bar_indicators = compute_daily_bar_indicators(code, combined)
        daily_indicators = compute_daily_indicators(code, combined)
        enriched = dict(bar)
        enriched.update(bar_indicators.get(target_iso, {}))
        indicator_row = daily_indicators.get(target_iso, {})
        enriched.update(indicator_row)
        if "macd_bar" in indicator_row:
            enriched["macd_bar"] = indicator_row.get("macd_bar")
        enriched_bars.append(enriched)

    _persist_kbars(db, enriched_bars)
    db.commit()
    return len(enriched_bars)


def _summarize_kline_skip_reasons(kline_errors: dict[str, list[str]]) -> dict[str, int]:
    summary: Counter[str] = Counter()
    for errors in kline_errors.values():
        if not errors:
            summary["unknown"] += 1
            continue
        first = str(errors[0])
        if "不足30条" in first:
            summary["日K不足30条"] += 1
        elif "未覆盖报价日期" in first:
            summary["日K未覆盖报价日期"] += 1
        else:
            summary[first.split(":", 1)[0] or "其他"] += 1
    return dict(summary)


def _merge_quote_rows(
    code: str,
    rows: list[dict[str, Any]],
    source_priority: list[str],
    settings: Any,
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: _source_rank(row.get("source"), source_priority))
    base = ordered[0]
    result = {
        "code": code,
        "name": _first_non_empty(ordered, "name") or code,
        "exchange": base.get("exchange"),
        "industry": _first_non_empty(ordered, "industry", skip_values={"未分类"}) or "未分类",
        "is_st": any(bool(row.get("is_st")) for row in ordered),
        "trade_date": _latest_trade_date_from_quotes(ordered),
        "source": "unified_pool",
        "source_count": len({row.get("source") for row in ordered if row.get("source")}),
        "raw_sources": sorted({str(row.get("source")) for row in ordered if row.get("source")}),
        "quote_time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }

    conflicts: list[str] = []
    field_sources: dict[str, list[str]] = {}
    for field in NUMERIC_QUOTE_FIELDS:
        values = [
            (_num(row.get(field)), str(row.get("source") or "unknown"))
            for row in ordered
            if row.get(field) not in {None, "", "-"}
        ]
        values = [(value, source) for value, source in values if value != 0 or field in {"pct", "main_net_inflow_yi"}]
        if not values:
            result[field] = None if field in DERIVED_RETURN_FIELDS else 0.0
            continue
        numeric_values = [value for value, _source in values]
        merged = _robust_median(numeric_values)
        result[field] = round(merged, 4)
        field_sources[field] = [source for _value, source in values]
        if field in STRICT_CONSENSUS_FIELDS and len(numeric_values) >= 2:
            max_deviation = _max_relative_deviation(numeric_values, merged)
            if max_deviation > STRICT_CONSENSUS_FIELDS[field]:
                conflicts.append(f"{field}: 多源偏差 {round(max_deviation * 100, 2)}%")

    source_count = int(result["source_count"])
    quality = 100.0 - min(60.0, len(conflicts) * 12.0)
    result["quality_score"] = round(max(0.0, min(100.0, quality)), 2)
    result["freshness_status"] = "fresh"
    result["conflicts"] = conflicts
    result["source_trace"] = {
        "raw_sources": result["raw_sources"],
        "source_count": source_count,
        "field_sources": field_sources,
        "conflicts": conflicts,
        "merged_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    return result


def _enrich_industries(settings: Any, merged: list[dict[str, Any]]) -> None:
    from app.services.market_data import fetch_tushare_industries

    industry_map = fetch_tushare_industries(settings)
    if not industry_map:
        return
    enriched = 0
    for quote in merged:
        code = str(quote.get("code") or "")
        if not code:
            continue
        industry = industry_map.get(code)
        if industry and str(quote.get("industry")) == "未分类":
            quote["industry"] = industry
            enriched += 1
    if enriched:
        logger.info("行业富化: %d 只股票从 Tushare stock_basic 获取行业分类", enriched)


def _merge_quality(
    stock_quality: dict[str, Any] | None,
    quote: dict[str, Any],
    kline_trace: dict[str, Any],
) -> dict[str, Any]:
    quality = dict(stock_quality or {})
    missing = set(quality.get("missing_fields") or [])
    source_count = int(quote.get("source_count") or 0)
    quality["quote_source"] = "unified_pool"
    quality["quote_raw_sources"] = quote.get("raw_sources", [])
    quality["quote_source_count"] = source_count
    quality["quote_quality_score"] = quote.get("quality_score", 0)
    quality["quote_conflicts"] = quote.get("conflicts", [])
    quality["quote_cache_status"] = quote.get("freshness_status")
    quality["bars_source"] = kline_trace.get("selected_source")
    quality["kline_cache_status"] = kline_trace.get("freshness_status")
    if quote.get("conflicts"):
        missing.add("quote_conflict")
    if quote.get("freshness_status") == "stale_cache":
        missing.add("quote_freshness")
    quality["missing_fields"] = sorted(missing)
    return quality


def _persist_stock_quotes(
    db: Session,
    rows: list[dict[str, Any]],
    *,
    batch_size: int = STOCK_QUOTE_UPSERT_BATCH_SIZE,
) -> None:
    if not rows:
        return

    trade_date = _latest_trade_date_from_quotes(rows)
    seen_batch: set[tuple] = set()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    payload: list[dict[str, Any]] = []
    for row in rows:
        key = (row["code"], trade_date)
        if key in seen_batch:
            continue
        seen_batch.add(key)
        source = str(row.get("raw_sources", ["unknown"])[0]) if row.get("raw_sources") else "unknown"
        payload.append(
            {
                "code": row["code"],
                "trade_date": trade_date,
                "name": str(row.get("name") or ""),
                "price": float(row.get("price") or 0),
                "pct": float(row.get("pct") or 0),
                "volume": float(row.get("volume") or 0),
                "amount_yi": float(row.get("amount_yi") or 0),
                "turnover_rate": float(row.get("turnover_rate") or 0),
                "pe_ttm": float(row.get("pe_ttm")) if row.get("pe_ttm") is not None else None,
                "total_mv_yi": float(row.get("total_mv_yi")) if row.get("total_mv_yi") is not None else None,
                "free_mv_yi": float(row.get("free_mv_yi")) if row.get("free_mv_yi") is not None else None,
                "chg60": float(row.get("chg60")) if row.get("chg60") is not None else None,
                "high": float(row.get("high")) if row.get("high") is not None else None,
                "low": float(row.get("low")) if row.get("low") is not None else None,
                "open": float(row.get("open")) if row.get("open") is not None else None,
                "preclose": float(row.get("preclose")) if row.get("preclose") is not None else None,
                "industry": row.get("industry") or "",
                "source": source,
                "source_count": int(row.get("source_count") or 0),
                "quality_score": float(row.get("quality_score") or 0),
                "freshness_status": str(row.get("freshness_status") or "fresh"),
                "snapped_at": now,
            }
        )
    if payload:
        db.execute(
            update(StockQuote)
            .where(StockQuote.freshness_status != "inactive")
            .values(freshness_status="inactive")
        )
        size = max(1, int(batch_size or STOCK_QUOTE_UPSERT_BATCH_SIZE))
        for start in range(0, len(payload), size):
            chunk = payload[start:start + size]
            stmt = mysql_insert(StockQuote).values(chunk)
            db.execute(
                stmt.on_duplicate_key_update(
                    name=stmt.inserted.name,
                    price=stmt.inserted.price,
                    pct=stmt.inserted.pct,
                    volume=stmt.inserted.volume,
                    amount_yi=stmt.inserted.amount_yi,
                    turnover_rate=stmt.inserted.turnover_rate,
                    pe_ttm=stmt.inserted.pe_ttm,
                    total_mv_yi=stmt.inserted.total_mv_yi,
                    free_mv_yi=stmt.inserted.free_mv_yi,
                    chg60=stmt.inserted.chg60,
                    high=stmt.inserted.high,
                    low=stmt.inserted.low,
                    open=stmt.inserted.open,
                    preclose=stmt.inserted.preclose,
                    industry=stmt.inserted.industry,
                    source=stmt.inserted.source,
                    source_count=stmt.inserted.source_count,
                    quality_score=stmt.inserted.quality_score,
                    freshness_status=stmt.inserted.freshness_status,
                    snapped_at=stmt.inserted.snapped_at,
                )
            )


def _persist_kbars(db: Session, bars: list[dict[str, Any]]) -> None:
    if not bars:
        return
    latest_allowed_date = _latest_allowed_bar_date()
    seen_batch: set[tuple] = set()
    rows: list[dict[str, Any]] = []
    for bar in bars:
        code = str(bar.get("code") or "")
        trade_date = _normalize_trade_date(bar.get("trade_date"))
        if not code or not trade_date:
            continue
        if trade_date > latest_allowed_date:
            continue
        key = (code, trade_date)
        if key in seen_batch:
            continue
        seen_batch.add(key)
        rows.append({
            "code": code,
            "trade_date": trade_date,
            "open": float(bar.get("open") or 0),
            "high": float(bar.get("high") or 0),
            "low": float(bar.get("low") or 0),
            "close": float(bar.get("close") or 0),
            "volume": float(bar.get("volume") or 0),
            "amount": float(bar.get("amount") or 0),
            "pct_chg": float(bar.get("pct_chg") or 0),
            "turnover_rate": float(bar.get("turnover_rate") or 0),
            "ma5": _float_or_none(bar.get("ma5")),
            "ma10": _float_or_none(bar.get("ma10")),
            "ma20": _float_or_none(bar.get("ma20")),
            "ma60": _float_or_none(bar.get("ma60")),
            "ma250": _float_or_none(bar.get("ma250")),
            "macd_dif": _float_or_none(bar.get("macd_dif")),
            "macd_dea": _float_or_none(bar.get("macd_dea")),
            "macd_bar": _float_or_none(bar.get("macd_bar", bar.get("macd"))),
            "kdj_k": _float_or_none(bar.get("kdj_k")),
            "kdj_d": _float_or_none(bar.get("kdj_d")),
            "kdj_j": _float_or_none(bar.get("kdj_j")),
            "rsi_6": _float_or_none(bar.get("rsi_6")),
            "rsi_12": _float_or_none(bar.get("rsi_12")),
            "rsi_24": _float_or_none(bar.get("rsi_24")),
            "boll_upper": _float_or_none(bar.get("boll_upper")),
            "boll_mid": _float_or_none(bar.get("boll_mid")),
            "boll_lower": _float_or_none(bar.get("boll_lower")),
            "cci": _float_or_none(bar.get("cci")),
            "bias_5": _float_or_none(bar.get("bias_ma5", bar.get("bias_5"))),
            "bias_10": _float_or_none(bar.get("bias_ma10", bar.get("bias_10"))),
            "bias_20": _float_or_none(bar.get("bias_ma20", bar.get("bias_20"))),
            "vol_ratio_5": _float_or_none(bar.get("volume_ratio_5d", bar.get("vol_ratio_5"))),
            "vol_ratio_20": _float_or_none(bar.get("vol_ratio_20")),
            "breakout20": int(bar.get("breakout20") or 0),
            "breakout60": int(bar.get("breakout60") or 0),
            "max_dd20": _float_or_none(bar.get("max_dd20")),
            "max_dd60": _float_or_none(bar.get("max_dd60")),
            "upper_shadow_count20": int(bar.get("upper_shadow_count20") or 0),
            "source": bar.get("source", "tushare"),
        })
    if not rows:
        return

    stmt = mysql_insert(DailyKbar).values(rows)
    db.execute(stmt.on_duplicate_key_update(
        open=stmt.inserted.open,
        high=stmt.inserted.high,
        low=stmt.inserted.low,
        close=stmt.inserted.close,
        volume=stmt.inserted.volume,
        amount=stmt.inserted.amount,
        pct_chg=stmt.inserted.pct_chg,
        turnover_rate=stmt.inserted.turnover_rate,
        ma5=stmt.inserted.ma5,
        ma10=stmt.inserted.ma10,
        ma20=stmt.inserted.ma20,
        ma60=stmt.inserted.ma60,
        ma250=stmt.inserted.ma250,
        macd_dif=stmt.inserted.macd_dif,
        macd_dea=stmt.inserted.macd_dea,
        macd_bar=stmt.inserted.macd_bar,
        kdj_k=stmt.inserted.kdj_k,
        kdj_d=stmt.inserted.kdj_d,
        kdj_j=stmt.inserted.kdj_j,
        rsi_6=stmt.inserted.rsi_6,
        rsi_12=stmt.inserted.rsi_12,
        rsi_24=stmt.inserted.rsi_24,
        boll_upper=stmt.inserted.boll_upper,
        boll_mid=stmt.inserted.boll_mid,
        boll_lower=stmt.inserted.boll_lower,
        cci=stmt.inserted.cci,
        bias_5=stmt.inserted.bias_5,
        bias_10=stmt.inserted.bias_10,
        bias_20=stmt.inserted.bias_20,
        vol_ratio_5=stmt.inserted.vol_ratio_5,
        vol_ratio_20=stmt.inserted.vol_ratio_20,
        breakout20=stmt.inserted.breakout20,
        breakout60=stmt.inserted.breakout60,
        max_dd20=stmt.inserted.max_dd20,
        max_dd60=stmt.inserted.max_dd60,
        upper_shadow_count20=stmt.inserted.upper_shadow_count20,
    ))


def _load_cached_bars_batch(
    db: Session, codes: list[str], limit: int
) -> dict[str, list[dict[str, Any]]]:
    """批量加载日K（DailyKbar 单表，无需 JOIN）。"""
    if not codes:
        return {}
    latest_allowed_date = _latest_allowed_bar_date()

    all_rows = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code.in_(codes), DailyKbar.trade_date <= latest_allowed_date)
        .order_by(DailyKbar.code, DailyKbar.trade_date.desc())
    ).all()

    bars_by_code: dict[str, list] = defaultdict(list)
    for row in all_rows:
        if len(bars_by_code[row.code]) >= limit:
            continue
        bars_by_code[row.code].append(row)

    result: dict[str, list[dict[str, Any]]] = {}
    for code in codes:
        rows = sorted(bars_by_code.get(code, []), key=lambda r: r.trade_date)
        bars = []
        for row in rows:
            bar = {
                "code": row.code,
                "trade_date": _normalize_trade_date(row.trade_date),
                "open": float(row.open or 0),
                "high": float(row.high or 0),
                "low": float(row.low or 0),
                "close": float(row.close or 0),
                "volume": float(row.volume or 0),
                "amount": float(row.amount or 0),
                "pct_chg": float(row.pct_chg or 0),
                "turnover_rate": float(row.turnover_rate or 0),
                "ma5": row.ma5,
                "ma10": row.ma10,
                "ma20": row.ma20,
                "ma60": row.ma60,
                "volume_ratio_5d": row.vol_ratio_5,
                # 筹码分布字段（Tushare 真实数据）
                "chip_winner_rate": row.chip_winner_rate,
                "chip_weight_avg": row.chip_weight_avg,
                "chip_cost_15": row.chip_cost_15,
                "chip_cost_50": row.chip_cost_50,
                "chip_cost_85": row.chip_cost_85,
                "holder_pct": row.holder_pct,
                "profit_efficiency": row.profit_efficiency,
                # 筹码集中度（前移计算，供 prefilter 阶段使用，避免 chip-single-peak 策略 prefilter 死锁）
                "chip_concentration": round((float(row.chip_cost_85) - float(row.chip_cost_15)) / float(row.chip_cost_15) * 100, 2) if row.chip_cost_15 and float(row.chip_cost_15) > 0 and row.chip_cost_85 else None,
                "_indicator": {
                    "rsi_6": row.rsi_6,
                    "rsi_12": row.rsi_12,
                    "rsi_24": row.rsi_24,
                    "macd_dif": row.macd_dif,
                    "macd_dea": row.macd_dea,
                    "macd_bar": row.macd_bar,
                    "bias_ma5": row.bias_5,
                    "bias_ma10": row.bias_10,
                    "bias_ma20": row.bias_20,
                    "breakout20": row.breakout20,
                    "breakout60": row.breakout60,
                    "upper_shadow_count20": row.upper_shadow_count20,
                    "max_dd20": row.max_dd20,
                    "max_dd60": row.max_dd60,
                },
            }
            bars.append(bar)
        result[code] = bars
    return result


def _load_cached_bars(db: Session, code: str, limit: int) -> list[dict[str, Any]]:
    latest_allowed_date = _latest_allowed_bar_date()
    rows = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code == code, DailyKbar.trade_date <= latest_allowed_date)
        .order_by(DailyKbar.trade_date.desc())
        .limit(limit)
    ).all()

    result = []
    for row in reversed(rows):
        bar = {
            "code": row.code,
            "trade_date": _normalize_trade_date(row.trade_date),
            "open": float(row.open or 0),
            "high": float(row.high or 0),
            "low": float(row.low or 0),
            "close": float(row.close or 0),
            "volume": float(row.volume or 0),
            "amount": float(row.amount or 0),
            "pct_chg": float(row.pct_chg or 0),
            "turnover_rate": float(row.turnover_rate or 0),
            "ma5": row.ma5,
            "ma10": row.ma10,
            "ma20": row.ma20,
            "ma60": row.ma60,
            "volume_ratio_5d": row.vol_ratio_5,
            # 筹码分布字段（Tushare 真实数据）
            "chip_winner_rate": row.chip_winner_rate,
            "chip_weight_avg": row.chip_weight_avg,
            "chip_cost_15": row.chip_cost_15,
            "chip_cost_50": row.chip_cost_50,
            "chip_cost_85": row.chip_cost_85,
            "holder_pct": row.holder_pct,
            "profit_efficiency": row.profit_efficiency,
            # 筹码集中度（前移计算，供 prefilter 阶段使用，避免 chip-single-peak 策略 prefilter 死锁）
            "chip_concentration": round((float(row.chip_cost_85) - float(row.chip_cost_15)) / float(row.chip_cost_15) * 100, 2) if row.chip_cost_15 and float(row.chip_cost_15) > 0 and row.chip_cost_85 else None,
            "_indicator": {
                "rsi_6": row.rsi_6,
                "rsi_12": row.rsi_12,
                "rsi_24": row.rsi_24,
                "macd_dif": row.macd_dif,
                "macd_dea": row.macd_dea,
                "macd_bar": row.macd_bar,
                "bias_ma5": row.bias_5,
                "bias_ma10": row.bias_10,
                "bias_ma20": row.bias_20,
                "breakout20": row.breakout20,
                "breakout60": row.breakout60,
                "upper_shadow_count20": row.upper_shadow_count20,
                "max_dd20": row.max_dd20,
                "max_dd60": row.max_dd60,
            },
        }
        result.append(bar)
    return result


def _record_source_health(
    db: Session,
    source: str,
    source_type: str,
    status: str,
    error: str | None = None,
    latency_ms: float = 0.0,
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    latency = round(float(latency_ms), 2)
    row = db.get(DataSourceHealth, {"source": source, "source_type": source_type})
    if row is None:
        row = DataSourceHealth(
            source=source,
            source_type=source_type,
            success_count=0,
            failure_count=0,
            empty_count=0,
            latency_ms=latency,
            updated_at=now,
        )
        db.add(row)

    if status == "success":
        row.success_count = int(row.success_count or 0) + 1
        row.last_success_at = now
        row.last_error = None
    elif status == "empty":
        row.empty_count = int(row.empty_count or 0) + 1
        row.last_failure_at = now
        row.last_error = error or "empty result"
    else:
        row.failure_count = int(row.failure_count or 0) + 1
        row.last_failure_at = now
        row.last_error = error or "unknown"
    row.latency_ms = latency
    row.updated_at = now

def _quote_meta(status: str, quotes: list[dict[str, Any]], errors: list[str] | None = None) -> dict[str, Any]:
    fetched_times = [
        datetime.fromisoformat(str(item.get("fetched_at")))
        for item in quotes
        if item.get("fetched_at") and "T" in str(item.get("fetched_at"))
    ]
    newest = max(fetched_times) if fetched_times else datetime.now(timezone.utc).replace(tzinfo=None)
    sources = sorted({source for quote in quotes for source in quote.get("raw_sources", [])})
    source_counts = [int(quote.get("source_count") or 0) for quote in quotes]
    return {
        "status": status,
        "count": len(quotes),
        "sources": sources,
        "source_count": round(statistics.fmean(source_counts), 2) if source_counts else 0,
        "cache_age_seconds": max(0, int((datetime.now(timezone.utc).replace(tzinfo=None) - newest).total_seconds())),
        "errors": errors or [],
    }


def _load_cached_bars_summary(
    db: Session, codes: list[str], min_bar_count: int
) -> dict[str, dict[str, Any]]:
    """Batch load bar summary for readiness check: count + latest trade_date per code.

    当候选码数量接近全市场总量时，全量 GROUP BY + Python 过滤比
    IN 子句逐码查找快 3x（220ms vs 669ms @ 5000 codes）。
    """
    if not codes:
        return {}
    code_set = set(codes)
    rows = db.execute(
        select(
            DailyKbar.code,
            func.count().label("bar_count"),
            func.max(DailyKbar.trade_date).label("latest_trade_date"),
        )
        .group_by(DailyKbar.code)
    ).all()
    return {
        row.code: {
            "bar_count": int(row.bar_count or 0),
            "latest_trade_date": _normalize_trade_date(row.latest_trade_date),
        }
        for row in rows
        if row.code in code_set
    }


def _load_history_maturity_map(db: Session, codes: list[str]) -> dict[str, dict[str, str | None]]:
    normalized_codes = [str(code or "").strip() for code in codes if str(code or "").strip()]
    if not normalized_codes:
        return {}
    code_set = set(normalized_codes)
    result: dict[str, dict[str, str | None]] = {}
    first_bar_rows = db.execute(
        select(
            DailyKbar.code.label("code"),
            func.min(DailyKbar.trade_date).label("first_bar_date"),
        )
        .group_by(DailyKbar.code)
    ).all()
    for row in first_bar_rows:
        if row.code in code_set:
            result[str(row.code)] = {
                "first_bar_date": _normalize_trade_date(row.first_bar_date),
                "list_date": None,
            }
    stock_rows = db.execute(
        select(Stock.code, Stock.list_date).where(Stock.code.in_(normalized_codes))
    ).all()
    for row in stock_rows:
        item = result.setdefault(str(row.code), {"first_bar_date": None, "list_date": None})
        item["list_date"] = _normalize_trade_date(row.list_date)
    return result


def _is_natural_short_history(
    *,
    bar_count: int,
    latest_bar_date: str | None,
    quote_date: str,
    first_bar_date: str | None,
    list_date: str | None,
) -> bool:
    return bool(
        0 < int(bar_count or 0)
        and latest_bar_date
        and quote_date
        and latest_bar_date >= quote_date
        and first_bar_date
        and list_date
        and first_bar_date <= list_date
    )


def _bars_cover_quote_date(bars: list[dict[str, Any]], quote: dict[str, Any]) -> bool:
    quote_date = _required_bar_date_for_quote(quote)
    latest = max(_normalize_trade_date(bar.get("trade_date")) for bar in bars)
    return latest >= quote_date


def _latest_allowed_bar_date(today: date | None = None) -> str:
    if today is None:
        now = datetime.now()
        today = now.date()
        if today.weekday() < 5 and now.hour < 16:
            today = _previous_weekday(today)
    if today.weekday() == 5:
        return (today - timedelta(days=1)).isoformat()
    if today.weekday() == 6:
        return (today - timedelta(days=2)).isoformat()
    return today.isoformat()


def _previous_weekday(day: date) -> date:
    current = day - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def _source_rank(source: Any, source_priority: list[str]) -> int:
    try:
        return source_priority.index(str(source))
    except ValueError:
        return len(source_priority) + 1


def _first_non_empty(rows: list[dict[str, Any]], field: str, skip_values: set[str] | None = None) -> str:
    skip_values = skip_values or set()
    for row in rows:
        value = str(row.get(field) or "").strip()
        if value and value not in skip_values:
            return value
    return ""


def _robust_median(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def _float_or_none(value: Any) -> float | None:
    if value in {None, "", "-"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _max_relative_deviation(values: list[float], center: float) -> float:
    denominator = max(abs(center), 0.0001)
    return max(abs(value - center) / denominator for value in values)


# ── 辅助数据池：刷新入口 ───────────────────────────────────────────────

def refresh_limit_up_board(db: Session, trade_date: str | None = None) -> list[dict[str, Any]]:
    """刷新涨停板数据。"""
    settings = get_settings()
    if not settings.enable_limit_up_fetch:
        return []
    sources = enabled_sources(db, "limit_up", _limit_up_sources(settings))
    trade_date = _resolve_snapshot_trade_date(db, trade_date)
    return _refresh_aux_data(
        db, "limit_up", sources, settings,
        fetcher=lambda source, _settings: _fetch_limit_up_board(source, _settings, trade_date=trade_date),
        normalized_persister=_persist_normalized_limit_up_board,
    )


def refresh_concept_sectors(db: Session, trade_date: str | None = None) -> list[dict[str, Any]]:
    """刷新概念板块数据。"""
    settings = get_settings()
    if not settings.enable_concept_sector_fetch:
        return []
    sources = enabled_sources(db, "concept_sector", _concept_sector_sources(settings))
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    result = _refresh_aux_data(
        db, "concept_sector", sources, settings,
        fetcher=lambda source, _settings: _fetch_concept_sectors(source, _settings, trade_date=target_trade_date),
        normalized_persister=_persist_normalized_concept_sector,
    )
    # 额外持久化热门板块的成分股（Tushare）
    from app.services.tushare_client import fetch_concept_members

    # 检查快照交易日是否已有成分股数据，有则跳过
    existing_count = db.scalar(
        select(func.count()).select_from(ConceptSectorStock).where(
            ConceptSectorStock.trade_date == target_trade_date
        )
    ) or 0
    if existing_count > 0:
        logger.info("概念板块成分股已覆盖 %s，跳过拉取", target_trade_date)
        return result

    top_sectors = [s for s in result if s.get("heat_rank", 999) <= 60]
    if not top_sectors:
        return result

    all_stocks: list[dict[str, Any]] = []
    sector_codes = [str(s.get("sector_code") or "") for s in top_sectors]
    sector_codes = [c for c in sector_codes if c]

    timeout_seconds = max(1.0, float(getattr(settings, "concept_member_fetch_timeout_seconds", 30.0) or 30.0))
    pool = ThreadPoolExecutor(max_workers=6)
    futures = {pool.submit(fetch_concept_members, sc, target_trade_date): sc for sc in sector_codes}
    processed: set[Any] = set()
    try:
        for future in as_completed(futures, timeout=timeout_seconds):
            processed.add(future)
            try:
                stocks = future.result()
                if stocks:
                    all_stocks.extend(stocks)
            except Exception:
                pass
    except TimeoutError:
        logger.warning(
            "概念板块成分股拉取超过 %.1fs，已降级使用已完成结果：%d/%d 个板块",
            timeout_seconds,
            len(processed),
            len(futures),
        )
    finally:
        for future in futures:
            if future not in processed:
                future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)

    if all_stocks:
        _persist_concept_sector_stocks(db, all_stocks)
        db.commit()
        logger.info("概念板块成分股已持久化 %d 条，覆盖 %d 个板块", len(all_stocks), len(sector_codes))
    return result


def refresh_dragon_tiger(db: Session, trade_date: str | None = None) -> list[dict[str, Any]]:
    """刷新龙虎榜数据。"""
    settings = get_settings()
    if not settings.enable_dragon_tiger_fetch:
        return []
    sources = enabled_sources(db, "dragon_tiger", _dragon_tiger_sources(settings))
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    return _refresh_aux_data(
        db, "dragon_tiger", sources, settings,
        fetcher=lambda source, _settings: [
            row
            for row in _fetch_dragon_tiger(source, _settings, trade_date=target_trade_date)
            if _normalize_trade_date(row.get("trade_date")) == target_trade_date
        ],
        normalized_persister=_persist_normalized_dragon_tiger,
    )


def refresh_capital_flows_for_codes(db: Session, codes: list[str], trade_date: str | None = None) -> dict[str, dict[str, Any]]:
    """刷新资金流向数据（批量模式优先，fallback 到逐股票）。"""
    settings = get_settings()
    if not settings.enable_capital_flow_fetch:
        return {}
    sources = enabled_sources(db, "capital_flow", _capital_flow_sources(settings))
    if not sources:
        return {}
    source = sources[0]
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    result: dict[str, dict[str, Any]] = {}

    # 批量模式：一次 API 拉全市场，过滤目标 codes（~12x 快于逐股票）
    batch_data = _fetch_capital_flow_batch(source, codes, settings, trade_date=target_trade_date)
    if batch_data:
        for code, data in batch_data.items():
            data["trade_date"] = _normalize_trade_date(data.get("trade_date"))
            if target_trade_date and data["trade_date"] != target_trade_date:
                continue
            _persist_normalized_capital_flow(db, data)
            result[code] = data
        db.commit()
        return result

    # Fallback：逐股票调用
    for code in codes:
        try:
            data = _fetch_capital_flow(source, code, settings, trade_date=target_trade_date)
            if data:
                data["trade_date"] = _normalize_trade_date(data.get("trade_date"))
                if target_trade_date and data["trade_date"] != target_trade_date:
                    continue
                _persist_normalized_capital_flow(db, data)
                result[code] = data
        except Exception:
            pass
    db.commit()
    return result


class NewsRefreshResult(dict[str, list[dict[str, Any]]]):
    def __init__(self, rows: dict[str, list[dict[str, Any]]], checked_codes: list[str]) -> None:
        super().__init__(rows)
        self.checked_codes = checked_codes


def refresh_news_for_codes(db: Session, codes: list[str], trade_date: str | None = None) -> NewsRefreshResult:
    """按股刷新新闻公告数据。"""
    settings = get_settings()
    if not settings.enable_news_fetch:
        return NewsRefreshResult({}, [])
    sources = enabled_sources(db, "news", _news_sources(settings))
    if not sources:
        return NewsRefreshResult({}, [])
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    checked_codes: list[str] = []
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    ttl_days = _date_window_days(settings.news_cache_ttl_seconds, minimum_days=7)
    latest_allowed = date.fromisoformat(target_trade_date)
    earliest_allowed = latest_allowed - timedelta(days=ttl_days)
    for code in codes:
        code_checked = False
        seen_articles: set[tuple[str, str]] = set()
        try:
            normalized_items = []
            for source in sources:
                try:
                    items = _fetch_news(source, code, settings, trade_date=target_trade_date)
                    code_checked = True
                except Exception:
                    continue
                for item in items:
                    announce_date = _normalize_trade_date(item.get("announce_date"))
                    if target_trade_date:
                        if not announce_date:
                            continue
                        announce_day = date.fromisoformat(announce_date)
                        if announce_day > latest_allowed or announce_day < earliest_allowed:
                            continue
                    article_key = (
                        str(item.get("source") or source or "unknown"),
                        str(item.get("article_id") or ""),
                    )
                    if not article_key[1] or article_key in seen_articles:
                        continue
                    seen_articles.add(article_key)
                    normalized_items.append({**item, "source": article_key[0], "announce_date": announce_date})
            if code_checked:
                checked_codes.append(code)
            if not normalized_items:
                continue
            _persist_normalized_news(db, normalized_items)
            result[code] = normalized_items
        except Exception:
            pass
    db.commit()
    return NewsRefreshResult(dict(result), checked_codes)


# ── 辅助数据池：通用刷新模式 ───────────────────────────────────────────

def _refresh_aux_data(
    db: Session,
    source_type: str,
    sources: list[str],
    settings: Any,
    fetcher: Any,
    normalized_persister: Any,
) -> list[dict[str, Any]]:
    if not sources:
        return []
    all_rows: list[dict[str, Any]] = []
    for source in sources:
        start = time.perf_counter()
        try:
            rows = fetcher(source, settings)
            latency_ms = (time.perf_counter() - start) * 1000
            if not rows:
                _record_source_health(db, source, source_type, "empty", latency_ms=latency_ms)
                continue
            _record_source_health(db, source, source_type, "success", latency_ms=latency_ms)
            if all_rows:
                all_rows.extend(rows)
            else:
                all_rows = rows
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            _record_source_health(db, source, source_type, "failure", error=str(exc), latency_ms=latency_ms)
    if all_rows:
        normalized_persister(db, all_rows)
        db.commit()
    return all_rows


# ── 辅助数据池：标准化数据持久化 ────────────────────────────────────────

def _persist_normalized_limit_up_board(db: Session, rows: list[dict[str, Any]]) -> None:
    seen_batch: set[tuple] = set()
    for row in rows:
        code = str(row.get("code") or "")
        trade_date = str(row.get("trade_date") or "")
        if not code or not trade_date:
            continue
        key = (code, trade_date)
        if key in seen_batch:
            continue
        seen_batch.add(key)
        existing = db.scalar(
            select(LimitUpBoard).where(LimitUpBoard.code == code, LimitUpBoard.trade_date == trade_date)
        )
        if existing is None:
            db.add(LimitUpBoard(**{k: v for k, v in row.items() if hasattr(LimitUpBoard, k)}))
        else:
            for key_name, value in row.items():
                if hasattr(existing, key_name) and key_name not in {"id", "created_at"}:
                    setattr(existing, key_name, value)
            existing.created_at = datetime.now(timezone.utc).replace(tzinfo=None)


def _persist_normalized_concept_sector(db: Session, rows: list[dict[str, Any]]) -> None:
    seen_batch: set[tuple] = set()
    for row in rows:
        sector_code = str(row.get("sector_code") or "")
        trade_date = str(row.get("trade_date") or "")
        if not sector_code or not trade_date:
            continue
        key = (sector_code, trade_date)
        if key in seen_batch:
            continue
        seen_batch.add(key)
        existing = db.scalar(
            select(ConceptSector).where(ConceptSector.sector_code == sector_code, ConceptSector.trade_date == trade_date)
        )
        if existing is None:
            db.add(ConceptSector(**{k: v for k, v in row.items() if hasattr(ConceptSector, k)}))
        else:
            for key_name, value in row.items():
                if hasattr(existing, key_name) and key_name not in {"id", "created_at"}:
                    setattr(existing, key_name, value)
            existing.created_at = datetime.now(timezone.utc).replace(tzinfo=None)


def _persist_concept_sector_stocks(db: Session, rows: list[dict[str, Any]]) -> None:
    seen_batch: set[tuple] = set()
    for row in rows:
        sector_code = str(row.get("sector_code") or "")
        code = str(row.get("code") or "")
        trade_date = str(row.get("trade_date") or "")
        if not sector_code or not code or not trade_date:
            continue
        key = (sector_code, code, trade_date)
        if key in seen_batch:
            continue
        seen_batch.add(key)
        existing = db.scalar(
            select(ConceptSectorStock).where(
                ConceptSectorStock.sector_code == sector_code,
                ConceptSectorStock.code == code,
                ConceptSectorStock.trade_date == trade_date,
            )
        )
        if existing is None:
            db.add(ConceptSectorStock(**{k: v for k, v in row.items() if hasattr(ConceptSectorStock, k)}))
        else:
            for key_name, value in row.items():
                if hasattr(existing, key_name) and key_name not in {"id", "created_at"}:
                    setattr(existing, key_name, value)
            existing.created_at = datetime.now(timezone.utc).replace(tzinfo=None)


def _persist_normalized_capital_flow(db: Session, data: dict[str, Any]) -> None:
    code = str(data.get("code") or "")
    trade_date = _normalize_trade_date(data.get("trade_date"))
    if not code or not trade_date:
        return
    existing = db.scalar(
        select(CapitalFlow).where(CapitalFlow.code == code, CapitalFlow.trade_date == trade_date)
    )
    if existing is None:
        db.add(CapitalFlow(**{k: v for k, v in data.items() if hasattr(CapitalFlow, k)}))
    else:
        for key, value in data.items():
            if hasattr(existing, key) and key not in {"id", "created_at"}:
                setattr(existing, key, value)
        existing.created_at = datetime.now(timezone.utc).replace(tzinfo=None)


def _persist_normalized_dragon_tiger(db: Session, rows: list[dict[str, Any]]) -> None:
    seen_batch: set[tuple] = set()
    for row in rows:
        code = str(row.get("code") or "")
        trade_date = _normalize_trade_date(row.get("trade_date"))
        if not code or not trade_date:
            continue
        key = (code, trade_date)
        if key in seen_batch:
            continue
        seen_batch.add(key)
        existing = db.scalar(
            select(DragonTiger).where(DragonTiger.code == code, DragonTiger.trade_date == trade_date)
        )
        if existing is None:
            db.add(DragonTiger(**{k: v for k, v in row.items() if hasattr(DragonTiger, k)}))
        else:
            for key_name, value in row.items():
                if hasattr(existing, key_name) and key_name not in {"id", "created_at"}:
                    setattr(existing, key_name, value)
            existing.created_at = datetime.now(timezone.utc).replace(tzinfo=None)


def _persist_normalized_news(db: Session, rows: list[dict[str, Any]]) -> None:
    seen_batch: set[tuple] = set()
    for row in rows:
        article_id = str(row.get("article_id") or "")
        source = str(row.get("source") or "unknown")
        if not article_id:
            continue
        key = (source, article_id)
        if key in seen_batch:
            continue
        seen_batch.add(key)
        row_with_source = {**row, "source": source, "announce_date": _normalize_trade_date(row.get("announce_date"))}
        existing = db.scalar(
            select(NewsAnnouncement).where(NewsAnnouncement.source == source, NewsAnnouncement.article_id == article_id)
        )
        if existing is None:
            try:
                db.add(NewsAnnouncement(**{k: v for k, v in row_with_source.items() if hasattr(NewsAnnouncement, k)}))
                db.flush()
            except Exception:
                db.rollback()
        else:
            for key_name, value in row_with_source.items():
                if hasattr(existing, key_name) and key_name not in {"id", "created_at"}:
                    setattr(existing, key_name, value)
            existing.created_at = datetime.now(timezone.utc).replace(tzinfo=None)


# ── 辅助数据池：缓存读取 ───────────────────────────────────────────────

def _load_cached_limit_up_board(
    db: Session,
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> list[dict[str, Any]]:
    target_trade_date = _normalize_trade_date(trade_date)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds) if max_age_seconds else None
    stmt = select(LimitUpBoard).order_by(LimitUpBoard.limit_times.desc())
    # limit_type='U' 涨停 / 'D' 跌停；涨停策略只关心涨停，排除跌停股被误标 is_limit_up
    stmt = stmt.where(LimitUpBoard.limit_type == "U")
    if target_trade_date:
        stmt = stmt.where(LimitUpBoard.trade_date == target_trade_date)
    if cutoff is not None:
        stmt = stmt.where(LimitUpBoard.created_at >= cutoff)
    rows = db.scalars(stmt).all()
    return [
        {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    ]


def _load_cached_broken_limit_up(
    db: Session,
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, dict[str, Any]]:
    """加载炸板数据（limit_type='Z'）。返回 {code: row} 字典。

    炸板=曾涨停后打开，炸板率高=市场情绪转弱，用于短线情绪温度计。
    """
    target_trade_date = _normalize_trade_date(trade_date)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds) if max_age_seconds else None
    stmt = select(LimitUpBoard).where(LimitUpBoard.limit_type == "Z")
    if target_trade_date:
        stmt = stmt.where(LimitUpBoard.trade_date == target_trade_date)
    if cutoff is not None:
        stmt = stmt.where(LimitUpBoard.created_at >= cutoff)
    rows = db.scalars(stmt).all()
    return {
        str(r.code): {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    }


def _load_cached_concept_sectors(
    db: Session,
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> list[dict[str, Any]]:
    target_trade_date = _normalize_trade_date(trade_date)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds) if max_age_seconds else None
    stmt = select(ConceptSector).order_by(ConceptSector.heat_rank)
    if target_trade_date:
        stmt = stmt.where(ConceptSector.trade_date == target_trade_date)
    if cutoff is not None:
        stmt = stmt.where(ConceptSector.created_at >= cutoff)
    rows = db.scalars(stmt).all()
    return [
        {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    ]


def _load_cached_capital_flows(
    db: Session,
    codes: list[str],
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, dict[str, Any]]:
    target_trade_date = _normalize_trade_date(trade_date)
    stmt = select(CapitalFlow).where(CapitalFlow.code.in_(codes))
    if target_trade_date:
        stmt = stmt.where(CapitalFlow.trade_date == target_trade_date)
    if max_age_seconds:
        stmt = stmt.where(CapitalFlow.created_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds))
    rows = db.scalars(stmt).all()
    return {
        r.code: {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    }


def _load_cached_dragon_tigers(
    db: Session,
    codes: list[str],
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, dict[str, Any]]:
    target_trade_date = _normalize_trade_date(trade_date)
    stmt = select(DragonTiger).where(DragonTiger.code.in_(codes))
    if target_trade_date:
        stmt = stmt.where(DragonTiger.trade_date == target_trade_date)
    if max_age_seconds:
        stmt = stmt.where(DragonTiger.created_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds))
    rows = db.scalars(stmt).all()
    return {
        r.code: {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    }


def _load_cached_news(
    db: Session,
    codes: list[str],
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    target_trade_date = _normalize_trade_date(trade_date)
    stmt = select(NewsAnnouncement).where(NewsAnnouncement.code.in_(codes))
    if target_trade_date:
        ttl_days = _date_window_days(max_age_seconds, minimum_days=2)
        latest_allowed = date.fromisoformat(target_trade_date)
        earliest_allowed = latest_allowed - timedelta(days=ttl_days)
        stmt = stmt.where(
            NewsAnnouncement.announce_date <= latest_allowed.isoformat(),
            NewsAnnouncement.announce_date >= earliest_allowed.isoformat(),
        )
    if max_age_seconds:
        stmt = stmt.where(NewsAnnouncement.created_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds))
    rows = db.scalars(stmt).all()
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        result[r.code].append({c.name: getattr(r, c.name) for c in r.__table__.columns})
    for code, items in result.items():
        items.sort(key=lambda item: str(item.get("announce_date") or ""), reverse=True)
    return dict(result)


def _load_concept_sectors_for_stocks(
    db: Session,
    codes: list[str],
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """根据成分股表返回每只股票所属的概念板块。"""
    target_trade_date = _normalize_trade_date(trade_date)
    stmt = select(ConceptSectorStock).where(ConceptSectorStock.code.in_(codes))
    if target_trade_date:
        stmt = stmt.where(ConceptSectorStock.trade_date == target_trade_date)
    cutoff = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds)
        if max_age_seconds
        else None
    )
    if cutoff is not None:
        stmt = stmt.where(ConceptSectorStock.created_at >= cutoff)
    rows = db.scalars(stmt).all()
    sector_codes = {r.sector_code for r in rows}
    sectors = {
        s.sector_code: {c.name: getattr(s, c.name) for c in s.__table__.columns}
        for s in db.scalars(
            select(ConceptSector).where(
                ConceptSector.sector_code.in_(sector_codes),
                *((
                    ConceptSector.trade_date == target_trade_date,
                ) if target_trade_date else ()),
                *((
                    ConceptSector.created_at >= cutoff,
                ) if cutoff is not None else ()),
            )
        ).all()
    }
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        sector = sectors.get(r.sector_code)
        if sector:
            result[r.code].append(sector)
    return dict(result)


# ── 辅助数据：批量加载入口（供feature_builder使用） ────────────────────

def _load_auction_for_stocks(db: Session, codes: list[str], trade_date: str | None = None) -> dict[str, dict[str, Any]]:
    """加载当日集合竞价数据。"""
    from app.models.tables import AuctionData
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    rows = db.scalars(
        select(AuctionData).where(
            AuctionData.code.in_(codes), AuctionData.trade_date == target_trade_date
        )
    ).all()
    return {
        r.code: {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    }


def _load_pledge_for_stocks(db: Session, codes: list[str]) -> dict[str, dict[str, Any]]:
    """加载最新股权质押统计。"""
    from app.models.tables import PledgeStat
    from sqlalchemy import and_
    subq = (
        select(PledgeStat.code, func.max(PledgeStat.end_date).label("max_ed"))
        .where(PledgeStat.code.in_(codes))
        .group_by(PledgeStat.code)
        .subquery()
    )
    rows = db.scalars(
        select(PledgeStat).join(
            subq, and_(PledgeStat.code == subq.c.code, PledgeStat.end_date == subq.c.max_ed)
        )
    ).all()
    return {
        r.code: {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    }


def _load_broker_recs_for_stocks(db: Session, codes: list[str], trade_date: str | None = None) -> dict[str, int]:
    """加载本月券商金股推荐次数。"""
    from app.models.tables import BrokerRecommend
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    month = target_trade_date[:7].replace("-", "")
    rows = db.execute(
        select(BrokerRecommend.code, func.count().label("cnt"))
        .where(BrokerRecommend.code.in_(codes), BrokerRecommend.month == month)
        .group_by(BrokerRecommend.code)
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _load_share_float_upcoming(
    db: Session,
    codes: list[str],
    trade_date: str | None = None,
    days: int = 30,
) -> dict[str, list[dict[str, Any]]]:
    """加载即将到来的限售解禁（未来N天）。"""
    from app.models.tables import ShareFloat
    today = date.fromisoformat(_resolve_snapshot_trade_date(db, trade_date))
    cutoff = (today + timedelta(days=days)).isoformat()
    rows = db.scalars(
        select(ShareFloat).where(
            ShareFloat.code.in_(codes),
            ShareFloat.float_date >= today.isoformat(),
            ShareFloat.float_date <= cutoff,
        )
    ).all()
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        result[r.code].append({c.name: getattr(r, c.name) for c in r.__table__.columns})
    return dict(result)


def _persist_fina_indicator(db: Session, rows: list[dict[str, Any]]) -> None:
    """持久化财务指标（原子 upsert by code+end_date）。

    用 mysql_insert.on_duplicate_key_update 替代 check-then-insert：
    并发 run 时两个请求同时检查\"不存在\"→ 同时 INSERT → 唯一键冲突 (1062)
    导致整个推荐管线 500。原子 upsert 由 MySQL 保证 by code+end_date 唯一性。
    """
    if not rows:
        return
    from app.models.tables import FinaIndicator

    columns = set(FinaIndicator.__mapper__.columns.keys()) - {"id", "updated_at"}
    chunk: list[dict[str, Any]] = []
    # 收集本次 INSERT 实际出现的列（不含 code/end_date 唯一键）
    update_col_names: set[str] = set()
    for row in rows:
        code = str(row.get("code") or "").strip()
        end_date = str(row.get("end_date") or "").strip()
        if len(code) != 6 or not end_date:
            continue
        payload = {k: v for k, v in row.items() if k in columns}
        if not payload:
            continue
        chunk.append(payload)
        update_col_names.update(k for k in payload if k not in {"code", "end_date"})
    if not chunk:
        return
    stmt = mysql_insert(FinaIndicator).values(chunk)
    # 只 UPDATE 本次 INSERT 实际出现的列（避免引用 new.<missing_col>）
    update_cols = {c: stmt.inserted[c] for c in update_col_names}
    db.execute(stmt.on_duplicate_key_update(**update_cols))
    db.commit()


def _load_fina_indicator_for_stocks(db: Session, codes: list[str], trade_date: str | None = None) -> dict[str, dict[str, Any]]:
    """加载最新一期已披露财务指标；本地缺失时按需从 Tushare 拉取并入库。"""
    if not codes:
        return {}
    from app.models.tables import FinaIndicator
    from sqlalchemy import and_

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    target_ann_date = target_trade_date.replace("-", "") if target_trade_date else ""

    def _query_latest() -> dict[str, dict[str, Any]]:
        subq_stmt = (
            select(FinaIndicator.code, func.max(FinaIndicator.end_date).label("max_ed"))
            .where(FinaIndicator.code.in_(codes))
        )
        if target_ann_date:
            subq_stmt = subq_stmt.where(FinaIndicator.ann_date <= target_ann_date)
        subq = subq_stmt.group_by(FinaIndicator.code).subquery()
        rows = db.scalars(
            select(FinaIndicator).join(
                subq, and_(
                    FinaIndicator.code == subq.c.code,
                    FinaIndicator.end_date == subq.c.max_ed,
                )
            )
        ).all()
        return {
            r.code: {
                "roe": r.roe,
                "gross_margin": r.grossprofit_margin,        # feature_builder 读 gross_margin
                "net_profit_yoy": r.netprofit_yoy,           # feature_builder 读 net_profit_yoy
                "revenue_yoy": r.or_yoy,                     # feature_builder 读 revenue_yoy
                "netprofit_margin": r.netprofit_margin,
                "eps": r.eps,
                "bps": r.bps,
                "end_date": r.end_date,
                "ann_date": r.ann_date,
            }
            for r in rows
        }

    result = _query_latest()
    missing_codes = [code for code in codes if code not in result]
    if missing_codes:
        from app.services.tushare_client import fetch_fina_indicator

        fetched: list[dict[str, Any]] = []
        for code in missing_codes:
            fetched.extend(fetch_fina_indicator(code, trade_date=target_trade_date, limit=4))
        if fetched:
            _persist_fina_indicator(db, fetched)
            result = _query_latest()
    return result


def load_auxiliary_for_stocks(db: Session, codes: list[str], trade_date: str | None = None) -> dict[str, Any]:
    """批量加载所有辅助数据，返回按code索引的字典。"""
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    limit_up = {
        r["code"]: r
        for r in _load_cached_limit_up_board(
            db,
            trade_date=target_trade_date,
        )
    }
    concept_sectors = _load_concept_sectors_for_stocks(
        db,
        codes,
        trade_date=target_trade_date,
    )
    capital_flows = _load_cached_capital_flows(
        db,
        codes,
        trade_date=target_trade_date,
    )
    dragon_tigers = _load_cached_dragon_tigers(
        db,
        codes,
        trade_date=target_trade_date,
    )
    news = _load_cached_news(
        db,
        codes,
        trade_date=target_trade_date,
    )
    return {
        "limit_up": limit_up,
        "concept_sectors": concept_sectors,
        "capital_flows": capital_flows,
        "dragon_tigers": dragon_tigers,
        "news": news,
        "auctions": _load_auction_for_stocks(db, codes, trade_date=target_trade_date),
        "pledges": _load_pledge_for_stocks(db, codes),
        "broker_recs": _load_broker_recs_for_stocks(db, codes, trade_date=target_trade_date),
        "share_floats_upcoming": _load_share_float_upcoming(db, codes, trade_date=target_trade_date),
        "margin_details": _load_margin_detail_for_stocks(db, codes, trade_date=target_trade_date),
        "block_trades": _load_block_trade_for_stocks(db, codes, trade_date=target_trade_date),
        "hsgt_top10": _load_hsgt_top10_for_stocks(db, codes, trade_date=target_trade_date),
        "hsgt_flow": _load_hsgt_flow(db, trade_date=target_trade_date),
        "finas": _load_fina_indicator_for_stocks(db, codes, trade_date=target_trade_date),
        "broken_limit_up": _load_cached_broken_limit_up(db, trade_date=target_trade_date),
        "holder_numbers": _load_holder_number_for_stocks(db, codes, trade_date=target_trade_date),
        "holder_trades": _load_holder_trade_for_stocks(db, codes, trade_date=target_trade_date),
        "surveys": _load_survey_for_stocks(db, codes, trade_date=target_trade_date),
        "coverage": _auxiliary_coverage_for_stocks(
            db,
            codes,
            target_trade_date,
            concept_sectors=concept_sectors,
            capital_flows=capital_flows,
            dragon_tigers=dragon_tigers,
            news=news,
        ),
    }


def inject_limit_up_flags(db: Session, stocks: list[dict[str, Any]], trade_date: str | None = None) -> int:
    """prefilter 前给 stocks 注入涨停板基础字段（is_limit_up / limit_up_consecutive_days 等）。

    涨停是已发生的客观事实，本地 limit_up_board 表已持久化。limit-up-reseal-relay 等
    策略的 universe 依赖这些字段，但 feature_builder 在 prefilter 之后才运行，导致
    prefilter 阶段 is_limit_up 全部缺失 → 策略 universe 永远 0 命中。本函数轻量注入
    （单表查询，~133 行），让 prefilter 能正确识别涨停股。
    返回命中的涨停股数量。
    """
    if not stocks:
        return 0
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    rows = _load_cached_limit_up_board(db, trade_date=target_trade_date)
    lu_map: dict[str, dict[str, Any]] = {r["code"]: r for r in rows}
    hit = 0
    for stock in stocks:
        code = str(stock.get("code") or "")
        lu = lu_map.get(code)
        if lu:
            hit += 1
            stock["is_limit_up"] = True
            stock["limit_up_consecutive_days"] = int(lu.get("limit_times") or 1)
            stock["limit_up_open_times"] = int(lu.get("open_times") or 0)
            stock["limit_up_fd_amount_yi"] = float(lu.get("fd_amount_yi") or 0.0)
            stock["limit_up_limit_amount_yi"] = float(lu.get("limit_amount_yi") or 0.0)
            stock["limit_up_turnover_ratio"] = float(lu.get("turnover_ratio") or 0.0)
            # 涨停股必然 limit_up_days20>=1；twenty_cm_risk_gate 默认 pass（feature_builder 会重算覆盖）
            stock.setdefault("limit_up_days20", 1)
            stock.setdefault("twenty_cm_risk_gate", "pass")
        else:
            stock.setdefault("is_limit_up", False)
            stock.setdefault("limit_up_consecutive_days", 0)
            stock.setdefault("limit_up_open_times", 0)
    return hit


def inject_dragon_tiger_flags(db: Session, stocks: list[dict[str, Any]], trade_date: str | None = None) -> int:
    """prefilter 前给 stocks 注入龙虎榜基础字段（dragon_tiger_is_on_list / net_amount_yi / net_buy_ratio 等）。

    龙虎榜是已发生的客观事实，本地 dragon_tiger 表已持久化。dragon-tiger-smart-money 等策略的
    universe 依赖 dragon_tiger_is_on_list，但 feature_builder 在 prefilter 之后才运行，导致 prefilter
    阶段 dragon_tiger 字段全部缺失 → 策略 universe 永远 0 命中。本函数轻量注入（单表按交易日查询），
    让 prefilter 能正确识别龙虎榜股。net_buy_ratio 计算与 feature_builder 一致（净买占成交额比例），
    避免重复计算与语义漂移。返回命中龙虎榜的股票数。
    """
    if not stocks:
        return 0
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    rows = db.scalars(select(DragonTiger).where(DragonTiger.trade_date == target_trade_date)).all()
    dt_map: dict[str, dict[str, Any]] = {r.code: {c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows}
    hit = 0
    for stock in stocks:
        code = str(stock.get("code") or "")
        dt = dt_map.get(code)
        if dt:
            hit += 1
            net_amount_yi = float(dt.get("net_amount_yi") or 0.0)
            amount_yi = float(stock.get("amount_yi") or 0.0)
            net_buy_ratio = round(min(1.0, max(-1.0, net_amount_yi / amount_yi)), 4) if amount_yi else 0.0
            stock["dragon_tiger_is_on_list"] = True
            stock["dragon_tiger_net_amount_yi"] = net_amount_yi
            # 修复沉睡因子：institution_net_buy_yi 此前从未赋值 → inst_buy_ratio 恒为 0。
            # 龙虎榜席位=机构+游资=聪明钱，用净买额作为聪明钱净买代理。
            stock["dragon_tiger_institution_net_buy_yi"] = net_amount_yi
            stock["dragon_tiger_net_buy_ratio"] = net_buy_ratio
        else:
            stock.setdefault("dragon_tiger_is_on_list", False)
    return hit


def _auxiliary_coverage_for_stocks(
    db: Session,
    codes: list[str],
    target_trade_date: str,
    *,
    concept_sectors: dict[str, list[dict[str, Any]]],
    capital_flows: dict[str, dict[str, Any]],
    dragon_tigers: dict[str, dict[str, Any]],
    news: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """记录辅助数据覆盖语义，区分“已查为空”和“未覆盖”。"""
    dt_stmt = select(func.count()).select_from(DragonTiger).where(DragonTiger.trade_date == target_trade_date)
    dragon_tiger_count = int(db.scalar(dt_stmt) or 0)

    concept_stmt = select(func.count()).select_from(ConceptSector).where(ConceptSector.trade_date == target_trade_date)
    member_stmt = select(func.count()).select_from(ConceptSectorStock).where(ConceptSectorStock.trade_date == target_trade_date)

    return {
        "trade_date": target_trade_date,
        "dragon_tiger_trade_date_covered": dragon_tiger_count > 0,
        "dragon_tiger_trade_date_count": dragon_tiger_count,
        "dragon_tiger_codes": sorted(dragon_tigers),
        "concept_sector_trade_date_covered": int(db.scalar(concept_stmt) or 0) > 0,
        "concept_member_trade_date_covered": int(db.scalar(member_stmt) or 0) > 0,
        "concept_sector_codes": sorted(concept_sectors),
        "capital_flow_checked_codes": sorted(capital_flows),
        "news_codes_with_items": sorted(news),
        "requested_codes": sorted({str(code) for code in codes if code}),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 融资融券明细（全市场批量同步）
# ═════════════════════════════════════════════════════════════════════════════


def refresh_margin_detail(db: Session, trade_date: str | None = None) -> int:
    """批量同步全市场融资融券明细。返回入库条数。"""
    from app.services.tushare_client import fetch_margin_detail_batch
    rows = fetch_margin_detail_batch(trade_date=trade_date)
    if not rows:
        return 0
    _persist_margin_detail(db, rows)
    return len(rows)


def _persist_margin_detail(db: Session, rows: list[dict[str, Any]]) -> None:
    """持久化融资融券明细（upsert by code+trade_date）。"""
    for row in rows:
        existing = db.scalar(
            select(MarginDetail).where(
                MarginDetail.code == row["code"],
                MarginDetail.trade_date == row["trade_date"],
            )
        )
        if existing:
            existing.rzye = row.get("rzye")
            existing.rqye = row.get("rqye")
            existing.rzmre = row.get("rzmre")
            existing.rzche = row.get("rzche")
            existing.rzrqye = row.get("rzrqye")
            existing.rqyl = row.get("rqyl")
        else:
            db.add(MarginDetail(**{k: v for k, v in row.items() if k in MarginDetail.__mapper__.columns.keys()}))
    db.commit()


def _load_margin_detail_for_stocks(
    db: Session, codes: list[str], trade_date: str | None = None
) -> dict[str, dict[str, Any]]:
    """加载指定股票的融资融券明细。"""
    stmt = select(MarginDetail).where(
        MarginDetail.code.in_(codes),
        MarginDetail.trade_date == trade_date,
    )
    rows = db.scalars(stmt).all()
    return {r.code: {"rzye": r.rzye, "rqye": r.rqye, "rzmre": r.rzmre, "rzche": r.rzche, "rzrqye": r.rzrqye} for r in rows}


# ═════════════════════════════════════════════════════════════════════════════
# 大宗交易
# ═════════════════════════════════════════════════════════════════════════════


def refresh_block_trade(db: Session, trade_date: str | None = None) -> int:
    """同步大宗交易数据。返回入库条数。"""
    from app.services.tushare_client import fetch_block_trade
    rows = fetch_block_trade(trade_date=trade_date)
    if not rows:
        return 0
    _persist_block_trade(db, rows)
    return len(rows)


def _persist_block_trade(db: Session, rows: list[dict[str, Any]]) -> None:
    """持久化大宗交易（先删后插，避免重复）。"""
    if not rows:
        return
    trade_dates = {r["trade_date"] for r in rows}
    for td in trade_dates:
        codes = {r["code"] for r in rows if r["trade_date"] == td}
        db.execute(BlockTrade.__table__.delete().where(
            BlockTrade.trade_date == td,
            BlockTrade.code.in_(codes),
        ))
    for row in rows:
        db.add(BlockTrade(**{k: v for k, v in row.items() if k in BlockTrade.__mapper__.columns.keys()}))
    db.commit()


def _load_block_trade_for_stocks(
    db: Session, codes: list[str], trade_date: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """加载指定股票的大宗交易。"""
    stmt = select(BlockTrade).where(
        BlockTrade.code.in_(codes),
        BlockTrade.trade_date == trade_date,
    )
    rows = db.scalars(stmt).all()
    result: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        result.setdefault(r.code, []).append({
            "price": r.price,
            "vol": r.vol,
            "amount_yi": r.amount_yi,
            "buyer": r.buyer,
            "seller": r.seller,
        })
    return result


def _load_holder_number_for_stocks(
    db: Session, codes: list[str], trade_date: str | None = None
) -> dict[str, dict[str, Any]]:
    """加载股东人数（筹码集中度代理）。取最近两期对比变化率。"""
    if not codes:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for code in codes:
        rows = db.scalars(
            select(HolderNumber)
            .where(HolderNumber.code == code)
            .order_by(HolderNumber.end_date.desc())
            .limit(2)
        ).all()
        if not rows:
            continue
        latest = rows[0]
        holder_num = latest.holder_num or 0
        prev_num = rows[1].holder_num if len(rows) > 1 else 0
        # 股东人数减少 = 筹码集中 = 看多信号
        holder_num_change_pct = (
            round((holder_num - prev_num) / prev_num * 100, 2)
            if prev_num > 0 else 0.0
        )
        result[code] = {
            "holder_num": holder_num,
            "holder_num_end_date": latest.end_date,
            "holder_num_prev": prev_num,
            "holder_num_change_pct": holder_num_change_pct,
            "holder_num_decreasing": holder_num_change_pct < -3,  # 减少>3%为筹码集中信号
        }
    return result


def _load_holder_trade_for_stocks(
    db: Session, codes: list[str], trade_date: str | None = None
) -> dict[str, dict[str, Any]]:
    """加载股东增减持数据（近30天）。trade_date 为 YYYY-MM-DD 或空。"""
    if not codes:
        return {}
    # trade_date 可能是空字符串（snapshot 表为空时），用今天兜底，避免 fromisoformat("") 崩溃
    ref_date: date
    if trade_date and len(trade_date) >= 10:
        try:
            ref_date = date.fromisoformat(trade_date[:10])
        except ValueError:
            ref_date = date.today()
    else:
        ref_date = date.today()
    cutoff = (ref_date - timedelta(days=30)).isoformat()
    stmt = select(HolderTrade).where(
        HolderTrade.code.in_(codes),
        HolderTrade.ann_date >= cutoff,
    )
    rows = db.scalars(stmt).all()
    # 按股票聚合
    by_code: dict[str, list] = {}
    for r in rows:
        by_code.setdefault(r.code, []).append(r)
    result: dict[str, dict[str, Any]] = {}
    for code, recs in by_code.items():
        increase_count = sum(1 for r in recs if r.in_de == "IN")
        decrease_count = sum(1 for r in recs if r.in_de == "DE")
        total_change_ratio = sum(r.change_ratio or 0 for r in recs)
        result[code] = {
            "holder_trade_count": len(recs),
            "holder_trade_increase_count": increase_count,
            "holder_trade_decrease_count": decrease_count,
            "holder_trade_net_ratio": round(total_change_ratio, 4),
            "holder_trade_latest_ann": max((r.ann_date or "") for r in recs),
        }
    return result


def _load_survey_for_stocks(
    db: Session, codes: list[str], trade_date: str | None = None
) -> dict[str, dict[str, Any]]:
    """加载机构调研数据（近30天）。trade_date 为 YYYY-MM-DD 或空。"""
    if not codes:
        return {}
    # 同 _load_holder_trade_for_stocks：空 trade_date 用今天兜底
    ref_date: date
    if trade_date and len(trade_date) >= 10:
        try:
            ref_date = date.fromisoformat(trade_date[:10])
        except ValueError:
            ref_date = date.today()
    else:
        ref_date = date.today()
    cutoff = (ref_date - timedelta(days=30)).isoformat()
    stmt = select(InstitutionalSurvey).where(
        InstitutionalSurvey.code.in_(codes),
        InstitutionalSurvey.surv_date >= cutoff,
    )
    rows = db.scalars(stmt).all()
    by_code: dict[str, list] = {}
    for r in rows:
        by_code.setdefault(r.code, []).append(r)
    result: dict[str, dict[str, Any]] = {}
    for code, recs in by_code.items():
        total_visitors = sum(r.fund_visitors or 0 for r in recs)
        result[code] = {
            "survey_count": len(recs),
            "survey_fund_visitors": total_visitors,
            "survey_latest_date": max((r.surv_date or "") for r in recs),
            "survey_org_types": list({r.org_type or "" for r in recs if r.org_type})[:5],
        }
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 北向资金
# ═════════════════════════════════════════════════════════════════════════════


def refresh_hsgt_flow(db: Session, trade_date: str | None = None) -> dict[str, Any] | None:
    """同步沪深港通资金流向。返回入库数据。"""
    from app.services.tushare_client import fetch_hsgt_flow
    data = fetch_hsgt_flow(trade_date=trade_date)
    if not data:
        return None
    _persist_hsgt_flow(db, data)
    return data


def _persist_hsgt_flow(db: Session, data: dict[str, Any]) -> None:
    """持久化北向资金流向。"""
    existing = db.scalar(
        select(HsgtFlow).where(HsgtFlow.trade_date == data["trade_date"])
    )
    if existing:
        existing.north_money = data.get("north_money")
        existing.south_money = data.get("south_money")
        existing.hgt = data.get("hgt")
        existing.sgt = data.get("sgt")
    else:
        db.add(HsgtFlow(**{k: v for k, v in data.items() if k in HsgtFlow.__mapper__.columns.keys()}))
    db.commit()


def refresh_hsgt_top10(db: Session, trade_date: str | None = None) -> int:
    """同步北向持股TOP10。返回入库条数。"""
    from app.services.tushare_client import fetch_hsgt_top10
    rows = fetch_hsgt_top10(trade_date=trade_date)
    if not rows:
        return 0
    _persist_hsgt_top10(db, rows)
    return len(rows)


def _persist_hsgt_top10(db: Session, rows: list[dict[str, Any]]) -> None:
    """持久化北向持股TOP10。"""
    for row in rows:
        existing = db.scalar(
            select(HsgtTop10).where(
                HsgtTop10.code == row["code"],
                HsgtTop10.trade_date == row["trade_date"],
            )
        )
        if existing:
            existing.name = row.get("name")
            existing.close = row.get("close")
            existing.change_pct = row.get("change")
            existing.rank = row.get("rank")
            existing.amount = row.get("amount")
            existing.net_amount = row.get("net_amount")
            existing.buy = row.get("buy")
        else:
            db.add(HsgtTop10(**{k: v for k, v in row.items() if k in HsgtTop10.__mapper__.columns.keys()}))
    db.commit()


def _load_hsgt_top10_for_stocks(
    db: Session, codes: list[str], trade_date: str | None = None
) -> dict[str, dict[str, Any]]:
    """加载指定股票的北向持股数据。"""
    stmt = select(HsgtTop10).where(
        HsgtTop10.code.in_(codes),
        HsgtTop10.trade_date == trade_date,
    )
    rows = db.scalars(stmt).all()
    return {r.code: {"rank": r.rank, "amount": r.amount, "net_amount": r.net_amount, "buy": r.buy} for r in rows}


def _load_hsgt_flow(db: Session, trade_date: str | None = None) -> dict[str, Any] | None:
    """加载北向资金流向。"""
    row = db.scalar(select(HsgtFlow).where(HsgtFlow.trade_date == trade_date))
    if not row:
        return None
    return {"north_money": row.north_money, "south_money": row.south_money, "hgt": row.hgt, "sgt": row.sgt}
