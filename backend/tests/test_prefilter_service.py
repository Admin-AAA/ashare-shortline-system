from pathlib import Path

from app.engine.strategy_runner import evaluate_skill_prefilter
from app.engine.skill_loader import load_skills
from app.services.prefilter_service import apply_prefilters, common_tradeability_exclusion_reason


def _selected_skills():
    skills = load_skills(Path(__file__).resolve().parents[1] / "skills")
    return [{"skill_id": skill.skill_id, "name": skill.name, "skill": skill} for skill in skills]


def _tradeable_stock(**overrides):
    stock = {
        "code": "000001",
        "name": "测试股",
        "amount_yi": 8.0,
        "turnover_rate": 6.0,
        "pct": 3.0,
        "limit_up_days20": 1,
        "chg60": 18.0,
        "pos60": 72.0,
        "dist60": 8.0,
        "off_high_pct": -4.0,
        "upper_shadow_count20": 2,
        "max_dd20": -8.0,
        "breakout20": 1,
        "support_ma5": True,
        "support_ma10": True,
        "rsi_12": 61.0,
        "industry_capacity_like_count": 3,
        "_is_main": True,
        "free_mv_yi": 80.0,
        "announcement_negative_count": 0,
        "is_st": False,
    }
    stock.update(overrides)
    return stock


def test_common_tradeability_prefilter_rejects_low_amount():
    reason = common_tradeability_exclusion_reason(_tradeable_stock(amount_yi=0.8))
    assert reason == "low_amount"


def test_common_tradeability_prefilter_rejects_late_acceleration():
    reason = common_tradeability_exclusion_reason(_tradeable_stock(pos60=99.0, dist60=42.0))
    assert reason == "late_acceleration"


def test_skill_market_prefilters_accept_tradeable_mainline_stock():
    for selected in _selected_skills():
        passed, reason = evaluate_skill_prefilter(selected["skill"], _tradeable_stock(), stage="market")
        assert passed is True, (selected["skill_id"], reason)


def test_apply_prefilters_keeps_union_and_fills_if_needed():
    selected_skills = _selected_skills()
    stocks = [
        _tradeable_stock(code="000001"),
        _tradeable_stock(
            code="000002",
            _is_main=False,
            industry_capacity_like_count=0,
            breakout20=0,
            support_ma5=False,
            support_ma10=False,
            pct=1.0,
            off_high_pct=-12.0,
        ),
    ]

    filtered, audit = apply_prefilters(stocks, selected_skills, min_candidate_count=2)

    assert [stock["code"] for stock in filtered] == ["000001", "000002"]
    assert audit["union_pass_count"] == 1
    assert audit["fill_count"] == 1
    assert audit["fallback_used"] is False
