"""
数据质量诊断服务：报告各表的数据覆盖情况、日期连续性、异常检测。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import distinct, func, select, text
from sqlalchemy.orm import Session

from app.engine.trading_calendar import latest_allowed_bar_date as _latest_allowed_bar_date

from app.models.tables import (
    AuctionData,
    BlockTrade,
    BrokerRecommend,
    CapitalFlow,
    ConceptSector,
    ConceptSectorStock,
    DailyKbar,
    DragonTiger,
    FinaIndicator,
    HkHolding,
    HolderNumber,
    HolderTrade,
    HsgtFlow,
    HsgtTop10,
    IndexDaily,
    InstitutionalSurvey,
    LimitUpBoard,
    MarginData,
    MarginDetail,
    MinuteKbar,
    NewsAnnouncement,
    PledgeStat,
    Recommendation,
    RecommendationOutcome,
    Repurchase,
    ShareFloat,
    Stock,
    StockQuote,
    StkLimit,
)

logger = logging.getLogger(__name__)

# ── 注册需要监控的表 ─────────────────────────────────────────────────────

_TABLE_DEFS: list[dict[str, Any]] = [
    {"key": "daily_kbar",       "label": "日K线",       "table": DailyKbar,       "date_col": "trade_date", "critical": True,  "expect_recent_coverage": True},
    {"key": "stock_quote",      "label": "实时快照",     "table": StockQuote,      "date_col": "trade_date", "critical": False, "expect_recent_coverage": False, "lag_alert_after_days": 1},
    {"key": "minute_kbar",      "label": "分钟K线",     "table": MinuteKbar,      "date_col": "trade_date", "critical": False, "expect_recent_coverage": True, "estimated_row_count": True, "fast_date_bounds": True},
    {"key": "index_daily",      "label": "指数日线",     "table": IndexDaily,      "date_col": "trade_date", "critical": True,  "expect_recent_coverage": True},
    {"key": "limit_up_board",   "label": "涨停板",       "table": LimitUpBoard,    "date_col": "trade_date", "critical": True,  "expect_recent_coverage": True},
    {"key": "dragon_tiger",     "label": "龙虎榜",       "table": DragonTiger,     "date_col": "trade_date", "critical": False, "expect_recent_coverage": False},
    {"key": "concept_sector",   "label": "概念板块",     "table": ConceptSector,   "date_col": "trade_date", "critical": False, "expect_recent_coverage": False, "lag_alert_after_days": 1},
    {"key": "capital_flow",     "label": "资金流向",     "table": CapitalFlow,     "date_col": "trade_date", "critical": False, "expect_recent_coverage": True},
    {"key": "news",             "label": "新闻公告",     "table": NewsAnnouncement, "date_col": "announce_date", "critical": False, "expect_recent_coverage": False},
    {"key": "recommendation",   "label": "推荐记录",     "table": Recommendation,   "date_col": "trade_date", "critical": True,  "expect_recent_coverage": False},
    {"key": "outcome",          "label": "标注结果",     "table": RecommendationOutcome, "date_col": None, "critical": True, "expect_recent_coverage": False},
]

_RECENT_REFERENCE_WINDOW = 10
_RECENT_WARN_MISSING_DAYS = 2
_RECENT_HIGH_MISSING_DAYS = 4
_MIN_RECENT_DAILY_KBAR_CODES = 3000

# ── 数据域（指标级）覆盖目录 ─────────────────────────────────────────
# 与 provider 级「数据源接入」不同，这里站在「数据域 / 指标」维度，
# 回答：系统关注哪些市场数据域（K线、筹码峰、龙虎榜、资金流…），
# 每个域是否真的有接入策略并持续拉取。
# - table / date_col：存储位置（用于运行时统计行数与最新日期）
# - fresh_days：判定「接入且新鲜」的容忍滞后天数；为 None 表示事件型
#   数据（自然稀疏，有数据即视为已接入，不判滞后）
# - column：可选，非 NULL 计数列（用于部分字段型域，如 筹码峰 chip_cost_50）
_DAILY_DATA_FRESH_DAYS = 0

_DATA_DOMAIN_DEFS: list[dict[str, Any]] = [
    # 行情 K 线
    {"key": "daily_kbar", "label": "日K线(含技术指标)", "category": "行情K线", "table": DailyKbar, "date_col": "trade_date", "critical": True, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "chip", "label": "筹码分布(筹码峰)", "category": "行情K线", "table": DailyKbar, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS, "column": "chip_cost_50"},
    {"key": "minute_kbar", "label": "分钟K线", "category": "行情K线", "table": MinuteKbar, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "index_daily", "label": "指数日线", "category": "行情K线", "table": IndexDaily, "date_col": "trade_date", "critical": True, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "stk_limit", "label": "涨跌停价格", "category": "行情K线", "table": StkLimit, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "auction", "label": "集合竞价", "category": "行情K线", "table": AuctionData, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    # 实时盘口
    {"key": "realtime_quote", "label": "盘中实时快照", "category": "实时盘口", "table": StockQuote, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    # 情绪题材
    {"key": "limit_up", "label": "涨停板", "category": "情绪题材", "table": LimitUpBoard, "date_col": "trade_date", "critical": True, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "concept", "label": "概念板块", "category": "情绪题材", "table": ConceptSector, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "concept_member", "label": "概念板块成分", "category": "情绪题材", "table": ConceptSectorStock, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "dragon_tiger", "label": "龙虎榜", "category": "情绪题材", "table": DragonTiger, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "broker_recommend", "label": "券商金股", "category": "情绪题材", "table": BrokerRecommend, "date_col": "month", "critical": False, "fresh_days": 45},
    # 资金流向
    {"key": "capital_flow", "label": "资金流向", "category": "资金流向", "table": CapitalFlow, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    # 北向 / 港股通
    {"key": "hsgt_flow", "label": "沪深港通资金流", "category": "北向港股通", "table": HsgtFlow, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "hsgt_top10", "label": "北向持股TOP10", "category": "北向港股通", "table": HsgtTop10, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "hk_hold", "label": "港股通持股", "category": "北向港股通", "table": HkHolding, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    # 融资融券
    {"key": "margin", "label": "融资融券", "category": "融资融券", "table": MarginData, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "margin_detail", "label": "融资融券明细", "category": "融资融券", "table": MarginDetail, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    # 股东 / 筹码
    {"key": "holder_number", "label": "股东人数(筹码集中度)", "category": "股东筹码", "table": HolderNumber, "date_col": "end_date", "critical": False, "fresh_days": 130},
    {"key": "holder_trade", "label": "股东增减持", "category": "股东筹码", "table": HolderTrade, "date_col": "ann_date", "critical": False, "fresh_days": None},
    {"key": "pledge_stat", "label": "股权质押", "category": "股东筹码", "table": PledgeStat, "date_col": "end_date", "critical": False, "fresh_days": None},
    # 基本面
    {"key": "fina_indicator", "label": "财务指标", "category": "基本面", "table": FinaIndicator, "date_col": "end_date", "critical": False, "fresh_days": 130},
    {"key": "institutional_survey", "label": "机构调研", "category": "基本面", "table": InstitutionalSurvey, "date_col": "surv_date", "critical": False, "fresh_days": None},
    # 事件 / 公告
    {"key": "news", "label": "新闻公告", "category": "事件公告", "table": NewsAnnouncement, "date_col": "announce_date", "critical": False, "fresh_days": 14},
    {"key": "block_trade", "label": "大宗交易", "category": "事件公告", "table": BlockTrade, "date_col": "trade_date", "critical": False, "fresh_days": None},
    {"key": "share_float", "label": "限售解禁", "category": "事件公告", "table": ShareFloat, "date_col": "float_date", "critical": False, "fresh_days": None},
    {"key": "repurchase", "label": "股票回购", "category": "事件公告", "table": Repurchase, "date_col": "ann_date", "critical": False, "fresh_days": None},
    # 基础参照
    {"key": "stock_basic", "label": "股票基础信息", "category": "基础参照", "table": Stock, "date_col": "list_date", "critical": False, "fresh_days": None},
]

# ── 进程内诊断缓存 ─────────────────────────────────────────────────────
# 诊断结果在同一个参考交易日内基本稳定（数据只在交易日盘中/盘后增量写入），
# 按 reference_trade_date 缓存，参考交易日变更即失效。进程重启自然清空。
_diagnosis_cache: dict[str, Any] = {"reference_trade_date": None, "result": None}


def _get_cached_diagnosis() -> dict[str, Any] | None:
    """命中返回缓存结果，否则 None。"""
    cached = _diagnosis_cache.get("result")
    if cached is None:
        return None
    if cached.get("reference_trade_date") != _diagnosis_cache.get("reference_trade_date"):
        return None
    return cached


def _set_cached_diagnosis(result: dict[str, Any]) -> None:
    _diagnosis_cache["reference_trade_date"] = result.get("reference_trade_date")
    _diagnosis_cache["result"] = result


def clear_data_health_cache() -> None:
    """显式清空诊断缓存（修复任务完成后调用）。"""
    _diagnosis_cache["reference_trade_date"] = None
    _diagnosis_cache["result"] = None


def diagnose_all(db: Session, *, force_refresh: bool = False) -> dict[str, Any]:
    """全面数据质量诊断。

    结果按参考交易日缓存：同一个 reference_trade_date 内重复调用直接返回缓存，
    避免驾驶舱/详情页每次都全量重算（实测 ~12s）。参考交易日变更或 force_refresh
    时失效。修复任务完成后调用方应传 force_refresh=True 刷新。
    """
    cached = _get_cached_diagnosis()
    if cached is not None and not force_refresh:
        logger.debug("data_health: cache hit (reference_date=%s)", cached.get("reference_trade_date"))
        return cached

    result = _diagnose_all_uncached(db)
    _set_cached_diagnosis(result)
    return result


def _diagnose_all_uncached(db: Session) -> dict[str, Any]:
    """全面数据质量诊断（无缓存，实际计算）。"""

    # 1. 各表基础统计
    table_stats = _table_stats(db)

    # 2. 计算参考交易日（优先 Tushare，失败时回退本地）
    reference_info = _resolve_reference_trade_date(db)
    reference_trade_date = reference_info["reference_trade_date"]
    external_trade_date = reference_info["external_trade_date"]

    # 3. 关键表滞后天数
    lag_days = {}
    if reference_trade_date:
        for td in table_stats:
            info = table_stats[td]
            if info.get("max_date"):
                lag = _date_diff(reference_trade_date, info["max_date"])
                lag_days[td] = lag

    # 4. 最近交易日覆盖与缺口
    recent_coverage = _recent_trade_day_coverage(db, reference_trade_date)

    # 4. 数据源接入健康（通 / 不通），并计入评分
    source_health = _data_source_health(db)

    # 4b. 数据域（指标级）覆盖：哪些数据域有接入策略且持续拉取
    domain_summary = _data_domain_coverage(db, reference_trade_date)

    # 5. 健康评分 (0-100)
    score = _compute_health_score(table_stats, lag_days, reference_trade_date, recent_coverage, source_health, domain_summary)

    # 6. 关键问题列表
    issues = _find_issues(table_stats, lag_days, reference_trade_date, recent_coverage, reference_info, domain_summary)

    result = {
        "score": score,
        "tushare_latest_trade_date": external_trade_date,
        "reference_trade_date": reference_trade_date,
        "reference_trade_date_source": reference_info["source"],
        "reference_trade_date_note": reference_info["note"],
        "tables": table_stats,
        "lag_days": lag_days,
        "recent_trade_day_coverage": recent_coverage,
        "issues": issues,
        "data_sources": source_health,
        "data_domains": domain_summary,
        "recommendation_summary": _recommendation_summary(db),
    }
    result["recommendation_readiness"] = recommendation_readiness_from_diagnosis(result)
    return result


def _table_stats(db: Session) -> dict[str, Any]:
    """各表行数 + 日期范围。"""
    result = {}
    for tdef in _TABLE_DEFS:
        key = tdef["key"]
        tbl = tdef["table"]
        dc = tdef["date_col"]
        label = tdef["label"]

        try:
            total, total_estimated = _table_row_count(db, tbl, prefer_estimate=bool(tdef.get("estimated_row_count")))

            min_date = None
            max_date = None
            if dc and hasattr(tbl, dc):
                col = getattr(tbl, dc)
                if tbl is StockQuote:
                    min_date, max_date = _active_stock_quote_date_bounds(db)
                elif tdef.get("fast_date_bounds"):
                    # minute_kbar 行数巨大，用归一化日期避免混存格式干扰
                    min_date, max_date = _date_bounds(db, col)
                else:
                    min_date, max_date = _date_bounds(db, col)

            result[key] = {
                "label": label,
                "table": tbl.__tablename__,
                "total_rows": total,
                "total_rows_estimated": total_estimated,
                "min_date": min_date,
                "max_date": max_date,
                "critical": tdef["critical"],
            }
        except Exception as e:
            _rollback_session_safely(db)
            logger.warning("诊断 %s 失败: %s", key, e)
            result[key] = {"label": label, "table": tbl.__tablename__, "error": str(e)}

    return result


def _get_tushare_latest_trade_date() -> str | None:
    try:
        from app.services.tushare_client import fetch_latest_trade_date
        compact = fetch_latest_trade_date()
        if compact and len(compact) == 8:
            return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
    except Exception:
        pass
    return None


def _resolve_reference_trade_date(db: Session) -> dict[str, str | None]:
    external_trade_date = _get_tushare_latest_trade_date()
    if external_trade_date:
        latest_closed_trade_date = _latest_allowed_bar_date()
        reference_trade_date = min(external_trade_date, latest_closed_trade_date)
        return {
            "reference_trade_date": reference_trade_date,
            "external_trade_date": external_trade_date,
            "source": "tushare",
            "note": "参考交易日来自 Tushare 交易日历，并限制为当前已收盘交易日。",
        }

    local_trade_date = _infer_local_reference_trade_date(db)
    if local_trade_date:
        return {
            "reference_trade_date": local_trade_date,
            "external_trade_date": None,
            "source": "local",
            "note": "Tushare 最新交易日不可用，已回退到本地关键表最新交易日。",
        }

    return {
        "reference_trade_date": None,
        "external_trade_date": None,
        "source": "unknown",
        "note": "无法从 Tushare 或本地关键表推断参考交易日。",
    }


def _infer_local_reference_trade_date(db: Session) -> str | None:
    candidates = [
        _date_bounds(db, DailyKbar.trade_date)[1],
        _date_bounds(db, LimitUpBoard.trade_date)[1],
        _active_stock_quote_max_date(db),
        _date_bounds(db, Recommendation.trade_date)[1],
    ]
    normalized = [_fmt_date(item) for item in candidates if item]
    return max(normalized) if normalized else None


def _active_stock_quote_max_date(db: Session) -> str | None:
    return _active_stock_quote_date_bounds(db)[1]


def _active_stock_quote_date_bounds(db: Session) -> tuple[str | None, str | None]:
    normalized_col = func.replace(StockQuote.trade_date, "-", "")
    min_date, max_date = db.execute(
        select(func.min(normalized_col), func.max(normalized_col)).where(
            StockQuote.trade_date.is_not(None),
            StockQuote.freshness_status != "inactive",
        )
    ).one()
    return _fmt_date(min_date), _fmt_date(max_date)


def _recent_trade_day_coverage(
    db: Session,
    reference_trade_date: str | None,
    window: int = _RECENT_REFERENCE_WINDOW,
) -> dict[str, Any]:
    if not reference_trade_date:
        return {
            "window": window,
            "reference_trade_date": None,
            "trade_days": [],
            "tables": {},
            "summary": {
                "max_missing_days": 0,
                "high_impact_tables": [],
                "warning_tables": [],
            },
        }

    try:
        trade_days = _recent_daily_trade_days(db, reference_trade_date, window)
    except Exception as exc:
        _rollback_session_safely(db)
        logger.warning("最近交易日列表诊断失败: %s", exc)
        return {
            "window": window,
            "reference_trade_date": reference_trade_date,
            "trade_days": [],
            "tables": {},
            "summary": {
                "max_missing_days": 0,
                "high_impact_tables": [],
                "warning_tables": [],
                "error": str(exc),
            },
        }
    ordered_days = sorted(trade_days)
    if not ordered_days:
        return {
            "window": window,
            "reference_trade_date": reference_trade_date,
            "trade_days": [],
            "tables": {},
            "summary": {
                "max_missing_days": 0,
                "high_impact_tables": [],
                "warning_tables": [],
            },
        }

    coverage_tables: dict[str, Any] = {}
    max_missing_days = 0
    high_impact_tables: list[str] = []
    warning_tables: list[str] = []
    for tdef in _TABLE_DEFS:
        date_col = tdef["date_col"]
        tbl = tdef["table"]
        if not date_col or not tdef.get("expect_recent_coverage") or not hasattr(tbl, date_col):
            continue
        col = getattr(tbl, date_col)
        try:
            present = _present_dates(db, col, ordered_days)
            missing = [day for day in ordered_days if day not in present]
            incomplete_dates = _incomplete_daily_kbar_dates(db, ordered_days) if tdef["key"] == "daily_kbar" else []
            tolerated_gap_dates = _realtime_tolerated_gap_dates(
                missing_dates=missing,
                incomplete_dates=incomplete_dates,
                reference_trade_date=reference_trade_date,
            )
            effective_missing_dates = [day for day in missing if day not in tolerated_gap_dates]
            effective_incomplete_dates = [day for day in incomplete_dates if day not in tolerated_gap_dates]
        except Exception as exc:
            _rollback_session_safely(db)
            logger.warning("最近交易日覆盖诊断 %s 失败: %s", tdef["key"], exc)
            coverage_tables[tdef["key"]] = {
                "label": tdef["label"],
                "critical": tdef["critical"],
                "covered_count": 0,
                "expected_count": len(ordered_days),
                "coverage_rate": 0.0,
                "present_dates": [],
                "missing_dates": [],
                "incomplete_dates": [],
                "effective_missing_dates": [],
                "effective_incomplete_dates": [],
                "tolerated_gap_dates": [],
                "effective_gap_count": 0,
                "error": str(exc),
            }
            continue
        covered_count = len(present)
        coverage_rate = round(covered_count / len(ordered_days), 3) if ordered_days else 0.0
        effective_gap_count = len(set(effective_missing_dates + effective_incomplete_dates))
        coverage_tables[tdef["key"]] = {
            "label": tdef["label"],
            "critical": tdef["critical"],
            "covered_count": covered_count,
            "expected_count": len(ordered_days),
            "coverage_rate": coverage_rate,
            "present_dates": present,
            "missing_dates": missing,
            "incomplete_dates": incomplete_dates,
            "effective_missing_dates": effective_missing_dates,
            "effective_incomplete_dates": effective_incomplete_dates,
            "tolerated_gap_dates": sorted(tolerated_gap_dates),
            "effective_gap_count": effective_gap_count,
        }
        gap_count = effective_gap_count
        max_missing_days = max(max_missing_days, gap_count)
        if tdef["critical"] and gap_count >= _RECENT_HIGH_MISSING_DAYS:
            high_impact_tables.append(tdef["key"])
        elif gap_count >= _RECENT_WARN_MISSING_DAYS:
            warning_tables.append(tdef["key"])

    return {
        "window": window,
        "reference_trade_date": reference_trade_date,
        "trade_days": ordered_days,
        "tables": coverage_tables,
        "summary": {
            "max_missing_days": max_missing_days,
            "high_impact_tables": high_impact_tables,
            "warning_tables": warning_tables,
        },
    }


def _compute_health_score(
    table_stats: dict[str, Any],
    lag_days: dict[str, int],
    reference_trade_date: str | None,
    recent_coverage: dict[str, Any],
    source_health: dict[str, Any] | None = None,
    domain_summary: dict[str, Any] | None = None,
) -> int:
    """0-100 健康评分。"""
    if not reference_trade_date:
        return 0

    score = 100

    # 扣分：关键表为空
    for key, info in table_stats.items():
        if info.get("critical") and info.get("total_rows", 0) == 0:
            score -= 20
        if info.get("error"):
            score -= 10

    # 扣分：滞后。保持与 issue 规则一致，非关键且未配置滞后告警的表不因新旧节奏不同扣分。
    table_defs_by_key = {item["key"]: item for item in _TABLE_DEFS}
    for key, lag in lag_days.items():
        info = table_stats.get(key, {})
        tdef = table_defs_by_key.get(key, {})
        should_penalize = bool(info.get("critical")) or tdef.get("lag_alert_after_days") is not None
        if not should_penalize:
            continue
        if lag > 5:
            score -= 10
        elif lag > 2:
            score -= 5

    recent_tables = recent_coverage.get("tables") or {}
    for key, coverage in recent_tables.items():
        gap_count = _coverage_gap_count(coverage, reference_trade_date)
        if gap_count == 0:
            continue
        if coverage.get("critical"):
            score -= min(15, gap_count * 3)
        else:
            score -= min(6, gap_count)

    # 扣分：outcome 没有标注
    outc = table_stats.get("outcome", {})
    if outc.get("total_rows", 0) == 0:
        score -= 15

    # 扣分：recommendation 为空
    rec = table_stats.get("recommendation", {})
    if rec.get("total_rows", 0) == 0:
        score -= 15

    # 扣分：数据源接入健康（来自 source_health 的预计算 penalty）
    if source_health:
        penalty = int(source_health.get("score_penalty", 0) or 0)
        if penalty:
            score -= penalty

    # 扣分：数据域覆盖缺口（来自 domain_summary 的预计算 penalty）
    if domain_summary:
        penalty = int(domain_summary.get("score_penalty", 0) or 0)
        if penalty:
            score -= penalty

    return max(0, min(100, score))


def _data_source_health(db: Session) -> dict[str, Any]:
    """汇总各数据源接入状态：通 / 不通 / 未测试 / 未启用，并计算评分扣减。

    直接复用 list_data_sources（其会先 sync 注册表，自动清理孤儿行），
    因此返回的源集合即「实际有接入策略」的集合。
    """
    from app.services.data_sources import list_data_sources

    try:
        sources = list_data_sources(db)
    except Exception as exc:
        logger.warning("数据源健康汇总失败: %s", exc)
        return {
            "sources": [],
            "working_count": 0,
            "broken_count": 0,
            "untested_count": 0,
            "disabled_count": 0,
            "total": 0,
            "score_penalty": 0,
            "error": str(exc),
        }

    working: list[dict[str, Any]] = []
    broken: list[dict[str, Any]] = []
    untested: list[dict[str, Any]] = []
    disabled: list[dict[str, Any]] = []
    for s in sources:
        status = s.get("status", "unknown")
        entry = {
            "source": s.get("source"),
            "source_type": s.get("source_type"),
            "status": status,
            "enabled": bool(s.get("enabled")),
            "success_rate": s.get("success_rate"),
            "last_error": s.get("last_error"),
            "last_success_at": s.get("last_success_at"),
        }
        if status == "healthy":
            working.append(entry)
        elif status == "degraded":
            broken.append(entry)
        elif status == "disabled":
            disabled.append(entry)
        else:  # untested / unknown
            untested.append(entry)

    # 评分扣减：enabled 且 degraded 的每个 -5（上限 15）；enabled 且 untested 的每个 -1（上限 5）
    penalty = min(15, len(broken) * 5) + min(5, len([u for u in untested if u["enabled"]]) * 1)

    return {
        "sources": sources,
        "working_count": len(working),
        "broken_count": len(broken),
        "untested_count": len(untested),
        "disabled_count": len(disabled),
        "total": len(sources),
        "working_sources": working,
        "broken_sources": broken,
        "untested_sources": untested,
        "disabled_sources": disabled,
        "score_penalty": penalty,
    }


def _data_domain_coverage(db: Session, reference_trade_date: str | None) -> dict[str, Any]:
    """数据域（指标级）覆盖：哪些市场数据域有接入策略且持续拉取。

    返回按类别分组的覆盖明细 + 汇总（覆盖率、评分扣减）。
    状态判定：
      - empty：行数为 0 → 无数据（未接入 / 未拉取）
      - covered：有数据，且在 fresh_days 容忍窗口内（事件型域有数据即 covered）
      - stale：有数据，但最新日期超出 fresh_days 容忍窗口（策略未持续刷新）
    """
    ref = _fmt_date(reference_trade_date) if reference_trade_date else None

    domains: list[dict[str, Any]] = []
    covered = stale = empty = 0
    penalty = 0

    for ddef in _DATA_DOMAIN_DEFS:
        key = ddef["key"]
        label = ddef["label"]
        category = ddef["category"]
        tbl = ddef["table"]
        dc = ddef["date_col"]
        fresh = ddef.get("fresh_days")
        critical = bool(ddef.get("critical"))
        column = ddef.get("column")

        total = 0
        max_date = None
        try:
            if column and hasattr(tbl, column):
                total = int(
                    db.scalar(select(func.count()).select_from(tbl).where(getattr(tbl, column).is_not(None)))
                    or 0
                )
                # 归一化日期格式（与 _date_bounds 一致），避免 YYYYMMDD / YYYY-MM-DD 混存时
                # 字符串排序导致 max_date 误判（如 '20260525' > '2026-06-23'）
                normalized_col = func.replace(getattr(tbl, dc), "-", "")
                max_date = db.scalar(
                    select(func.max(normalized_col)).where(getattr(tbl, column).is_not(None))
                )
            else:
                total = int(db.scalar(select(func.count()).select_from(tbl)) or 0)
                if hasattr(tbl, dc):
                    normalized_col = func.replace(getattr(tbl, dc), "-", "")
                    max_date = db.scalar(select(func.max(normalized_col)).where(getattr(tbl, dc).is_not(None)))
        except Exception as exc:
            _rollback_session_safely(db)
            logger.warning("数据域 %s 统计失败: %s", key, exc)
            domains.append({
                "key": key, "label": label, "category": category, "status": "error",
                "total_rows": 0, "max_date": None, "critical": critical,
                "fresh_window_days": fresh, "gap_days": None, "note": str(exc),
            })
            continue

        max_date = _fmt_date(max_date)
        # 月份型列（YYYYMM，如 券商金股 month）归一化为 YYYY-MM 便于展示
        if max_date and len(max_date) == 6 and max_date.isdigit():
            max_date = f"{max_date[:4]}-{max_date[4:]}"

        gap_days: int | None = None
        if total == 0:
            status = "empty"
            empty += 1
            penalty += 2
        elif fresh is None:
            status = "covered"
            covered += 1
        else:
            gap_cmp = max_date
            if gap_cmp and len(gap_cmp) == 7:  # YYYY-MM（month 列）→ 补首日再比较
                gap_cmp = gap_cmp + "-01"
            gap = _date_diff(ref, gap_cmp) if ref else 999
            gap_days = gap if (ref and gap != 999) else None
            if gap != 999 and gap <= fresh:
                status = "covered"
                covered += 1
            else:
                status = "stale"
                stale += 1
                penalty += 3 if critical else 1

        domains.append({
            "key": key,
            "label": label,
            "category": category,
            "status": status,
            "total_rows": total,
            "max_date": max_date,
            "critical": critical,
            "fresh_window_days": fresh,
            "gap_days": gap_days if (fresh is not None and total > 0) else None,
            "note": None,
        })

    # 按类别分组
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for d in domains:
        by_cat.setdefault(d["category"], []).append(d)
    categories = []
    for cat, items in by_cat.items():
        categories.append({
            "category": cat,
            "domains": items,
            "covered_count": sum(1 for i in items if i["status"] == "covered"),
            "stale_count": sum(1 for i in items if i["status"] == "stale"),
            "empty_count": sum(1 for i in items if i["status"] == "empty"),
        })
    # 有缺口（滞后/空）的类别排前面，便于一眼看到问题
    categories.sort(key=lambda c: (c["empty_count"] + c["stale_count"] == 0, c["category"]))

    total_domains = len(domains)
    coverage_rate = round(covered / total_domains, 3) if total_domains else 0.0

    return {
        "categories": categories,
        "total": total_domains,
        "covered_count": covered,
        "stale_count": stale,
        "empty_count": empty,
        "coverage_rate": coverage_rate,
        "score_penalty": min(penalty, 15),
        "reference_trade_date": reference_trade_date,
        "empty_domains": [d for d in domains if d["status"] == "empty"],
        "stale_domains": [d for d in domains if d["status"] == "stale"],
    }


def _find_issues(
    table_stats: dict[str, Any],
    lag_days: dict[str, int],
    reference_trade_date: str | None,
    recent_coverage: dict[str, Any],
    reference_info: dict[str, str | None],
    domain_summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """列出所有需要关注的问题。"""
    issues = []

    if not reference_trade_date:
        issues.append({"severity": "high", "message": "无法推断参考交易日，数据质量诊断无法完成"})
        return issues

    if reference_info.get("source") != "tushare":
        issues.append({
            "severity": "medium",
            "message": str(reference_info.get("note") or "Tushare 最新交易日不可用，已回退到本地参考日"),
        })

    for key, info in table_stats.items():
        if info.get("error"):
            issues.append({"severity": "high", "message": f"{info['label']} 诊断失败: {info['error']}"})
            continue
        if info.get("critical") and info.get("total_rows", 0) == 0:
            issues.append({"severity": "high", "message": f"关键表 {info['label']} ({info['table']}) 为空"})

    table_defs_by_key = {item["key"]: item for item in _TABLE_DEFS}
    for key, lag in lag_days.items():
        tbl_info = table_stats.get(key, {})
        tdef = table_defs_by_key.get(key, {})
        if tbl_info.get("critical") and lag > 5:
            issues.append({
                "severity": "medium" if lag < 20 else "high",
                "message": f"{tbl_info.get('label', key)} 滞后 {lag} 天 (最新: {tbl_info.get('max_date')}, 参考日: {reference_trade_date})",
            })
        lag_alert_after_days = tdef.get("lag_alert_after_days")
        if lag_alert_after_days is not None and lag > int(lag_alert_after_days):
            issues.append({
                "severity": "medium",
                "message": f"{tbl_info.get('label', key)} 未覆盖参考交易日，当前滞后 {lag} 天 (最新: {tbl_info.get('max_date')})",
            })

    outc = table_stats.get("outcome", {})
    if outc.get("total_rows", 0) == 0:
        issues.append({"severity": "high", "message": "推荐结果未标注 (outcome 表为空), 回测/评估无法进行"})

    recent_tables = recent_coverage.get("tables") or {}
    for key, coverage in recent_tables.items():
        missing_dates, incomplete_dates = _effective_coverage_dates(coverage, reference_trade_date)
        gap_dates = sorted(set([*missing_dates, *incomplete_dates]))
        if not gap_dates:
            continue
        label = coverage.get("label") or key
        severity = "high" if coverage.get("critical") and len(gap_dates) >= _RECENT_HIGH_MISSING_DAYS else "medium"
        parts = []
        if missing_dates:
            parts.append(f"缺失 {len(missing_dates)} 天: {', '.join(missing_dates[:4])}" + (" ..." if len(missing_dates) > 4 else ""))
        if incomplete_dates:
            parts.append(f"不完整 {len(incomplete_dates)} 天: {', '.join(incomplete_dates[:4])}" + (" ..." if len(incomplete_dates) > 4 else ""))
        issues.append({
            "severity": severity,
            "message": (
                f"{label} 最近 {recent_coverage.get('window', _RECENT_REFERENCE_WINDOW)} 个交易日"
                f"{'，'.join(parts)}"
            ),
        })

    # 数据域（指标级）覆盖缺口
    if domain_summary:
        empty = domain_summary.get("empty_domains") or []
        stale = domain_summary.get("stale_domains") or []
        if empty:
            labels = "、".join(d.get("label") for d in empty)
            issues.append({
                "severity": "medium",
                "message": f"数据域无数据（未接入 / 未拉取）共 {len(empty)} 个：{labels}",
            })
        if stale:
            labels = "、".join(d.get("label") for d in stale)
            issues.append({
                "severity": "low",
                "message": f"数据域接入但数据滞后共 {len(stale)} 个（策略未持续刷新）：{labels}",
            })

    return issues


def _recommendation_summary(db: Session) -> dict[str, Any]:
    """推荐记录摘要。"""
    total = db.scalar(select(func.count()).select_from(Recommendation)) or 0
    if total == 0:
        return {"total": 0, "by_skill": {}, "latest": None}

    # Recommendation 表没有 skill_id 字段，skill 信息存储在 skill_breakdown_json 中
    # 改用 run_id 聚合
    by_run_rows = db.execute(
        select(Recommendation.run_id, func.count().label("cnt"))
        .group_by(Recommendation.run_id)
        .order_by(func.count().desc())
    ).all()
    by_run = {row.run_id: row.cnt for row in by_run_rows}

    latest = db.scalar(
        select(func.max(Recommendation.trade_date))
    )

    labeled = db.scalar(select(func.count()).select_from(RecommendationOutcome)) or 0

    return {
        "total": total,
        "by_run_id": by_run,
        "latest_trade_date": _fmt_date(latest),
        "labeled_count": labeled,
    }


def _fmt_date(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    s = str(val).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return s if s else None


def _date_diff(d1: str, d2: str) -> int:
    try:
        a = date.fromisoformat(d1)
        b = date.fromisoformat(d2)
        return (a - b).days
    except Exception:
        return 999


def _compact_date(value: Any) -> str:
    normalized = _fmt_date(value) or ""
    return normalized.replace("-", "")


def _date_variants(value: Any) -> list[str]:
    normalized = _fmt_date(value)
    if not normalized:
        return []
    return sorted({normalized, normalized.replace("-", "")})


def _table_row_count(db: Session, tbl: Any, *, prefer_estimate: bool = False) -> tuple[int, bool]:
    if prefer_estimate:
        estimated = _estimated_table_rows(db, tbl.__tablename__)
        if estimated is not None:
            return estimated, True
    return int(db.scalar(select(func.count()).select_from(tbl)) or 0), False


def _estimated_table_rows(db: Session, table_name: str) -> int | None:
    try:
        bind = db.get_bind()
        dialect_name = bind.dialect.name if bind is not None else ""
    except Exception:
        dialect_name = ""
    if dialect_name not in {"mysql", "mariadb"}:
        return None
    try:
        row = db.execute(
            text(
                "SELECT TABLE_ROWS "
                "FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name"
            ),
            {"table_name": table_name},
        ).first()
        if row and row[0] is not None:
            return int(row[0])
    except Exception:
        _rollback_session_safely(db)
    return None


def _date_bounds(db: Session, col: Any) -> tuple[str | None, str | None]:
    normalized_col = func.replace(col, "-", "")
    min_date, max_date = db.execute(
        select(func.min(normalized_col), func.max(normalized_col)).where(col.is_not(None))
    ).one()
    return _fmt_date(min_date), _fmt_date(max_date)


def _recent_daily_trade_days(db: Session, reference_trade_date: str, window: int) -> list[str]:
    reference_compact = _compact_date(reference_trade_date)
    normalized_col = func.replace(DailyKbar.trade_date, "-", "")
    rows = db.scalars(
        select(func.distinct(normalized_col))
        .where(DailyKbar.trade_date.is_not(None), normalized_col <= reference_compact)
        .order_by(normalized_col.desc())
        .limit(max(1, window))
    ).all()
    normalized = [_fmt_date(item) for item in rows if _fmt_date(item)]
    reference = _fmt_date(reference_trade_date)
    if reference and reference not in normalized:
        normalized = [reference, *normalized]
    return list(reversed(normalized[:window]))


def _present_dates(db: Session, col: Any, ordered_days: list[str]) -> list[str]:
    ordered_set = set(ordered_days)
    target_values = sorted({variant for day in ordered_days for variant in _date_variants(day)})
    if not target_values:
        return []
    rows = db.scalars(select(distinct(col)).where(col.in_(target_values))).all()
    return sorted({_fmt_date(item) for item in rows if _fmt_date(item) in ordered_set})


def _incomplete_daily_kbar_dates(db: Session, ordered_days: list[str]) -> list[str]:
    if not ordered_days:
        return []
    counts: dict[str, int] = {}
    target_values = sorted({variant for day in ordered_days for variant in _date_variants(day)})
    rows = db.execute(
        select(DailyKbar.trade_date, func.count(distinct(DailyKbar.code)))
        .where(DailyKbar.trade_date.in_(target_values))
        .group_by(DailyKbar.trade_date)
    ).all()
    for trade_date, count in rows:
        normalized = _fmt_date(trade_date)
        if normalized in ordered_days:
            counts[normalized] = counts.get(normalized, 0) + int(count or 0)
    return [day for day in ordered_days if 0 < counts.get(day, 0) < _MIN_RECENT_DAILY_KBAR_CODES]


def _coverage_gap_count(coverage: dict[str, Any], reference_trade_date: str | None = None) -> int:
    if "effective_gap_count" in coverage:
        try:
            return int(coverage.get("effective_gap_count") or 0)
        except (TypeError, ValueError):
            return 0
    missing_dates, incomplete_dates = _effective_coverage_dates(coverage, reference_trade_date)
    return len(set(missing_dates + incomplete_dates))


def _effective_coverage_dates(
    coverage: dict[str, Any],
    reference_trade_date: str | None,
) -> tuple[list[str], list[str]]:
    if "effective_missing_dates" in coverage or "effective_incomplete_dates" in coverage:
        return list(coverage.get("effective_missing_dates") or []), list(coverage.get("effective_incomplete_dates") or [])

    missing_dates = list(coverage.get("missing_dates") or [])
    incomplete_dates = list(coverage.get("incomplete_dates") or [])
    tolerated = _realtime_tolerated_gap_dates(
        missing_dates=missing_dates,
        incomplete_dates=incomplete_dates,
        reference_trade_date=reference_trade_date,
    )
    return (
        [day for day in missing_dates if day not in tolerated],
        [day for day in incomplete_dates if day not in tolerated],
    )


def _realtime_tolerated_gap_dates(
    *,
    missing_dates: list[str],
    incomplete_dates: list[str],
    reference_trade_date: str | None,
) -> set[str]:
    # reference_trade_date 已被限制为最新“已收盘”交易日，因此这里不再容忍任何
    # 参考日缺口。盘中实时快照由 market_pool 的 realtime 模式单独处理，不能掩盖
    # 日 K、指数、涨停板或资金流在收盘后的整日缺失。
    del missing_dates, incomplete_dates, reference_trade_date
    return set()


_RECOMMENDATION_REQUIRED_TABLES = (
    "daily_kbar",
    "stock_quote",
    "index_daily",
    "limit_up_board",
)


def recommendation_readiness_from_diagnosis(
    diagnosis: dict[str, Any],
    *,
    expected_trade_date: str | None = None,
) -> dict[str, Any]:
    """把全面诊断投影为盘后本地推荐的硬闸门。"""
    reference = _fmt_date(expected_trade_date or diagnosis.get("reference_trade_date"))
    tables = diagnosis.get("tables") or {}
    recent = (diagnosis.get("recent_trade_day_coverage") or {}).get("tables") or {}
    blockers: list[str] = []
    warnings: list[str] = []

    if not reference:
        blockers.append("无法确定最新已收盘交易日")
    for key in _RECOMMENDATION_REQUIRED_TABLES:
        info = tables.get(key) or {}
        max_date = _fmt_date(info.get("max_date"))
        if info.get("error"):
            blockers.append(f"{info.get('label') or key}诊断失败")
        elif reference and max_date != reference:
            blockers.append(f"{info.get('label') or key}未覆盖 {reference}（最新 {max_date or '无'}）")
        coverage = recent.get(key) or {}
        if int(coverage.get("effective_gap_count") or 0) > 0:
            blockers.append(f"{coverage.get('label') or key}近期仍有缺口")

    for issue in diagnosis.get("issues") or []:
        if issue.get("severity") in {"medium", "low"}:
            warnings.append(str(issue.get("message") or ""))

    return {
        "status": "blocked" if blockers else ("degraded" if warnings else "ready"),
        "can_run_local_recommendation": not blockers,
        "reference_trade_date": reference,
        "blockers": blockers,
        "warnings": warnings,
    }


def _rollback_session_safely(db: Session) -> None:
    try:
        db.rollback()
    except Exception:
        pass
