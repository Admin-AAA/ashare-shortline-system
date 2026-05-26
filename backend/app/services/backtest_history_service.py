from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BacktestRun


def list_backtest_runs(
    db: Session,
    *,
    skill_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    stmt = select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)
    if skill_id:
        stmt = select(BacktestRun).where(BacktestRun.skill_id == skill_id).order_by(BacktestRun.created_at.desc()).limit(limit)
    return [_serialize_backtest_run(row, include_result=False) for row in db.scalars(stmt).all()]


def get_backtest_run(db: Session, backtest_id: str) -> dict[str, Any] | None:
    row = db.get(BacktestRun, backtest_id)
    if row is None:
        return None
    return _serialize_backtest_run(row, include_result=True)


def _serialize_backtest_run(row: BacktestRun, *, include_result: bool) -> dict[str, Any]:
    result = _loads(row.result_json, {})
    if not isinstance(result, dict):
        result = {}
    metrics = _dict(result.get("aggregate_metrics"))
    coverage = _dict(result.get("data_coverage"))
    comparison = _dict(result.get("comparison"))
    minute_diag = _dict(coverage.get("minute_source_diagnostics"))
    payload: dict[str, Any] = {
        "backtest_id": row.backtest_id,
        "skill_id": row.skill_id,
        "strategy_name": str(result.get("strategy_name") or row.skill_id),
        "date_from": row.date_from,
        "date_to": row.date_to,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "selected_variant": comparison.get("selected_variant"),
        "selected_variant_label": comparison.get("selected_variant_label"),
        "adoption_reason": comparison.get("adoption_reason"),
        "metrics": _compact_metrics(metrics),
        "data_coverage": {
            "signal_date_count": coverage.get("signal_date_count", 0),
            "candidate_count": coverage.get("candidate_count", 0),
            "candidate_minute_coverage_rate": coverage.get("candidate_minute_coverage_rate", 0),
            "benchmark_minute_coverage_rate": coverage.get("benchmark_minute_coverage_rate", 0),
            "tushare_stk_mins_status": minute_diag.get("tushare_stk_mins_status", "not_attempted"),
            "tushare_permission_denied": bool(minute_diag.get("tushare_permission_denied")),
            "fallback_used": bool(minute_diag.get("fallback_used")),
        },
    }
    if include_result:
        payload["result"] = result
    return payload


def _compact_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "trade_count",
        "recommendation_accuracy",
        "average_net_return",
        "median_net_return",
        "profit_loss_ratio",
        "max_drawdown",
        "positive_month_ratio",
        "benchmark_excess_return",
        "sample_confidence",
    ]
    return {key: metrics.get(key, 0 if key != "sample_confidence" else "low") for key in keys}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
