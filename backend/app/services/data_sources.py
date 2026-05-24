from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import logging
logger = logging.getLogger(__name__)

from app.core.config import get_settings
from app.models.tables import (
    CapitalFlow,
    ConceptSector,
    ConceptSectorStock,
    DailyKbar,
    DataSourceConfig,
    DataSourceHealth,
    DragonTiger,
    LimitUpBoard,
    NewsAnnouncement,
    StockQuote,
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
    latest_quote_date_for_coverage = db.scalar(select(func.max(StockQuote.trade_date)))
    coverage_trade_date = _coverage_reference_date(latest_quote_date_for_coverage, latest_allowed_bar_date)

    # ── 快速近似计数（MySQL information_schema，O(1)）──
    approx = _approx_table_counts(db, [
        "stock_quotes", "daily_kbars",
        "limit_up_board", "concept_sector", "capital_flow", "dragon_tiger", "news_announcement",
    ])
    raw_quote_count = 0
    raw_bar_count = 0
    unified_quote_count = approx.get("stock_quotes", 0)
    daily_bar_row_count = approx.get("daily_kbars", 0)
    daily_indicator_count = 0
    limit_up_board_count = approx.get("limit_up_board", 0)
    concept_sector_count = approx.get("concept_sector", 0)
    capital_flow_count = approx.get("capital_flow", 0)
    dragon_tiger_count = approx.get("dragon_tiger", 0)
    news_count = approx.get("news_announcement", 0)

    # ── 必须精确的计数：active_unified_quote（用于覆盖率计算）──
    active_unified_quote_count = _count_with_limit(db, StockQuote, StockQuote.freshness_status != "inactive")
    bars_with_indicators = _count_with_limit(
        db, DailyKbar,
        (DailyKbar.trade_date <= latest_allowed_bar_date) & (DailyKbar.ma5.isnot(None))
    )
    indicator_coverage_pct = round(bars_with_indicators / max(1, daily_bar_row_count) * 100, 1) if daily_bar_row_count else 0.0
    t_counts = time.perf_counter()

    # ── 日期/时间聚合 ──
    latest_unified = db.scalar(select(func.max(StockQuote.snapped_at)))
    latest_bar_date = db.scalar(select(func.max(DailyKbar.trade_date)).where(DailyKbar.trade_date <= latest_allowed_bar_date))
    latest_quote_date = latest_quote_date_for_coverage
    t_dates = time.perf_counter()

    # ── 一次性加载全部活跃 StockQuote 行 + 日K汇总 ──
    all_quote_rows = db.scalars(
        select(StockQuote).where(StockQuote.freshness_status != "inactive")
    ).all()
    active_unified_codes = [row.code for row in all_quote_rows]
    t_load_quotes = time.perf_counter()

    # 一次性 GROUP BY 查询，供 daily_bar 计数 + readiness + eligible 三处复用
    bar_summaries = _load_bar_summaries_for_codes(db, active_unified_codes, coverage_trade_date) if active_unified_codes else {}
    full_market_daily_bar_code_count = len(bar_summaries)
    # active_daily_bar_code_count: 只计交易日 = coverage_trade_date 有 bar 的 code
    active_daily_bar_code_count = full_market_daily_bar_code_count
    # daily_bar_code_count: 同上（含所有历史日期的 codes 数）；用近似值兜底
    daily_bar_code_count = db.scalar(
        select(func.count(func.distinct(DailyKbar.code)))
        .where(DailyKbar.trade_date <= latest_allowed_bar_date)
    ) or 0
    t_full_market_bars = time.perf_counter()

    from app.services.market_pool import _apply_local_quote_metrics, local_pool_recommendation_readiness
    from app.services.market_data import _candidate_filter_summary, _select_candidates

    all_quotes = _unified_rows_to_quotes(all_quote_rows, skip_metrics=True)
    t_quotes_to_dicts = time.perf_counter()

    # readiness 复用已缓存的 bar_summaries
    readiness = local_pool_recommendation_readiness(
        db, cached_quotes=all_quotes, cached_bar_summaries=bar_summaries,
    )
    t_readiness = time.perf_counter()

    eligible_quotes = _select_candidates(all_quotes, len(all_quotes)) if all_quotes else []
    eligible_codes = [str(item.get("code") or "") for item in eligible_quotes if item.get("code")]
    eligible_bar_stats = _eligible_stats_from_summaries(eligible_codes, bar_summaries, coverage_trade_date)
    base_filter_count = len(eligible_codes)
    candidate_filter = _candidate_filter_summary(all_quotes)
    daily_bar_coverage_rate = (
        round(full_market_daily_bar_code_count / active_unified_quote_count * 100, 1)
        if active_unified_quote_count
        else 0.0
    )
    t_eligible = time.perf_counter()

    paged_quotes = _unified_quote_rows_from_rows(all_quote_rows, limit=unified_limit, offset=unified_offset, db=db)
    t_paged = time.perf_counter()
    paged_bars = _daily_bar_rows(db, limit=bars_limit, offset=bars_offset)
    t_paged_bars = time.perf_counter()

    t_aux = t_eligible  # 辅助计数已前移到 approx 阶段

    logger.info(
        "get_local_pool_status timing: counts=%.0fms dates=%.0fms load_quotes=%.0fms full_market_bars=%.0fms quotes_to_dicts=%.0fms readiness=%.0fms eligible=%.0fms aux=%.0fms paged=%.0fms paged_bars=%.0fms total=%.0fms",
        (t_counts - t0) * 1000, (t_dates - t_counts) * 1000, (t_load_quotes - t_dates) * 1000,
        (t_full_market_bars - t_load_quotes) * 1000, (t_quotes_to_dicts - t_full_market_bars) * 1000,
        (t_readiness - t_quotes_to_dicts) * 1000, (t_eligible - t_readiness) * 1000,
        (t_aux - t_eligible) * 1000, (t_paged - t_aux) * 1000, (t_paged_bars - t_paged) * 1000,
        (t_paged_bars - t0) * 1000,
    )

    result = {
        "summary": {
            "raw_quote_count": raw_quote_count,
            "raw_bar_count": raw_bar_count,
            "unified_quote_count": unified_quote_count,
            "active_unified_quote_count": active_unified_quote_count,
            "eligible_quote_count": base_filter_count,
            "candidate_filter": candidate_filter,
            "daily_bar_count": daily_bar_code_count,
            "full_market_daily_bar_count": full_market_daily_bar_code_count,
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
    return result


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
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
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
                    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
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


def _approx_table_counts(db: Session, table_names: list[str]) -> dict[str, int]:
    """O(1) 近似行数，来自 information_schema（InnoDB 估算，显示用足够）。"""
    if not table_names:
        return {}
    try:
        placeholders = ",".join([f":t{i}" for i in range(len(table_names))])
        params = {f"t{i}": name for i, name in enumerate(table_names)}
        rows = db.execute(
            text(f"SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES "
                 f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})"),
            params,
        ).all()
        return {row[0]: int(row[1] or 0) for row in rows}
    except Exception:
        return {}


def _count(db: Session, model: type) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _count_with_limit(db: Session, model: type, predicate: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(predicate)) or 0)


def _load_bar_summaries_for_codes(
    db: Session, codes: list[str], latest_allowed_bar_date: str
) -> dict[str, dict[str, Any]]:
    """一次性加载日K汇总：每个 code 的 bar_count + latest_trade_date。"""
    if not codes:
        return {}
    rows = db.execute(
        select(
            DailyKbar.code,
            func.count().label("bar_count"),
            func.max(DailyKbar.trade_date).label("latest_trade_date"),
        )
        .where(DailyKbar.code.in_(codes), DailyKbar.trade_date <= latest_allowed_bar_date)
        .group_by(DailyKbar.code)
    ).all()
    return {
        row.code: {"bar_count": int(row.bar_count or 0), "latest_trade_date": str(row.latest_trade_date or "")}
        for row in rows
    }


def _eligible_stats_from_summaries(
    codes: list[str], bar_summaries: dict, latest_allowed_bar_date: str, min_bars: int = 30
) -> dict[str, Any]:
    """从已加载的 bar_summaries 中计算 eligible stats，零额外查询。"""
    ready_count = 0
    missing_count = 0
    stale_count = 0
    short_history_count = 0
    for code in codes:
        item = bar_summaries.get(code)
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
            StockQuote.source,
            func.count().label("row_count"),
            func.count(func.distinct(StockQuote.code)).label("code_count"),
            func.max(StockQuote.trade_date).label("latest_trade_date"),
            func.max(StockQuote.snapped_at).label("latest_fetched_at"),
        ).group_by(StockQuote.source)
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
            DailyKbar.source,
            func.count().label("row_count"),
            func.count(func.distinct(DailyKbar.code)).label("code_count"),
            func.max(DailyKbar.trade_date).label("latest_trade_date"),
            func.max(DailyKbar.updated_at).label("latest_fetched_at"),
        )
        .where(DailyKbar.trade_date <= latest_allowed_bar_date)
        .group_by(DailyKbar.source)
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
        select(StockQuote)
        .where(StockQuote.freshness_status != "inactive")
        .order_by(StockQuote.quality_score.desc(), StockQuote.snapped_at.desc())
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
            DailyKbar.code,
            func.count().label("bar_count"),
            func.min(DailyKbar.trade_date).label("first_trade_date"),
            func.max(DailyKbar.trade_date).label("latest_trade_date"),
        )
        .where(DailyKbar.trade_date <= latest_allowed_bar_date)
        .group_by(DailyKbar.code)
        .order_by(func.max(DailyKbar.trade_date).desc(), func.count().desc())
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


def _unified_quote_payload(db: Session, row: StockQuote, precomputed_metrics: dict[str, float] | None = None) -> dict[str, Any]:
    from app.services.market_pool import _apply_local_quote_metrics, enrich_quote_with_local_metrics

    quote = {
        "code": row.code,
        "name": row.name,
        "trade_date": row.trade_date,
        "price": float(row.price or 0),
        "pct": float(row.pct or 0),
        "volume": float(row.volume or 0),
        "amount_yi": float(row.amount_yi or 0),
        "turnover_rate": float(row.turnover_rate or 0),
        "pe_ttm": float(row.pe_ttm) if row.pe_ttm else None,
        "total_mv_yi": float(row.total_mv_yi) if row.total_mv_yi else None,
        "free_mv_yi": float(row.free_mv_yi) if row.free_mv_yi else None,
        "chg60": float(row.chg60) if row.chg60 else None,
        "high": float(row.high) if row.high else None,
        "low": float(row.low) if row.low else None,
        "open": float(row.open) if row.open else None,
        "preclose": float(row.preclose) if row.preclose else None,
        "industry": row.industry or "",
        "source_count": row.source_count,
        "quality_score": row.quality_score,
        "freshness_status": row.freshness_status,
        "fetched_at": row.snapped_at.isoformat() if row.snapped_at else None,
        "raw_sources": [row.source] if row.source else [],
        "conflicts": [],
    }
    source_trace = {"raw_sources": [row.source]} if row.source else {}
    if precomputed_metrics is not None:
        _apply_local_quote_metrics(quote, precomputed_metrics)
    else:
        quote = enrich_quote_with_local_metrics(db, row.code, quote)
    return {
        "code": row.code,
        "trade_date": row.trade_date,
        "source_count": row.source_count or 0,
        "quality_score": row.quality_score,
        "freshness_status": row.freshness_status,
        "fetched_at": row.snapped_at.isoformat() if row.snapped_at else None,
        "quote": quote,
        "source_trace": source_trace,
    }


def _raw_aux_source_rows(db: Session) -> list[dict[str, Any]]:
    """汇总所有辅助数据源的 original source breakdown。"""
    aux_models: dict[str, tuple[type, str]] = {
        "limit_up": (LimitUpBoard, "limit_up"),
        "concept_sector": (ConceptSector, "concept_sector"),
        "capital_flow": (CapitalFlow, "capital_flow"),
        "dragon_tiger": (DragonTiger, "dragon_tiger"),
        "news": (NewsAnnouncement, "news"),
    }
    rows: list[dict[str, Any]] = []
    for aux_type, (model, label) in aux_models.items():
        source_col = getattr(model, "source", None)
        if source_col is not None:
            query = select(
                source_col,
                func.count().label("row_count"),
                func.max(model.created_at).label("latest_fetched_at"),
            ).group_by(source_col)
        else:
            count = db.scalar(select(func.count()).select_from(model))
            rows.append({
                "source": label,
                "source_type": label,
                "row_count": int(count or 0),
                "latest_fetched_at": None,
            })
            continue
        for row in db.execute(query).all():
            rows.append({
                "source": row.source,
                "source_type": label,
                "row_count": int(row.row_count or 0),
                "latest_fetched_at": row.latest_fetched_at.isoformat() if row.latest_fetched_at else None,
            })
    return rows


def _unified_rows_to_quotes(rows: list, skip_metrics: bool = False) -> list[dict[str, Any]]:
    """从 StockQuote ORM 行直接构建 quote 字典，避免重复查询。"""
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
            "pe_ttm": float(row.pe_ttm) if row.pe_ttm else None,
            "total_mv_yi": float(row.total_mv_yi) if row.total_mv_yi else None,
            "free_mv_yi": float(row.free_mv_yi) if row.free_mv_yi else None,
            "chg60": float(row.chg60) if row.chg60 else None,
            "high": float(row.high) if row.high else None,
            "low": float(row.low) if row.low else None,
            "open": float(row.open) if row.open else None,
            "preclose": float(row.preclose) if row.preclose else None,
            "industry": row.industry or "",
            "source_count": row.source_count,
            "quality_score": row.quality_score,
            "freshness_status": row.freshness_status,
            "fetched_at": row.snapped_at.isoformat() if row.snapped_at else "",
            "raw_sources": [row.source] if row.source else [],
            "conflicts": [],
        }
        quotes.append(quote)
    return quotes


def _unified_quote_rows_from_rows(all_rows: list, limit: int = 50, offset: int = 0, db: Session = None) -> list[dict[str, Any]]:
    """从预加载的 StockQuote ORM 行构建分页的 quote 展示数据。"""
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

