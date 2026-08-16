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
from app.engine.trading_calendar import (
    latest_allowed_bar_date as _latest_allowed_bar_date,
    normalize_trade_date as _normalize_trade_date,
    previous_weekday as _previous_weekday,
)

# Tushare provider 管理（配置页「数据源管理」Tab）：运行时启停覆盖 + 探针 health 所用的
# source_type。置于模块顶部：sync_data_source_configs 的孤儿清理需引用它做豁免。
_PROVIDER_SOURCE_TYPE = "tushare_provider"


def _compact_trade_date(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else digits



def list_data_sources(db: Session) -> list[dict[str, Any]]:
    sync_data_source_configs(db)
    configs = {
        (row.source, row.source_type): row
        for row in db.scalars(select(DataSourceConfig).order_by(DataSourceConfig.source_type, DataSourceConfig.priority)).all()
        if row.source_type != _PROVIDER_SOURCE_TYPE
    }
    health = {
        (row.source, row.source_type): row
        for row in db.scalars(select(DataSourceHealth)).all()
        if row.source_type != _PROVIDER_SOURCE_TYPE
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
        # success_rate 只算 success / (success + failure)，empty 是正常返回不计入分母
        effective_total = success_count + failure_count
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
                "success_rate": round(success_count / effective_total, 3) if effective_total else None,
                "latency_ms": health_row.latency_ms if health_row else None,
                "last_success_at": health_row.last_success_at.isoformat() if health_row and health_row.last_success_at else None,
                "last_failure_at": health_row.last_failure_at.isoformat() if health_row and health_row.last_failure_at else None,
                "last_error": health_row.last_error if health_row else None,
                "status": _source_status(enabled, health_row),
                "updated_at": config.updated_at.isoformat() if config and config.updated_at else None,
            }
        )

    # 各 Tushare Provider 是同一逻辑源下的独立故障域。它们的实时状态
    # 来自 Router 进程内遥测，不写入旧的可编辑注册表，也不会被孤儿清理删除。
    from app.services.tushare_client import get_tushare_provider_health

    rows.extend(get_tushare_provider_health())
    return sorted(
        rows,
        key=lambda item: (
            str(item.get("source_type") or ""),
            int(item.get("priority") or 0),
            str(item.get("source") or ""),
        ),
    )


def get_local_pool_status(
    db: Session,
    unified_limit: int = 50,
    unified_offset: int = 0,
    bars_limit: int = 50,
    bars_offset: int = 0,
    include_details: bool = True,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    latest_allowed_bar_date = _latest_allowed_bar_date()
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

    t_counts = time.perf_counter()

    # ── 日期/时间聚合 ──
    latest_unified = db.scalar(select(func.max(StockQuote.snapped_at)))
    latest_bar_date = db.scalar(
        select(func.max(DailyKbar.trade_date)).where(DailyKbar.trade_date <= latest_allowed_bar_date)
    )
    t_dates = time.perf_counter()

    # ── 一次性加载全部活跃 StockQuote 行 ──
    all_quote_rows = db.scalars(
        select(StockQuote).where(StockQuote.freshness_status != "inactive")
    ).all()
    from app.services.market_pool.core import _dedupe_latest_quote_rows
    current_quote_rows = _dedupe_latest_quote_rows(all_quote_rows)
    active_unified_quote_count = len(current_quote_rows)
    latest_quote_date = max(
        (_normalize_trade_date(row.trade_date) for row in current_quote_rows if row.trade_date),
        default=None,
    )
    t_load_quotes = time.perf_counter()

    # 从已加载的 current_quote_rows 直接计算 distinct code 数量，避免 daily_kbars 全表扫描
    # count(distinct DailyKbar.code) 需 347ms（128万行），用 stock_quotes 代替只需内存计算
    daily_bar_code_count = len({row.code for row in current_quote_rows})
    full_market_daily_bar_code_count = int(daily_bar_code_count)
    active_daily_bar_code_count = full_market_daily_bar_code_count
    daily_indicator_count = 0
    indicator_coverage_pct = 0.0
    t_distinct_bars = time.perf_counter()

    from app.services.market_pool.core import (
        _candidate_recall_plan,
        _eligible_market_quotes,
        _execution_recall_count,
        _load_cached_bars_summary,
        _load_cached_quotes,
        _quote_sources as _pool_quote_sources,
        _required_bar_date_for_quote,
        _select_recall_candidates,
    )
    from app.services.market_pool.core import local_pool_recommendation_readiness
    from app.services.market_data import _candidate_filter_summary

    settings = get_settings()
    active_sources = enabled_sources(db, "quote", _pool_quote_sources(settings))
    all_quotes = _load_cached_quotes(
        db,
        allowed_sources=set(active_sources) if active_sources else None,
        skip_metrics=True,
    )
    eligible_quotes = _eligible_market_quotes(all_quotes) if all_quotes else []
    coverage_trade_date = (
        _required_bar_date_for_quote({"trade_date": latest_quote_date})
        if latest_quote_date
        else latest_allowed_bar_date
    )
    recall_plan = _candidate_recall_plan(len(all_quotes), len(eligible_quotes), settings)
    execution_recall_count = _execution_recall_count(recall_plan, len(eligible_quotes))
    candidate_quotes = _select_recall_candidates(eligible_quotes, execution_recall_count)
    eligible_codes = [str(item.get("code") or "") for item in candidate_quotes if item.get("code")]
    candidate_filter = _candidate_filter_summary(all_quotes)
    t_quotes_to_dicts = time.perf_counter()

    # 只为真正可能进入推荐链路的股票加载日K汇总，复用给 readiness 与 eligible stats
    eligible_bar_summaries = (
        _load_cached_bars_summary(db, eligible_codes, 30)
        if eligible_codes
        else {}
    )
    t_eligible_bars = time.perf_counter()

    readiness = local_pool_recommendation_readiness(
        db,
        cached_quotes=all_quotes,
        cached_bar_summaries=eligible_bar_summaries,
    )
    t_readiness = time.perf_counter()

    eligible_bar_stats = _eligible_stats_from_summaries(eligible_codes, eligible_bar_summaries, coverage_trade_date)
    base_filter_count = len(eligible_codes)
    daily_bar_coverage_rate = (
        round(full_market_daily_bar_code_count / active_unified_quote_count * 100, 1)
        if active_unified_quote_count
        else 0.0
    )
    t_eligible = time.perf_counter()

    paged_quotes = (
        _unified_quote_rows_from_rows(current_quote_rows, limit=unified_limit, offset=unified_offset, db=db)
        if include_details
        else []
    )
    t_paged = time.perf_counter()
    paged_bars = _daily_bar_rows(db, limit=bars_limit, offset=bars_offset) if include_details else []
    t_paged_bars = time.perf_counter()

    t_aux = t_eligible  # 辅助计数已前移到 approx 阶段

    logger.info(
        "get_local_pool_status timing: counts=%.0fms dates=%.0fms load_quotes=%.0fms distinct_bars=%.0fms quotes_to_dicts=%.0fms eligible_bars=%.0fms readiness=%.0fms eligible=%.0fms aux=%.0fms paged=%.0fms paged_bars=%.0fms total=%.0fms",
        (t_counts - t0) * 1000, (t_dates - t_counts) * 1000, (t_load_quotes - t_dates) * 1000,
        (t_distinct_bars - t_load_quotes) * 1000, (t_quotes_to_dicts - t_distinct_bars) * 1000,
        (t_eligible_bars - t_quotes_to_dicts) * 1000, (t_readiness - t_eligible_bars) * 1000,
        (t_eligible - t_readiness) * 1000, (t_aux - t_eligible) * 1000,
        (t_paged - t_aux) * 1000, (t_paged_bars - t_paged) * 1000,
        (t_paged_bars - t0) * 1000,
    )

    kline_readiness = readiness.get("kline") if isinstance(readiness.get("kline"), dict) else {}
    immature_history_count = int(kline_readiness.get("immature_history_count") or 0)
    raw_short_history_count = int(eligible_bar_stats["short_history_count"] or 0)
    remediable_short_history_count = max(0, raw_short_history_count - immature_history_count)

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
            "daily_bar_short_history_count": remediable_short_history_count,
            "daily_bar_immature_history_count": immature_history_count,
            "daily_bar_total_short_history_count": raw_short_history_count,
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
        "quote_sources": _raw_quote_source_rows(db) if include_details else [],
        "kline_sources": _raw_bar_source_rows(db) if include_details else [],
        "auxiliary_sources": _raw_aux_source_rows(db) if include_details else [],
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
    """对账数据源注册表：注册 desired（来自配置的实际接入策略），并清理孤儿行。

    孤儿 = 存在于 DataSourceConfig / DataSourceHealth 但不在 desired 中的条目
    （历史上多 provider 行情源等残留，当前代码仅实现了 tushare 等部分 provider）。
    清理让注册表与实际策略保持一致。desired 是注册表的唯一真相源。
    """
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
    desired_set = {(src, st) for st, srcs in desired.items() for src in srcs}

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
        # 清理孤儿：注册表只保留 desired 中声明的 source×type，删除其余 config + 对应 health。
        # 豁免 tushare_provider：provider 的运行时启停覆盖（set_provider_enabled）与探针
        # health 都写在这个 source_type 下，不属于 quote/kline/... 七类注册表；若一并按孤儿
        # 删除，会把「禁用脏数据源」的覆盖行秒删、被禁源静默回流（spec: 孤儿清理对 provider 豁免）。
        for row in db.scalars(select(DataSourceConfig)).all():
            if row.source_type == _PROVIDER_SOURCE_TYPE:
                continue
            if (row.source, row.source_type) not in desired_set:
                health_row = db.get(DataSourceHealth, {"source": row.source, "source_type": row.source_type})
                if health_row is not None:
                    db.delete(health_row)
                db.delete(row)
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
    # 只有 failure_count（真正的 API 错误）才标 degraded，
    # empty_count 是正常返回（股票无龙虎榜/无涨停等），不触发 degraded。
    if int(health.failure_count or 0):
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
        row.code: {"bar_count": int(row.bar_count or 0), "latest_trade_date": _normalize_trade_date(row.latest_trade_date)}
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
    from app.services.market_pool.core import _required_bar_date_for_quote

    return _required_bar_date_for_quote({"trade_date": quote_date})


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
            "latest_trade_date": _normalize_trade_date(row.latest_trade_date),
            "latest_fetched_at": row.latest_fetched_at.isoformat() if row.latest_fetched_at else None,
        }
        for row in rows
    ]


def _unified_quote_rows(db: Session, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    from app.services.market_pool.core import _dedupe_latest_quote_rows, _quote_metrics_from_bars

    rows = db.scalars(
        select(StockQuote)
        .where(StockQuote.freshness_status != "inactive")
        .order_by(StockQuote.trade_date.desc(), StockQuote.snapped_at.desc(), StockQuote.quality_score.desc())
    ).all()
    rows = _dedupe_latest_quote_rows(rows)[offset:offset + limit]
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
            "first_trade_date": _normalize_trade_date(row.first_trade_date),
            "latest_trade_date": _normalize_trade_date(row.latest_trade_date),
        }
        for row in rows
    ]


def _unified_quote_payload(db: Session, row: StockQuote, precomputed_metrics: dict[str, float] | None = None) -> dict[str, Any]:
    from app.services.market_pool.core import _apply_local_quote_metrics, enrich_quote_with_local_metrics

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
    from app.services.market_pool.core import _quote_metrics_from_bars, _apply_local_quote_metrics, enrich_quote_with_local_metrics

    # 按质量分数降序排序后分页
    sorted_rows = sorted(all_rows, key=lambda r: (r.quality_score or 0), reverse=True)
    paged = sorted_rows[offset:offset + limit]
    codes = [row.code for row in paged]
    local_metrics = _quote_metrics_from_bars(db, codes) if codes else {}
    return [
        _unified_quote_payload(db, row, precomputed_metrics=local_metrics.get(row.code))
        for row in paged
    ]


# ═════════════════════════════════════════════════════════════════════════════
# Tushare provider 管理（配置页「数据源管理」Tab）：启停 + 测试
# ═════════════════════════════════════════════════════════════════════════════

def _known_provider_names() -> set[str]:
    """当前进程已接入（configured）的 provider 名集合，从 health 投影读取。"""
    from app.services.tushare_client import get_tushare_provider_health

    return {str(r["provider_name"]) for r in get_tushare_provider_health()}


def set_provider_enabled(db: Session, provider_name: str, enabled: bool) -> dict[str, Any] | None:
    """启停某个 tushare provider（写 DB 覆盖，运行时即时生效，不动 config）。

    返回更新后的 provider 条目；provider 未接入返回 None。
    """
    if provider_name not in _known_provider_names():
        return None
    row = db.get(DataSourceConfig, {"source": provider_name, "source_type": _PROVIDER_SOURCE_TYPE})
    if row is None:
        row = DataSourceConfig(source=provider_name, source_type=_PROVIDER_SOURCE_TYPE, enabled=enabled, priority=100)
        db.add(row)
    else:
        row.enabled = enabled
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    from app.services.tushare_client import invalidate_provider_enabled_override

    invalidate_provider_enabled_override()
    return next(
        (r for r in _provider_health_rows() if r["provider_name"] == provider_name),
        None,
    )


# 参与路由调序的 tushare pro provider（realtime_quote 是独立爬虫，不在 Router 链里，不调序）。
_ROUTABLE_PROVIDERS = (
    "promax",
    "fastapic",
    "xiaodefa",
    "quantdata",
    "ai_tool",
    "tushare_official",
)


def set_provider_priorities(db: Session, ordered_names: list[str]) -> list[dict[str, Any]]:
    """按给定顺序重排 tushare pro provider 的路由优先级（写 DB 覆盖，运行时即时生效）。

    ordered_names 为期望的路由顺序（主源在前）。未提及的 provider 追加在末尾，保持原相对序。
    持久化为优先级覆盖；同时确保这些 provider 存在启停覆盖行，让孤儿清理豁免
    tushare_provider。返回重排后的 provider 列表。
    """
    known = {name for name in _known_provider_names() if name in _ROUTABLE_PROVIDERS}
    # 期望顺序（仅保留可路由 provider，去重保序）+ 未提及的按 _ROUTABLE_PROVIDERS 默认序补尾
    seen: set[str] = set()
    merged: list[str] = []
    for name in ordered_names:
        if name in known and name not in seen:
            seen.add(name)
            merged.append(name)
    for name in _ROUTABLE_PROVIDERS:
        if name in known and name not in seen:
            merged.append(name)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for idx, name in enumerate(merged):
        row = db.get(DataSourceConfig, {"source": name, "source_type": _PROVIDER_SOURCE_TYPE})
        if row is None:
            row = DataSourceConfig(source=name, source_type=_PROVIDER_SOURCE_TYPE, enabled=True, priority=idx)
            db.add(row)
        else:
            row.priority = idx
        row.updated_at = now
    db.commit()

    from app.services.tushare_client import invalidate_provider_enabled_override

    invalidate_provider_enabled_override()
    return _provider_health_rows()


def _provider_health_rows() -> list[dict[str, Any]]:
    from app.services.tushare_client import get_tushare_provider_health

    return get_tushare_provider_health()


def test_provider(db: Session, provider_name: str) -> dict[str, Any]:
    """探针测试某 provider 当前是否可用，结果写入 data_source_health。

    - pro_http 源（promax/fastapic/xiaodefa/ai_tool）：直连该 provider 调 stock_basic，
      绕过 Router 选路，专测这一源的连通 + 鉴权。
    - realtime_quote：调 fetch_realtime_quotes(["000001"]) 测爬虫端点。
    返回 {ok, latency_ms, status, error}；provider 未接入抛 KeyError。
    """
    started = time.perf_counter()
    ok = False
    error: str | None = None
    try:
        if provider_name == "xiaoshi":
            from app.services.xiaoshi_client import get_xiaoshi_client

            payload = get_xiaoshi_client().get_stocks(market="CN", limit=1)
            items = payload.get("items") if isinstance(payload, dict) else None
            ok = bool(items)
            if not ok:
                error = "xiaoshi stocks 返回空"
        elif provider_name == "realtime_quote":
            from app.services.realtime_quote_client import fetch_realtime_quotes

            rows = fetch_realtime_quotes(["000001"], use_cache=False)
            ok = bool(rows)
            if not ok:
                error = "realtime_quote 返回空（端点不可达或无数据）"
        else:
            ok, error = _probe_pro_provider(provider_name)
    except KeyError:
        raise
    except Exception as exc:  # 探针本身异常也算失败
        ok = False
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    latency_ms = round((time.perf_counter() - started) * 1000, 1)

    if provider_name == "xiaoshi":
        _record_source_test(db, provider_name, "quote", ok=ok, latency_ms=latency_ms, error=error)
        _record_source_test(db, provider_name, "kline", ok=ok, latency_ms=latency_ms, error=error)
    else:
        _record_provider_test(db, provider_name, ok=ok, latency_ms=latency_ms, error=error)
    return {
        "provider_name": provider_name,
        "ok": ok,
        "latency_ms": latency_ms,
        "status": "healthy" if ok else "degraded",
        "error": error,
    }


def _probe_pro_provider(provider_name: str) -> tuple[bool, str | None]:
    """绕过 Router，用目标 provider 自己的 config 直连调 stock_basic 测连通。"""
    from app.services.tushare_client import get_pro

    router = get_pro()._router
    provider = router._providers.get(provider_name)
    if provider is None:
        raise KeyError(provider_name)
    try:
        # 不传 limit：部分第三方源（如 quantdata）不支持 limit 参数，传了会被判 invalid_request。
        # stock_basic 全量返回也仅数千行，探针直接取全集判断非空即可。
        envelope = provider.request("stock_basic", params={})
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"
    items = (envelope.get("data") or {}).get("items") if isinstance(envelope, dict) else None
    if items:
        return True, None
    return False, "stock_basic 返回空"


def _record_provider_test(db: Session, provider_name: str, *, ok: bool, latency_ms: float, error: str | None) -> None:
    _record_source_test(
        db,
        provider_name,
        _PROVIDER_SOURCE_TYPE,
        ok=ok,
        latency_ms=latency_ms,
        error=error,
    )


def _record_source_test(
    db: Session,
    source: str,
    source_type: str,
    *,
    ok: bool,
    latency_ms: float,
    error: str | None,
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = db.get(DataSourceHealth, {"source": source, "source_type": source_type})
    if row is None:
        row = DataSourceHealth(source=source, source_type=source_type)
        db.add(row)
    if ok:
        row.success_count = int(row.success_count or 0) + 1
        row.last_success_at = now
        row.last_error = None
    else:
        row.failure_count = int(row.failure_count or 0) + 1
        row.last_failure_at = now
        row.last_error = error
    row.latency_ms = latency_ms
    row.updated_at = now
    db.commit()
