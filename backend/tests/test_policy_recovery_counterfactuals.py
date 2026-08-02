from __future__ import annotations

from scripts.run_policy_recovery_counterfactuals import recovery_policy_patches


def test_recovery_suite_has_five_single_rule_counterfactuals() -> None:
    patches = recovery_policy_patches()
    assert len(patches) == 5
    assert [rule_id for _label, rule_id, _value in patches] == [
        "structural_strong_hard_veto",
        "oscillation_hard_veto",
        "board_universe",
        "excluded_strategy_buckets",
        "excluded_strategy_buckets",
    ]
    assert len({label for label, _rule, _value in patches}) == 5
