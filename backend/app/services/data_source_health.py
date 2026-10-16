"""数据源健康告警与系统就绪检查服务。

补充 data_sources.list_data_sources 的功能：
- active_alerts: 从数据源健康状态提取活跃告警 + 系统级告警
- readiness_check: 运行推荐流水线的全部前置条件验证
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
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
from app.services.evidence.validation import canonical_live_forward_run_ids


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

    try:
        from app.services.llm_review_cache_service import model_completion_capability

        model_alert = _model_completion_alert(model_completion_capability(db))
        if model_alert is not None:
            alerts.append(model_alert)
    except Exception:  # noqa: BLE001 - best-effort fallback, now debug-logged
        # 数据库局部初始化或迁移期间仍应返回其余告警，同时明确健康投影不可用。
        alerts.append({
            "source": "model_completion",
            "source_type": "llm",
            "status": "unknown",
            "alert_level": "warning",
            "message": "模型真实 completion 健康检查不可用",
        })

    # 系统级告警
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 最近一次可信 LIVE 推荐运行是否在 24 小时内；历史回放不刷新生产心跳。
    live_run_ids = canonical_live_forward_run_ids(db)
    latest_run = (
        db.scalar(
            select(RecommendationRun)
            .where(
                RecommendationRun.run_id.in_(live_run_ids),
                RecommendationRun.finished_at.is_not(None),
            )
            .order_by(RecommendationRun.finished_at.desc())
            .limit(1)
        )
        if live_run_ids
        else None
    )
    if latest_run:
        hours_since_run = round((now - latest_run.finished_at).total_seconds() / 3600, 1)
        reference_trade_date: str | None = None
        try:
            from app.services.data_health_service import _resolve_reference_trade_date

            reference_trade_date = str(
                _resolve_reference_trade_date(db).get("reference_trade_date") or ""
            ) or None
        except Exception:  # noqa: BLE001 - best-effort fallback, now debug-logged
            reference_trade_date = None
        if _recommendation_is_stale(
            reference_trade_date=reference_trade_date,
            latest_trade_date=str(latest_run.trade_date or "") or None,
            hours_since_run=hours_since_run,
        ):
            message = (
                f"最近可信推荐停在 {latest_run.trade_date}，当前交易日 {reference_trade_date}"
                if reference_trade_date
                else f"最近一次推荐运行已超过 {hours_since_run:.0f} 小时"
            )
            alerts.append({
                "source": "system",
                "source_type": "recommendation",
                "status": "stale",
                "alert_level": "warning",
                "message": message,
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

    from app.services.llm_review_cache_service import model_completion_capability

    checks["model_completion"] = model_completion_capability(db, now=now)

    # 整体判定
    overall, can_run = _readiness_outcome(checks)

    return {
        "checked_at": now.isoformat(),
        "reference_trade_date": reference_trade_date or None,
        "reference_trade_date_source": reference_info.get("source"),
        "overall_status": overall,
        "can_run_recommendation": can_run,
        "checks": checks,
    }


def _readiness_outcome(checks: dict[str, dict[str, Any]]) -> tuple[str, bool]:
    """Collapse detailed readiness checks into one conservative run decision."""
    statuses = [str(check.get("status") or "unknown") for check in checks.values()]
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
    return overall, can_run


def _recommendation_is_stale(
    *,
    reference_trade_date: str | None,
    latest_trade_date: str | None,
    hours_since_run: float,
) -> bool:
    """Prefer trading-date freshness; wall-clock hours are a fallback only."""
    if reference_trade_date and latest_trade_date:
        if latest_trade_date <= reference_trade_date:
            return latest_trade_date < reference_trade_date
        # A future/inconsistent run date must not suppress the legacy heartbeat
        # guard; it is not comparable to the current trading calendar.
    return hours_since_run > 24


def _model_completion_alert(health: dict[str, Any]) -> dict[str, Any] | None:
    status = str(health.get("status") or "unknown")
    if status == "ok":
        return None
    attempts = int(health.get("live_attempt_count") or 0)
    successes = int(health.get("live_success_count") or 0)
    counts = dict(health.get("status_counts") or {})
    reasons = ", ".join(
        f"{name}={count}"
        for name, count in sorted(counts.items(), key=lambda item: (-int(item[1]), item[0]))
        if name != "cache_hit"
    )
    message = f"模型真实 completion 健康异常：成功 {successes}/{attempts}"
    if reasons:
        message += f"（{reasons}）"
    return {
        "source": "model_completion",
        "source_type": "llm",
        "status": status,
        "alert_level": "critical" if status == "critical" else "warning",
        "message": message,
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
