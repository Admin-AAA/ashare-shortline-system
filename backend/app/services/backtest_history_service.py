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


def get_backtest_version_matrix(
    db: Session,
    *,
    skill_id: str | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    stmt = select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)
    if skill_id:
        stmt = select(BacktestRun).where(BacktestRun.skill_id == skill_id).order_by(BacktestRun.created_at.desc()).limit(limit)
    rows = db.scalars(stmt).all()
    matrix: list[dict[str, Any]] = []
    for row in rows:
        result = _loads(row.result_json, {})
        if not isinstance(result, dict):
            continue
        comparison = _dict(result.get("comparison"))
        if not comparison:
            continue
        baseline_metrics = _dict((comparison.get("decision_metrics") or {}).get("baseline")) or _dict(comparison.get("baseline"))
        candidate_metrics = _dict((comparison.get("decision_metrics") or {}).get("candidate")) or _dict(comparison.get("candidate"))
        if not baseline_metrics and not candidate_metrics:
            continue
        matrix.append(_serialize_version_matrix_row(row, result, comparison, baseline_metrics, candidate_metrics))
    return matrix


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
    validation_profile = _dict(comparison.get("validation_profile"))
    minute_diag = _dict(coverage.get("minute_source_diagnostics"))

    # 时态一致性检查摘要
    temporal = _dict(result.get("temporal_consistency") or coverage.get("temporal_consistency"))
    
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
        "decision_scope": comparison.get("decision_scope"),
        "decision_scope_label": comparison.get("decision_scope_label"),
        "guardrail_notes": list(comparison.get("guardrail_notes") or []),
        "decision_metrics": {
            "baseline": _dict((comparison.get("decision_metrics") or {}).get("baseline")),
            "candidate": _dict((comparison.get("decision_metrics") or {}).get("candidate")),
        },
        "validation_profile": {
            "primary_scope": validation_profile.get("primary_scope"),
            "primary_scope_label": validation_profile.get("primary_scope_label"),
            "fallback_reason": validation_profile.get("fallback_reason"),
            "primary_samples": _dict(validation_profile.get("primary_samples")),
            "walk_forward": {
                "baseline": _dict(_dict(validation_profile.get("walk_forward")).get("baseline")),
                "candidate": _dict(_dict(validation_profile.get("walk_forward")).get("candidate")),
            },
            "time_split": {
                "baseline": _dict(_dict(validation_profile.get("time_split")).get("baseline")),
                "candidate": _dict(_dict(validation_profile.get("time_split")).get("candidate")),
            },
            "aggregate": {
                "baseline": _dict(_dict(validation_profile.get("aggregate")).get("baseline")),
                "candidate": _dict(_dict(validation_profile.get("aggregate")).get("candidate")),
            },
        },
        "metrics": _compact_metrics(metrics),
        "data_coverage": {
            "signal_date_count": coverage.get("signal_date_count", 0),
            "candidate_count": coverage.get("candidate_count", 0),
            "candidate_minute_coverage_rate": coverage.get("candidate_minute_coverage_rate", 0),
            "benchmark_minute_coverage_rate": coverage.get("benchmark_minute_coverage_rate", 0),
            "tushare_stk_mins_status": minute_diag.get("tushare_stk_mins_status", "not_attempted"),
            "tushare_permission_denied": bool(minute_diag.get("tushare_permission_denied")),
            "fallback_used": bool(minute_diag.get("fallback_used")),
            "temporal_consistency": {
                "passed": temporal.get("passed", True),
                "violation_count": temporal.get("violation_count", 0),
                "error_count": temporal.get("error_count", 0),
                "warning_count": temporal.get("warning_count", 0),
            },
        },
    }
    if include_result:
        payload["result"] = result
    return payload


def _compact_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    trade_count = _metric_int(metrics, "trade_count", "labeled_count")
    executed_trade_count = _metric_int(metrics, "executed_trade_count", "triggered_count")
    accuracy_denominator = _metric_int(metrics, "accuracy_denominator", "labeled_count")
    if accuracy_denominator <= 0:
        accuracy_denominator = trade_count
    unexecuted_count = _metric_int(metrics, "unexecuted_count", "missed_count")
    if unexecuted_count <= 0 and accuracy_denominator and executed_trade_count:
        unexecuted_count = max(0, accuracy_denominator - executed_trade_count)
    return {
        "trade_count": trade_count,
        "recommendation_accuracy": _metric_float(metrics, "recommendation_accuracy", "hit_rate"),
        "accuracy_numerator": _metric_int(metrics, "accuracy_numerator", "success_count"),
        "accuracy_denominator": accuracy_denominator,
        "trade_accuracy": _metric_float(metrics, "trade_accuracy", "hit_rate"),
        "executed_trade_count": executed_trade_count,
        "unexecuted_count": unexecuted_count,
        "average_net_return": _metric_float(metrics, "average_net_return", "avg_return"),
        "average_executed_return": _metric_float(metrics, "average_executed_return", "avg_executed_return"),
        "median_net_return": _float_value(metrics.get("median_net_return")),
        "profit_loss_ratio": _metric_float(metrics, "profit_loss_ratio", "profit_factor"),
        "max_drawdown": _float_value(metrics.get("max_drawdown")),
        "positive_month_ratio": _float_value(metrics.get("positive_month_ratio")),
        "benchmark_excess_return": _float_value(metrics.get("benchmark_excess_return")),
        "sample_confidence": str(metrics.get("sample_confidence") or "low"),
        "accuracy_policy": _dict(metrics.get("accuracy_policy")),
        "accuracy_gate_summary": _dict(metrics.get("accuracy_gate_summary")),
    }


def _serialize_version_matrix_row(
    row: BacktestRun,
    result: dict[str, Any],
    comparison: dict[str, Any],
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> dict[str, Any]:
    baseline_accuracy = _float_value(baseline_metrics.get("recommendation_accuracy"))
    candidate_accuracy = _float_value(candidate_metrics.get("recommendation_accuracy"))
    baseline_trade_accuracy = _metric_float(baseline_metrics, "trade_accuracy", "hit_rate")
    candidate_trade_accuracy = _metric_float(candidate_metrics, "trade_accuracy", "hit_rate")
    baseline_return = _float_value(baseline_metrics.get("average_net_return"))
    candidate_return = _float_value(candidate_metrics.get("average_net_return"))
    baseline_drawdown = _float_value(baseline_metrics.get("max_drawdown"))
    candidate_drawdown = _float_value(candidate_metrics.get("max_drawdown"))
    return {
        "backtest_id": row.backtest_id,
        "skill_id": row.skill_id,
        "strategy_name": str(result.get("strategy_name") or row.skill_id),
        "date_from": row.date_from,
        "date_to": row.date_to,
        "created_at": _iso(row.created_at),
        "selected_variant": comparison.get("selected_variant"),
        "selected_variant_label": comparison.get("selected_variant_label"),
        "decision_scope": comparison.get("decision_scope"),
        "decision_scope_label": comparison.get("decision_scope_label"),
        "baseline_label": comparison.get("baseline_label") or "基线版",
        "candidate_label": comparison.get("candidate_label") or "候选版",
        "baseline_accuracy": baseline_accuracy,
        "candidate_accuracy": candidate_accuracy,
        "accuracy_delta": round(candidate_accuracy - baseline_accuracy, 4),
        "baseline_accuracy_denominator": _metric_int(baseline_metrics, "accuracy_denominator", "trade_count"),
        "candidate_accuracy_denominator": _metric_int(candidate_metrics, "accuracy_denominator", "trade_count"),
        "baseline_trade_accuracy": baseline_trade_accuracy,
        "candidate_trade_accuracy": candidate_trade_accuracy,
        "trade_accuracy_delta": round(candidate_trade_accuracy - baseline_trade_accuracy, 4),
        "baseline_executed_trade_count": _metric_int(baseline_metrics, "executed_trade_count", "triggered_count"),
        "candidate_executed_trade_count": _metric_int(candidate_metrics, "executed_trade_count", "triggered_count"),
        "baseline_unexecuted_count": _metric_int(baseline_metrics, "unexecuted_count", "missed_count"),
        "candidate_unexecuted_count": _metric_int(candidate_metrics, "unexecuted_count", "missed_count"),
        "baseline_return": baseline_return,
        "candidate_return": candidate_return,
        "return_delta": round(candidate_return - baseline_return, 4),
        "baseline_drawdown": baseline_drawdown,
        "candidate_drawdown": candidate_drawdown,
        "drawdown_delta": round(candidate_drawdown - baseline_drawdown, 4),
        "baseline_confidence": str(baseline_metrics.get("sample_confidence") or "low"),
        "candidate_confidence": str(candidate_metrics.get("sample_confidence") or "low"),
        "validation_profile": {
            "primary_scope": _dict(comparison.get("validation_profile")).get("primary_scope"),
            "primary_scope_label": _dict(comparison.get("validation_profile")).get("primary_scope_label"),
            "fallback_reason": _dict(comparison.get("validation_profile")).get("fallback_reason"),
        },
        "guardrail_notes": list(comparison.get("guardrail_notes") or []),
        "adoption_reason": comparison.get("adoption_reason"),
    }


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


def _float_value(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _metric_float(metrics: dict[str, Any], key: str, fallback_key: str) -> float:
    value = metrics.get(key)
    if value is None:
        value = metrics.get(fallback_key)
    return _float_value(value)


def _metric_int(metrics: dict[str, Any], key: str, fallback_key: str) -> int:
    value = metrics.get(key)
    if value is None:
        value = metrics.get(fallback_key)
    try:
        return int(value or 0)
    except Exception:
        return 0
