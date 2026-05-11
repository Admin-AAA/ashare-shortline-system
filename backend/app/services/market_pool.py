from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.tables import DailyBar, DailyIndicator, DataSourceHealth, RawDailyBar, RawQuote, UnifiedQuote
from app.services.data_sources import enabled_sources
from app.services.indicator_computer import compute_daily_bar_indicators, compute_daily_indicators
from app.services.market_data import (
    MarketDataError,
    _apply_relative_features,
    _breadth_from_counts,
    _build_snapshot,
    _build_stock_from_bars,
    _fetch_kline,
    _fetch_quotes,
    _global_failed_kline_sources,
    _kline_sources,
    _latest_trade_date_from_quotes,
    _num,
    _quote_sources,
    _select_candidates,
    _top_industries_from_quotes,
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
    quotes, quote_meta = _load_or_refresh_quotes(db, settings) if refresh else _load_quotes_from_pool(db, settings)
    target_count = max(1, int(settings.market_candidate_count))
    lookahead_count = min(
        len(quotes),
        max(target_count, target_count * max(1, int(settings.market_candidate_lookahead_multiplier))),
    )
    candidates = _select_candidates(quotes, lookahead_count)
    if not candidates:
        raise MarketDataError("统一行情池没有可用候选股票，已停止运行。")

    stocks: list[dict[str, Any]] = []
    kline_errors: dict[str, list[str]] = {}
    kline_usage: Counter[str] = Counter()
    disabled_kline_sources: set[str] = set()
    for quote in candidates:
        if len(stocks) >= target_count:
            break
        if refresh:
            bars, kline_trace, errors = _load_or_refresh_bars(db, quote, settings, disabled_kline_sources)
        else:
            bars, kline_trace, errors = _load_bars_from_pool(db, quote, settings)
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

    _apply_relative_features(stocks)
    snapshot = _build_snapshot(
        stocks=stocks,
        quote_source="unified_pool",
        quote_errors=quote_meta.get("errors", []),
        kline_sources=_kline_sources(settings),
        kline_usage=kline_usage,
        kline_errors=kline_errors,
        disabled_kline_sources=disabled_kline_sources,
    )
    snapshot["breadth"].update(
        {
            "data_source": "local_unified_market_pool",
            "quote_pool_status": quote_meta.get("status"),
            "quote_source_count": quote_meta.get("source_count"),
            "quote_sources": quote_meta.get("sources", []),
            "quote_cache_age_seconds": quote_meta.get("cache_age_seconds"),
            "quote_pool_min_source_count": settings.market_pool_min_source_count,
            "generated_at": datetime.utcnow().isoformat(),
        }
    )
    snapshot["snapshot_id"] = f"snapshot_pool_{snapshot['trade_date']}_{datetime.utcnow().strftime('%H%M%S')}"
    return stocks, snapshot


def get_market_snapshot_from_pool(db: Session, refresh: bool = False) -> dict[str, Any]:
    settings = get_settings()
    quotes, quote_meta = _load_or_refresh_quotes(db, settings) if refresh else _load_quotes_from_pool(db, settings)
    quotes = _select_candidates(quotes, min(settings.market_scan_size, 300))
    if not quotes:
        raise MarketDataError("统一行情池没有可用市场快照。")
    trade_date = _latest_trade_date_from_quotes(quotes)
    up_count = sum(1 for item in quotes if item.get("pct", 0) > 0)
    down_count = sum(1 for item in quotes if item.get("pct", 0) < 0)
    limit_up_count = sum(1 for item in quotes if item.get("pct", 0) >= 9.8)
    limit_down_count = sum(1 for item in quotes if item.get("pct", 0) <= -9.8)
    industries = _top_industries_from_quotes(quotes)
    breadth = _breadth_from_counts(len(quotes), up_count, down_count, limit_up_count, limit_down_count)
    breadth.update(
        {
            "data_source": "local_unified_market_pool",
            "quote_pool_status": quote_meta.get("status"),
            "quote_source_count": quote_meta.get("source_count"),
            "quote_sources": quote_meta.get("sources", []),
            "quote_cache_age_seconds": quote_meta.get("cache_age_seconds"),
            "generated_at": datetime.utcnow().isoformat(),
            "hot_theme": " / ".join(industries[:2]) if industries else "未知",
        }
    )
    return {
        "snapshot_id": f"snapshot_pool_{trade_date}",
        "trade_date": trade_date,
        "sample_count": len(quotes),
        "up_count": up_count,
        "down_count": down_count,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "main_industries": industries,
        "breadth": breadth,
    }


def refresh_quote_pool(db: Session) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _refresh_quote_pool(db, get_settings())


def local_pool_recommendation_readiness(db: Session) -> dict[str, Any]:
    settings = get_settings()
    target_count = max(1, int(settings.market_candidate_count))
    lookahead_count = target_count * max(1, int(settings.market_candidate_lookahead_multiplier))
    min_bar_count = 30
    messages: list[str] = []
    examples: list[dict[str, Any]] = []

    active_sources = enabled_sources(db, "quote", _quote_sources(settings))
    if not active_sources:
        return {
            "status": "blocked",
            "can_run_local_recommendation": False,
            "has_missing_data": True,
            "required_candidate_count": target_count,
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

    quotes = _load_cached_quotes(db, allowed_sources=set(active_sources), skip_metrics=True)
    quote_trade_dates = [str(item.get("trade_date") or "") for item in quotes if item.get("trade_date")]
    candidates = _select_candidates(quotes, min(len(quotes), lookahead_count)) if quotes else []

    # Batch load bar summaries for all candidates in a single query
    candidate_codes = [str(item.get("code") or "") for item in candidates]
    bar_summaries = _load_cached_bars_summary(db, candidate_codes, min_bar_count) if candidate_codes else {}

    ready_count = 0
    missing_count = 0
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
            missing_count += 1
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

    can_run = len(quotes) >= target_count and ready_count > 0
    has_missing_data = len(quotes) < target_count or len(candidates) < target_count or ready_count < target_count
    if len(quotes) < target_count:
        status = "blocked"
        messages.append(f"本地统一报价不足，当前 {len(quotes)} 条，至少需要 {target_count} 条。")
    elif not candidates:
        status = "blocked"
        messages.append("本地报价存在，但没有股票通过候选过滤条件。")
    elif ready_count == 0:
        status = "blocked"
        messages.append("候选股的本地日K全部缺失或未覆盖报价日期。")
    elif ready_count < target_count:
        status = "warning"
        messages.append(f"可运行本地推荐，但可用候选只有 {ready_count} 只，目标候选数为 {target_count}。")
    else:
        status = "ready"
        messages.append(f"本地池可直接运行推荐，已有 {ready_count} 只候选股报价与日K覆盖完整。")

    if missing_count or stale_count:
        messages.append(f"当前存在数据缺口：日K缺失 {missing_count} 只，日K过期 {stale_count} 只。")

    return {
        "status": status,
        "can_run_local_recommendation": can_run,
        "has_missing_data": has_missing_data,
        "required_candidate_count": target_count,
        "quote": {
            "available_count": len(quotes),
            "candidate_count": len(candidates),
            "latest_trade_date": max(quote_trade_dates) if quote_trade_dates else None,
            "active_sources": active_sources,
        },
        "kline": {
            "checked_count": checked_count,
            "ready_count": ready_count,
            "missing_count": missing_count,
            "stale_count": stale_count,
            "min_bars": min_bar_count,
            "examples": examples,
        },
        "messages": messages,
    }


def _load_quotes_from_pool(db: Session, settings: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    active_sources = enabled_sources(db, "quote", _quote_sources(settings))
    if not active_sources:
        raise MarketDataError("所有报价数据源都已关闭，请至少启用一个报价源。")
    quotes = _load_cached_quotes(db, allowed_sources=set(active_sources))
    if len(quotes) < int(settings.market_candidate_count):
        raise MarketDataError(
            f"本地数据池可用报价不足，当前 {len(quotes)} 条，至少需要 {settings.market_candidate_count} 条。"
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
    if len(cached) >= int(settings.market_candidate_count):
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
            _persist_raw_quotes(db, source, trade_date, rows)
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
    _persist_unified_quotes(db, merged)
    db.commit()
    return merged, _quote_meta("refreshed", merged, errors=errors)


def _load_cached_quotes(
    db: Session,
    max_age: timedelta | None = None,
    allowed_sources: set[str] | None = None,
    skip_metrics: bool = False,
) -> list[dict[str, Any]]:
    cutoff = datetime.utcnow() - max_age if max_age is not None else None
    stmt = (
        select(UnifiedQuote)
        .where(UnifiedQuote.freshness_status != "inactive")
        .order_by(UnifiedQuote.quality_score.desc(), UnifiedQuote.fetched_at.desc())
    )
    if cutoff is not None:
        stmt = stmt.where(UnifiedQuote.fetched_at >= cutoff)
    rows = db.scalars(stmt).all()
    local_metrics = {} if skip_metrics else _quote_metrics_from_bars(db, [row.code for row in rows])
    quotes = []
    for row in rows:
        quote = _loads(row.quote_json, {})
        if not quote:
            continue
        quote["trade_date"] = row.trade_date
        quote["source_count"] = row.source_count
        quote["quality_score"] = row.quality_score
        quote["freshness_status"] = row.freshness_status if cutoff is None or row.fetched_at >= cutoff else "stale_cache"
        quote["fetched_at"] = row.fetched_at.isoformat()
        quote["source_trace"] = _loads(row.source_trace_json, {})
        if not skip_metrics:
            _apply_local_quote_metrics(quote, local_metrics.get(row.code, {}))
        quote["raw_sources"] = quote["source_trace"].get("raw_sources", [])
        quote["conflicts"] = quote["source_trace"].get("conflicts", [])
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
        select(DailyBar)
        .where(DailyBar.code.in_(unique_codes), DailyBar.trade_date <= latest_allowed_date)
        .order_by(DailyBar.code, DailyBar.trade_date.desc())
    ).all()

    bars_by_code: dict[str, list[DailyBar]] = defaultdict(list)
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
        _persist_normalized_bars(db, bars)
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
            _persist_raw_bars(db, source, bars)
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
        select(func.max(DailyBar.trade_date)).where(DailyBar.code == code)
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
        "quote_time": datetime.utcnow().isoformat(),
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

    coverage = sum(1 for field in NUMERIC_QUOTE_FIELDS if result.get(field) not in {None, 0.0})
    source_count = int(result["source_count"])
    min_sources = int(getattr(settings, "market_pool_min_source_count", 2))
    quality = 48 + min(source_count, 4) * 12 + coverage / max(1, len(NUMERIC_QUOTE_FIELDS)) * 24
    if source_count < min_sources:
        conflicts.append(f"source_count: 仅 {source_count} 个报价源")
        quality -= 12
    quality -= min(30, len(conflicts) * 8)
    result["quality_score"] = round(max(0.0, min(100.0, quality)), 2)
    result["freshness_status"] = "fresh"
    result["conflicts"] = conflicts
    result["source_trace"] = {
        "raw_sources": result["raw_sources"],
        "source_count": source_count,
        "field_sources": field_sources,
        "conflicts": conflicts,
        "merged_at": datetime.utcnow().isoformat(),
    }
    return result


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
    if source_count < int(get_settings().market_pool_min_source_count):
        missing.add("quote_consensus")
    if quote.get("conflicts"):
        missing.add("quote_conflict")
    if quote.get("freshness_status") == "stale_cache":
        missing.add("quote_freshness")
    quality["missing_fields"] = sorted(missing)
    return quality


def _persist_raw_quotes(db: Session, source: str, trade_date: str, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        code = str(row.get("code") or "")
        if not code:
            continue
        existing = db.scalar(
            select(RawQuote).where(
                RawQuote.source == source,
                RawQuote.code == code,
                RawQuote.trade_date == trade_date,
            )
        )
        payload = _dumps(row)
        quality = _dumps({"field_count": sum(1 for value in row.values() if value not in {None, "", 0})})
        if existing is None:
            db.add(RawQuote(source=source, code=code, trade_date=trade_date, payload_json=payload, quality_json=quality))
        else:
            existing.payload_json = payload
            existing.quality_json = quality
            existing.fetched_at = datetime.utcnow()


def _persist_unified_quotes(db: Session, rows: list[dict[str, Any]]) -> None:
    trade_date = _latest_trade_date_from_quotes(rows)
    active_codes = {row["code"] for row in rows}
    for row in rows:
        existing = db.scalar(select(UnifiedQuote).where(UnifiedQuote.code == row["code"], UnifiedQuote.trade_date == trade_date))
        quote_json = _dumps({key: value for key, value in row.items() if key not in {"source_trace", "conflicts"}})
        trace_json = _dumps(row.get("source_trace", {}))
        if existing is None:
            db.add(
                UnifiedQuote(
                    code=row["code"],
                    trade_date=trade_date,
                    quote_json=quote_json,
                    source_trace_json=trace_json,
                    source_count=int(row.get("source_count") or 0),
                    quality_score=float(row.get("quality_score") or 0),
                    freshness_status=str(row.get("freshness_status") or "fresh"),
                )
            )
        else:
            existing.quote_json = quote_json
            existing.source_trace_json = trace_json
            existing.source_count = int(row.get("source_count") or 0)
            existing.quality_score = float(row.get("quality_score") or 0)
            existing.freshness_status = str(row.get("freshness_status") or "fresh")
            existing.fetched_at = datetime.utcnow()
            existing.updated_at = datetime.utcnow()
    if active_codes:
        inactive_rows = db.scalars(
            select(UnifiedQuote).where(
                UnifiedQuote.trade_date == trade_date,
                UnifiedQuote.code.not_in(active_codes),
            )
        ).all()
        for row in inactive_rows:
            row.freshness_status = "inactive"
            row.updated_at = datetime.utcnow()


def _persist_raw_bars(db: Session, source: str, bars: list[dict[str, Any]]) -> None:
    latest_allowed_date = _latest_allowed_bar_date()
    for bar in bars:
        if str(bar.get("trade_date") or "") > latest_allowed_date:
            continue
        existing = db.scalar(
            select(RawDailyBar).where(
                RawDailyBar.source == source,
                RawDailyBar.code == bar["code"],
                RawDailyBar.trade_date == bar["trade_date"],
            )
        )
        payload = _dumps(bar)
        if existing is None:
            db.add(RawDailyBar(source=source, code=bar["code"], trade_date=bar["trade_date"], payload_json=payload))
        else:
            existing.payload_json = payload
            existing.fetched_at = datetime.utcnow()


def _persist_normalized_bars(db: Session, bars: list[dict[str, Any]]) -> None:
    if not bars:
        return
    latest_allowed_date = _latest_allowed_bar_date()
    code = str(bars[0].get("code") or "")
    filtered = [bar for bar in bars if str(bar.get("trade_date") or "") <= latest_allowed_date]
    for bar in filtered:
        existing = db.scalar(select(DailyBar).where(DailyBar.code == bar["code"], DailyBar.trade_date == bar["trade_date"]))
        if existing is None:
            db.add(DailyBar(**bar))
        else:
            existing.open = float(bar["open"])
            existing.high = float(bar["high"])
            existing.low = float(bar["low"])
            existing.close = float(bar["close"])
            existing.volume = float(bar.get("volume") or 0)
            existing.amount = float(bar.get("amount") or 0)
            existing.pct_chg = float(bar.get("pct_chg") or 0)
            existing.turnover_rate = float(bar.get("turnover_rate") or 0)

    if not code or not filtered:
        return

    # Compute and persist indicators for this code's bars
    sorted_bars = sorted(filtered, key=lambda b: b["trade_date"])
    db.flush()

    # Update DailyBar indicator columns
    bar_indicators = compute_daily_bar_indicators(code, sorted_bars)
    for bar in filtered:
        trade_date = bar["trade_date"]
        ind = bar_indicators.get(trade_date, {})
        row = db.scalar(select(DailyBar).where(DailyBar.code == code, DailyBar.trade_date == trade_date))
        if row is not None:
            row.ma5 = ind.get("ma5")
            row.ma10 = ind.get("ma10")
            row.ma20 = ind.get("ma20")
            row.ma60 = ind.get("ma60")
            row.volume_ratio_5d = ind.get("volume_ratio_5d")

    db.flush()

    # Upsert DailyIndicator rows
    daily_indicators = compute_daily_indicators(code, sorted_bars)
    for bar in filtered:
        trade_date = bar["trade_date"]
        ind = daily_indicators.get(trade_date, {})
        existing_ind = db.scalar(
            select(DailyIndicator).where(
                DailyIndicator.code == code, DailyIndicator.trade_date == trade_date
            )
        )
        if existing_ind is None:
            db.add(DailyIndicator(code=code, trade_date=trade_date, **ind))
        else:
            for key, value in ind.items():
                setattr(existing_ind, key, value)


def _load_cached_bars(db: Session, code: str, limit: int) -> list[dict[str, Any]]:
    latest_allowed_date = _latest_allowed_bar_date()
    rows = db.scalars(
        select(DailyBar)
        .where(DailyBar.code == code, DailyBar.trade_date <= latest_allowed_date)
        .order_by(DailyBar.trade_date.desc())
        .limit(limit)
    ).all()

    # Load DailyIndicator rows for this code (same date range)
    trade_dates = [row.trade_date for row in rows]
    if trade_dates:
        indicator_rows = db.scalars(
            select(DailyIndicator)
            .where(
                DailyIndicator.code == code,
                DailyIndicator.trade_date.in_(trade_dates),
            )
        ).all()
        indicator_map = {ind.trade_date: ind for ind in indicator_rows}
    else:
        indicator_map = {}

    result = []
    for row in reversed(rows):
        bar = {
            "code": row.code,
            "trade_date": row.trade_date,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
            "amount": row.amount,
            "pct_chg": row.pct_chg,
            "turnover_rate": row.turnover_rate,
            "ma5": row.ma5,
            "ma10": row.ma10,
            "ma20": row.ma20,
            "ma60": row.ma60,
            "volume_ratio_5d": row.volume_ratio_5d,
        }
        # Attach DailyIndicator data if available
        ind = indicator_map.get(row.trade_date)
        if ind is not None:
            bar["_indicator"] = {
                "rsi_6": ind.rsi_6,
                "rsi_12": ind.rsi_12,
                "rsi_24": ind.rsi_24,
                "macd_dif": ind.macd_dif,
                "macd_dea": ind.macd_dea,
                "macd_bar": ind.macd_bar,
                "bias_ma5": ind.bias_ma5,
                "bias_ma10": ind.bias_ma10,
                "bias_ma20": ind.bias_ma20,
                "breakout20": ind.breakout20,
                "breakout60": ind.breakout60,
                "upper_shadow_count20": ind.upper_shadow_count20,
                "max_dd20": ind.max_dd20,
                "max_dd60": ind.max_dd60,
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
    now = datetime.utcnow()
    # Use raw INSERT OR REPLACE to avoid UNIQUE constraint race with multi-source fetch
    from sqlalchemy import text
    if status == "success":
        db.execute(text("""
            INSERT INTO data_source_health (source, source_type, success_count, failure_count, empty_count,
                last_success_at, last_failure_at, last_error, latency_ms, updated_at)
            VALUES (:source, :source_type, 1, 0, 0, :now, NULL, NULL, :latency, :now)
            ON CONFLICT(source, source_type) DO UPDATE SET
                success_count = data_source_health.success_count + 1,
                last_success_at = :now,
                last_error = NULL,
                latency_ms = :latency,
                updated_at = :now
        """), {"source": source, "source_type": source_type, "now": now, "latency": round(float(latency_ms), 2)})
    elif status == "empty":
        db.execute(text("""
            INSERT INTO data_source_health (source, source_type, success_count, failure_count, empty_count,
                last_success_at, last_failure_at, last_error, latency_ms, updated_at)
            VALUES (:source, :source_type, 0, 0, 1, NULL, :now, :err, :latency, :now)
            ON CONFLICT(source, source_type) DO UPDATE SET
                empty_count = data_source_health.empty_count + 1,
                last_failure_at = :now,
                last_error = :err,
                latency_ms = :latency,
                updated_at = :now
        """), {"source": source, "source_type": source_type, "now": now, "err": "empty result", "latency": round(float(latency_ms), 2)})
    else:
        db.execute(text("""
            INSERT INTO data_source_health (source, source_type, success_count, failure_count, empty_count,
                last_success_at, last_failure_at, last_error, latency_ms, updated_at)
            VALUES (:source, :source_type, 0, 1, 0, NULL, :now, :err, :latency, :now)
            ON CONFLICT(source, source_type) DO UPDATE SET
                failure_count = data_source_health.failure_count + 1,
                last_failure_at = :now,
                last_error = :err,
                latency_ms = :latency,
                updated_at = :now
        """), {"source": source, "source_type": source_type, "now": now, "err": error or "unknown", "latency": round(float(latency_ms), 2)})

def _quote_meta(status: str, quotes: list[dict[str, Any]], errors: list[str] | None = None) -> dict[str, Any]:
    fetched_times = [
        datetime.fromisoformat(str(item.get("fetched_at")))
        for item in quotes
        if item.get("fetched_at") and "T" in str(item.get("fetched_at"))
    ]
    newest = max(fetched_times) if fetched_times else datetime.utcnow()
    sources = sorted({source for quote in quotes for source in quote.get("raw_sources", [])})
    source_counts = [int(quote.get("source_count") or 0) for quote in quotes]
    return {
        "status": status,
        "count": len(quotes),
        "sources": sources,
        "source_count": round(statistics.fmean(source_counts), 2) if source_counts else 0,
        "cache_age_seconds": max(0, int((datetime.utcnow() - newest).total_seconds())),
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
            DailyBar.code,
            func.count().label("bar_count"),
            func.max(DailyBar.trade_date).label("latest_trade_date"),
        )
        .where(DailyBar.code.in_(codes), DailyBar.trade_date <= latest_allowed_date)
        .group_by(DailyBar.code)
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
    today = today or date.today()
    if today.weekday() == 5:
        return (today - timedelta(days=1)).isoformat()
    if today.weekday() == 6:
        return (today - timedelta(days=2)).isoformat()
    return today.isoformat()


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


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default
