from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "self_check.py"
spec = importlib.util.spec_from_file_location("self_check_zero_health", SCRIPT)
assert spec and spec.loader
self_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(self_check)


def _audit(*, input_count: int, pass_count: int, empty_reason: str | None = None) -> dict:
    return {
        "empty_reason": empty_reason,
        "stage_summary": [
            {"stage": "score_line", "input_count": input_count, "pass_count": pass_count}
        ],
    }


def test_zero_output_health_distinguishes_no_opportunity_policy_abstain_and_fault() -> None:
    assert self_check.classify_zero_output_health([_audit(input_count=0, pass_count=0)]) == "no_opportunity"
    assert self_check.classify_zero_output_health([
        _audit(
            input_count=4,
            pass_count=0,
            empty_reason="production_policy_blocked: structural_strong_hard_veto=4",
        )
    ]) == "policy_blocked"
    assert self_check.classify_zero_output_health([
        _audit(input_count=4, pass_count=0, empty_reason="below_effective_score_line")
    ]) == "reasonable_abstain"
    assert self_check.classify_zero_output_health([]) == "pipeline_fault"


def test_five_consecutive_policy_blocked_opportunity_days_are_inactive() -> None:
    classifications = ["policy_blocked"] * 5
    assert self_check.strategy_inactive_from_recent_zero_days(classifications) is True
    assert self_check.strategy_inactive_from_recent_zero_days(
        ["policy_blocked", "reasonable_abstain", "policy_blocked", "policy_blocked", "policy_blocked"]
    ) is False
