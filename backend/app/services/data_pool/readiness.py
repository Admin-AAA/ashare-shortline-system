from __future__ import annotations

from typing import Any


def _pct(value: Any) -> float:
    try:
        return round(float(value or 0) * 100, 2)
    except Exception:
        return 0.0


def summarize_pool_status(status: dict[str, Any]) -> dict[str, Any]:
    readiness = status.get("recommendation_readiness") or {}
    kline = readiness.get("kline") if isinstance(readiness.get("kline"), dict) else {}
    return {
        "status": str(readiness.get("status") or status.get("status") or "unknown"),
        "can_run_local_recommendation": bool(readiness.get("can_run_local_recommendation")),
        "quote_total": int(status.get("unified_quote_count") or status.get("stock_quote_count") or 0),
        "active_quote_count": int(status.get("active_unified_quote_count") or 0),
        "eligible_quote_count": int(status.get("eligible_quote_count") or 0),
        "bar_coverage_pct": _pct(status.get("daily_bar_coverage_rate")),
        "eligible_bar_coverage_pct": _pct(status.get("eligible_daily_bar_coverage_rate")),
        "messages": [str(item) for item in readiness.get("messages") or []],
        "examples": list(kline.get("examples") or []),
    }
