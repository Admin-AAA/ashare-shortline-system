"""P0 Runner-based backtest comparison: baseline vs candidate score_line.

Replaces ``backtest_service.run_backtest_comparison`` which called the legacy
``run_backtest`` and wrote ``btrec_*`` recommendations.  This version uses
``ResearchBacktestRunner`` for both arms and only writes to isolated
``backtest_*`` tables.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.engine.backtest import (
    BacktestMetrics,
    BacktestRequest,
    DateRange,
    ExecutionPolicy,
    SingleDate,
    Trade,
    TradeOutcome,
)
from app.services.backtest.research_repository import ResearchBacktestRepository
from app.services.backtest.research_runner import ResearchBacktestRunner

logger = logging.getLogger(__name__)


def _p0_trade_to_metric_record(trade: Trade) -> dict[str, Any]:
    """Convert a P0 Trade DTO into the dict format expected by compute_full_metrics."""
    executed = trade.outcome_label in (TradeOutcome.SUCCESS, TradeOutcome.FAILED)
    return {
        "recommendation_id": trade.trade_id or "",
        "trade_date": trade.signal_date,
        "code": trade.code,
        "name": trade.code,
        "net_return_pct": trade.net_return_pct,
        "gross_return_pct": trade.gross_return_pct,
        "achievable_return_pct": 0.0,
        "morning_drawdown_pct": 0.0,
        "holding_days": trade.holding_days,
        "benchmark_return_pct": 0.0,
        "accurate": trade.outcome_label == TradeOutcome.SUCCESS,
        "signal_valid_at_recommend_time": True,
        "executed": executed,
        "net_return_positive": trade.net_return_pct > 0,
        "false_positive_reason": trade.exit_reason if trade.outcome_label == TradeOutcome.FAILED else None,
        "resolved": True,
    }


def _run_p0_backtest(
    db: Session,
    strategy_id: str,
    start_date: str,
    end_date: str,
    score_line: float,
    horizon_days: int,
) -> dict[str, Any]:
    """Run a single P0 research backtest and return the Runner result dict."""
    request = BacktestRequest(
        strategy_id=strategy_id,
        date_selector=SingleDate(start_date) if start_date == end_date else DateRange(start_date, end_date),
        execution_policy=ExecutionPolicy(horizon_days=horizon_days),
        parameter_set={"minimum_signal_score": score_line},
    )
    repo = ResearchBacktestRepository(db)
    runner = ResearchBacktestRunner(repo)
    return runner.run(request)


def _trading_dates_from_p0_result(result: dict[str, Any]) -> list[str]:
    """Extract sorted signal_dates from a P0 result."""
    dates = result.get("signal_dates") or []
    return sorted(dates)


def run_p0_comparison(
    db: Session,
    start_date: str,
    end_date: str,
    horizon_days: int = 5,
    baseline_score_line: float = 72.0,
    candidate_score_line: float = 65.0,
    strategy_id: str = "daily-stock-analysis-skill",
) -> dict[str, Any]:
    """Execute baseline vs candidate comparison via the P0 Research Gateway.

    Both arms run through ``ResearchBacktestRunner`` and write only to
    ``backtest_*`` tables — no ``btrec_*`` or ``Recommendation`` evidence.
    """
    from app.services.backtest_validation import (
        _build_backtest_validation_profile,
        _build_backtest_walk_forward,
        _build_time_split_analysis,
        _choose_backtest_variant,
    )
    from app.services.evaluation_metrics import compute_full_metrics

    started_at = time.perf_counter()

    baseline_label = f"基线版 (score_line={baseline_score_line})"
    candidate_label = f"候选版 (score_line={candidate_score_line})"
    logger.info("P0 回测比较开始: baseline=%.1f candidate=%.1f", baseline_score_line, candidate_score_line)

    # ── 1. 跑两个版本 ──
    baseline_result = _run_p0_backtest(db, strategy_id, start_date, end_date, baseline_score_line, horizon_days)
    candidate_result = _run_p0_backtest(db, strategy_id, start_date, end_date, candidate_score_line, horizon_days)

    # ── 2. 提取 trade 数据 ──
    repo = ResearchBacktestRepository(db)
    baseline_trades = repo.list_trades(baseline_result.get("backtest_id", ""))
    candidate_trades = repo.list_trades(candidate_result.get("backtest_id", ""))

    baseline_records = [_p0_trade_to_metric_record(t) for t in baseline_trades]
    candidate_records = [_p0_trade_to_metric_record(t) for t in candidate_trades]

    # ── 3. 构建 walk_forward 分析 ──
    all_dates = _trading_dates_from_p0_result(baseline_result)
    if not all_dates:
        all_dates = _trading_dates_from_p0_result(candidate_result)
    walk_baseline = _build_backtest_walk_forward(all_dates, baseline_records)
    walk_candidate = _build_backtest_walk_forward(all_dates, candidate_records)

    # ── 3.5 严格时间切分验证 ──
    time_split_baseline = _build_time_split_analysis(baseline_records, all_dates, train_ratio=0.6)
    time_split_candidate = _build_time_split_analysis(candidate_records, all_dates, train_ratio=0.6)

    # ── 4. 决策：选择更优版本 ──
    baseline_metrics_raw = baseline_result.get("metrics") or {}
    candidate_metrics_raw = candidate_result.get("metrics") or {}
    baseline_agg = compute_full_metrics(baseline_records) if baseline_records else {}
    candidate_agg = compute_full_metrics(candidate_records) if candidate_records else {}

    # Build minimal result dicts compatible with _choose_backtest_variant
    baseline_compat = {"status": baseline_result.get("status", "unknown"), "aggregate_metrics": baseline_agg}
    candidate_compat = {"status": candidate_result.get("status", "unknown"), "aggregate_metrics": candidate_agg}

    selected_variant, adoption_reason, decision_context = _choose_backtest_variant(
        baseline_compat, candidate_compat,
        walk_baseline, walk_candidate,
        time_split_baseline, time_split_candidate,
    )

    selected_result = baseline_result if selected_variant == "baseline" else candidate_result
    selected_trades = baseline_trades if selected_variant == "baseline" else candidate_trades

    summary = {
        "status": "completed",
        "strategy_id": strategy_id,
        "strategy_name": f"P0 回测比较 ({strategy_id})",
        "strategy_window": {"start_date": start_date, "end_date": end_date},
        "dates_scanned": len(all_dates),
        "dates_succeeded": len(all_dates),
        "dates_failed": 0,
        "total_recommendations": len(selected_trades),
        "total_elapsed_ms": int((time.perf_counter() - started_at) * 1000),
        "labeled_count": len([t for t in selected_trades if t.outcome_label != TradeOutcome.NOT_EXECUTED]),
        "baseline": {
            "label": baseline_label,
            "score_line": baseline_score_line,
            "status": baseline_result.get("status"),
            "backtest_id": baseline_result.get("backtest_id"),
            "total_recommendations": len(baseline_trades),
            "dates_succeeded": len(baseline_result.get("signal_dates") or []),
            "aggregate_metrics": baseline_agg,
            "walk_forward": walk_baseline,
            "time_split": time_split_baseline,
        },
        "candidate": {
            "label": candidate_label,
            "score_line": candidate_score_line,
            "status": candidate_result.get("status"),
            "backtest_id": candidate_result.get("backtest_id"),
            "total_recommendations": len(candidate_trades),
            "dates_succeeded": len(candidate_result.get("signal_dates") or []),
            "aggregate_metrics": candidate_agg,
            "walk_forward": walk_candidate,
            "time_split": time_split_candidate,
        },
        "comparison": {
            "baseline_label": baseline_label,
            "candidate_label": candidate_label,
            "selected_variant": selected_variant,
            "selected_variant_label": candidate_label if selected_variant == "candidate" else baseline_label,
            "adoption_reason": adoption_reason,
            "decision_scope": decision_context["scope"],
            "decision_scope_label": decision_context["scope_label"],
            "guardrail_notes": decision_context["guardrail_notes"],
            "decision_metrics": {
                "baseline": decision_context["baseline_metrics"],
                "candidate": decision_context["candidate_metrics"],
            },
            "walk_forward": {
                "baseline": walk_baseline,
                "candidate": walk_candidate,
            },
            "time_split": {
                "baseline": time_split_baseline,
                "candidate": time_split_candidate,
            },
            "validation_profile": _build_backtest_validation_profile(
                decision_context=decision_context,
                walk_baseline=walk_baseline,
                walk_candidate=walk_candidate,
                time_split_baseline=time_split_baseline,
                time_split_candidate=time_split_candidate,
                baseline_metrics=baseline_agg,
                candidate_metrics=candidate_agg,
            ),
            "hypothesis": "降低评分线可以增加候选数量，但可能降低命中率。通过 walk-forward 验证评估 trade-off。",
        },
        "result_source": "p0_research_gateway",
        "legacy_evidence_mutation": False,
    }

    logger.info(
        "P0 回测比较完成: selected=%s baseline_recs=%d candidate_recs=%d elapsed=%dms",
        selected_variant,
        len(baseline_trades),
        len(candidate_trades),
        summary["total_elapsed_ms"],
    )
    return summary
