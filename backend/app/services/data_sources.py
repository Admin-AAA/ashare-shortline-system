from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import logging
logger = logging.getLogger(__name__)

from app.core.config import get_settings
from app.models.tables import (
    CapitalFlow,
    ConceptSector,
    ConceptSectorStock,
    DailyBar,
    DailyIndicator,
    DataSourceConfig,
    DataSourceHealth,
    DragonTiger,
    LimitUpBoard,
    NewsAnnouncement,
    RawCapitalFlow,
    RawConceptSector,
    RawDailyBar,
    RawDragonTiger,
    RawLimitUpBoard,
    RawNewsAnnouncement,
    RawQuote,
    UnifiedQuote,
)
from app.services.market_auxiliary import (
    _capital_flow_sources,
    _concept_sector_sources,
    _dragon_tiger_sources,
    _limit_up_sources,
    _news_sources,
)
from app.services.market_data import _kline_sources, _quote_sources


def list_data_sources(db: Session) -> list[dict[str, Any]]:
    sync_data_source_configs(db)
    configs = {
        (row.source, row.source_type): row
        for row in db.scalars(select(DataSourceConfig).order_by(DataSourceConfig.source_type, DataSourceConfig.priority)).all()
    }
    health = {
        (row.source, row.source_type): row
        for row in db.scalars(select(DataSourceHealth)).all()
    }
    keys = sorted(set(configs) | set(health), key=lambda item: (item[1], configs.get(item).priority if configs.get(item) else 999, item[0]))
    rows = []
    for key in keys:
        source, source_type = key
        config = configs.get(key)
        health_row = health.get(key)
        enabled = bool(config.enabled) if config else False
        success_count = int(health_row.success_count or 0) if health_row else 0
        failure_count = int(health_row.failure_count or 0) if health_row else 0
        empty_count = int(health_row.empty_count or 0) if health_row else 0
        total = success_count + failure_count + empty_count
        rows.append(
            {
                "source": source,
                "source_type": source_type,
                "enabled": enabled,
                "priority": config.priority if config else 999,
                "note": config.note if config else None,
                "success_count": success_count,
                "failure_count": failure_count,
                "empty_count": empty_count,
                "success_rate": round(success_count / total, 3) if total else None,
                "latency_ms": health_row.latency_ms if health_row else None,
                "last_success_at": health_row.last_success_at.isoformat() if health_row and health_row.last_success_at else None,
                "last_failure_at": health_row.last_failure_at.isoformat() if health_row and health_row.last_failure_at else None,
                "last_error": health_row.last_error if health_row else None,
                "status": _source_status(enabled, health_row),
                "updated_at": config.updated_at.isoformat() if config and config.updated_at else None,
            }
        )
    return rows


def get_local_pool_status(
    db: Session,
    unified_limit: int = 50,
    unified_offset: int = 0,
    bars_limit: int = 50,
    bars_offset: int = 0,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    latest_allowed_bar_date = _latest_allowed_bar_date()
    latest_quote_date_for_coverage = db.scalar(select(func.max(UnifiedQuote.trade_date)))
    coverage_trade_date = _coverage_reference_date(latest_quote_date_for_coverage, latest_allowed_bar_date)

    # ── 批量加载计数（使用标量子查询避免笛卡尔积）──
    raw_quote_count = _count(db, RawQuote)
    raw_bar_count = _count_with_limit(db, RawDailyBar, RawDailyBar.trade_date <= latest_allowed_bar_date)
    unified_quote_count = _count(db, UnifiedQuote)
    active_unified_quote_count = _count_with_limit(db, UnifiedQuote, UnifiedQuote.freshness_status != "inactive")
    daily_bar_code_count = db.scalar(
        select(func.count(func.distinct(DailyBar.code)))
        .where(DailyBar.trade_date <= latest_allowed_bar_date)
    ) or 0
    active_daily_bar_code_count = db.scalar(
        select(func.count(func.distinct(DailyBar.code)))
        .where(DailyBar.trade_date == coverage_trade_date)
    ) or 0
    daily_indicator_count = _count_with_limit(db, DailyIndicator, DailyIndicator.trade_date <= latest_allowed_bar_date)
    daily_bar_row_count = _count_with_limit(db, DailyBar, DailyBar.trade_date <= latest_allowed_bar_date)
    bars_with_indicators = _count_with_limit(
        db, DailyBar,
        (DailyBar.trade_date <= latest_allowed_bar_date) & (DailyBar.ma5.isnot(None))
    )
    indicator_coverage_pct = round(bars_with_indicators / daily_bar_row_count * 100, 1) if daily_bar_row_count else 0.0
    t_counts = time.perf_counter()

    # ── 日期/时间聚合 ──
    latest_unified = db.scalar(select(func.max(UnifiedQuote.fetched_at)))
    latest_bar_date = db.scalar(select(func.max(DailyBar.trade_date)).where(DailyBar.trade_date <= latest_allowed_bar_date))
    latest_quote_date = latest_quote_date_for_coverage
    t_dates = time.perf_counter()

    # ── 一次性加载全部活跃 UnifiedQuote 行（供多处复用）──
    all_quote_rows = db.scalars(
        select(UnifiedQuote).where(UnifiedQuote.freshness_status != "inactive")
    ).all()
    active_unified_codes = [row.code for row in all_quote_rows]
    t_load_quotes = time.perf_counter()

    # ── 批量计算 full_market_daily_bar_code_count ──
    full_market_daily_bar_code_count = (
        db.scalar(
            select(func.count(func.distinct(DailyBar.code))).where(
                DailyBar.code.in_(active_unified_codes) if active_unified_codes else [],
                DailyBar.trade_date <= coverage_trade_date,
            )
        ) or 0
    ) if active_unified_codes else 0
    t_full_market_bars = time.perf_counter()

    from app.services.market_pool import _loads, _apply_local_quote_metrics, local_pool_recommendation_readiness
    from app.services.market_data import _candidate_filter_summary, _select_candidates

    # ── 从已加载的 ORM 行直接构建 quotes（跳过 _load_cached_quotes 的重复查询+JSON反序列化）──
    all_quotes = _unified_rows_to_quotes(all_quote_rows, skip_metrics=True)
    t_quotes_to_dicts = time.perf_counter()

    # ── readiness 复用已加载的 quotes ──
    readiness = local_pool_recommendation_readiness(db, cached_quotes=all_quotes)
    t_readiness = time.perf_counter()

    eligible_quotes = _select_candidates(all_quotes, len(all_quotes)) if all_quotes else []
    eligible_codes = [str(item.get("code") or "") for item in eligible_quotes if item.get("code")]
    eligible_bar_stats = _eligible_daily_bar_stats(db, eligible_codes, coverage_trade_date)
    base_filter_count = len(eligible_codes)
    candidate_filter = _candidate_filter_summary(all_quotes)
    daily_bar_coverage_rate = (
        round(int(full_market_daily_bar_code_count or 0) / active_unified_quote_count * 100, 1)
        if active_unified_quote_count
        else 0.0
    )
    t_eligible = time.perf_counter()

    # ── 辅助数据行数 ──
    limit_up_board_count = _count(db, LimitUpBoard)
    concept_sector_count = _count(db, ConceptSector)
    capital_flow_count = _count(db, CapitalFlow)
    dragon_tiger_count = _count(db, DragonTiger)
    news_count = _count(db, NewsAnnouncement)
    t_aux = time.perf_counter()

    paged_quotes = _unified_quote_rows_from_rows(all_quote_rows, limit=unified_limit, offset=unified_offset, db=db)
    t_paged = time.perf_counter()
    paged_bars = _daily_bar_rows(db, limit=bars_limit, offset=bars_offset)
    t_paged_bars = time.perf_counter()

    logger.info(
        "get_local_pool_status timing: counts=%.0fms dates=%.0fms load_quotes=%.0fms full_market_bars=%.0fms quotes_to_dicts=%.0fms readiness=%.0fms eligible=%.0fms aux=%.0fms paged=%.0fms paged_bars=%.0fms total=%.0fms",
        (t_counts - t0) * 1000, (t_dates - t_counts) * 1000, (t_load_quotes - t_dates) * 1000,
        (t_full_market_bars - t_load_quotes) * 1000, (t_quotes_to_dicts - t_full_market_bars) * 1000,
        (t_readiness - t_quotes_to_dicts) * 1000, (t_eligible - t_readiness) * 1000,
        (t_aux - t_eligible) * 1000, (t_paged - t_aux) * 1000, (t_paged_bars - t_paged) * 1000,
        (t_paged_bars - t0) * 1000,
    )

    return {
        "summary": {
            "raw_quote_count": raw_quote_count,
            "raw_bar_count": raw_bar_count,
            "unified_quote_count": unified_quote_count,
            "active_unified_quote_count": active_unified_quote_count,
            "eligible_quote_count": base_filter_count,
            "candidate_filter": candidate_filter,
            "daily_bar_count": daily_bar_code_count,
            "full_market_daily_bar_count": int(full_market_daily_bar_code_count or 0),
            "active_daily_bar_count": active_daily_bar_code_count,
            "eligible_daily_bar_count": eligible_bar_stats["ready_count"],
            "daily_bar_coverage_rate": daily_bar_coverage_rate,
            "eligible_daily_bar_coverage_rate": eligible_bar_stats["coverage_rate"],
            "daily_bar_missing_count": eligible_bar_stats["missing_count"],
            "daily_bar_stale_count": eligible_bar_stats["stale_count"],
            "daily_bar_short_history_count": eligible_bar_stats["short_history_count"],
            "actual_recommend_scan_count": int((readiness.get("scan") or {}).get("actual_scan_count") or 0),
            "skill_input_count": int((readiness.get("scan") or {}).get("skill_input_count") or 0),
            "daily_bar_row_count": daily_bar_row_count,
            "daily_indicator_count": daily_indicator_count,
            "indicator_coverage_pct": indicator_coverage_pct,
            "latest_quote_date": latest_quote_date,
            "latest_bar_date": latest_bar_date,
            "coverage_trade_date": coverage_trade_date,
            "latest_unified_at": latest_unified.isoformat() if latest_unified else None,
            "limit_up_board_count": limit_up_board_count,
            "concept_sector_count": concept_sector_count,
            "capital_flow_count": capital_flow_count,
            "dragon_tiger_count": dragon_tiger_count,
            "news_count": news_count,
        },
        "quote_sources": _raw_quote_source_rows(db),
        "kline_sources": _raw_bar_source_rows(db),
        "auxiliary_sources": _raw_aux_source_rows(db),
        "recommendation_readiness": readiness,
        "unified_quotes": paged_quotes,
        "unified_quotes_total": unified_quote_count,
        "daily_bars": paged_bars,
        "daily_bars_total": daily_bar_code_count,
    }


def set_data_source_enabled(
    db: Session,
    source: str,
    source_type: str,
    enabled: bool,
    note: str | None = None,
) -> dict[str, Any] | None:
    sync_data_source_configs(db)
    row = db.get(DataSourceConfig, {"source": source, "source_type": source_type})
    if row is None:
        return None
    row.enabled = enabled
    row.note = note
    row.updated_at = datetime.utcnow()
    db.commit()
    return next(
        (
            item
            for item in list_data_sources(db)
            if item["source"] == source and item["source_type"] == source_type
        ),
        None,
    )


def enabled_sources(db: Session, source_type: str, configured_sources: list[str]) -> list[str]:
    sync_data_source_configs(db)
    rows = {
        row.source: row
        for row in db.scalars(select(DataSourceConfig).where(DataSourceConfig.source_type == source_type)).all()
    }
    active = []
    for index, source in enumerate(configured_sources):
        row = rows.get(source)
        if row is None or row.enabled:
            active.append(source)
    if active:
        return active
    return []


def sync_data_source_configs(db: Session) -> None:
    settings = get_settings()
    desired = {
        "quote": _quote_sources(settings),
        "kline": _kline_sources(settings),
        "limit_up": _limit_up_sources(settings),
        "concept_sector": _concept_sector_sources(settings),
        "capital_flow": _capital_flow_sources(settings),
        "dragon_tiger": _dragon_tiger_sources(settings),
        "news": _news_sources(settings),
    }
    last_error: IntegrityError | None = None
    for _attempt in range(3):
        changed = False
        for source_type, sources in desired.items():
            for index, source in enumerate(sources):
                row = db.get(DataSourceConfig, {"source": source, "source_type": source_type})
                if row is None:
                    db.add(DataSourceConfig(source=source, source_type=source_type, enabled=True, priority=index))
                    changed = True
                elif row.priority != index:
                    row.priority = index
                    row.updated_at = datetime.utcnow()
                    changed = True
        if not changed:
            return
        try:
            db.commit()
            return
        except IntegrityError as exc:
            last_error = exc
            db.rollback()
    if last_error is not None:
        raise last_error


def _source_status(enabled: bool, health: DataSourceHealth | None) -> str:
    if not enabled:
        return "disabled"
    if health is None:
        return "untested"
    if health.last_success_at and (
        not health.last_failure_at or health.last_success_at >= health.last_failure_at
    ):
        return "healthy"
    if int(health.failure_count or 0) or int(health.empty_count or 0):
        return "degraded"
    return "untested"


def _count(db: Session, model: type) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _count_with_limit(db: Session, model: type, predicate: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(predicate)) or 0)


def _eligible_daily_bar_stats(db: Session, codes: list[str], latest_allowed_bar_date: str, min_bars: int = 30) -> dict[str, Any]:
    if not codes:
        return {
            "ready_count": 0,
            "missing_count": 0,
            "stale_count": 0,
            "short_history_count": 0,
            "coverage_rate": 0.0,
        }

    rows = db.execute(
        select(
            DailyBar.code,
            func.count().label("bar_count"),
            func.max(DailyBar.trade_date).label("latest_trade_date"),
        )
        .where(DailyBar.code.in_(codes), DailyBar.trade_date <= latest_allowed_bar_date)
        .group_by(DailyBar.code)
    ).all()
    summary = {
        row.code: {
            "bar_count": int(row.bar_count or 0),
            "latest_trade_date": str(row.latest_trade_date or ""),
        }
        for row in rows
    }

    ready_count = 0
    missing_count = 0
    stale_count = 0
    short_history_count = 0
    for code in codes:
        item = summary.get(code)
        if item is None:
            missing_count += 1
            continue
        bar_count = int(item.get("bar_count") or 0)
        latest_trade_date = str(item.get("latest_trade_date") or "")
        if not latest_trade_date or latest_trade_date < latest_allowed_bar_date:
            stale_count += 1
        elif bar_count < min_bars:
            short_history_count += 1
        else:
            ready_count += 1

    return {
        "ready_count": ready_count,
        "missing_count": missing_count,
        "stale_count": stale_count,
        "short_history_count": short_history_count,
        "coverage_rate": round(ready_count / max(1, len(codes)) * 100, 1),
    }


def _coverage_reference_date(latest_quote_date: str | None, latest_allowed_bar_date: str) -> str:
    if not latest_quote_date:
        return latest_allowed_bar_date
    quote_date = str(latest_quote_date)[:10]
    if not quote_date:
        return latest_allowed_bar_date
    return min(quote_date, latest_allowed_bar_date)


def _raw_quote_source_rows(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            RawQuote.source,
            func.count().label("row_count"),
            func.count(func.distinct(RawQuote.code)).label("code_count"),
            func.max(RawQuote.trade_date).label("latest_trade_date"),
            func.max(RawQuote.fetched_at).label("latest_fetched_at"),
        ).group_by(RawQuote.source)
    ).all()
    return [
        {
            "source": row.source,
            "row_count": int(row.row_count or 0),
            "code_count": int(row.code_count or 0),
            "latest_trade_date": row.latest_trade_date,
            "latest_fetched_at": row.latest_fetched_at.isoformat() if row.latest_fetched_at else None,
        }
        for row in rows
    ]


def _raw_bar_source_rows(db: Session) -> list[dict[str, Any]]:
    latest_allowed_bar_date = _latest_allowed_bar_date()
    rows = db.execute(
        select(
            RawDailyBar.source,
            func.count().label("row_count"),
            func.count(func.distinct(RawDailyBar.code)).label("code_count"),
            func.max(RawDailyBar.trade_date).label("latest_trade_date"),
            func.max(RawDailyBar.fetched_at).label("latest_fetched_at"),
        )
        .where(RawDailyBar.trade_date <= latest_allowed_bar_date)
        .group_by(RawDailyBar.source)
    ).all()
    return [
        {
            "source": row.source,
            "row_count": int(row.row_count or 0),
            "code_count": int(row.code_count or 0),
            "latest_trade_date": row.latest_trade_date,
            "latest_fetched_at": row.latest_fetched_at.isoformat() if row.latest_fetched_at else None,
        }
        for row in rows
    ]


def _unified_quote_rows(db: Session, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    from app.services.market_pool import _quote_metrics_from_bars

    rows = db.scalars(
        select(UnifiedQuote)
        .where(UnifiedQuote.freshness_status != "inactive")
        .order_by(UnifiedQuote.quality_score.desc(), UnifiedQuote.fetched_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    codes = [row.code for row in rows]
    local_metrics = _quote_metrics_from_bars(db, codes) if codes else {}
    return [
        _unified_quote_payload(db, row, precomputed_metrics=local_metrics.get(row.code))
        for row in rows
    ]


def _daily_bar_rows(db: Session, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    latest_allowed_bar_date = _latest_allowed_bar_date()
    rows = db.execute(
        select(
            DailyBar.code,
            func.count().label("bar_count"),
            func.min(DailyBar.trade_date).label("first_trade_date"),
            func.max(DailyBar.trade_date).label("latest_trade_date"),
        )
        .where(DailyBar.trade_date <= latest_allowed_bar_date)
        .group_by(DailyBar.code)
        .order_by(func.max(DailyBar.trade_date).desc(), func.count().desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        {
            "code": row.code,
            "bar_count": int(row.bar_count or 0),
            "first_trade_date": row.first_trade_date,
            "latest_trade_date": row.latest_trade_date,
        }
        for row in rows
    ]


def _unified_quote_payload(db: Session, row: UnifiedQuote, precomputed_metrics: dict[str, float] | None = None) -> dict[str, Any]:
    from app.services.market_pool import _apply_local_quote_metrics, enrich_quote_with_local_metrics

    quote = _loads(row.quote_json, {})
    source_trace = _loads(row.source_trace_json, {})
    if isinstance(quote, dict):
        quote["source_trace"] = source_trace
        if precomputed_metrics is not None:
            _apply_local_quote_metrics(quote, precomputed_metrics)
        else:
            quote = enrich_quote_with_local_metrics(db, row.code, quote)
        source_trace = quote.pop("source_trace", source_trace)
    raw_sources = source_trace.get("raw_sources") or quote.get("raw_sources") or []
    if isinstance(raw_sources, list) and raw_sources:
        source_count = len({str(source) for source in raw_sources if source})
    else:
        source_count = int(source_trace.get("source_count") or quote.get("source_count") or row.source_count or 0)
    return {
        "code": row.code,
        "trade_date": row.trade_date,
        "source_count": source_count,
        "quality_score": row.quality_score,
        "freshness_status": row.freshness_status,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "quote": quote,
        "source_trace": source_trace,
    }


def _raw_aux_source_rows(db: Session) -> list[dict[str, Any]]:
    """汇总所有辅助数据源的 original source breakdown。"""
    aux_models: dict[str, tuple[type, str]] = {
        "limit_up": (RawLimitUpBoard, "limit_up"),
        "concept_sector": (RawConceptSector, "concept_sector"),
        "capital_flow": (RawCapitalFlow, "capital_flow"),
        "dragon_tiger": (RawDragonTiger, "dragon_tiger"),
        "news": (RawNewsAnnouncement, "news"),
    }
    rows: list[dict[str, Any]] = []
    for aux_type, (model, label) in aux_models.items():
        for row in db.execute(
            select(
                model.source,
                func.count().label("row_count"),
                func.max(model.fetched_at).label("latest_fetched_at"),
            ).group_by(model.source)
        ).all():
            rows.append({
                "source": row.source,
                "source_type": label,
                "row_count": int(row.row_count or 0),
                "latest_fetched_at": row.latest_fetched_at.isoformat() if row.latest_fetched_at else None,
            })
    return rows


def _unified_rows_to_quotes(rows: list, skip_metrics: bool = False) -> list[dict[str, Any]]:
    """从 UnifiedQuote ORM 行直接构建 quote 字典，避免重复查询。"""
    quotes = []
    for row in rows:
        quote = _loads(row.quote_json, {})
        if not quote:
            continue
        quote["trade_date"] = row.trade_date
        quote["source_count"] = row.source_count
        quote["quality_score"] = row.quality_score
        quote["freshness_status"] = row.freshness_status
        quote["fetched_at"] = row.fetched_at.isoformat()
        quote["source_trace"] = _loads(row.source_trace_json, {})
        quote["raw_sources"] = quote["source_trace"].get("raw_sources", [])
        quote["conflicts"] = quote["source_trace"].get("conflicts", [])
        quotes.append(quote)
    return quotes


def _unified_quote_rows_from_rows(all_rows: list, limit: int = 50, offset: int = 0, db: Session = None) -> list[dict[str, Any]]:
    """从预加载的 UnifiedQuote ORM 行构建分页的 quote 展示数据。"""
    from app.services.market_pool import _quote_metrics_from_bars, _apply_local_quote_metrics, enrich_quote_with_local_metrics

    # 按质量分数降序排序后分页
    sorted_rows = sorted(all_rows, key=lambda r: (r.quality_score or 0), reverse=True)
    paged = sorted_rows[offset:offset + limit]
    codes = [row.code for row in paged]
    local_metrics = _quote_metrics_from_bars(db, codes) if codes else {}
    return [
        _unified_quote_payload(db, row, precomputed_metrics=local_metrics.get(row.code))
        for row in paged
    ]


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


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        import json

        return json.loads(raw)
    except Exception:  # noqa: BLE001 - best effort dashboard payload
        return default
