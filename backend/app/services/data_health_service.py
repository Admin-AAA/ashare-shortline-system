"""
数据质量诊断服务：报告各表的数据覆盖情况、日期连续性、异常检测。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    CapitalFlow,
    ConceptSector,
    DailyKbar,
    DragonTiger,
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
    {"key": "daily_kbar",       "label": "日K线",       "table": DailyKbar,       "date_col": "trade_date", "critical": True},
    {"key": "stock_quote",      "label": "实时快照",     "table": StockQuote,      "date_col": "trade_date", "critical": False},
    {"key": "minute_kbar",      "label": "分钟K线",     "table": MinuteKbar,      "date_col": "trade_date", "critical": False},
    {"key": "limit_up_board",   "label": "涨停板",       "table": LimitUpBoard,    "date_col": "trade_date", "critical": True},
    {"key": "dragon_tiger",     "label": "龙虎榜",       "table": DragonTiger,     "date_col": "trade_date", "critical": False},
    {"key": "concept_sector",   "label": "概念板块",     "table": ConceptSector,   "date_col": "trade_date", "critical": False},
    {"key": "capital_flow",     "label": "资金流向",     "table": CapitalFlow,     "date_col": "trade_date", "critical": False},
    {"key": "news",             "label": "新闻公告",     "table": NewsAnnouncement, "date_col": "announce_date", "critical": False},
    {"key": "recommendation",   "label": "推荐记录",     "table": Recommendation,   "date_col": "trade_date", "critical": True},
    {"key": "outcome",          "label": "标注结果",     "table": RecommendationOutcome, "date_col": None, "critical": True},
]

_TUSHARE_TABLES = {"daily_kbar", "stock_quote", "minute_kbar", "limit_up_board", "dragon_tiger", "concept_sector", "capital_flow"}


def diagnose_all(db: Session) -> dict[str, Any]:
    """全面数据质量诊断。"""

    # 1. 各表基础统计
    table_stats = _table_stats(db)

    # 2. 计算 Tushare 最新交易日
    tushare_latest = _get_tushare_latest_trade_date()

    # 3. 关键表滞后天数
    lag_days = {}
    if tushare_latest:
        for td in table_stats:
            info = table_stats[td]
            if info.get("max_date") and info.get("critical"):
                lag = _date_diff(tushare_latest, info["max_date"])
                lag_days[td] = lag

    # 4. 健康评分 (0-100)
    score = _compute_health_score(table_stats, lag_days, tushare_latest)

    # 5. 关键问题列表
    issues = _find_issues(table_stats, lag_days, tushare_latest)

    return {
        "score": score,
        "tushare_latest_trade_date": tushare_latest,
        "tables": table_stats,
        "lag_days": lag_days,
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


def _compute_health_score(
    table_stats: dict[str, Any],
    lag_days: dict[str, int],
    tushare_latest: str | None,
) -> int:
    """0-100 健康评分。"""
    if not tushare_latest:
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
    tushare_latest: str | None,
) -> list[dict[str, Any]]:
    """列出所有需要关注的问题。"""
    issues = []

    if not tushare_latest:
        issues.append({"severity": "high", "message": "无法获取 Tushare 最新交易日"})
        return issues

    for key, info in table_stats.items():
        if info.get("error"):
            issues.append({"severity": "high", "message": f"{info['label']} 诊断失败: {info['error']}"})
            continue
        if info.get("critical") and info.get("total_rows", 0) == 0:
            issues.append({"severity": "high", "message": f"关键表 {info['label']} ({info['table']}) 为空"})

    for key, lag in lag_days.items():
        if lag > 5:
            tbl_info = table_stats.get(key, {})
            issues.append({
                "severity": "medium" if lag < 20 else "high",
                "message": f"{tbl_info.get('label', key)} 滞后 {lag} 天 (最新: {tbl_info.get('max_date')}, Tushare: {tushare_latest})",
            })

    outc = table_stats.get("outcome", {})
    if outc.get("total_rows", 0) == 0:
        issues.append({"severity": "high", "message": "推荐结果未标注 (outcome 表为空), 回测/评估无法进行"})

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
