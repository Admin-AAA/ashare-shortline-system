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

_TUSHARE_TABLES = {"daily_kbar", "stock_quote", "minute_kbar", "index_daily", "limit_up_board", "dragon_tiger", "concept_sector", "capital_flow"}
_RECENT_REFERENCE_WINDOW = 10
_RECENT_WARN_MISSING_DAYS = 2
_RECENT_HIGH_MISSING_DAYS = 4
_MIN_RECENT_DAILY_KBAR_CODES = 3000


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
            total, total_estimated = _table_row_count(db, tbl, prefer_estimate=bool(tdef.get("estimated_row_count")))

            min_date = None
            max_date = None
            if dc and hasattr(tbl, dc):
                col = getattr(tbl, dc)
                min_date, max_date = _fast_date_bounds(db, col) if tdef.get("fast_date_bounds") else _date_bounds(db, col)

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
        _date_bounds(db, DailyKbar.trade_date)[1],
        _date_bounds(db, LimitUpBoard.trade_date)[1],
        _date_bounds(db, StockQuote.trade_date)[1],
        _date_bounds(db, Recommendation.trade_date)[1],
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
                "error": str(exc),
            }
            continue
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
            "incomplete_dates": incomplete_dates,
        }
        gap_count = len(set(missing + incomplete_dates))
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
        gap_count = _coverage_gap_count(coverage)
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
        incomplete_dates = coverage.get("incomplete_dates") or []
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


def _fast_date_bounds(db: Session, col: Any) -> tuple[str | None, str | None]:
    min_date = db.scalar(select(col).where(col.is_not(None)).order_by(col.asc()).limit(1))
    max_date = db.scalar(select(col).where(col.is_not(None)).order_by(col.desc()).limit(1))
    return _fmt_date(min_date), _fmt_date(max_date)


def _recent_daily_trade_days(db: Session, reference_trade_date: str, window: int) -> list[str]:
    reference_compact = _compact_date(reference_trade_date)
    normalized_col = func.replace(DailyKbar.trade_date, "-", "")
    rows = db.scalars(
        select(distinct(normalized_col))
        .where(DailyKbar.trade_date.is_not(None), normalized_col <= reference_compact)
        .order_by(normalized_col.desc())
        .limit(max(1, window))
    ).all()
    normalized = [_fmt_date(item) for item in rows if _fmt_date(item)]
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


def _coverage_gap_count(coverage: dict[str, Any]) -> int:
    return len(set((coverage.get("missing_dates") or []) + (coverage.get("incomplete_dates") or [])))


def _rollback_session_safely(db: Session) -> None:
    try:
        db.rollback()
    except Exception:
        pass
