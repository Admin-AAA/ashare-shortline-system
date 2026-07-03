from __future__ import annotations

from typing import Any

from app.services.evaluation_metrics import (
    compute_full_metrics,
    _empty_metrics as _backtest_empty_metrics,
)

DECISION_MIN_COMPARABLE_TRADES = 5
DECISION_LOW_CONFIDENCE_TRADES = 30
DECISION_STRONG_ACC_EDGE = 0.05
DECISION_NORMAL_ACC_EDGE = 0.03
DECISION_STRONG_RETURN_EDGE = 0.5
DECISION_NORMAL_RETURN_BUFFER = -0.2
DECISION_LOW_CONFIDENCE_RETURN_BUFFER = -0.05
DECISION_NORMAL_DRAWDOWN_TOLERANCE = 1.0
DECISION_LOW_CONFIDENCE_DRAWDOWN_TOLERANCE = 0.5
DECISION_DRAWDOWN_BLOCK = 2.5


def _build_time_split_analysis(
    trades: list[dict[str, Any]],
    all_dates: list[str],
    train_ratio: float = 0.6,
) -> dict[str, Any]:
    if len(trades) < 3 or len(all_dates) < 5:
        return {
            "eligible": False,
            "message": f"交易笔数或交易日数不足 (trades={len(trades)}, dates={len(all_dates)})",
            "train_metrics": _backtest_empty_metrics(),
            "test_metrics": _backtest_empty_metrics(),
        }

    split_index = max(1, int(len(all_dates) * train_ratio))
    if split_index >= len(all_dates):
        split_index = max(1, len(all_dates) - 1)

    train_date_set = set(all_dates[:split_index])
    test_date_set = set(all_dates[split_index:])

    train_trades = [t for t in trades if t["trade_date"] in train_date_set]
    test_trades = [t for t in trades if t["trade_date"] in test_date_set]

    train_metrics = compute_full_metrics(train_trades) if train_trades else _backtest_empty_metrics()
    test_metrics = compute_full_metrics(test_trades) if test_trades else _backtest_empty_metrics()

    return {
        "eligible": True,
        "train_ratio": train_ratio,
        "train_date_range": {
            "start": all_dates[0],
            "end": all_dates[split_index - 1],
        },
        "test_date_range": {
            "start": all_dates[split_index],
            "end": all_dates[-1],
        },
        "train_trade_count": len(train_trades),
        "test_trade_count": len(test_trades),
        "train_metrics": _validation_metric_view(train_metrics),
        "test_metrics": _validation_metric_view(test_metrics),
        "train_vs_test": {
            "accuracy_diff": round(
                train_metrics.get("recommendation_accuracy", 0)
                - test_metrics.get("recommendation_accuracy", 0),
                3,
            ),
            "return_diff": round(
                train_metrics.get("average_net_return", 0)
                - test_metrics.get("average_net_return", 0),
                3,
            ),
        },
    }


def _build_backtest_walk_forward(
    signal_dates: list[str],
    trades: list[dict[str, Any]],
    min_train_dates: int = 2,
) -> dict[str, Any]:
    if len(trades) < min_train_dates:
        return {
            "eligible": False,
            "message": f"交易笔数不足 ({len(trades)} < {min_train_dates})",
            "window_count": 0,
            "test_aggregate": {},
        }

    date_order = sorted(set(t["trade_date"] for t in trades))
    if len(date_order) < min_train_dates + 1:
        return {
            "eligible": False,
            "message": f"交易日期数不足 ({len(date_order)} < {min_train_dates + 1})",
            "window_count": 0,
            "test_aggregate": {},
        }

    windows = []
    for i in range(min_train_dates, len(date_order)):
        train_end = date_order[i - 1]
        test_start = date_order[i]
        test_end = date_order[-1]

        train_trades = [t for t in trades if t["trade_date"] <= train_end]
        test_trades = [t for t in trades if t["trade_date"] >= test_start]

        if len(test_trades) < 1:
            continue

        train_metrics = compute_full_metrics(train_trades) if train_trades else {}
        test_metrics = compute_full_metrics(test_trades) if test_trades else {}

        windows.append(
            {
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "train_count": len(train_trades),
                "test_count": len(test_trades),
                "train_accuracy": train_metrics.get("recommendation_accuracy", 0),
                "test_accuracy": test_metrics.get("recommendation_accuracy", 0),
                "train_avg_return": train_metrics.get("average_net_return", 0),
                "test_avg_return": test_metrics.get("average_net_return", 0),
                "train_drawdown": train_metrics.get("max_drawdown", 0),
                "test_drawdown": test_metrics.get("max_drawdown", 0),
            }
        )

    if not windows:
        return {
            "eligible": False,
            "message": "无法构建任何验证窗口",
            "window_count": 0,
            "test_aggregate": {},
        }

    all_test_trades = [t for t in trades if t["trade_date"] >= date_order[min_train_dates - 1]]
    test_aggregate = compute_full_metrics(all_test_trades) if all_test_trades else {}

    return {
        "eligible": True,
        "min_train_dates": min_train_dates,
        "date_count": len(date_order),
        "window_count": len(windows),
        "windows": windows,
        "test_aggregate": _validation_metric_view(test_aggregate),
        "notes": [f"滚动验证按最少 {min_train_dates} 个训练信号日构建 expanding windows。"],
    }


def _validation_metric_view(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_count": int(metrics.get("trade_count", 0) or 0),
        "recommendation_accuracy": float(metrics.get("recommendation_accuracy", 0) or 0),
        "accuracy_numerator": int(metrics.get("accuracy_numerator", 0) or 0),
        "accuracy_denominator": int(metrics.get("accuracy_denominator", metrics.get("trade_count", 0)) or 0),
        "trade_accuracy": float(metrics.get("trade_accuracy", metrics.get("hit_rate", 0)) or 0),
        "executed_trade_count": int(metrics.get("executed_trade_count", metrics.get("triggered_count", 0)) or 0),
        "unexecuted_count": int(metrics.get("unexecuted_count", metrics.get("missed_count", 0)) or 0),
        "trigger_rate": float(metrics.get("trigger_rate", 0) or 0),
        "average_net_return": float(metrics.get("average_net_return", 0) or 0),
        "average_executed_return": float(
            metrics.get("average_executed_return", metrics.get("avg_executed_return", 0)) or 0
        ),
        "max_drawdown": float(metrics.get("max_drawdown", 0) or 0),
        "profit_loss_ratio": float(metrics.get("profit_loss_ratio", 0) or 0),
        "sample_confidence": str(metrics.get("sample_confidence", "low") or "low"),
        "accuracy_basis": str(metrics.get("accuracy_basis", "labeled_recommendations") or "labeled_recommendations"),
    }


def _choose_backtest_variant(
    baseline_result: dict[str, Any],
    candidate_result: dict[str, Any],
    walk_baseline: dict[str, Any],
    walk_candidate: dict[str, Any],
    time_split_baseline: dict[str, Any],
    time_split_candidate: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    baseline_ok = baseline_result.get("status") == "completed"
    candidate_ok = candidate_result.get("status") == "completed"
    if not baseline_ok and not candidate_ok:
        return "baseline", "两个版本均未完成，默认选 baseline", _decision_context(
            scope="aggregate",
            scope_label="全量指标",
            baseline_metrics={},
            candidate_metrics={},
            guardrail_notes=["两个版本均未完成，无法形成有效对比。"],
        )
    if not candidate_ok:
        return "baseline", "候选版未完成，选基线版", _decision_context(
            scope="aggregate",
            scope_label="全量指标",
            baseline_metrics=_decision_metric_view(baseline_result.get("aggregate_metrics") or {}),
            candidate_metrics={},
            guardrail_notes=["候选版未完成，仅保留基线结果。"],
        )
    if not baseline_ok:
        return "candidate", "基线版未完成，选候选版", _decision_context(
            scope="aggregate",
            scope_label="全量指标",
            baseline_metrics={},
            candidate_metrics=_decision_metric_view(candidate_result.get("aggregate_metrics") or {}),
            guardrail_notes=["基线版未完成，仅保留候选结果。"],
        )

    baseline_metrics = baseline_result.get("aggregate_metrics") or {}
    candidate_metrics = candidate_result.get("aggregate_metrics") or {}
    scope = _pick_decision_scope(
        walk_baseline,
        walk_candidate,
        time_split_baseline,
        time_split_candidate,
        baseline_metrics,
        candidate_metrics,
    )
    base_view = scope["baseline_metrics"]
    cand_view = scope["candidate_metrics"]
    guardrail_notes = list(scope["guardrail_notes"])

    low_confidence = _is_low_confidence(base_view) or _is_low_confidence(cand_view)
    if low_confidence:
        guardrail_notes.append("当前对比样本低于 30 笔，按低置信度标准处理。")

    drawdown_worse_by = _drawdown_worse_by(base_view, cand_view)
    acc_edge = cand_view["recommendation_accuracy"] - base_view["recommendation_accuracy"]
    return_edge = cand_view["average_net_return"] - base_view["average_net_return"]
    trade_edge = cand_view["trade_count"] - base_view["trade_count"]

    if drawdown_worse_by >= DECISION_DRAWDOWN_BLOCK and (
        acc_edge < DECISION_STRONG_ACC_EDGE or return_edge < DECISION_STRONG_RETURN_EDGE
    ):
        reason = (
            f"{scope['scope_label']}: 候选版回撤较基线恶化 {drawdown_worse_by:.2f}pct，"
            f"准确率/收益优势不足以覆盖风险，保留基线版。"
        )
        guardrail_notes.append("命中回撤护栏，禁止仅凭轻微优势切换到候选版。")
        return "baseline", reason, _decision_context(
            scope=scope["scope"],
            scope_label=scope["scope_label"],
            baseline_metrics=base_view,
            candidate_metrics=cand_view,
            guardrail_notes=guardrail_notes,
        )

    acc_threshold = DECISION_STRONG_ACC_EDGE if low_confidence else DECISION_NORMAL_ACC_EDGE
    return_buffer = (
        DECISION_LOW_CONFIDENCE_RETURN_BUFFER
        if low_confidence
        else DECISION_NORMAL_RETURN_BUFFER
    )
    drawdown_tolerance = (
        DECISION_LOW_CONFIDENCE_DRAWDOWN_TOLERANCE
        if low_confidence
        else DECISION_NORMAL_DRAWDOWN_TOLERANCE
    )

    if (
        acc_edge >= acc_threshold
        and return_edge >= return_buffer
        and drawdown_worse_by <= drawdown_tolerance
    ):
        reason = (
            f"{scope['scope_label']}: 候选版准确率 {cand_view['recommendation_accuracy']:.1%} "
            f"> 基线版 {base_view['recommendation_accuracy']:.1%}，"
            f"且收益/回撤未明显恶化。"
        )
        return "candidate", reason, _decision_context(
            scope=scope["scope"],
            scope_label=scope["scope_label"],
            baseline_metrics=base_view,
            candidate_metrics=cand_view,
            guardrail_notes=guardrail_notes,
        )

    if (
        return_edge >= DECISION_STRONG_RETURN_EDGE
        and acc_edge >= -0.01
        and drawdown_worse_by <= DECISION_LOW_CONFIDENCE_DRAWDOWN_TOLERANCE
    ):
        reason = (
            f"{scope['scope_label']}: 候选版平均净收益 {cand_view['average_net_return']:.2f}% "
            f"> 基线版 {base_view['average_net_return']:.2f}%，"
            f"准确率未明显变差。"
        )
        return "candidate", reason, _decision_context(
            scope=scope["scope"],
            scope_label=scope["scope_label"],
            baseline_metrics=base_view,
            candidate_metrics=cand_view,
            guardrail_notes=guardrail_notes,
        )

    if base_view["recommendation_accuracy"] >= cand_view["recommendation_accuracy"] + 0.02:
        reason = (
            f"{scope['scope_label']}: 基线版准确率 {base_view['recommendation_accuracy']:.1%} "
            f"> 候选版 {cand_view['recommendation_accuracy']:.1%}，保留基线版。"
        )
        return "baseline", reason, _decision_context(
            scope=scope["scope"],
            scope_label=scope["scope_label"],
            baseline_metrics=base_view,
            candidate_metrics=cand_view,
            guardrail_notes=guardrail_notes,
        )

    if (
        scope["scope"] == "aggregate"
        and trade_edge > 0
        and cand_view["trade_count"] >= max(1, int(base_view["trade_count"] * 1.5))
        and acc_edge >= -0.02
        and drawdown_worse_by <= DECISION_LOW_CONFIDENCE_DRAWDOWN_TOLERANCE
    ):
        reason = (
            f"全量指标: 候选版样本 {cand_view['trade_count']} > 基线版 {base_view['trade_count']}，"
            "准确率基本持平，扩大覆盖后仍可接受。"
        )
        guardrail_notes.append("由于样本外可比交易不足，本次仅按全量指标做保守放宽。")
        return "candidate", reason, _decision_context(
            scope=scope["scope"],
            scope_label=scope["scope_label"],
            baseline_metrics=base_view,
            candidate_metrics=cand_view,
            guardrail_notes=guardrail_notes,
        )

    reason = f"{scope['scope_label']}: 候选版优势不足或风险代价偏大，保留基线版。"
    return "baseline", reason, _decision_context(
        scope=scope["scope"],
        scope_label=scope["scope_label"],
        baseline_metrics=base_view,
        candidate_metrics=cand_view,
        guardrail_notes=guardrail_notes,
    )


def _pick_decision_scope(
    walk_baseline: dict[str, Any],
    walk_candidate: dict[str, Any],
    time_split_baseline: dict[str, Any],
    time_split_candidate: dict[str, Any],
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> dict[str, Any]:
    wf_base = _decision_metric_view(walk_baseline.get("test_aggregate") or {})
    wf_cand = _decision_metric_view(walk_candidate.get("test_aggregate") or {})
    if (
        walk_baseline.get("eligible")
        and walk_candidate.get("eligible")
        and wf_base["trade_count"] >= DECISION_MIN_COMPARABLE_TRADES
        and wf_cand["trade_count"] >= DECISION_MIN_COMPARABLE_TRADES
    ):
        return {
            "scope": "walk_forward",
            "scope_label": "滚动样本外验证",
            "baseline_metrics": wf_base,
            "candidate_metrics": wf_cand,
            "guardrail_notes": [],
        }

    ts_base = _decision_metric_view((time_split_baseline or {}).get("test_metrics") or {})
    ts_cand = _decision_metric_view((time_split_candidate or {}).get("test_metrics") or {})
    if (
        time_split_baseline.get("eligible")
        and time_split_candidate.get("eligible")
        and ts_base["trade_count"] >= DECISION_MIN_COMPARABLE_TRADES
        and ts_cand["trade_count"] >= DECISION_MIN_COMPARABLE_TRADES
    ):
        return {
            "scope": "time_split",
            "scope_label": "时间切分测试集",
            "baseline_metrics": ts_base,
            "candidate_metrics": ts_cand,
            "guardrail_notes": ["滚动样本外可比交易不足，已回退到 time-split 测试集。"],
        }

    return {
        "scope": "aggregate",
        "scope_label": "全量指标",
        "baseline_metrics": _decision_metric_view(baseline_metrics),
        "candidate_metrics": _decision_metric_view(candidate_metrics),
        "guardrail_notes": ["样本外与时间切分测试样本不足，最终按全量指标保守判断。"],
    }


def _decision_metric_view(metrics: dict[str, Any]) -> dict[str, Any]:
    return _validation_metric_view(metrics)


def _is_low_confidence(metrics: dict[str, Any]) -> bool:
    return (
        int(metrics.get("trade_count", 0) or 0) < DECISION_LOW_CONFIDENCE_TRADES
        or str(metrics.get("sample_confidence", "low") or "low") == "low"
    )


def _drawdown_worse_by(
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> float:
    base_drawdown = abs(float(baseline_metrics.get("max_drawdown", 0) or 0))
    cand_drawdown = abs(float(candidate_metrics.get("max_drawdown", 0) or 0))
    return round(max(0.0, cand_drawdown - base_drawdown), 3)


def _decision_context(
    *,
    scope: str,
    scope_label: str,
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    guardrail_notes: list[str],
) -> dict[str, Any]:
    return {
        "scope": scope,
        "scope_label": scope_label,
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
        "guardrail_notes": guardrail_notes,
    }


def _build_backtest_validation_profile(
    *,
    decision_context: dict[str, Any],
    walk_baseline: dict[str, Any],
    walk_candidate: dict[str, Any],
    time_split_baseline: dict[str, Any],
    time_split_candidate: dict[str, Any],
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "primary_scope": decision_context["scope"],
        "primary_scope_label": decision_context["scope_label"],
        "fallback_reason": _validation_fallback_reason(decision_context["scope"]),
        "primary_samples": {
            "baseline_trade_count": int(decision_context["baseline_metrics"].get("trade_count", 0) or 0),
            "candidate_trade_count": int(decision_context["candidate_metrics"].get("trade_count", 0) or 0),
            "baseline_confidence": str(decision_context["baseline_metrics"].get("sample_confidence", "low") or "low"),
            "candidate_confidence": str(decision_context["candidate_metrics"].get("sample_confidence", "low") or "low"),
        },
        "walk_forward": {
            "baseline": _compact_walk_forward_validation(walk_baseline),
            "candidate": _compact_walk_forward_validation(walk_candidate),
        },
        "time_split": {
            "baseline": _compact_time_split_validation(time_split_baseline),
            "candidate": _compact_time_split_validation(time_split_candidate),
        },
        "aggregate": {
            "baseline": _decision_metric_view(baseline_metrics),
            "candidate": _decision_metric_view(candidate_metrics),
        },
    }


def _compact_walk_forward_validation(result: dict[str, Any]) -> dict[str, Any]:
    aggregate = _validation_metric_view((result or {}).get("test_aggregate") or {})
    notes = list((result or {}).get("notes") or [])
    message = (result or {}).get("message")
    if message:
        notes.append(str(message))
    return {
        "eligible": bool((result or {}).get("eligible")),
        "window_count": int((result or {}).get("window_count", 0) or 0),
        "date_count": int((result or {}).get("date_count", 0) or 0),
        **aggregate,
        "notes": notes,
    }


def _compact_time_split_validation(result: dict[str, Any]) -> dict[str, Any]:
    result = result or {}
    train_metrics = _decision_metric_view(result.get("train_metrics") or {})
    test_metrics = _decision_metric_view(result.get("test_metrics") or {})
    notes: list[str] = []
    message = result.get("message")
    if message:
        notes.append(str(message))
    return {
        "eligible": bool(result.get("eligible")),
        "train_trade_count": int(result.get("train_trade_count", 0) or 0),
        "test_trade_count": int(result.get("test_trade_count", 0) or 0),
        "train_date_range": result.get("train_date_range") or {},
        "test_date_range": result.get("test_date_range") or {},
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
        "notes": notes,
    }


def _validation_fallback_reason(scope: str) -> str | None:
    if scope == "time_split":
        return "滚动样本外可比交易不足，已回退到 time-split 测试集。"
    if scope == "aggregate":
        return "样本外与时间切分测试样本不足，最终按全量指标保守判断。"
    return None
