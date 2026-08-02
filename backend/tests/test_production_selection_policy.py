from __future__ import annotations

from app.engine.production_policy import (
    DEFAULT_PRODUCTION_SELECTION_POLICY,
    SelectionPolicyContext,
    evaluate_selection_policy,
)
from app.services.runs.filter_audit import build_filter_audit


def _card(code: str, score: float = 90.0, *, bucket: str = "chen-xiaoqun-skill") -> dict:
    return {
        "code": code,
        "bucket": bucket,
        "final_score": score,
        "evidence_quality_score": 92.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
    }


def test_default_policy_returns_versioned_result_and_explains_every_drop() -> None:
    assert DEFAULT_PRODUCTION_SELECTION_POLICY.fingerprint == (
        "c56de0298e24b2c8a70b5747f3b5c0865368e55bb95bc6a7b559cff96c599b39"
    )
    cards = [
        _card("300001"),
        _card("600001", 99.0),
        _card("300002", 98.0, bucket="daily-stock-analysis-skill"),
    ]

    result = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(
            score_line=72.0,
            output_count=6,
            market_snapshot={
                "sample_count": 100,
                "up_count": 60,
                "limit_up_count": 0,
                "limit_down_count": 0,
                "market_regime": "结构性强势",
            },
        ),
    )

    assert result.policy_fingerprint == DEFAULT_PRODUCTION_SELECTION_POLICY.fingerprint
    assert result.recommendations == ()
    verdicts = {item.code: item for item in result.verdicts}
    assert verdicts["600001"].drop_owner == "board_universe"
    assert verdicts["300002"].drop_owner == "excluded_strategy_bucket"
    assert verdicts["300001"].drop_owner == "structural_strong_hard_veto"
    assert all(item.steps for item in result.verdicts)


def test_one_rule_patch_changes_only_structural_veto_and_keeps_same_input_cards() -> None:
    champion_cards = [_card("300001")]
    challenger_cards = [_card("300001")]
    snapshot = {
        "sample_count": 100,
        "up_count": 60,
        "limit_up_count": 0,
        "limit_down_count": 0,
        "market_regime": "结构性强势",
    }
    context = SelectionPolicyContext(
        score_line=72.0,
        output_count=6,
        market_snapshot=snapshot,
    )

    champion = evaluate_selection_policy(champion_cards, context)
    challenger_policy = DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch(
        "structural_strong_hard_veto", False
    )
    challenger = evaluate_selection_policy(
        challenger_cards, context, challenger_policy
    )

    assert champion.recommendations == ()
    assert [card["code"] for card in challenger.recommendations] == ["300001"]
    assert champion.policy_fingerprint != challenger.policy_fingerprint


def test_filter_audit_names_policy_drop_owner_instead_of_missing_metadata() -> None:
    cards = [_card("600001", 99.0)]
    result = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(
            score_line=72.0,
            output_count=6,
            market_snapshot={"sample_count": 100, "market_regime": "弱势"},
        ),
    )

    audit = build_filter_audit(
        prefilter_audit={},
        skill_gate_audit={},
        scored_cards=cards,
        recommended_cards=list(result.recommendations),
        pipeline={},
    )

    score_row = audit["rows"][0]["score_line"]
    assert score_row["policy_drop_owner"] == "board_universe"
    assert score_row["reason"] == "production_policy: board_universe"
    assert audit["empty_reason"].startswith("production_policy_blocked:")


def test_regime_hard_veto_is_persisted_as_policy_drop_owner() -> None:
    cards = [_card("300001", 99.0)]
    result = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(
            score_line=72.0,
            output_count=6,
            market_snapshot={
                "sample_count": 100,
                "market_regime": "震荡",
            },
        ),
    )
    audit = build_filter_audit(
        prefilter_audit={},
        skill_gate_audit={},
        scored_cards=cards,
        recommended_cards=list(result.recommendations),
        pipeline={},
    )
    assert cards[0]["policy_drop_owner"] == "oscillation_hard_veto"
    assert audit["empty_reason"].startswith("production_policy_blocked:")
