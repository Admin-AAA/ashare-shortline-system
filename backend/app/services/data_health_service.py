"""
数据质量诊断服务：报告各表的数据覆盖情况、日期连续性、异常检测。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from threading import Condition
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
# - column：可选，非 NULL 表示该数据域已有数据（如 筹码峰 chip_cost_50）
# - estimated_row_count：超大整表只展示 MySQL 估算行数，避免健康检查全表计数
_DAILY_DATA_FRESH_DAYS = 0
# 已知 Tushare 盘后延迟出数的数据域：融资融券明细(margin_detail)盘后晚出。
# hk_hold 是季末持股快照，不是日频数据；按季报/股东人数相同窗口检查。
_MARGIN_LIKE_FRESH_DAYS = 1
_HK_HOLD_FRESH_DAYS = 130

# ---------------------------------------------------------------------------
# 值分布哨兵（L-143）
#
# 既有的四类扣分全是**结构性**的：表是否为空、最新日期滞后几天、近 N 个交易日缺
# 哪几天、数据源是否连通。它们回答「数据管道有没有在跑」，回答不了「数据值可不可
# 信」——一列恒零、恒同值或整列 NULL 时，行数、lag、覆盖天数全都正常，评分满分。
#
# 实测代价：daily_kbars.upper_shadow_count20 自 2026-09-09 起全表恒 0 达 14 天，
# 使 6 个策略的长上影 prefilter 静默失效、全市场 18% 本该被挡的候选混入；
# macd_dif/dea 连续 12 个交易日全表 0.0，使 macd_status 恒为「下穿零轴」；
# pct_chg 更是在两次人工体检之间被刷成全零，而 limit_up_days20 直接数它，
# 导致 limit-up-reseal-relay 的 universe 硬条件全拒。健康度全程 100 分。
#
# 哨兵按「当日横截面」检查三件事：整列是否 NULL、唯一值个数、非零占比。
# 每列必须显式声明期望，否则布尔型列（breakout20 只有 0/1 两个值）会误报。
# critical=True 的列失真按更高权重扣分——它们直接进选股决策。
# ---------------------------------------------------------------------------
_VALUE_SENTINEL_DEFS: list[dict[str, Any]] = [
    # 核心行情：任何一列失真都直接污染决策
    {"column": "close", "label": "收盘价", "critical": True, "min_distinct": 100, "min_nonzero_ratio": 0.99},
    {"column": "volume", "label": "成交量", "critical": True, "min_distinct": 100, "min_nonzero_ratio": 0.90},
    {"column": "amount", "label": "成交额", "critical": True, "min_distinct": 100, "min_nonzero_ratio": 0.90},
    # pct_chg：limit_up_days20 直接数它（>=9.8），全零会让涨停类策略 universe 全拒
    {"column": "pct_chg", "label": "涨跌幅", "critical": True, "min_distinct": 100, "min_nonzero_ratio": 0.50},
    # turnover_rate：9 个策略的 prefilter 用它
    {"column": "turnover_rate", "label": "换手率", "critical": True, "min_distinct": 100, "min_nonzero_ratio": 0.80},
    # 本地派生指标：消费侧已改为无条件重算，缓存失真不污染决策，但仍是脏数据，
    # 且下一个开发者可能再次 prefer cached，所以照样要可观测（非 critical）。
    {"column": "ma5", "label": "MA5", "critical": False, "min_distinct": 100, "min_nonzero_ratio": 0.95},
    {"column": "ma20", "label": "MA20", "critical": False, "min_distinct": 100, "min_nonzero_ratio": 0.95},
    {"column": "bias_5", "label": "5日乖离", "critical": False, "min_distinct": 100, "min_nonzero_ratio": 0.80},
    {"column": "vol_ratio_5", "label": "5日量比", "critical": False, "min_distinct": 100, "min_nonzero_ratio": 0.90},
    {"column": "max_dd20", "label": "20日最大回撤", "critical": False, "min_distinct": 100, "min_nonzero_ratio": 0.80},
    {"column": "upper_shadow_count20", "label": "20日长上影次数", "critical": False, "min_distinct": 3, "min_nonzero_ratio": 0.05},
    # 布尔型：唯一值只有 0/1，期望必须放宽否则必然误报
    {"column": "breakout20", "label": "20日突破", "critical": False, "min_distinct": 2, "min_nonzero_ratio": 0.0},
]

# 成交额(元)/(成交量×收盘价)：按手计约 100，按股计约 1；低于该值即判定这一行按股写入。
_VOLUME_UNIT_SHARES_RATIO_CEILING = 10
# 按股写入的行超过该占比即判定成交量列失真（停牌、极端价格的个别行不误报）。
_VOLUME_UNIT_MAX_SHARES_RATIO = 0.05

_DATA_DOMAIN_DEFS: list[dict[str, Any]] = [
    # 行情 K 线
    {"key": "daily_kbar", "label": "日K线(含技术指标)", "category": "行情K线", "table": DailyKbar, "date_col": "trade_date", "critical": True, "fresh_days": _DAILY_DATA_FRESH_DAYS, "estimated_row_count": True},
    {"key": "chip", "label": "筹码分布(筹码峰)", "category": "行情K线", "table": DailyKbar, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS, "column": "chip_cost_50"},
    {"key": "minute_kbar", "label": "分钟K线", "category": "行情K线", "table": MinuteKbar, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS, "estimated_row_count": True},
    {"key": "index_daily", "label": "指数日线", "category": "行情K线", "table": IndexDaily, "date_col": "trade_date", "critical": True, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "stk_limit", "label": "涨跌停价格", "category": "行情K线", "table": StkLimit, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS, "estimated_row_count": True},
    {"key": "auction", "label": "集合竞价", "category": "行情K线", "table": AuctionData, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    # 实时盘口
    {"key": "realtime_quote", "label": "盘中实时快照", "category": "实时盘口", "table": StockQuote, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    # 情绪题材
    {"key": "limit_up", "label": "涨停板", "category": "情绪题材", "table": LimitUpBoard, "date_col": "trade_date", "critical": True, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "concept", "label": "概念板块", "category": "情绪题材", "table": ConceptSector, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "concept_member", "label": "概念板块成分", "category": "情绪题材", "table": ConceptSectorStock, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS, "estimated_row_count": True},
    {"key": "dragon_tiger", "label": "龙虎榜", "category": "情绪题材", "table": DragonTiger, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    {"key": "broker_recommend", "label": "券商金股", "category": "情绪题材", "table": BrokerRecommend, "date_col": "month", "critical": False, "fresh_days": 45},
    # 资金流向
    {"key": "capital_flow", "label": "资金流向", "category": "资金流向", "table": CapitalFlow, "date_col": "trade_date", "critical": False, "fresh_days": _DAILY_DATA_FRESH_DAYS},
    # 北向 / 港股通
    {"key": "hsgt_flow", "label": "沪深港通资金流", "category": "北向港股通", "table": HsgtFlow, "date_col": "trade_date", "critical": False, "fresh_days": _MARGIN_LIKE_FRESH_DAYS, "source_lag_prone": True},
    {"key": "hsgt_top10", "label": "北向持股TOP10", "category": "北向港股通", "table": HsgtTop10, "date_col": "trade_date", "critical": False, "fresh_days": _MARGIN_LIKE_FRESH_DAYS, "source_lag_prone": True},
    {"key": "hk_hold", "label": "港股通持股", "category": "北向港股通", "table": HkHolding, "date_col": "trade_date", "critical": False, "fresh_days": _HK_HOLD_FRESH_DAYS, "source_lag_prone": True},
    # 融资融券
    # margin_data 是旧版逐股投影，已无生产消费者；当前唯一执行数据源是 S4
    # margin_detail。继续把旧表计入健康分会在主数据正常时制造重复的假告警。
    {"key": "margin_detail", "label": "融资融券明细", "category": "融资融券", "table": MarginDetail, "date_col": "trade_date", "critical": False, "fresh_days": _MARGIN_LIKE_FRESH_DAYS, "source_lag_prone": True},
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
# 诊断结果会被驾驶舱与配置页同时读取。冷缓存时合并并发请求，避免 React
# StrictMode 等重复挂载让两个请求各自执行一次约 20 秒的全量诊断。
_diagnosis_cache: dict[str, Any] = {"reference_trade_date": None, "result": None}
_diagnosis_cache_condition = Condition()
_diagnosis_cache_generation = 0
_diagnosis_in_progress = False


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
    global _diagnosis_cache_generation
    with _diagnosis_cache_condition:
        _diagnosis_cache["reference_trade_date"] = None
        _diagnosis_cache["result"] = None
        _diagnosis_cache_generation += 1


def diagnose_all(db: Session, *, force_refresh: bool = False) -> dict[str, Any]:
    """全面数据质量诊断。

    默认命中进程内缓存；同步/修复写入会显式清空，force_refresh 会绕过已完成
    的缓存。若已有一次诊断正在运行，所有调用者复用该次结果，避免重复全量查询。
    """
    global _diagnosis_in_progress
    with _diagnosis_cache_condition:
        cached = _get_cached_diagnosis()
        if cached is not None and not force_refresh:
            logger.debug("data_health: cache hit (reference_date=%s)", cached.get("reference_trade_date"))
            return cached

        while _diagnosis_in_progress:
            logger.debug("data_health: waiting for in-flight diagnosis")
            _diagnosis_cache_condition.wait()
            cached = _get_cached_diagnosis()
            if cached is not None:
                logger.debug("data_health: reusing completed in-flight diagnosis")
                return cached

        cache_generation = _diagnosis_cache_generation
        _diagnosis_in_progress = True

    try:
        result = _diagnose_all_uncached(db)
    except Exception:
        with _diagnosis_cache_condition:
            _diagnosis_in_progress = False
            _diagnosis_cache_condition.notify_all()
        raise

    with _diagnosis_cache_condition:
        if cache_generation == _diagnosis_cache_generation:
            _set_cached_diagnosis(result)
        _diagnosis_in_progress = False
        _diagnosis_cache_condition.notify_all()
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

    # 4c. 值分布哨兵：抓恒零/恒同值/整列 NULL——结构性检查看不见的失真（L-143）
    value_sentinel = _value_distribution_sentinel(db, reference_trade_date)

    # 5. 健康评分 (0-100)
    score = _compute_health_score(
        table_stats, lag_days, reference_trade_date, recent_coverage,
        source_health, domain_summary, value_sentinel,
    )

    # 6. 关键问题列表
    issues = _find_issues(
        table_stats, lag_days, reference_trade_date, recent_coverage,
        reference_info, domain_summary, value_sentinel,
    )

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
        "value_distribution": value_sentinel,
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


def _get_tushare_recent_trade_dates() -> list[str]:
    """Tushare 交易日历里最近的交易日（ISO 升序）；不可用时返回空列表。"""
    try:
        from app.services.tushare_client import fetch_recent_trade_dates

        return [
            f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
            for compact in fetch_recent_trade_dates()
            if compact and len(compact) == 8
        ]
    except Exception:
        return []


def _resolve_reference_trade_date(db: Session) -> dict[str, str | None]:
    # 参考交易日必须落在交易所日历的真实交易日上。`_latest_allowed_bar_date()` 只表达
    # 「盘后 17:30 数据就绪」这个时间判据，它按工作日算术回退，会落到休市的工作日上
    # （2026-09-25 即如此），于是所有表都被判成缺那一天。用它作时间上界，再与真实交易日
    # 列表求交，取最后一个（L-147）。
    trade_dates = _get_tushare_recent_trade_dates()
    if trade_dates:
        data_ready_cutoff = _latest_allowed_bar_date()
        closed_trade_dates = [day for day in trade_dates if day <= data_ready_cutoff]
        if closed_trade_dates:
            return {
                "reference_trade_date": closed_trade_dates[-1],
                "external_trade_date": trade_dates[-1],
                "source": "tushare",
                "note": "参考交易日取 Tushare 交易日历中最新一个已收盘交易日，休市日不计入。",
            }

    local_trade_date = _infer_local_reference_trade_date(db)
    if local_trade_date:
        return {
            "reference_trade_date": local_trade_date,
            "external_trade_date": None,
            "source": "local",
            "note": "Tushare 交易日历不可用，已回退到本地关键表最新交易日。",
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
    min_date, max_date = db.execute(
        select(func.min(StockQuote.trade_date), func.max(StockQuote.trade_date)).where(
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
    value_sentinel: dict[str, Any] | None = None,
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

    # 扣分：值分布失真（恒零/恒同值/整列 NULL）。结构性检查全绿但值不可信时，
    # 这是唯一能把分数压下来的维度——否则一列恒零 14 天仍是满分（L-143）。
    if value_sentinel:
        penalty = int(value_sentinel.get("score_penalty", 0) or 0)
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
            "note": s.get("note"),
            "last_api": s.get("last_api"),
            "breaker_state": s.get("breaker_state"),
            "rate_limit_per_minute": s.get("rate_limit_per_minute"),
        }
        if status == "healthy":
            working.append(entry)
        elif status == "degraded":
            broken.append(entry)
        elif status == "disabled":
            disabled.append(entry)
        else:  # untested / unknown
            untested.append(entry)

    # 评分只扣明确失败。未测试代表“尚无观测证据”，继续展示但不扣分；
    # 数据是否真的缺失已由 data_domains / recent_coverage 单独计分，避免重复惩罚。
    # Fastapic 与 ai-tool 属于同一逻辑 Tushare 源下的两个独立故障域：只要其中
    # 一个健康，Router 仍有可用路径，单个 Provider 降级不再重复扣分。
    all_entries = working + broken + untested + disabled
    provider_entries = [
        item for item in all_entries if item.get("source_type") == "tushare_provider"
    ]
    non_provider_broken = [
        item for item in broken if item.get("source_type") != "tushare_provider"
    ]
    penalty = min(15, len(non_provider_broken) * 5)
    enabled_providers = [item for item in provider_entries if item.get("enabled")]
    healthy_providers = [item for item in enabled_providers if item.get("status") == "healthy"]
    degraded_providers = [item for item in enabled_providers if item.get("status") == "degraded"]
    provider_penalty = 0
    if provider_entries and not enabled_providers:
        provider_penalty = 5
    elif enabled_providers and not healthy_providers and degraded_providers:
        provider_penalty = 5
    penalty = min(15, penalty + provider_penalty)

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
        "score_policy": "仅明确失败且没有可用同组容灾时扣分；未测试仅提示，不扣分。",
        "provider_redundancy": {
            "configured_count": len(enabled_providers),
            "healthy_count": len(healthy_providers),
            "degraded_count": len(degraded_providers),
            "fallback_available": bool(healthy_providers),
            "score_penalty": provider_penalty,
        },
    }


def _data_domain_coverage(db: Session, reference_trade_date: str | None) -> dict[str, Any]:
    """数据域（指标级）覆盖：哪些市场数据域有接入策略且持续拉取。

    返回按类别分组的覆盖明细 + 汇总（覆盖率、评分扣减）。
    状态判定：
      - empty：行数为 0 → 无数据（未接入 / 未拉取）
      - covered：有数据，且在 fresh_days 容忍窗口内（事件型域有数据即 covered）
      - stale：有数据，但最新日期超出 fresh_days 容忍窗口
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

        total: int | None = 0
        total_estimated = False
        has_data = False
        max_date = None
        try:
            value_col = getattr(tbl, column) if column and hasattr(tbl, column) else None
            presence_query = select(1).select_from(tbl)
            if value_col is not None:
                presence_query = presence_query.where(value_col.is_not(None))
            has_data = db.scalar(presence_query.limit(1)) is not None

            if value_col is not None:
                # 字段型数据域只需要知道是否有值；精确 COUNT 会全表扫描且不参与健康判定。
                total = None
            else:
                total, total_estimated = _table_row_count(
                    db,
                    tbl,
                    prefer_estimate=bool(ddef.get("estimated_row_count")),
                )

            if hasattr(tbl, dc):
                date_col = getattr(tbl, dc)
                latest_query = select(date_col).where(date_col.is_not(None))
                if value_col is not None:
                    latest_query = latest_query.where(value_col.is_not(None))
                max_date = db.scalar(latest_query.order_by(date_col.desc()).limit(1))
        except Exception as exc:
            _rollback_session_safely(db)
            logger.warning("数据域 %s 统计失败: %s", key, exc)
            domains.append({
                "key": key, "label": label, "category": category, "status": "error",
                "total_rows": 0, "total_rows_estimated": False, "max_date": None, "critical": critical,
                "fresh_window_days": fresh, "gap_days": None, "note": str(exc),
            })
            continue

        max_date = _fmt_date(max_date)
        # 月份型列（YYYYMM，如 券商金股 month）归一化为 YYYY-MM 便于展示
        if max_date and len(max_date) == 6 and max_date.isdigit():
            max_date = f"{max_date[:4]}-{max_date[4:]}"

        source_lag_prone = bool(ddef.get("source_lag_prone"))
        gap_days: int | None = None
        note: str | None = None
        if not has_data:
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
                # 区分「系统漏拉」与「数据源盘后延迟」：已知延迟型数据域
                # (融资融券、港股通持股) 在参考日附近滞后，更可能是 Tushare
                # 当日数据未出而非补数链路故障，标注让用户优先排查系统侧。
                if source_lag_prone and gap_days is not None and gap_days <= max(2, fresh + 1):
                    note = "可能数据源盘后延迟（非系统漏拉），下一盘后周期会自动补"

        domains.append({
            "key": key,
            "label": label,
            "category": category,
            "status": status,
            "total_rows": total,
            "total_rows_estimated": total_estimated,
            "max_date": max_date,
            "critical": critical,
            "fresh_window_days": fresh,
            "gap_days": gap_days if (fresh is not None and has_data) else None,
            "note": note,
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


def _value_distribution_sentinel(db: Session, reference_trade_date: str | None) -> dict[str, Any]:
    """按当日横截面检查 daily_kbars 关键列的值分布，抓「有值但值是错的」（L-143）。

    结构性检查（行数/lag/覆盖天数）看不见恒零与恒同值：整列被写成 0 时行数依然
    完整、日期依然最新。这里用三个指标补上：
      - 整列 NULL      -> 该列当日完全没有数据
      - 唯一值个数     -> <= 1 即恒同值（恒零是它的特例）
      - 非零占比       -> 低于该列声明的期望即可疑（布尔列期望为 0，不误报）

    只查参考交易日单日（约 5 千行），COUNT(DISTINCT) 成本可接受；不做全表扫描。
    """
    if not reference_trade_date:
        return {
            "reference_trade_date": None,
            "checked": 0,
            "distorted": [],
            "healthy_count": 0,
            "score_penalty": 0,
            "note": "无参考交易日，跳过值分布检查",
        }

    aggregates = []
    for spec in _VALUE_SENTINEL_DEFS:
        col = spec["column"]
        aggregates.append(f"SUM(CASE WHEN `{col}` IS NOT NULL THEN 1 ELSE 0 END) AS nn_{col}")
        aggregates.append(f"COUNT(DISTINCT `{col}`) AS ud_{col}")
        aggregates.append(f"SUM(CASE WHEN `{col}` <> 0 THEN 1 ELSE 0 END) AS nz_{col}")

    sql = (
        "SELECT COUNT(*) AS rows_n, " + ", ".join(aggregates) +
        # 成交量单位：daily_kbars 按手计，成交额(元)/(成交量×收盘价) 约为 100；
        # 按股写入时约为 1。值分布指标看不出单位错，必须单独数（L-159）。
        ", SUM(CASE WHEN volume > 0 AND close > 0 AND amount / (volume * close) < "
        f"{_VOLUME_UNIT_SHARES_RATIO_CEILING} THEN 1 ELSE 0 END) AS volume_in_shares"
        " FROM daily_kbars WHERE trade_date = :ref"
    )
    try:
        row = db.execute(text(sql), {"ref": reference_trade_date}).mappings().first()
    except Exception as exc:
        _rollback_session_safely(db)
        logger.warning("值分布哨兵查询失败: %s", exc)
        return {
            "reference_trade_date": reference_trade_date,
            "checked": 0,
            "distorted": [],
            "healthy_count": 0,
            "score_penalty": 0,
            "note": f"查询失败: {exc}",
        }

    total_rows = int((row or {}).get("rows_n") or 0)
    if not total_rows:
        return {
            "reference_trade_date": reference_trade_date,
            "checked": 0,
            "distorted": [],
            "healthy_count": 0,
            "score_penalty": 0,
            "note": "参考交易日无 daily_kbars 行，值分布无法判定",
        }

    distorted: list[dict[str, Any]] = []
    healthy = 0
    penalty = 0
    for spec in _VALUE_SENTINEL_DEFS:
        col = spec["column"]
        critical = bool(spec.get("critical"))
        not_null = int(row.get(f"nn_{col}") or 0)
        distinct = int(row.get(f"ud_{col}") or 0)
        non_zero = int(row.get(f"nz_{col}") or 0)
        nonzero_ratio = round(non_zero / total_rows, 4) if total_rows else 0.0

        reason: str | None = None
        if not_null == 0:
            reason = "整列 NULL"
        elif distinct <= 1:
            reason = f"恒同值（唯一值 {distinct}）"
        elif distinct < int(spec.get("min_distinct") or 0):
            reason = f"唯一值过少（{distinct} < 期望 {spec['min_distinct']}）"
        elif nonzero_ratio < float(spec.get("min_nonzero_ratio") or 0.0):
            reason = f"非零占比过低（{nonzero_ratio:.1%} < 期望 {float(spec['min_nonzero_ratio']):.0%}）"

        if reason is None:
            healthy += 1
            continue

        penalty += 5 if critical else 2
        distorted.append({
            "column": col,
            "label": spec.get("label") or col,
            "critical": critical,
            "reason": reason,
            "not_null": not_null,
            "distinct": distinct,
            "non_zero": non_zero,
            "nonzero_ratio": nonzero_ratio,
            "total_rows": total_rows,
        })

    volume_in_shares = int(row.get("volume_in_shares") or 0)
    volume_in_shares_ratio = round(volume_in_shares / total_rows, 4) if total_rows else 0.0
    already_flagged = any(item["column"] == "volume" for item in distorted)
    if volume_in_shares_ratio > _VOLUME_UNIT_MAX_SHARES_RATIO and not already_flagged:
        healthy -= 1
        penalty += 5
        distorted.append({
            "column": "volume",
            "label": "成交量",
            "critical": True,
            "reason": f"成交量按股写入（{volume_in_shares} 行，占 {volume_in_shares_ratio:.1%}），与日线按手计不一致",
            "volume_in_shares": volume_in_shares,
            "volume_in_shares_ratio": volume_in_shares_ratio,
            "total_rows": total_rows,
        })

    # 与 domain penalty 同量级封顶，避免单一维度独吞整个评分
    capped = min(penalty, 20)
    return {
        "reference_trade_date": reference_trade_date,
        "total_rows": total_rows,
        "checked": len(_VALUE_SENTINEL_DEFS),
        "healthy_count": healthy,
        "distorted": distorted,
        "distorted_count": len(distorted),
        "critical_distorted_count": sum(1 for d in distorted if d["critical"]),
        "score_penalty": capped,
    }


def _find_issues(
    table_stats: dict[str, Any],
    lag_days: dict[str, int],
    reference_trade_date: str | None,
    recent_coverage: dict[str, Any],
    reference_info: dict[str, str | None],
    domain_summary: dict[str, Any] | None = None,
    value_sentinel: dict[str, Any] | None = None,
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
                "message": f"数据域最新日期未达参考交易日共 {len(stale)} 个：{labels}",
            })
            # 已知数据源延迟型数据域单独显示发布进度。
            lag_notes = [d["note"] for d in stale if d.get("note")]
            if lag_notes:
                issues.append({
                    "severity": "low",
                    "message": "其中部分缺口可能源于 Tushare 盘后延迟发布，"
                               "下一盘后周期会自动补齐",
                })

    if value_sentinel:
        distorted = value_sentinel.get("distorted") or []
        critical = [d for d in distorted if d.get("critical")]
        others = [d for d in distorted if not d.get("critical")]
        if critical:
            detail = "、".join(f"{d['label']}（{d['reason']}）" for d in critical)
            issues.append({
                "severity": "high",
                "message": f"关键列值分布失真共 {len(critical)} 列，直接影响选股决策：{detail}",
            })
        if others:
            detail = "、".join(f"{d['label']}（{d['reason']}）" for d in others)
            issues.append({
                "severity": "medium",
                "message": f"派生指标列值分布失真共 {len(others)} 列"
                           f"（消费侧已改重算，不污染决策但属脏数据）：{detail}",
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
    min_date, max_date = db.execute(
        select(func.min(col), func.max(col)).where(col.is_not(None))
    ).one()
    return _fmt_date(min_date), _fmt_date(max_date)


def _recent_daily_trade_days(db: Session, reference_trade_date: str, window: int) -> list[str]:
    reference = _fmt_date(reference_trade_date)
    rows = db.scalars(
        select(func.distinct(DailyKbar.trade_date))
        .where(DailyKbar.trade_date.is_not(None), DailyKbar.trade_date <= reference)
        .order_by(DailyKbar.trade_date.desc())
        .limit(max(1, window))
    ).all()
    normalized = [_fmt_date(item) for item in rows if _fmt_date(item)]
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
    """把全面诊断投影为盘后本地推荐的硬闸门。

    ``blockers`` 保留面向用户的中文摘要；``blocker_details`` 提供稳定的结构化
    契约，供调度层区分可恢复的当日辅助数据滞后与不可重试的行情/历史缺口。
    """
    reference = _fmt_date(expected_trade_date or diagnosis.get("reference_trade_date"))
    tables = diagnosis.get("tables") or {}
    recent = (diagnosis.get("recent_trade_day_coverage") or {}).get("tables") or {}
    blockers: list[str] = []
    blocker_details: list[dict[str, Any]] = []
    warnings: list[str] = []

    def add_blocker(code: str, message: str, **details: Any) -> None:
        blockers.append(message)
        blocker_details.append({"code": code, "message": message, **details})

    if not reference:
        add_blocker("missing_reference_trade_date", "无法确定最新已收盘交易日")
    for key in _RECOMMENDATION_REQUIRED_TABLES:
        info = tables.get(key) or {}
        label = str(info.get("label") or key)
        max_date = _fmt_date(info.get("max_date"))
        if info.get("error"):
            add_blocker(
                "table_diagnosis_failed",
                f"{label}诊断失败",
                table=key,
                expected_trade_date=reference,
            )
        elif reference and max_date < reference:
            add_blocker(
                "table_not_current",
                f"{label}未覆盖 {reference}（最新 {max_date or '无'}）",
                table=key,
                expected_trade_date=reference,
                latest_trade_date=max_date or None,
            )

        coverage = recent.get(key) or {}
        if coverage.get("error"):
            add_blocker(
                "recent_coverage_diagnosis_failed",
                f"{coverage.get('label') or label}近期覆盖诊断失败",
                table=key,
                expected_trade_date=reference,
            )
            continue
        gap_count = _coverage_gap_count(coverage, reference)
        if gap_count > 0:
            missing_dates, incomplete_dates = _effective_coverage_dates(coverage, reference)
            add_blocker(
                "recent_coverage_gap",
                f"{coverage.get('label') or label}近期仍有缺口",
                table=key,
                expected_trade_date=reference,
                missing_dates=sorted(set(missing_dates)),
                incomplete_dates=sorted(set(incomplete_dates)),
                gap_count=gap_count,
            )

    # 结构性检查只回答「有没有」；关键行情列「值是错的」时（恒零、单位错）照常出票，
    # 推荐就建在坏数据上。09-28 起四个交易日的涨跌幅与换手率被写成 0，盘后推荐全部放行（L-159）。
    sentinel = diagnosis.get("value_distribution") or {}
    sentinel_date = _fmt_date(sentinel.get("reference_trade_date"))
    if reference and sentinel_date == reference:
        for item in sentinel.get("distorted") or []:
            if not item.get("critical"):
                continue
            add_blocker(
                "value_distribution_distorted",
                f"{item.get('label') or item.get('column')} {reference} 值失真：{item.get('reason')}",
                table="value_distribution",
                column=item.get("column"),
                expected_trade_date=reference,
            )

    for issue in diagnosis.get("issues") or []:
        if issue.get("severity") in {"medium", "low"}:
            warnings.append(str(issue.get("message") or ""))

    return {
        "status": "blocked" if blockers else ("degraded" if warnings else "ready"),
        "can_run_local_recommendation": not blockers,
        "reference_trade_date": reference,
        "blockers": blockers,
        "blocker_details": blocker_details,
        "warnings": warnings,
    }


def _rollback_session_safely(db: Session) -> None:
    try:
        db.rollback()
    except Exception:
        pass
