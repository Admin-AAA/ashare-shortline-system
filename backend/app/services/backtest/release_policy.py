"""Versioned release gates for canonical production-policy experiments."""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Mapping

from app.services.backtest.canonical_experiment import _performance_snapshot
from app.services.backtest.challenger_decision import evaluate_challenger


REQUIRED_RELEASE_GATES = frozenset(
    {
        "oos_executed_trade_required",
        "oos_productive_day_required",
        "productive_day_retention",
        "opportunity_capture_retention",
        "max_consecutive_opportunity_abstain",
        "single_rule_change",
        "changed_rule_no_dropped_positive_trade",
        "counterfactual_outcomes_complete",
        "vectorbt_truth",
    }
)


@dataclass(frozen=True)
class ReleasePolicyV2:
    policy_id: str = "release-policy"
    version: int = 2
    minimum_capture_rate_pct: float = 20.0
    champion_capture_retention_ratio: float = 0.5
    champion_productive_day_retention_ratio: float = 0.5
    maximum_consecutive_opportunity_abstain_days: int = 5

    @property
    def identity(self) -> str:
        return f"{self.policy_id}:v{self.version}"

    def evaluate(
        self,
        *,
        champion: Mapping[str, Any],
        challenger: Mapping[str, Any],
        changed_rule_ids: tuple[str, ...],
        opportunity_cost: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        champion_metrics = champion["split_metrics"]["out_of_sample"]
        challenger_metrics = challenger["split_metrics"]["out_of_sample"]
        financial = evaluate_challenger(
            _performance_snapshot(champion_metrics),
            _performance_snapshot(challenger_metrics),
        ).to_dict()
        champion_coverage = champion["split_coverage_evidence"]["out_of_sample"]
        challenger_coverage = challenger["split_coverage_evidence"]["out_of_sample"]
        opportunity_cost = dict(opportunity_cost or {})
        required_capture = max(
            self.minimum_capture_rate_pct,
            float(champion_coverage["opportunity_capture_rate_pct"])
            * self.champion_capture_retention_ratio,
        )
        required_productive_days = max(
            1,
            ceil(
                int(champion_coverage.get("productive_days") or 0)
                * self.champion_productive_day_retention_ratio
            ),
        )
        gates = {
            "oos_executed_trade_required": int(challenger_coverage["executed_trades"]) > 0,
            "oos_productive_day_required": int(challenger_coverage.get("productive_days") or 0) > 0,
            "productive_day_retention": int(challenger_coverage.get("productive_days") or 0) >= required_productive_days,
            "opportunity_capture_retention": (
                float(challenger_coverage["opportunity_capture_rate_pct"])
                >= required_capture
            ),
            "max_consecutive_opportunity_abstain": (
                int(challenger_coverage["max_consecutive_opportunity_abstain_days"])
                <= self.maximum_consecutive_opportunity_abstain_days
            ),
            "single_rule_change": len(changed_rule_ids) == 1,
            "changed_rule_no_dropped_positive_trade": int(
                opportunity_cost.get("newly_rejected_positive_trades") or 0
            ) == 0,
            "counterfactual_outcomes_complete": int(
                opportunity_cost.get("missing_counterfactual_outcomes") or 0
            ) == 0,
            "vectorbt_truth": (
                champion["cross_validation"].get("passed") is True
                and challenger["cross_validation"].get("passed") is True
            ),
        }
        failed = [name for name, passed in gates.items() if not passed]
        should_publish = bool(financial["should_publish"] and not failed)
        return {
            **financial,
            "decision": "PRODUCTION_CANDIDATE" if should_publish else "FALSIFIED",
            "should_publish": should_publish,
            "release_policy": self.identity,
            "liveness_passed": not any(
                name in failed
                for name in {
                    "oos_executed_trade_required",
                    "oos_productive_day_required",
                    "productive_day_retention",
                    "opportunity_capture_retention",
                    "max_consecutive_opportunity_abstain",
                }
            ),
            "required_opportunity_capture_rate_pct": required_capture,
            "required_productive_days": required_productive_days,
            "opportunity_cost_evidence": opportunity_cost,
            "gates": gates,
            "failed_gates": failed,
        }


DEFAULT_RELEASE_POLICY = ReleasePolicyV2()


__all__ = ["DEFAULT_RELEASE_POLICY", "REQUIRED_RELEASE_GATES", "ReleasePolicyV2"]
