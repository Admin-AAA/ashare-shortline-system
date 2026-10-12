"""Compatibility projection for the canonical backtest experiment module."""
from __future__ import annotations

import time
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy.orm import Session

from app.engine.backtest import BacktestRequest, DateRange, SingleDate
from app.engine.execution_policy import (
    DEFAULT_EXECUTION_POLICY,
    execution_policy_from_config_projection,
)
from app.services.backtest.canonical_experiment import (
    CanonicalBacktestExperiment,
    CanonicalExperimentSpec,
)
from app.services.backtest.research_repository import ResearchBacktestRepository
from app.services.backtest.research_runner import ResearchBacktestRunner
from app.services.registry.default_strategy import DEFAULT_SINGLE_STRATEGY_ID


def run_p0_comparison(
    db: Session,
    start_date: str,
    end_date: str,
    horizon_days: int = 5,
    baseline_score_line: float = 72.0,
    candidate_score_line: float = 65.0,
    strategy_id: str = DEFAULT_SINGLE_STRATEGY_ID,
) -> dict[str, Any]:
    """Run the legacy score-line route through the one formal experiment interface."""

    started_at = time.perf_counter()
    selector = SingleDate(start_date) if start_date == end_date else DateRange(start_date, end_date)
    policy_config = dict(DEFAULT_EXECUTION_POLICY.config_projection())
    policy_config["horizon_days"] = horizon_days
    execution_policy = execution_policy_from_config_projection(policy_config)
    repository = ResearchBacktestRepository(db)
    artifact = CanonicalBacktestExperiment(
        repository,
        ResearchBacktestRunner(repository),
    ).run(
        CanonicalExperimentSpec(
            experiment_id=f"btexp_{uuid4().hex}",
            hypothesis="降低评分线可以增加候选数量，但必须在同一不可变输入上改善正式净收益。",
            champion=BacktestRequest(
                strategy_id=strategy_id,
                date_selector=selector,
                execution_policy=execution_policy,
                parameter_set={"minimum_signal_score": baseline_score_line},
            ),
            challenger=BacktestRequest(
                strategy_id=strategy_id,
                date_selector=selector,
                execution_policy=execution_policy,
                parameter_set={"minimum_signal_score": candidate_score_line},
            ),
        )
    )
    return _legacy_projection(
        artifact,
        strategy_id=strategy_id,
        start_date=start_date,
        end_date=end_date,
        baseline_score_line=baseline_score_line,
        candidate_score_line=candidate_score_line,
        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
    )


def _legacy_projection(
    artifact: Mapping[str, Any],
    *,
    strategy_id: str,
    start_date: str,
    end_date: str,
    baseline_score_line: float,
    candidate_score_line: float,
    elapsed_ms: int,
) -> dict[str, Any]:
    champion = artifact["arms"]["champion"]
    challenger = artifact["arms"]["challenger"]
    decision = artifact["decision"]
    selected_variant = "candidate" if decision.get("should_publish") is True else "baseline"
    selected = challenger if selected_variant == "candidate" else champion
    baseline_label = f"基线版 (score_line={baseline_score_line})"
    candidate_label = f"候选版 (score_line={candidate_score_line})"
    split = artifact.get("population_split", artifact["split"])
    dates_scanned = sum(len(dates) for dates in split.values())
    baseline_walk = _walk_forward_projection(champion)
    candidate_walk = _walk_forward_projection(challenger)
    baseline_split = _time_split_projection(champion)
    candidate_split = _time_split_projection(challenger)
    validation_profile = {
        "primary_scope": "out_of_sample",
        "truth_validation": artifact["truth_validation"],
        "aggregate": {
            "baseline": champion["metrics"],
            "candidate": challenger["metrics"],
        },
        "walk_forward": {
            "baseline": baseline_walk,
            "candidate": candidate_walk,
        },
        "time_split": {
            "baseline": baseline_split,
            "candidate": candidate_split,
        },
    }
    return {
        "status": artifact["status"],
        "strategy_id": strategy_id,
        "strategy_name": f"Canonical 回测比较 ({strategy_id})",
        "strategy_window": {"start_date": start_date, "end_date": end_date},
        "dates_scanned": dates_scanned,
        "dates_succeeded": dates_scanned,
        "dates_failed": 0 if artifact["status"] == "completed" else dates_scanned,
        "total_recommendations": int(selected["metrics"]["sample_count"]),
        "total_elapsed_ms": elapsed_ms,
        "labeled_count": int(selected["metrics"]["executed_trade_count"]),
        "baseline": {
            "label": baseline_label,
            "score_line": baseline_score_line,
            "status": "completed",
            "backtest_id": champion["backtest_id"],
            "total_recommendations": int(champion["metrics"]["sample_count"]),
            "dates_succeeded": dates_scanned,
            "aggregate_metrics": champion["metrics"],
            "walk_forward": baseline_walk,
            "time_split": baseline_split,
        },
        "candidate": {
            "label": candidate_label,
            "score_line": candidate_score_line,
            "status": "completed",
            "backtest_id": challenger["backtest_id"],
            "total_recommendations": int(challenger["metrics"]["sample_count"]),
            "dates_succeeded": dates_scanned,
            "aggregate_metrics": challenger["metrics"],
            "walk_forward": candidate_walk,
            "time_split": candidate_split,
        },
        "comparison": {
            "baseline_label": baseline_label,
            "candidate_label": candidate_label,
            "selected_variant": selected_variant,
            "selected_variant_label": candidate_label if selected_variant == "candidate" else baseline_label,
            "adoption_reason": (
                "Challenger 已通过冻结 OOS，等待未来 canary。"
                if selected_variant == "candidate"
                else "冻结 OOS 或未来 canary 尚未授权 Challenger。"
            ),
            "decision": decision,
            "decision_scope": "out_of_sample",
            "decision_scope_label": "Purged 样本外窗口",
            "guardrail_notes": [
                "Champion 与 Challenger 共用同一不可变 snapshot。",
                "发布判定只读取 purge/embargo 后的 OOS；未来 canary 前保持 Champion。",
            ],
            "decision_metrics": {
                "baseline": champion["split_metrics"]["out_of_sample"],
                "candidate": challenger["split_metrics"]["out_of_sample"],
            },
            "walk_forward": {
                "baseline": baseline_walk,
                "candidate": candidate_walk,
            },
            "time_split": {
                "baseline": baseline_split,
                "candidate": candidate_split,
            },
            "validation_profile": validation_profile,
            "hypothesis": artifact["hypothesis"],
            "evidence_fingerprint": artifact["evidence_fingerprint"],
        },
        "evidence_artifact": dict(artifact),
        "result_source": "canonical_backtest_experiment",
        "legacy_evidence_mutation": False,
    }


def _walk_forward_projection(arm: Mapping[str, Any]) -> dict[str, Any]:
    oos = arm["split_metrics"]["out_of_sample"]
    return {
        "eligible": True,
        "window_count": 1,
        "trade_count": int(oos["executed_trade_count"]),
        "test_aggregate": oos,
        "notes": ["Canonical chronological split; diagnostic only."],
    }


def _time_split_projection(arm: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "eligible": True,
        "train": arm["split_metrics"]["train"],
        "validation": arm["split_metrics"]["validation"],
        "out_of_sample": arm["split_metrics"]["out_of_sample"],
    }


__all__ = ["run_p0_comparison"]
