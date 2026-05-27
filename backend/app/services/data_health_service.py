"""
数据质量诊断服务：报告各表的数据覆盖情况、日期连续性、异常检测。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    CapitalFlow,
    ConceptSector,
    DailyKbar,
    DragonTiger,
    IndexDaily,
    LimitUpBoard,
    MinuteKbar,
    NewsAnnouncement,
    Recommendation,
    RecommendationOutcome,
    StockQuote,
)

logger = logging.getLogger(__name__)

# ── 注册需要监控的表 ─────────────────────────────────────────────────────

_TABLE_DEFS: list[dict[str, Any]] = [
    {"key": "daily_kbar",       "label": "日K线",       "table": DailyKbar,       "date_col": "trade_date", "critical": True,  "expect_recent_coverage": True},
    {"key": "stock_quote",      "label": "实时快照",     "table": StockQuote,      "date_col": "trade_date", "critical": False, "expect_recent_coverage": False, "lag_alert_after_days": 1},
    {"key": "minute_kbar",      "label": "分钟K线",     "table": MinuteKbar,      "date_col": "trade_date", "critical": False, "expect_recent_coverage": True},
    {"key": "index_daily",      "label": "指数日线",     "table": IndexDaily,      "date_col": "trade_date", "critical": True,  "expect_recent_coverage": True},
    {"key": "limit_up_board",   "label": "涨停板",       "table": LimitUpBoard,    "date_col": "trade_date", "critical": True,  "expect_recent_coverage": True},
    {"key": "dragon_tiger",     "label": "龙虎榜",       "table": DragonTiger,     "date_col": "trade_date", "critical": False, "expect_recent_coverage": False},
    {"key": "concept_sector",   "label": "概念板块",     "table": ConceptSector,   "date_col": "trade_date", "critical": False, "expect_recent_coverage": False, "lag_alert_after_days": 1},
    {"key": "capital_flow",     "label": "资金流向",     "table": CapitalFlow,     "date_col": "trade_date", "critical": False, "expect_recent_coverage": True},
    {"key": "news",             "label": "新闻公告",     "table": NewsAnnouncement, "date_col": "announce_date", "critical": False, "expect_recent_coverage": False},
    {"key": "recommendation",   "label": "推荐记录",     "table": Recommendation,   "date_col": "trade_date", "critical": True,  "expect_recent_coverage": False},
    {"key": "outcome",          "label": "标注结果",     "table": RecommendationOutcome, "date_col": None, "critical": True, "expect_recent_coverage": False},
]

_TUSHARE_TABLES = {"daily_kbar", "stock_quote", "minute_kbar", "index_daily", "limit_up_board", "dragon_tiger", "concept_sector", "capital_flow"}
_RECENT_REFERENCE_WINDOW = 10
_RECENT_WARN_MISSING_DAYS = 2
_RECENT_HIGH_MISSING_DAYS = 4


def diagnose_all(db: Session) -> dict[str, Any]:
    """全面数据质量诊断。"""

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

    # 4. 健康评分 (0-100)
    score = _compute_health_score(table_stats, lag_days, reference_trade_date, recent_coverage)

    # 5. 关键问题列表
    issues = _find_issues(table_stats, lag_days, reference_trade_date, recent_coverage, reference_info)

    return {
        "score": score,
        "tushare_latest_trade_date": external_trade_date,
        "reference_trade_date": reference_trade_date,
        "reference_trade_date_source": reference_info["source"],
        "reference_trade_date_note": reference_info["note"],
        "tables": table_stats,
        "lag_days": lag_days,
        "recent_trade_day_coverage": recent_coverage,
        "issues": issues,
        "recommendation_summary": _recommendation_summary(db),
    }


def _table_stats(db: Session) -> dict[str, Any]:
    """各表行数 + 日期范围。"""
    result = {}
    for tdef in _TABLE_DEFS:
        key = tdef["key"]
        tbl = tdef["table"]
        dc = tdef["date_col"]
        label = tdef["label"]

        try:
            total = db.scalar(select(func.count()).select_from(tbl)) or 0

            min_date = None
            max_date = None
            if dc and hasattr(tbl, dc):
                col = getattr(tbl, dc)
                min_date = _fmt_date(db.scalar(select(func.min(col))))
                max_date = _fmt_date(db.scalar(select(func.max(col))))

            result[key] = {
                "label": label,
                "table": tbl.__tablename__,
                "total_rows": total,
                "min_date": min_date,
                "max_date": max_date,
                "critical": tdef["critical"],
            }
        except Exception as e:
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
        return {
            "reference_trade_date": external_trade_date,
            "external_trade_date": external_trade_date,
            "source": "tushare",
            "note": "参考交易日来自 Tushare 交易日历。",
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
        db.scalar(select(func.max(DailyKbar.trade_date))),
        db.scalar(select(func.max(LimitUpBoard.trade_date))),
        db.scalar(select(func.max(StockQuote.trade_date))),
        db.scalar(select(func.max(Recommendation.trade_date))),
    ]
    normalized = [_fmt_date(item) for item in candidates if item]
    return max(normalized) if normalized else None


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

    trade_days = db.scalars(
        select(distinct(DailyKbar.trade_date))
        .where(DailyKbar.trade_date <= reference_trade_date)
        .order_by(DailyKbar.trade_date.desc())
        .limit(window)
    ).all()
    ordered_days = sorted(str(day) for day in trade_days)
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
        present_rows = db.scalars(
            select(distinct(col))
            .where(col.in_(ordered_days))
            .order_by(col.asc())
        ).all()
        present = sorted(str(item) for item in present_rows if item)
        missing = [day for day in ordered_days if day not in present]
        covered_count = len(present)
        coverage_rate = round(covered_count / len(ordered_days), 3) if ordered_days else 0.0
        coverage_tables[tdef["key"]] = {
            "label": tdef["label"],
            "critical": tdef["critical"],
            "covered_count": covered_count,
            "expected_count": len(ordered_days),
            "coverage_rate": coverage_rate,
            "present_dates": present,
            "missing_dates": missing,
        }
        max_missing_days = max(max_missing_days, len(missing))
        if tdef["critical"] and len(missing) >= _RECENT_HIGH_MISSING_DAYS:
            high_impact_tables.append(tdef["key"])
        elif len(missing) >= _RECENT_WARN_MISSING_DAYS:
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

    # 扣分：滞后
    for key, lag in lag_days.items():
        if lag > 5:
            score -= 10
        elif lag > 2:
            score -= 5

    recent_tables = recent_coverage.get("tables") or {}
    for key, coverage in recent_tables.items():
        missing_count = len(coverage.get("missing_dates") or [])
        if missing_count == 0:
            continue
        if coverage.get("critical"):
            score -= min(15, missing_count * 3)
        else:
            score -= min(6, missing_count)

    # 扣分：outcome 没有标注
    outc = table_stats.get("outcome", {})
    if outc.get("total_rows", 0) == 0:
        score -= 15

    # 扣分：recommendation 为空
    rec = table_stats.get("recommendation", {})
    if rec.get("total_rows", 0) == 0:
        score -= 15

    return max(0, min(100, score))


def _find_issues(
    table_stats: dict[str, Any],
    lag_days: dict[str, int],
    reference_trade_date: str | None,
    recent_coverage: dict[str, Any],
    reference_info: dict[str, str | None],
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
        missing_dates = coverage.get("missing_dates") or []
        if not missing_dates:
            continue
        label = coverage.get("label") or key
        severity = "high" if coverage.get("critical") and len(missing_dates) >= _RECENT_HIGH_MISSING_DAYS else "medium"
        issues.append({
            "severity": severity,
            "message": (
                f"{label} 最近 {recent_coverage.get('window', _RECENT_REFERENCE_WINDOW)} 个交易日缺失 "
                f"{len(missing_dates)} 天: {', '.join(missing_dates[:4])}"
                + (" ..." if len(missing_dates) > 4 else "")
            ),
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
    if len(s) >= 10:
        return s[:10]
    return s if s else None


def _date_diff(d1: str, d2: str) -> int:
    try:
        a = date.fromisoformat(d1)
        b = date.fromisoformat(d2)
        return (a - b).days
    except Exception:
        return 999
