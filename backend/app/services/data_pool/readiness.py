from __future__ import annotations

from typing import Any


def _pct(value: Any) -> float:
    try:
        numeric = float(value or 0)
        if numeric <= 1:
            numeric *= 100
        return round(numeric, 2)
    except Exception:
        return 0.0


def summarize_pool_status(status: dict[str, Any]) -> dict[str, Any]:
    summary = status.get("summary") if isinstance(status.get("summary"), dict) else status
    readiness = status.get("recommendation_readiness") or {}
    kline = readiness.get("kline") if isinstance(readiness.get("kline"), dict) else {}
    immature_history_count = int(
        summary.get("daily_bar_immature_history_count")
        if summary.get("daily_bar_immature_history_count") is not None
        else kline.get("immature_history_count") or 0
    )
    raw_short_history_count = int(
        summary.get("daily_bar_total_short_history_count")
        if summary.get("daily_bar_total_short_history_count") is not None
        else summary.get("daily_bar_short_history_count") or 0
    )
    remediable_short_history_count = int(
        summary.get("daily_bar_short_history_count")
        if summary.get("daily_bar_short_history_count") is not None
        else max(0, raw_short_history_count - immature_history_count)
    )
    return {
        "status": str(readiness.get("status") or status.get("status") or "unknown"),
        "can_run_local_recommendation": bool(readiness.get("can_run_local_recommendation")),
        "quote_total": int(summary.get("unified_quote_count") or summary.get("stock_quote_count") or 0),
        "active_quote_count": int(summary.get("active_unified_quote_count") or 0),
        "eligible_quote_count": int(summary.get("eligible_quote_count") or 0),
        "checked_candidate_count": int(kline.get("checked_count") or 0),
        "ready_candidate_count": int(kline.get("ready_count") or 0),
        "example_count": int(kline.get("example_count") or len(kline.get("examples") or [])),
        "example_limit": int(kline.get("example_limit") or 8),
        "scope_note": str(
            status.get("overview_scope_note")
            or "概览只展示缺口样例；刷新本地数据池会全量检查候选池并补齐可处理日K缺口。"
        ),
        "bar_coverage_pct": _pct(summary.get("daily_bar_coverage_rate")),
        "eligible_bar_coverage_pct": _pct(summary.get("eligible_daily_bar_coverage_rate")),
        "missing_bar_count": int(summary.get("daily_bar_missing_count") or 0),
        "stale_bar_count": int(summary.get("daily_bar_stale_count") or 0),
        "short_history_count": remediable_short_history_count,
        "immature_history_count": immature_history_count,
        "total_short_history_count": raw_short_history_count,
        "messages": [str(item) for item in readiness.get("messages") or []],
        "examples": list(kline.get("examples") or []),
    }
