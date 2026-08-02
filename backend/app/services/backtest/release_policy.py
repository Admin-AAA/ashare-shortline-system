"""Versioned release gates for canonical production-policy experiments."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.services.backtest.canonical_experiment import _performance_snapshot
from app.services.backtest.challenger_decision import evaluate_challenger


@dataclass(frozen=True)
class ReleasePolicyV2:
    policy_id: str = "release-policy"
    version: int = 2
    minimum_capture_rate_pct: float = 20.0
    champion_capture_retention_ratio: float = 0.5
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
    ) -> dict[str, Any]:
        champion_metrics = champion["split_metrics"]["out_of_sample"]
        challenger_metrics = challenger["split_metrics"]["out_of_sample"]
        financial = evaluate_challenger(
            _performance_snapshot(champion_metrics),
            _performance_snapshot(challenger_metrics),
        ).to_dict()
        champion_coverage = champion["split_coverage_evidence"]["out_of_sample"]
        challenger_coverage = challenger["split_coverage_evidence"]["out_of_sample"]
        required_capture = max(
            self.minimum_capture_rate_pct,
            float(champion_coverage["opportunity_capture_rate_pct"])
            * self.champion_capture_retention_ratio,
        )
        gates = {
            "oos_executed_trade_required": int(challenger_coverage["executed_trades"]) > 0,
            "opportunity_capture_retention": (
                float(challenger_coverage["opportunity_capture_rate_pct"])
                >= required_capture
            ),
            "max_consecutive_opportunity_abstain": (
                int(challenger_coverage["max_consecutive_opportunity_abstain_days"])
                <= self.maximum_consecutive_opportunity_abstain_days
            ),
            "single_rule_change": len(changed_rule_ids) == 1,
            "vectorbt_truth": (
                champion["cross_validation"].get("passed") is True
                and challenger["cross_validation"].get("passed") is True
            ),
        }
        failed = [name for name, passed in gates.items() if not passed]
        should_publish = bool(financial["should_publish"] and not failed)
        return {
            **financial,
            "decision": "PRODUCTION" if should_publish else "FALSIFIED",
            "should_publish": should_publish,
            "release_policy": self.identity,
            "liveness_passed": not any(
                name in failed
                for name in {
                    "oos_executed_trade_required",
                    "opportunity_capture_retention",
                    "max_consecutive_opportunity_abstain",
                }
            ),
            "required_opportunity_capture_rate_pct": required_capture,
            "gates": gates,
            "failed_gates": failed,
        }


DEFAULT_RELEASE_POLICY = ReleasePolicyV2()


__all__ = ["DEFAULT_RELEASE_POLICY", "ReleasePolicyV2"]
