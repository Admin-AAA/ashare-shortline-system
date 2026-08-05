"""数据源健康告警与系统就绪检查服务。

补充 data_sources.list_data_sources 的功能：
- active_alerts: 从数据源健康状态提取活跃告警 + 系统级告警
- readiness_check: 运行推荐流水线的全部前置条件验证
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.tables import (
    CapitalFlow,
    ConceptSector,
    DailyKbar,
    DragonTiger,
    RecommendationRun,
    SkillRegistry,
    StockQuote,
)
from app.services.evidence.selectors import (
    historical_reconstruction_provenance_conditions,
    live_forward_run_conditions,
)


def active_alerts(db: Session) -> list[dict[str, Any]]:
    """返回当前活跃的数据源 + 系统级告警列表。"""
    from app.services.data_sources import list_data_sources

    sources = list_data_sources(db)
    alerts: list[dict[str, Any]] = []

    for src in sources:
        status = src.get("status", "unknown")
        if status in ("degraded", "disabled"):
            alerts.append({
                "source": src["source"],
                "source_type": src["source_type"],
                "status": status,
                "alert_level": "warning" if status == "degraded" else "info",
                "message": _source_alert_message(src),
            })
        elif status == "critical":
            alerts.append({
                "source": src["source"],
                "source_type": src["source_type"],
                "status": status,
                "alert_level": "critical",
                "message": _source_alert_message(src),
            })

    # 系统级告警
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 最近一次推荐运行是否在 24 小时内
    latest_run = db.scalar(
        select(RecommendationRun)
        .where(
            RecommendationRun.run_mode == "formal",
            RecommendationRun.status == "completed",
            or_(
                and_(*live_forward_run_conditions()),
                and_(
                    *historical_reconstruction_provenance_conditions(),
                    RecommendationRun.data_as_of <= RecommendationRun.trade_date,
                ),
            ),
        )
        .order_by(RecommendationRun.finished_at.desc())
    )
    if latest_run and latest_run.finished_at:
        hours_since_run = round((now - latest_run.finished_at).total_seconds() / 3600, 1)
        if hours_since_run > 24:
            alerts.append({
                "source": "system",
                "source_type": "recommendation",
                "status": "stale",
                "alert_level": "warning",
                "message": f"最近一次推荐运行已超过 {hours_since_run:.0f} 小时",
            })
    else:
        alerts.append({
            "source": "system",
            "source_type": "recommendation",
            "status": "never_run",
            "alert_level": "warning",
            "message": "尚未执行过推荐运行",
        })

    return alerts


def readiness_check(db: Session) -> dict[str, Any]:
    """系统就绪检查：验证运行推荐流水线所需的全部前置条件。"""
    from app.core.config import get_settings

    settings = get_settings()
    from app.services.data_health_service import _resolve_reference_trade_date

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    reference_info = _resolve_reference_trade_date(db)
    reference_trade_date = str(reference_info.get("reference_trade_date") or "")

    checks = {}

    # 报价数据
    quote_count = db.scalar(select(func.count()).select_from(StockQuote)) or 0
    recent_quotes = db.scalar(
        select(func.count()).select_from(StockQuote).where(
            StockQuote.snapped_at >= now - timedelta(hours=24)
        )
    ) or 0
    checks["quote"] = {
        "status": "ok" if quote_count >= 500 else "degraded",
        "total": quote_count,
        "recent_24h": recent_quotes,
        "min_required": 500,
    }

    # K线数据
    bar_count = db.scalar(select(func.count()).select_from(DailyKbar)) or 0
    today_bars = db.scalar(
        select(func.count()).select_from(DailyKbar).where(
            DailyKbar.trade_date == reference_trade_date
        )
    ) or 0
    checks["daily_bar"] = {
        "status": "ok" if bar_count >= 1000 else "degraded",
        "total": bar_count,
        "today_count": today_bars,
        "min_required": 1000,
    }

    # 概念板块
    concept_count = db.scalar(
        select(func.count()).select_from(ConceptSector).where(
            ConceptSector.trade_date == reference_trade_date
        )
    ) or 0
    checks["concept_sector"] = {
        "status": "ok" if concept_count >= 100 else "degraded",
        "today_count": concept_count,
        "min_required": 100,
    }

    # 龙虎榜（最近 5 天）
    recent_trade_dates = list(db.scalars(
        select(DailyKbar.trade_date)
        .where(DailyKbar.trade_date <= reference_trade_date)
        .distinct()
        .order_by(DailyKbar.trade_date.desc())
        .limit(5)
    )) if reference_trade_date else []
    dt_cutoff = min(recent_trade_dates) if recent_trade_dates else reference_trade_date
    dt_count = db.scalar(
        select(func.count()).select_from(DragonTiger).where(
            DragonTiger.trade_date >= dt_cutoff
        )
    ) or 0
    checks["dragon_tiger"] = {
        "status": "ok" if dt_count >= 20 else "degraded",
        "recent_5d_count": dt_count,
        "min_required": 20,
    }

    # 资金流向（今日）
    flow_count = db.scalar(
        select(func.count()).select_from(CapitalFlow).where(
            CapitalFlow.trade_date == reference_trade_date
        )
    ) or 0
    checks["capital_flow"] = {
        "status": "ok" if flow_count >= 50 else "degraded",
        "today_count": flow_count,
        "min_required": 50,
    }

    # Skill 注册
    skill_count = db.scalar(
        select(func.count()).select_from(SkillRegistry).where(
            SkillRegistry.enabled == True
        )
    ) or 0
    checks["skills"] = {
        "status": "ok" if skill_count >= 1 else "critical",
        "enabled_count": skill_count,
        "min_required": 1,
    }

    # 整体判定
    statuses = [c["status"] for c in checks.values()]
    if "critical" in statuses:
        overall = "not_ready"
        can_run = False
    elif statuses.count("degraded") >= 2:
        overall = "degraded"
        can_run = True
    elif "degraded" in statuses:
        overall = "degraded"
        can_run = True
    else:
        overall = "ready"
        can_run = True

    return {
        "checked_at": now.isoformat(),
        "reference_trade_date": reference_trade_date or None,
        "reference_trade_date_source": reference_info.get("source"),
        "overall_status": overall,
        "can_run_recommendation": can_run,
        "checks": checks,
    }


def _source_alert_message(src: dict[str, Any]) -> str:
    """从数据源状态生成人类可读的告警消息。"""
    source = src["source"]
    source_type = src["source_type"]
    success_rate = src.get("success_rate")
    last_error = src.get("last_error")
    enabled = src.get("enabled")

    if not enabled:
        return f"{source} ({source_type}) 已禁用"
    if success_rate is not None and success_rate < 0.5:
        msg = f"{source} ({source_type}) 成功率仅 {(success_rate * 100):.0f}%"
        if last_error:
            msg += f"，最近错误: {last_error[:80]}"
        return msg
    if success_rate is not None and success_rate < 0.8:
        return f"{source} ({source_type}) 成功率 {(success_rate * 100):.0f}%，轻度降级"
    if last_error:
        return f"{source} ({source_type}): {last_error[:100]}"
    return f"{source} ({source_type}) 状态异常"
