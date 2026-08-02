from __future__ import annotations

import pytest

from app.services.backtest.candidate_snapshot_builder import (
    _pre_policy_card,
    _reject_write_statement,
)


@pytest.mark.parametrize(
    "statement",
    (
        "INSERT INTO live_recommendations VALUES (1)",
        " update skill_registry set enabled = 0",
        "DELETE FROM formal_recommendations",
        "CREATE TABLE accidental_write (id INTEGER)",
    ),
)
def test_rule_replay_rejects_sql_that_can_mutate_the_database(statement: str) -> None:
    with pytest.raises(RuntimeError, match="attempted a database write"):
        _reject_write_statement(None, None, statement, None, None, False)


def test_rule_replay_allows_read_only_sql() -> None:
    _reject_write_statement(None, None, "SELECT * FROM daily_kbars", None, None, False)
    _reject_write_statement(None, None, "WITH bars AS (SELECT 1) SELECT * FROM bars", None, None, False)


def test_pre_policy_card_removes_only_selection_policy_mutations() -> None:
    card = {
        "code": "300001",
        "final_score": 88.0,
        "recommend_price": 10.0,
        "score_line_meta": {"effective_score_line": 999},
        "policy_drop_owner": "regime_structural_strong_hard_veto",
        "policy_verdict_steps": [{"rule_id": "regime_structural_strong_hard_veto"}],
        "_regime_score_adjustment": -4,
        "_panic_day_penalty": -5,
    }

    compact = _pre_policy_card(card)

    assert {
        key: value for key, value in compact.items() if key != "scored_card_fingerprint"
    } == {
        "code": "300001",
        "final_score": 88.0,
        "recommend_price": 10.0,
    }
    assert len(compact["scored_card_fingerprint"]) == 64


def test_pre_policy_card_keeps_only_nested_inputs_used_by_selection_policy() -> None:
    compact = _pre_policy_card(
        {
            "code": "300001",
            "final_score": 88.0,
            "feature_snapshot": {"chg20": 18.0, "pos60": 75.0, "unused": [1] * 100},
            "raw_component_scores": {"evidence": 68.0, "unused": 99.0},
            "data_quality": {"missing_fields": ["news"], "unused": [1] * 100},
            "strategy_signals": [
                {"score": 80.0, "runtime": "rule", "large_unused_payload": [1] * 100}
            ],
        }
    )

    assert compact["chg20"] == 18.0
    assert compact["pos60"] == 75.0
    assert compact["raw_component_scores"] == {"evidence": 68.0}
    assert compact["data_quality"] == {"missing_fields": ["news"]}
    assert compact["strategy_signals"] == [{"score": 80.0, "runtime": "rule"}]
    assert "feature_snapshot" not in compact
