from __future__ import annotations

import logging
import statistics
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.tables import (
    CapitalFlow,
    ConceptSector,
    ConceptSectorStock,
    DailyKbar,
    DataSourceHealth,
    DragonTiger,
    LimitUpBoard,
    NewsAnnouncement,
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
    _fetch_kline,
    _fetch_quotes,
    _global_failed_kline_sources,
    _kline_sources,
    _latest_trade_date_from_quotes,
    _num,
    _quote_sources,
    _select_recall_candidates,
)
from app.services.market_auxiliary import (
    _capital_flow_sources,
    _concept_sector_sources,
    _dragon_tiger_sources,
    _fetch_capital_flow,
    _fetch_concept_sectors,
    _fetch_dragon_tiger,
    _fetch_limit_up_board,
    _fetch_news,
    _limit_up_sources,
    _news_sources,
)


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
]

STRICT_CONSENSUS_FIELDS = {"price": 0.018, "pct": 0.08}
DERIVED_RETURN_FIELDS = {"chg60", "ytd_chg"}
LOCAL_DAILY_BAR_FIELDS = DERIVED_RETURN_FIELDS | {"ma5", "ma10", "ma20"}


def get_market_universe_from_pool(db: Session, refresh: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = get_settings()
    quotes, quote_meta = _load_or_refresh_quotes(db, settings) if refresh else _load_quotes_from_pool(db, settings, skip_metrics=True)
    total_quote_count = len(quotes)
    eligible_quotes = _eligible_market_quotes(quotes)
    filter_summary = _candidate_filter_summary(quotes)
    recall_plan = _candidate_recall_plan(total_quote_count, len(eligible_quotes), settings)
    candidates = _select_recall_candidates(eligible_quotes, recall_plan["broad_recall_count"])
    if not candidates:
        raise MarketDataError("统一行情池没有可用候选股票，已停止运行。")

    stocks: list[dict[str, Any]] = []
    kline_errors: dict[str, list[str]] = {}
    kline_usage: Counter[str] = Counter()
    disabled_kline_sources: set[str] = set()

    # ── 批量预加载日K（非刷新模式），避免 N 次逐个查询 ──
    candidate_codes = [quote["code"] for quote in candidates]
    if not refresh:
        bars_batch = _load_cached_bars_batch(db, candidate_codes, settings.market_kline_days)
    else:
        bars_batch = None

    for quote in candidates:
        if len(stocks) >= recall_plan["scan_target_count"]:
            break
        if refresh:
            bars, kline_trace, errors = _load_or_refresh_bars(db, quote, settings, disabled_kline_sources)
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
        disabled_kline_sources.update(_global_failed_kline_sources(errors))
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
        disabled_kline_sources=disabled_kline_sources,
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
                "broad_recall_count": recall_plan["broad_recall_count"],
                "broad_recall_limited": recall_plan["broad_recall_limited"],
                "recall_strategy": "multi_bucket_quote_recall",
            },
        }
    )
    snapshot["snapshot_id"] = f"snapshot_pool_{snapshot['trade_date']}_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%H%M%S')}"
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
    if not active_sources:
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

    quotes = cached_quotes if cached_quotes is not None else _load_cached_quotes(db, allowed_sources=set(active_sources), skip_metrics=True)
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
    candidates = _select_recall_candidates(eligible_quotes, recall_plan["broad_recall_count"])
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
    stale_count = 0
    checked_count = 0
    for quote in candidates:
        checked_count += 1
        code = str(quote.get("code") or "")
        summary = bar_summaries.get(code, {})
        bar_count = summary.get("bar_count", 0)
        latest_bar_date = summary.get("latest_trade_date")
        quote_date = min(str(quote.get("trade_date") or date.today().isoformat()), _latest_allowed_bar_date())
        quote_date = _latest_allowed_bar_date(date.fromisoformat(quote_date))
        if bar_count < min_bar_count:
            if bar_count <= 0:
                missing_count += 1
            else:
                short_history_count += 1
            if len(examples) < 8:
                examples.append(
                    _readiness_example(
                        quote,
                        "本地日K不足30条",
                        bar_count,
                        latest_bar_date,
                        quote_date,
                    )
                )
            continue
        if not latest_bar_date or latest_bar_date < quote_date:
            stale_count += 1
            if len(examples) < 8:
                examples.append(
                    _readiness_example(
                        quote,
                        "本地日K未覆盖报价日期",
                        bar_count,
                        latest_bar_date,
                        quote_date,
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

    if missing_count or stale_count:
        gap_parts = []
        if missing_count:
            gap_parts.append(f"日K缺失 {missing_count} 只")
        if short_history_count:
            gap_parts.append(f"短历史 {short_history_count} 只")
        if stale_count:
            gap_parts.append(f"日K过期 {stale_count} 只")
        messages.append(f"当前存在数据缺口：{'，'.join(gap_parts)}。")
    elif short_history_count:
        messages.append(f"当前存在数据缺口：短历史 {short_history_count} 只。")

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
        },
        "kline": {
            "checked_count": checked_count,
            "ready_count": ready_count,
            "missing_count": missing_count,
            "short_history_count": short_history_count,
            "stale_count": stale_count,
            "min_bars": min_bar_count,
            "coverage_count": ready_count,
            "coverage_rate": round(ready_count / max(1, len(candidates)), 4) if candidates else 0.0,
            "latest_trade_date": max(
                [str(summary.get("latest_trade_date") or "") for summary in bar_summaries.values() if summary.get("latest_trade_date")],
                default=None,
            ),
            "examples": examples,
        },
        "scan": {
            "configured_candidate_count": recall_plan["explicit_scan_limit"],
            "lookahead_multiplier": recall_plan["lookahead_multiplier"],
            "lookahead_count": recall_plan["broad_recall_count"],
            "actual_scan_count": min(ready_count, recall_plan["scan_target_count"]),
            "skill_input_count": min(ready_count, recall_plan["scan_target_count"]),
            "agent_review_input_count": min(ready_count, recall_plan["scan_target_count"]),
            "scan_limited": recall_plan["broad_recall_limited"] or (
                not recall_plan["full_market_mode"] and len(candidates) > recall_plan["scan_target_count"]
            ),
            "demo_limited": (not recall_plan["full_market_mode"]) and len(eligible_quotes) > recall_plan["scan_target_count"],
        },
        "messages": messages,
    }


def _load_quotes_from_pool(db: Session, settings: Any, skip_metrics: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    active_sources = enabled_sources(db, "quote", _quote_sources(settings))
    if not active_sources:
        raise MarketDataError("所有报价数据源都已关闭，请至少启用一个报价源。")
    quotes = _load_cached_quotes(db, allowed_sources=set(active_sources), skip_metrics=skip_metrics)
    required_count = max(0, int(settings.market_candidate_count))
    if required_count > 0 and len(quotes) < required_count:
        raise MarketDataError(
            f"本地数据池可用报价不足，当前 {len(quotes)} 条，至少需要 {required_count} 条。"
            "请先显式刷新本地数据池，或切换为实时数据。"
        )
    return quotes, _quote_meta("local_pool_readonly", quotes)


def _load_or_refresh_quotes(db: Session, settings: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    configured_sources = _quote_sources(settings)
    active_sources = enabled_sources(db, "quote", configured_sources)
    if not active_sources:
        raise MarketDataError("所有报价数据源都已关闭，请至少启用一个报价源。")
    allowed_sources = set(active_sources)
    cached = _load_cached_quotes(
        db,
        max_age=timedelta(seconds=settings.market_quote_cache_ttl_seconds),
        allowed_sources=allowed_sources,
    )
    required_count = max(0, int(settings.market_candidate_count))
    if len(cached) > 0 and (required_count == 0 or len(cached) >= required_count):
        return cached, _quote_meta("fresh_cache", cached)
    return _refresh_quote_pool(db, settings, active_sources=active_sources)


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
    stmt = (
        select(StockQuote)
        .where(StockQuote.freshness_status != "inactive")
        .order_by(StockQuote.quality_score.desc(), StockQuote.snapped_at.desc())
    )
    if cutoff is not None:
        stmt = stmt.where(StockQuote.snapped_at >= cutoff)
    rows = db.scalars(stmt).all()
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


def enrich_quote_with_local_metrics(db: Session, code: str, quote: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(quote)
    _apply_local_quote_metrics(enriched, _quote_metrics_from_bars(db, [code]).get(code, {}))
    return enriched


def _quote_metrics_from_bars(db: Session, codes: list[str]) -> dict[str, dict[str, float]]:
    unique_codes = sorted({str(code) for code in codes if code})
    if not unique_codes:
        return {}

    latest_allowed_date = _latest_allowed_bar_date()
    rows = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code.in_(unique_codes), DailyKbar.trade_date <= latest_allowed_date)
        .order_by(DailyKbar.code, DailyKbar.trade_date.desc())
    ).all()

    bars_by_code: dict[str, list[DailyKbar]] = defaultdict(list)
    for row in rows:
        bars_by_code[row.code].append(row)

    result: dict[str, dict[str, float]] = {}
    year_start = date.fromisoformat(latest_allowed_date).replace(month=1, day=1).isoformat()
    for code, bars in bars_by_code.items():
        if not bars:
            continue
        latest_close = float(bars[0].close or 0)
        if latest_close <= 0:
            continue

        latest_bar = bars[0]
        values: dict[str, float] = {}
        for field in ("ma5", "ma10", "ma20"):
            value = getattr(latest_bar, field, None)
            if value is not None:
                values[field] = round(float(value), 3)

        if len(bars) >= 60:
            base_close = float(bars[59].close or 0)
            if base_close:
                values["chg60"] = round((latest_close - base_close) / base_close * 100, 4)

        ytd_base = next((bar for bar in bars if str(bar.trade_date) < year_start), None)
        if ytd_base is not None:
            base_close = float(ytd_base.close or 0)
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
    disabled_sources: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    cached = _load_cached_bars(db, quote["code"], settings.market_kline_days)
    if len(cached) >= 30 and _bars_cover_quote_date(cached, quote):
        return cached, {"selected_source": "daily_bars_cache", "freshness_status": "fresh_cache"}, []
    bars, source, errors = _refresh_bars_for_quote(db, quote, settings, disabled_sources)
    if bars:
        _persist_kbars(db, bars)
        db.commit()
        return bars, {"selected_source": source, "freshness_status": "refreshed"}, errors
    return [], {"selected_source": "", "freshness_status": "missing"}, errors


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
    disabled_sources: set[str],
) -> tuple[list[dict[str, Any]], str, list[str]]:
    errors: list[str] = []
    sources = enabled_sources(db, "kline", _kline_sources(settings))
    if not sources:
        return [], "", ["kline: 所有日K数据源都已关闭"]

    # 增量拉取：查本地已存数据的最新日期，只拉缺失的天数
    start_date = _incremental_kline_start(db, quote["code"], settings)

    for source in sources:
        if source in disabled_sources:
            errors.append(f"{source}: 本次扫描已熔断，跳过")
            continue
        start = time.perf_counter()
        try:
            bars = _fetch_kline(source, quote, settings, start_date=start_date)
            latency_ms = (time.perf_counter() - start) * 1000
            if len(bars) < 30:
                _record_source_health(db, source, "kline", "empty", latency_ms=latency_ms)
                errors.append(f"{source}: 日K不足30条")
                continue
            _record_source_health(db, source, "kline", "success", latency_ms=latency_ms)
            return bars[-settings.market_kline_days :], source, errors
        except Exception as exc:  # noqa: BLE001 - returned as run evidence
            latency_ms = (time.perf_counter() - start) * 1000
            message = f"{source}: {exc}"
            errors.append(message)
            _record_source_health(db, source, "kline", "failure", error=str(exc), latency_ms=latency_ms)
            if source in _global_failed_kline_sources([message]):
                disabled_sources.add(source)
    return [], "", errors


def _incremental_kline_start(db: Session, code: str, settings: Any) -> str | None:
    """查询本地已有日K的最新交易日，返回增量拉取的起始日期 (YYYYMMDD)。

    已有数据时，从最新日期 - buffer 天开始拉取（覆盖可能的复权修正）。
    无数据时返回 None（通知调用方按全量拉取）。
    """
    existing_max_date = db.scalar(
        select(func.max(DailyKbar.trade_date)).where(DailyKbar.code == code)
    )
    if not existing_max_date:
        return None
    buffer_days = getattr(settings, "market_kline_incremental_buffer", 10)
    try:
        latest = datetime.strptime(existing_max_date, "%Y-%m-%d").date()
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
) -> dict[str, Any]:
    return {
        "code": quote.get("code"),
        "name": quote.get("name"),
        "reason": reason,
        "bar_count": bar_count,
        "latest_bar_date": latest_bar_date,
        "quote_date": quote_date,
    }


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


def _persist_stock_quotes(db: Session, rows: list[dict[str, Any]]) -> None:
    trade_date = _latest_trade_date_from_quotes(rows)
    active_codes = {row["code"] for row in rows}
    seen_batch: set[tuple] = set()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in rows:
        key = (row["code"], trade_date)
        if key in seen_batch:
            continue
        seen_batch.add(key)
        existing = db.scalar(
            select(StockQuote).where(StockQuote.code == row["code"], StockQuote.trade_date == trade_date)
        )
        source = str(row.get("raw_sources", ["unknown"])[0]) if row.get("raw_sources") else "unknown"
        if existing is None:
            db.add(StockQuote(
                code=row["code"],
                trade_date=trade_date,
                name=str(row.get("name") or ""),
                price=float(row.get("price") or 0),
                pct=float(row.get("pct") or 0),
                volume=float(row.get("volume") or 0),
                amount_yi=float(row.get("amount_yi") or 0),
                turnover_rate=float(row.get("turnover_rate") or 0),
                pe_ttm=float(row.get("pe_ttm")) if row.get("pe_ttm") is not None else None,
                total_mv_yi=float(row.get("total_mv_yi")) if row.get("total_mv_yi") is not None else None,
                free_mv_yi=float(row.get("free_mv_yi")) if row.get("free_mv_yi") is not None else None,
                chg60=float(row.get("chg60")) if row.get("chg60") is not None else None,
                high=float(row.get("high")) if row.get("high") is not None else None,
                low=float(row.get("low")) if row.get("low") is not None else None,
                open=float(row.get("open")) if row.get("open") is not None else None,
                preclose=float(row.get("preclose")) if row.get("preclose") is not None else None,
                industry=row.get("industry") or "",
                source=source,
                source_count=int(row.get("source_count") or 0),
                quality_score=float(row.get("quality_score") or 0),
                freshness_status=str(row.get("freshness_status") or "fresh"),
                snapped_at=now,
            ))
        else:
            existing.name = str(row.get("name") or "")
            existing.price = float(row.get("price") or 0)
            existing.pct = float(row.get("pct") or 0)
            existing.volume = float(row.get("volume") or 0)
            existing.amount_yi = float(row.get("amount_yi") or 0)
            existing.turnover_rate = float(row.get("turnover_rate") or 0)
            existing.pe_ttm = float(row.get("pe_ttm")) if row.get("pe_ttm") is not None else None
            existing.total_mv_yi = float(row.get("total_mv_yi")) if row.get("total_mv_yi") is not None else None
            existing.free_mv_yi = float(row.get("free_mv_yi")) if row.get("free_mv_yi") is not None else None
            existing.chg60 = float(row.get("chg60")) if row.get("chg60") is not None else None
            existing.high = float(row.get("high")) if row.get("high") is not None else None
            existing.low = float(row.get("low")) if row.get("low") is not None else None
            existing.open = float(row.get("open")) if row.get("open") is not None else None
            existing.preclose = float(row.get("preclose")) if row.get("preclose") is not None else None
            existing.industry = row.get("industry") or ""
            existing.source = source
            existing.source_count = int(row.get("source_count") or 0)
            existing.quality_score = float(row.get("quality_score") or 0)
            existing.freshness_status = str(row.get("freshness_status") or "fresh")
            existing.snapped_at = now
    if active_codes:
        inactive_rows = db.scalars(
            select(StockQuote).where(
                StockQuote.trade_date == trade_date,
                StockQuote.code.not_in(active_codes),
            )
        ).all()
        for row in inactive_rows:
            row.freshness_status = "inactive"


def _persist_kbars(db: Session, bars: list[dict[str, Any]]) -> None:
    if not bars:
        return
    latest_allowed_date = _latest_allowed_bar_date()
    code = str(bars[0].get("code") or "")
    seen_batch: set[tuple] = set()
    rows: list[dict[str, Any]] = []
    for bar in bars:
        trade_date = str(bar.get("trade_date") or "")
        if trade_date > latest_allowed_date:
            continue
        key = (str(bar.get("code") or ""), trade_date)
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
            "macd_dif": bar.get("macd_dif"),
            "macd_dea": bar.get("macd_dea"),
            "macd_bar": bar.get("macd"),
            "kdj_k": bar.get("kdj_k"),
            "kdj_d": bar.get("kdj_d"),
            "kdj_j": bar.get("kdj_j"),
            "rsi_6": bar.get("rsi_6"),
            "rsi_12": bar.get("rsi_12"),
            "rsi_24": bar.get("rsi_24"),
            "boll_upper": bar.get("boll_upper"),
            "boll_mid": bar.get("boll_mid"),
            "boll_lower": bar.get("boll_lower"),
            "cci": bar.get("cci"),
            "source": bar.get("source", "tushare"),
        })
    if not code or not rows:
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
                "trade_date": row.trade_date,
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
            "trade_date": row.trade_date,
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
    """Batch load bar summary for readiness check: count + latest trade_date per code."""
    if not codes:
        return {}
    latest_allowed_date = _latest_allowed_bar_date()
    rows = db.execute(
        select(
            DailyKbar.code,
            func.count().label("bar_count"),
            func.max(DailyKbar.trade_date).label("latest_trade_date"),
        )
        .where(DailyKbar.code.in_(codes), DailyKbar.trade_date <= latest_allowed_date)
        .group_by(DailyKbar.code)
    ).all()
    return {
        row.code: {
            "bar_count": int(row.bar_count or 0),
            "latest_trade_date": row.latest_trade_date,
        }
        for row in rows
    }


def _bars_cover_quote_date(bars: list[dict[str, Any]], quote: dict[str, Any]) -> bool:
    quote_date = str(quote.get("trade_date") or date.today().isoformat())
    quote_date = min(quote_date, _latest_allowed_bar_date())
    quote_date = _latest_allowed_bar_date(date.fromisoformat(quote_date))
    latest = max(str(bar.get("trade_date") or "") for bar in bars)
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


def _max_relative_deviation(values: list[float], center: float) -> float:
    denominator = max(abs(center), 0.0001)
    return max(abs(value - center) / denominator for value in values)


# ── 辅助数据池：刷新入口 ───────────────────────────────────────────────

def refresh_limit_up_board(db: Session) -> list[dict[str, Any]]:
    """刷新涨停板数据。"""
    settings = get_settings()
    if not settings.enable_limit_up_fetch:
        return []
    sources = enabled_sources(db, "limit_up", _limit_up_sources(settings))
    return _refresh_aux_data(
        db, "limit_up", sources, settings,
        fetcher=lambda source, _settings: _fetch_limit_up_board(source, _settings),
        normalized_persister=_persist_normalized_limit_up_board,
    )


def refresh_concept_sectors(db: Session) -> list[dict[str, Any]]:
    """刷新概念板块数据。"""
    settings = get_settings()
    if not settings.enable_concept_sector_fetch:
        return []
    sources = enabled_sources(db, "concept_sector", _concept_sector_sources(settings))
    result = _refresh_aux_data(
        db, "concept_sector", sources, settings,
        fetcher=lambda source, _settings: _fetch_concept_sectors(source, _settings),
        normalized_persister=_persist_normalized_concept_sector,
    )
    # 额外持久化热门板块的成分股（优先 akshare 同花顺源，东财源被封）
    from app.services.market_auxiliary import _fetch_ak_concept_sector_stocks, _fetch_eastmoney_concept_sector_stocks
    top_sectors = [s for s in result if s.get("heat_rank", 999) <= 60]
    if top_sectors:
        for sector in top_sectors:
            sector_name = str(sector.get("sector_name") or "")
            sector_code = str(sector.get("sector_code") or "")
            stocks: list[dict[str, Any]] = []
            if sector_name:
                try:
                    stocks = _fetch_ak_concept_sector_stocks(sector_name, settings)
                except Exception:
                    pass
            if not stocks and sector_code:
                try:
                    stocks = _fetch_eastmoney_concept_sector_stocks(sector_code, settings)
                except Exception:
                    pass
            if stocks:
                _persist_concept_sector_stocks(db, stocks)
        db.commit()
    return result


def refresh_dragon_tiger(db: Session) -> list[dict[str, Any]]:
    """刷新龙虎榜数据。"""
    settings = get_settings()
    if not settings.enable_dragon_tiger_fetch:
        return []
    sources = enabled_sources(db, "dragon_tiger", _dragon_tiger_sources(settings))
    return _refresh_aux_data(
        db, "dragon_tiger", sources, settings,
        fetcher=lambda source, _settings: _fetch_dragon_tiger(source, _settings),
        normalized_persister=_persist_normalized_dragon_tiger,
    )


def refresh_capital_flows_for_codes(db: Session, codes: list[str]) -> dict[str, dict[str, Any]]:
    """按股刷新资金流向数据。"""
    settings = get_settings()
    if not settings.enable_capital_flow_fetch:
        return {}
    sources = enabled_sources(db, "capital_flow", _capital_flow_sources(settings))
    if not sources:
        return {}
    source = sources[0]
    result: dict[str, dict[str, Any]] = {}
    for code in codes:
        try:
            data = _fetch_capital_flow(source, code, settings)
            if data:
                _persist_normalized_capital_flow(db, data)
                result[code] = data
        except Exception:
            pass
    db.commit()
    return result


def refresh_news_for_codes(db: Session, codes: list[str]) -> dict[str, list[dict[str, Any]]]:
    """按股刷新新闻公告数据。"""
    settings = get_settings()
    if not settings.enable_news_fetch:
        return {}
    sources = enabled_sources(db, "news", _news_sources(settings))
    if not sources:
        return {}
    source = sources[0]
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for code in codes:
        try:
            items = _fetch_news(source, code, settings)
            if items:
                _persist_normalized_news(db, items)
                result[code] = items
        except Exception:
            pass
    db.commit()
    return dict(result)


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
    trade_date = str(data.get("trade_date") or "")
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
        trade_date = str(row.get("trade_date") or "")
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
        row_with_source = {**row, "source": source}
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

def _load_cached_limit_up_board(db: Session, max_age_seconds: int | None = None) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds) if max_age_seconds else None
    stmt = select(LimitUpBoard).order_by(LimitUpBoard.limit_times.desc())
    if cutoff is not None:
        stmt = stmt.where(LimitUpBoard.created_at >= cutoff)
    rows = db.scalars(stmt).all()
    return [
        {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    ]


def _load_cached_concept_sectors(db: Session, max_age_seconds: int | None = None) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds) if max_age_seconds else None
    stmt = select(ConceptSector).order_by(ConceptSector.heat_rank)
    if cutoff is not None:
        stmt = stmt.where(ConceptSector.created_at >= cutoff)
    rows = db.scalars(stmt).all()
    return [
        {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    ]


def _load_cached_capital_flows(
    db: Session, codes: list[str], max_age_seconds: int | None = None
) -> dict[str, dict[str, Any]]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds) if max_age_seconds else None
    stmt = select(CapitalFlow).where(CapitalFlow.code.in_(codes))
    if cutoff is not None:
        stmt = stmt.where(CapitalFlow.created_at >= cutoff)
    rows = db.scalars(stmt).all()
    return {
        r.code: {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    }


def _load_cached_dragon_tigers(
    db: Session, codes: list[str], max_age_seconds: int | None = None
) -> dict[str, dict[str, Any]]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds) if max_age_seconds else None
    stmt = select(DragonTiger).where(DragonTiger.code.in_(codes))
    if cutoff is not None:
        stmt = stmt.where(DragonTiger.created_at >= cutoff)
    rows = db.scalars(stmt).all()
    return {
        r.code: {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    }


def _load_cached_news(
    db: Session, codes: list[str], max_age_seconds: int | None = None
) -> dict[str, list[dict[str, Any]]]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds) if max_age_seconds else None
    stmt = select(NewsAnnouncement).where(NewsAnnouncement.code.in_(codes))
    if cutoff is not None:
        stmt = stmt.where(NewsAnnouncement.created_at >= cutoff)
    rows = db.scalars(stmt).all()
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        result[r.code].append({c.name: getattr(r, c.name) for c in r.__table__.columns})
    return dict(result)


def _load_concept_sectors_for_stocks(
    db: Session, codes: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """根据成分股表返回每只股票所属的概念板块。"""
    rows = db.scalars(
        select(ConceptSectorStock).where(ConceptSectorStock.code.in_(codes))
    ).all()
    sector_codes = {r.sector_code for r in rows}
    sectors = {
        s.sector_code: {c.name: getattr(s, c.name) for c in s.__table__.columns}
        for s in db.scalars(
            select(ConceptSector).where(ConceptSector.sector_code.in_(sector_codes))
        ).all()
    }
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        sector = sectors.get(r.sector_code)
        if sector:
            result[r.code].append(sector)
    return dict(result)


# ── 辅助数据：批量加载入口（供feature_builder使用） ────────────────────

def load_auxiliary_for_stocks(db: Session, codes: list[str]) -> dict[str, Any]:
    """批量加载所有辅助数据，返回按code索引的字典。"""
    settings = get_settings()
    return {
        "limit_up": {r["code"]: r for r in _load_cached_limit_up_board(db, max_age_seconds=settings.limit_up_cache_ttl_seconds)},
        "concept_sectors": _load_concept_sectors_for_stocks(db, codes),
        "capital_flows": _load_cached_capital_flows(db, codes, max_age_seconds=settings.capital_flow_cache_ttl_seconds),
        "dragon_tigers": _load_cached_dragon_tigers(db, codes, max_age_seconds=settings.dragon_tiger_cache_ttl_seconds),
        "news": _load_cached_news(db, codes, max_age_seconds=settings.news_cache_ttl_seconds),
    }
