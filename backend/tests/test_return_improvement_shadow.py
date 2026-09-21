from app.engine.return_improvement_shadow import (
    annotate_return_improvement_shadow,
)


def test_shadow_preregisters_rank_setup_and_auction_dimensions_without_changing_cards():
    cards = [
        {
            "code": "600001",
            "final_score": 88.0,
            "setup_type": "一线游资净买接力",
            "market_discipline": {"market_regime": "弱势·活跃"},
            "feature_snapshot": {
                "auction_signal_available": True,
                "auction_gap_pct": 2.35,
            },
        },
        {
            "code": "300001",
            "final_score": 84.0,
            "setup_type": "趋势突破",
            "market_discipline": {"market_regime": "弱势·活跃"},
            "feature_snapshot": {
                "auction_signal_available": False,
                "auction_gap_pct": None,
            },
        },
    ]

    annotated = annotate_return_improvement_shadow(cards)

    assert annotated is cards
    assert [card["final_score"] for card in annotated] == [88.0, 84.0]
    first = annotated[0]["return_improvement_shadow"]
    second = annotated[1]["return_improvement_shadow"]
    assert first["mode"] == "shadow"
    assert first["production_effect_applied"] is False
    assert first["rank_hypothesis"]["rank"] == 1
    assert first["rank_hypothesis"]["target_position_cap_pct"] == 50.0
    assert first["setup_hypothesis"]["observe_only_candidate"] is True
    assert first["auction_dimension"] == {"available": True, "gap_pct": 2.35}
    assert first["weak_active_risk_budget"] == {
        "applies": True,
        "target_daily_output_limit": 2,
        "target_position_cap_pct": 20.0,
    }
    assert second["rank_hypothesis"]["target_position_cap_pct"] == 25.0
    assert second["auction_dimension"] == {"available": False, "gap_pct": None}
    assert second["weak_active_risk_budget"]["target_position_cap_pct"] == 15.0
    assert annotated[0]["feature_snapshot"]["return_improvement_shadow"] == first


def test_shadow_uses_zero_target_after_rank_three_but_never_changes_live_position():
    cards = [
        {
            "code": f"60000{index}",
            "final_score": 90.0 - index,
            "market_discipline": {"max_position_pct": 35.0},
            "feature_snapshot": {},
        }
        for index in range(1, 5)
    ]

    annotate_return_improvement_shadow(cards)

    assert cards[3]["return_improvement_shadow"]["rank_hypothesis"]["target_position_cap_pct"] == 0.0
    assert cards[3]["market_discipline"]["max_position_pct"] == 35.0
    assert cards[3]["return_improvement_shadow"]["release_gate"] == {
        "min_executed_trades": 100,
        "min_signal_days": 20,
        "requires_frozen_oos": True,
        "requires_forward_canary": True,
    }
