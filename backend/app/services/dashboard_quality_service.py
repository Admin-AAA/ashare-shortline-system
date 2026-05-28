from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import Recommendation, RecommendationOutcome, ReviewNote
from app.services.backtest_history_service import list_backtest_runs
from app.services.cost_service import list_scheduled_task_runs


def build_dashboard_quality_scorecard(
    db: Session,
    *,
    data_health: dict[str, Any],
    task_health: dict[str, Any],
    current_run: dict[str, Any] | None,
) -> dict[str, Any]:
    latest_backtest = _pick_latest_backtest(list_backtest_runs(db, limit=10))
    recent_task_runs = list_scheduled_task_runs(db, limit=12)
    research_metrics = _research_metrics(db)
    return build_quality_scorecard(
        data_health=data_health,
        task_health=task_health,
        current_run=current_run,
        latest_backtest=latest_backtest,
        recent_task_runs=recent_task_runs,
        research_metrics=research_metrics,
    )


def build_quality_scorecard(
    *,
    data_health: dict[str, Any],
    task_health: dict[str, Any],
    current_run: dict[str, Any] | None,
    latest_backtest: dict[str, Any] | None,
    recent_task_runs: list[dict[str, Any]],
    research_metrics: dict[str, Any],
) -> dict[str, Any]:
    dimensions = {
        "data": _score_data_quality(data_health),
        "validation": _score_validation_quality(latest_backtest),
        "execution": _score_execution_quality(task_health, current_run, recent_task_runs),
        "research": _score_research_quality(research_metrics),
    }
    overall_score = round(
        dimensions["data"]["score"] * 0.3
        + dimensions["validation"]["score"] * 0.3
        + dimensions["execution"]["score"] * 0.2
        + dimensions["research"]["score"] * 0.2,
        1,
    )
    highlights: list[str] = []
    for key in ("data", "validation", "execution", "research"):
        highlights.extend(dimensions[key]["highlights"][:1])
    return {
        "overall_score": overall_score,
        "overall_status": _status_from_score(overall_score),
        "dimensions": dimensions,
        "highlights": [item for item in highlights if item],
    }


def _score_data_quality(data_health: dict[str, Any]) -> dict[str, Any]:
    score = 100.0
    highlights: list[str] = []
    bar_coverage = float(data_health.get("eligible_bar_coverage_pct") or data_health.get("bar_coverage_pct") or 0)
    missing_bar_count = int(data_health.get("missing_bar_count") or 0)
    stale_bar_count = int(data_health.get("stale_bar_count") or 0)
    short_history_count = int(data_health.get("short_history_count") or 0)
    can_run = bool(data_health.get("can_run_local_recommendation"))

    if not can_run:
        score -= 22
        highlights.append("本地推荐尚未就绪，数据池仍需补齐。")
    if bar_coverage < 95:
        score -= min(18, (95 - bar_coverage) * 0.6)
        highlights.append(f"合格日K覆盖仅 {bar_coverage:.1f}%。")
    if missing_bar_count > 0:
        score -= min(18, missing_bar_count * 3)
        highlights.append(f"仍有 {missing_bar_count} 只股票缺日K。")
    if stale_bar_count > 0:
        score -= min(12, stale_bar_count * 2)
    if short_history_count > 0:
        score -= min(10, short_history_count * 1.2)

    return _dimension_payload(
        label="数据质量",
        score=score,
        highlights=highlights or ["数据池覆盖处于可运行区间。"],
        metrics={
            "bar_coverage_pct": round(bar_coverage, 1),
            "missing_bar_count": missing_bar_count,
            "stale_bar_count": stale_bar_count,
            "short_history_count": short_history_count,
            "can_run_local_recommendation": can_run,
        },
    )


def _score_validation_quality(latest_backtest: dict[str, Any] | None) -> dict[str, Any]:
    if not latest_backtest:
        return _dimension_payload(
            label="验证质量",
            score=25,
            highlights=["暂无最近回测，验证结论缺少最新样本支持。"],
            metrics={"latest_backtest_status": "missing"},
        )

    score = 52.0
    highlights: list[str] = []
    status = str(latest_backtest.get("status") or "unknown")
    latest_attempt_status = str(latest_backtest.get("_latest_attempt_status") or status)
    metrics = latest_backtest.get("metrics") if isinstance(latest_backtest.get("metrics"), dict) else {}
    trade_count = int(metrics.get("trade_count") or 0)
    has_execution_metrics = any(
        key in metrics
        for key in ("accuracy_denominator", "executed_trade_count", "triggered_count", "unexecuted_count")
    )
    accuracy_denominator = int(metrics.get("accuracy_denominator") or (trade_count if has_execution_metrics else 0))
    executed_trade_count = int(metrics.get("executed_trade_count") or metrics.get("triggered_count") or 0)
    unexecuted_count = int(metrics.get("unexecuted_count") or 0)
    if has_execution_metrics and accuracy_denominator and not unexecuted_count and executed_trade_count:
        unexecuted_count = max(0, accuracy_denominator - executed_trade_count)
    confidence = str(metrics.get("sample_confidence") or "low")
    recommendation_accuracy = _optional_float(metrics, "recommendation_accuracy")
    trade_accuracy = _optional_float(metrics, "trade_accuracy")
    if trade_accuracy is None:
        trade_accuracy = _optional_float(metrics, "hit_rate")
    average_net_return = _optional_float(metrics, "average_net_return")
    max_drawdown = _optional_float(metrics, "max_drawdown")
    benchmark_excess_return = _optional_float(metrics, "benchmark_excess_return")
    accuracy_numerator = int(metrics.get("accuracy_numerator") or metrics.get("success_count") or 0)
    if accuracy_numerator <= 0 and recommendation_accuracy is not None and accuracy_denominator:
        accuracy_numerator = int(round(recommendation_accuracy * accuracy_denominator))
    accuracy_gate_summary = metrics.get("accuracy_gate_summary") if isinstance(metrics.get("accuracy_gate_summary"), dict) else {}
    if not accuracy_gate_summary:
        accuracy_gate_summary = _derive_accuracy_gate_summary(
            accuracy_denominator=accuracy_denominator,
            executed_trade_count=executed_trade_count,
            accurate_count=accuracy_numerator,
        )
    accuracy_policy = metrics.get("accuracy_policy") if isinstance(metrics.get("accuracy_policy"), dict) else {}
    failure_breakdown = (
        accuracy_policy.get("failure_breakdown")
        if isinstance(accuracy_policy.get("failure_breakdown"), list)
        else []
    )
    scope = str(latest_backtest.get("decision_scope") or "aggregate")
    scope_label = _validation_scope_label(latest_backtest.get("decision_scope_label"), scope)
    temporal = ((latest_backtest.get("data_coverage") or {}).get("temporal_consistency") or {}) if isinstance(latest_backtest.get("data_coverage"), dict) else {}
    candidate_coverage = float(((latest_backtest.get("data_coverage") or {}).get("candidate_minute_coverage_rate") or 0)) if isinstance(latest_backtest.get("data_coverage"), dict) else 0.0
    benchmark_coverage = float(((latest_backtest.get("data_coverage") or {}).get("benchmark_minute_coverage_rate") or 0)) if isinstance(latest_backtest.get("data_coverage"), dict) else 0.0

    if latest_attempt_status == "insufficient_minute_coverage" and status != "insufficient_minute_coverage":
        score -= 6
        highlights.append("最近一次回测被分钟线门禁拦截，以下验证指标回退至最近可用回测。")
    elif status == "completed":
        score += 8
    elif status == "insufficient_minute_coverage":
        score -= 20
        highlights.append("最近一次回测被分钟线门禁拦截。")
    else:
        score -= 8

    if scope == "walk_forward":
        score += 16
        highlights.append("最近验证已使用滚动样本外窗口。")
    elif scope == "time_split":
        score += 8
        highlights.append("最近验证使用时间切分测试集。")
    else:
        highlights.append("最近验证仍主要依赖全量指标。")

    if trade_count >= 30:
        score += 12
    elif trade_count >= 10:
        score += 3
        highlights.append(f"最近验证样本仅 {trade_count} 笔，仍偏薄。")
    else:
        score -= 10
        highlights.append(f"最近验证样本仅 {trade_count} 笔，置信度偏低。")

    if confidence == "normal":
        score += 10
    else:
        score -= 8

    if temporal.get("passed", True):
        score += 8
    else:
        score -= 20
        highlights.append("最近回测存在时态一致性告警。")

    if candidate_coverage and candidate_coverage < 0.8:
        score -= 6
    if benchmark_coverage and benchmark_coverage < 0.8:
        score -= 6

    if recommendation_accuracy is None:
        score -= 5
        highlights.append("最近回测缺少推荐准确率，策略有效性不可量化。")
    elif recommendation_accuracy < 0.5:
        score -= 14
        highlights.append(f"最近推荐准确率仅 {recommendation_accuracy:.1%}。")
    elif recommendation_accuracy < 0.55:
        score -= 6
        highlights.append(f"最近推荐准确率 {recommendation_accuracy:.1%}，仍需谨慎。")
    elif recommendation_accuracy >= 0.6:
        score += 8

    execution_rate = (
        executed_trade_count / max(1, accuracy_denominator)
        if has_execution_metrics and accuracy_denominator
        else None
    )
    if trade_accuracy is not None:
        if trade_accuracy < 0.5:
            score -= 12
            highlights.append(f"最近成交后胜率仅 {trade_accuracy:.1%}。")
        elif trade_accuracy >= 0.65:
            score += 4
    if execution_rate is not None:
        if execution_rate < 0.7:
            score -= 18
            highlights.append(f"最近可成交率仅 {execution_rate:.1%}，未成交/未触发 {unexecuted_count} 条。")
        elif execution_rate >= 0.9:
            score += 4
    if accuracy_gate_summary:
        signal_invalid_count = int(accuracy_gate_summary.get("signal_invalid_count") or 0)
        unexecutable_count = int(accuracy_gate_summary.get("unexecutable_count") or 0)
        net_non_positive_count = int(accuracy_gate_summary.get("net_non_positive_count") or 0)
        if signal_invalid_count:
            score -= min(10, signal_invalid_count * 2)
            highlights.append(f"严格准确率闸门发现 {signal_invalid_count} 条信号当时失效。")
        if unexecutable_count:
            score -= min(14, unexecutable_count * 3)
            highlights.append(f"严格准确率闸门发现 {unexecutable_count} 条不可真实成交。")
        if net_non_positive_count and recommendation_accuracy is not None and recommendation_accuracy < 0.6:
            score -= min(10, net_non_positive_count * 1.5)

    if average_net_return is None:
        score -= 5
        highlights.append("最近回测缺少扣费后净收益。")
    elif average_net_return <= 0:
        score -= 16
        highlights.append(f"最近平均净收益 {average_net_return:.2f}%，扣费后不占优。")
    elif average_net_return < 0.2:
        score -= 6
        highlights.append(f"最近平均净收益仅 {average_net_return:.2f}%。")
    elif average_net_return >= 0.5:
        score += 8

    if max_drawdown is not None:
        drawdown_abs = abs(max_drawdown)
        if drawdown_abs > 5:
            score -= 14
            highlights.append(f"最近最大回撤 {max_drawdown:.2f}%，风险代价偏高。")
        elif drawdown_abs > 3:
            score -= 8
            highlights.append(f"最近最大回撤 {max_drawdown:.2f}%，需要控制风险。")
        elif drawdown_abs <= 1:
            score += 4

    if benchmark_excess_return is not None:
        if benchmark_excess_return < 0:
            score -= 8
            highlights.append(f"最近超额收益 {benchmark_excess_return:.2f}%，未跑赢基准。")
        elif benchmark_excess_return > 0.5:
            score += 5

    return _dimension_payload(
        label="验证质量",
        score=score,
        highlights=highlights or ["最近回测验证链路稳定。"],
        metrics={
            "latest_backtest_status": status,
            "latest_attempt_status": latest_attempt_status,
            "decision_scope": scope,
            "decision_scope_label": scope_label,
            "trade_count": trade_count,
            "accuracy_denominator": accuracy_denominator,
            "accuracy_numerator": accuracy_numerator,
            "executed_trade_count": executed_trade_count,
            "unexecuted_count": unexecuted_count,
            "recommendation_accuracy": recommendation_accuracy,
            "trade_accuracy": trade_accuracy,
            "execution_rate": round(execution_rate, 3) if execution_rate is not None else None,
            "average_net_return": average_net_return,
            "max_drawdown": max_drawdown,
            "benchmark_excess_return": benchmark_excess_return,
            "sample_confidence": confidence,
            "accuracy_gate_summary": accuracy_gate_summary,
            "accuracy_failure_breakdown": failure_breakdown,
            "temporal_passed": bool(temporal.get("passed", True)),
            "candidate_minute_coverage_rate": round(candidate_coverage, 3),
            "benchmark_minute_coverage_rate": round(benchmark_coverage, 3),
        },
    )


def _score_execution_quality(
    task_health: dict[str, Any],
    current_run: dict[str, Any] | None,
    recent_task_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    score = 88.0
    highlights: list[str] = []
    failed_count = int(task_health.get("failed_count") or 0)
    running_count = int(task_health.get("running_count") or 0)
    if failed_count > 0:
        score -= min(36, failed_count * 18)
        highlights.append(f"当前仍有 {failed_count} 个启用任务最近执行失败。")
    if current_run and bool(current_run.get("is_stale")):
        score -= 20
        highlights.append("当前运行心跳陈旧，执行稳定性需要关注。")
    finished_runs = [row for row in recent_task_runs if str(row.get("status") or "") != "running"]
    if finished_runs:
        success_like = sum(1 for row in finished_runs if str(row.get("status") or "") in {"completed", "dry_run", "already_ready"})
        completion_rate = success_like / max(1, len(finished_runs))
        if completion_rate < 0.7:
            score -= 18
            highlights.append(f"最近任务完成率仅 {completion_rate:.0%}。")
        elif completion_rate >= 0.9:
            score += 4
    if running_count == 0 and failed_count == 0:
        highlights.append("最近调度执行稳定，没有新增失败任务。")
    return _dimension_payload(
        label="执行稳定性",
        score=score,
        highlights=highlights or ["执行链路整体稳定。"],
        metrics={
            "failed_task_count": failed_count,
            "running_task_count": running_count,
            "recent_task_run_count": len(recent_task_runs),
        },
    )


def _score_research_quality(research_metrics: dict[str, Any]) -> dict[str, Any]:
    total = int(research_metrics.get("total_recommendations") or 0)
    labeled = int(research_metrics.get("labeled_count") or 0)
    review_count = int(research_metrics.get("review_note_count") or 0)
    latest_trade_date = research_metrics.get("latest_trade_date")
    label_coverage = labeled / max(1, total) if total else 0.0
    review_coverage = review_count / max(1, labeled) if labeled else 0.0

    if total == 0:
        return _dimension_payload(
            label="研究闭环",
            score=25,
            highlights=["暂无推荐样本，研究闭环尚未形成。"],
            metrics={"total_recommendations": 0},
        )

    score = 24 + min(46, label_coverage * 46) + min(18, review_coverage * 18)
    highlights: list[str] = []
    if labeled < 30:
        score -= 10
        highlights.append(f"已标注样本仅 {labeled} 条，研究结论仍属低置信度。")
    else:
        score += 8
        highlights.append(f"已形成 {labeled} 条已标注样本。")
    if review_count == 0:
        score -= 8
        highlights.append("人工复盘笔记仍为空，误判归因沉淀不足。")
    elif review_coverage >= 0.3:
        highlights.append("人工复盘笔记已开始覆盖标注样本。")

    age_days = _age_in_days(latest_trade_date)
    if age_days is not None and age_days > 5:
        score -= min(12, (age_days - 5) * 1.5)
        highlights.append(f"最近推荐样本距今 {age_days} 天，研究反馈不够新。")

    return _dimension_payload(
        label="研究闭环",
        score=score,
        highlights=highlights or ["研究标注与复盘闭环稳定。"],
        metrics={
            "total_recommendations": total,
            "labeled_count": labeled,
            "review_note_count": review_count,
            "label_coverage": round(label_coverage, 3),
            "review_coverage": round(review_coverage, 3),
            "latest_trade_date": latest_trade_date,
        },
    )


def _dimension_payload(*, label: str, score: float, highlights: list[str], metrics: dict[str, Any]) -> dict[str, Any]:
    normalized = round(max(0.0, min(100.0, score)), 1)
    return {
        "label": label,
        "score": normalized,
        "status": _status_from_score(normalized),
        "highlights": highlights,
        "metrics": metrics,
    }


def _status_from_score(score: float) -> str:
    if score >= 80:
        return "healthy"
    if score >= 60:
        return "warning"
    return "risk"


def _pick_latest_backtest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    latest = rows[0]
    for row in rows:
        if _has_validation_sample(row):
            if row is latest:
                return row
            selected = dict(row)
            selected["_latest_attempt_status"] = latest.get("status")
            selected["_latest_attempt_backtest_id"] = latest.get("backtest_id")
            return selected
    return latest


def _has_validation_sample(row: dict[str, Any]) -> bool:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    trade_count = int(metrics.get("trade_count") or 0)
    accuracy_denominator = int(metrics.get("accuracy_denominator") or 0)
    return max(trade_count, accuracy_denominator) > 0


def _derive_accuracy_gate_summary(
    *,
    accuracy_denominator: int,
    executed_trade_count: int,
    accurate_count: int,
) -> dict[str, int]:
    if accuracy_denominator <= 0:
        return {}
    executable_count = max(0, min(executed_trade_count, accuracy_denominator))
    net_positive_count = max(0, min(accurate_count, executable_count))
    return {
        "total_recommendations": accuracy_denominator,
        "signal_valid_count": accuracy_denominator,
        "signal_invalid_count": 0,
        "executable_count": executable_count,
        "unexecutable_count": max(0, accuracy_denominator - executable_count),
        "net_positive_count": net_positive_count,
        "net_non_positive_count": max(0, executable_count - net_positive_count),
        "manual_override_count": 0,
    }


def _validation_scope_label(raw_label: Any, scope: str) -> str:
    if raw_label:
        return str(raw_label)
    mapping = {
        "walk_forward": "滚动样本外验证",
        "time_split": "时间切分测试集",
        "aggregate": "全量指标",
    }
    return mapping.get(scope, scope)


def _research_metrics(db: Session) -> dict[str, Any]:
    total = int(db.scalar(select(func.count()).select_from(Recommendation)) or 0)
    labeled = int(db.scalar(select(func.count()).select_from(RecommendationOutcome)) or 0)
    review_count = int(db.scalar(select(func.count()).select_from(ReviewNote)) or 0)
    latest_trade_date = db.scalar(select(func.max(Recommendation.trade_date)))
    return {
        "total_recommendations": total,
        "labeled_count": labeled,
        "review_note_count": review_count,
        "latest_trade_date": str(latest_trade_date) if latest_trade_date else None,
    }


def _age_in_days(iso_date: Any) -> int | None:
    if not iso_date:
        return None
    try:
        current = datetime.now(timezone.utc).date()
        target = date.fromisoformat(str(iso_date)[:10])
        return max(0, (current - target).days)
    except Exception:
        return None


def _optional_float(values: dict[str, Any], key: str) -> float | None:
    if key not in values or values.get(key) is None:
        return None
    try:
        return float(values.get(key))
    except Exception:
        return None
