from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.tables import DailyBar, DailyIndicator, DataSourceConfig, DataSourceHealth, RawDailyBar, RawQuote, UnifiedQuote
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
    latest_allowed_bar_date = _latest_allowed_bar_date()
    raw_quote_count = _count(db, RawQuote)
    raw_bar_count = _count_with_limit(db, RawDailyBar, RawDailyBar.trade_date <= latest_allowed_bar_date)
    unified_quote_count = _count(db, UnifiedQuote)
    daily_bar_code_count = db.scalar(
        select(func.count(func.distinct(DailyBar.code)))
        .where(DailyBar.trade_date <= latest_allowed_bar_date)
    ) or 0
    active_unified_quote_count = db.scalar(
        select(func.count()).select_from(UnifiedQuote).where(UnifiedQuote.freshness_status != "inactive")
    ) or 0
    latest_unified = db.scalar(select(func.max(UnifiedQuote.fetched_at)))
    latest_bar_date = db.scalar(select(func.max(DailyBar.trade_date)).where(DailyBar.trade_date <= latest_allowed_bar_date))
    latest_quote_date = db.scalar(select(func.max(UnifiedQuote.trade_date)))
    from app.services.market_pool import local_pool_recommendation_readiness

    # Indicator coverage stats
    daily_indicator_count = _count_with_limit(db, DailyIndicator, DailyIndicator.trade_date <= latest_allowed_bar_date)
    daily_bar_row_count = _count_with_limit(db, DailyBar, DailyBar.trade_date <= latest_allowed_bar_date)
    bars_with_indicators = _count_with_limit(
        db, DailyBar,
        (DailyBar.trade_date <= latest_allowed_bar_date) & (DailyBar.ma5.isnot(None))
    )
    indicator_coverage_pct = round(bars_with_indicators / daily_bar_row_count * 100, 1) if daily_bar_row_count else 0.0

    return {
        "summary": {
            "raw_quote_count": raw_quote_count,
            "raw_bar_count": raw_bar_count,
            "unified_quote_count": unified_quote_count,
            "active_unified_quote_count": active_unified_quote_count,
            "daily_bar_count": daily_bar_code_count,
            "daily_bar_row_count": daily_bar_row_count,
            "daily_indicator_count": daily_indicator_count,
            "indicator_coverage_pct": indicator_coverage_pct,
            "latest_quote_date": latest_quote_date,
            "latest_bar_date": latest_bar_date,
            "latest_unified_at": latest_unified.isoformat() if latest_unified else None,
        },
        "quote_sources": _raw_quote_source_rows(db),
        "kline_sources": _raw_bar_source_rows(db),
        "recommendation_readiness": local_pool_recommendation_readiness(db),
        "unified_quotes": _unified_quote_rows(db, limit=unified_limit, offset=unified_offset),
        "unified_quotes_total": unified_quote_count,
        "daily_bars": _daily_bar_rows(db, limit=bars_limit, offset=bars_offset),
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
        if row is None:
            row = DataSourceConfig(source=source, source_type=source_type, enabled=True, priority=index)
            db.add(row)
            db.flush()
        if row.enabled:
            active.append(source)
    if active:
        return active
    return []


def sync_data_source_configs(db: Session) -> None:
    settings = get_settings()
    desired = {
        "quote": _quote_sources(settings),
        "kline": _kline_sources(settings),
    }
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
    if changed:
        db.commit()


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


def _latest_allowed_bar_date(today: date | None = None) -> str:
    today = today or date.today()
    if today.weekday() == 5:
        return (today - timedelta(days=1)).isoformat()
    if today.weekday() == 6:
        return (today - timedelta(days=2)).isoformat()
    return today.isoformat()


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        import json

        return json.loads(raw)
    except Exception:  # noqa: BLE001 - best effort dashboard payload
        return default
