from __future__ import annotations

from app.engine.production_policy import (
    DEFAULT_PRODUCTION_SELECTION_POLICY,
    ProductionSelectionPolicy,
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


def test_default_policy_covers_main_board_and_20cm_markets() -> None:
    assert DEFAULT_PRODUCTION_SELECTION_POLICY.board_universe is None
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
    verdicts = {item.code: item for item in result.verdicts}
    assert verdicts["600001"].drop_owner is None
    assert verdicts["300002"].drop_owner is None
    # 2026-08-02 用户决策：默认不保留结构性强势 hard_veto，高分候选按线通过
    assert verdicts["300001"].drop_owner is None
    assert [card["code"] for card in result.recommendations] == [
        "600001",
        "300002",
        "300001",
    ]
    assert all(item.steps for item in result.verdicts)


def test_policy_constructor_defaults_to_all_a_share_boards() -> None:
    assert ProductionSelectionPolicy().board_universe is None


def test_daily_top10_policy_counts_recommendations_from_earlier_same_day_runs() -> None:
    cards = [_card(f"300{index:03d}", 90.0) for index in range(12, 0, -1)]

    result = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(
            score_line=72.0,
            output_count=20,
            market_snapshot={
                "sample_count": 100,
                "up_count": 50,
                "limit_up_count": 0,
                "limit_down_count": 0,
            },
            same_day_recommendation_count=3,
        ),
    )

    assert [card["code"] for card in result.recommendations] == [
        "300001",
        "300002",
        "300003",
        "300004",
        "300005",
        "300006",
        "300007",
    ]
    assert sum(item.drop_owner == "daily_top_n" for item in result.verdicts) == 5


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

    # 2026-08-02 用户决策：默认关闭 hard_veto → 默认 policy 产出；
    # patch 开启 veto 后全拦（验证单规则 patch 只影响 veto 开关）。
    champion = evaluate_selection_policy(champion_cards, context)
    challenger_policy = DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch(
        "structural_strong_hard_veto", True
    )
    challenger = evaluate_selection_policy(
        challenger_cards, context, challenger_policy
    )

    assert [card["code"] for card in champion.recommendations] == ["300001"]
    assert challenger.recommendations == ()
    assert champion.policy_fingerprint != challenger.policy_fingerprint


def test_filter_audit_names_policy_drop_owner_instead_of_missing_metadata() -> None:
    cards = [_card("600001", 99.0)]
    board_limited_policy = DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch(
        "board_universe", "20cm"
    )
    result = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(
            score_line=72.0,
            output_count=6,
            market_snapshot={"sample_count": 100, "market_regime": "弱势"},
        ),
        board_limited_policy,
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
    # 2026-08-02 用户决策：默认关闭震荡 hard_veto；归因代码仍须可用，
    # 用 with_rule_patch 开启后验证 drop_owner 归因链路。
    policy = DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch(
        "oscillation_hard_veto", True
    )
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
        policy,
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


def test_regime_sentiment_line_overrides_precision() -> None:
    """L-060 精细方案回归：强牛·高潮/强牛·活跃/震荡·活跃 放行72，其余组合保持抬线。

    防止 regime_sentiment_line_overrides 被误改导致负期望组合(偏多·高潮)被放行。
    """
    from app.engine.aggregator import resolve_score_line_context

    def _snap(up: int, lu: int, ld: int) -> dict:
        return {
            "sample_count": 5000, "up_count": up, "down_count": 5000 - up,
            "limit_up_count": lu, "limit_down_count": ld,
        }

    policy = DEFAULT_PRODUCTION_SELECTION_POLICY
    # 强牛·高潮: up 76% + 涨停95 → 原91, override→72
    c = resolve_score_line_context(72.0, market_snapshot=_snap(3800, 95, 3), selection_policy=policy)
    assert (c["market_base"], c["market_sentiment"]) == ("强牛", "高潮")
    assert c["effective_score_line"] == 72.0 and c["regime_sentiment_override_applied"] is True
    # 偏多·高潮: up 52% + 涨停95 → 不在放行表, 保持抬线85
    c2 = resolve_score_line_context(72.0, market_snapshot=_snap(2600, 95, 3), selection_policy=policy)
    assert (c2["market_base"], c2["market_sentiment"]) == ("偏多", "高潮")
    assert c2["effective_score_line"] == 85.0 and c2["regime_sentiment_override_applied"] is False
    # 弱势·高潮: 仍受 weak_regime_score_cap=60 约束(组合override未覆盖, 不打扰弱势)
    c3 = resolve_score_line_context(72.0, market_snapshot=_snap(1500, 95, 3), selection_policy=policy)
    assert c3["market_base"] == "弱势" and c3["effective_score_line"] == 60.0
