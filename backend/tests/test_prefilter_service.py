from pathlib import Path

from app.engine.strategy_runner import evaluate_skill_prefilter, explain_skill_prefilter
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
        "pct": 1.5,  # 回调入场样本（避开 cold_chase_pullback 毒区 chg20<15 & pct>=3）
        "limit_up_days20": 1,
        "chg20": 12.0,
        "pos20": 72.0,
        "chg60": 18.0,
        "pos60": 72.0,
        "pos120": 72.0,
        "off_high60_pct": -2.0,
        "vol5_20": 1.2,
        "dist60": 8.0,
        "dist20": 4.0,
        "off_high_pct": -4.0,
        "breakout60": True,
        "upper_shadow_count20": 2,
        "max_dd20": -8.0,
        "breakout20": 1,
        "support_ma5": True,
        "support_ma10": True,
        "rsi_12": 61.0,
        "industry_capacity_like_count": 3,
        "is_main_theme": True,
        "is_shortline_leader_candidate": True,
        "market_sentiment_phase": "活跃",
        "capital_resonance_score": 65.0,
        "capital_resonance_signal": "资金配合",
        "sector_ladder_completeness_score": 60.0,
        "limit_up_overheat_score": 30.0,
        "twenty_cm_risk_gate": "pass",
        "free_mv_yi": 80.0,
        "announcement_negative_count": 0,
        "is_st": False,
    }
    stock.update(overrides)
    return stock


def _positive_stock_for_skill(skill_id: str):
    """每个 skill 的正向 stock（满足其 universe.include 的最小字段集）。"""
    if skill_id == "limit-up-reseal-relay":
        return _tradeable_stock(
            is_limit_up=True,
            limit_up_consecutive_days=1,
            limit_up_fd_amount_yi=0.5,
            limit_up_open_times=1,
            pct=9.8,
        )
    if skill_id == "high-range-breakout-momentum":
        return _tradeable_stock(pos120=82.0, off_high60_pct=-1.0, vol5_20=1.25, breakout60=True)
    if skill_id == "sector-relative-strength-leader":
        return _tradeable_stock(primary_excess20=10.0, industry_positive_excess20_count=3)
    if skill_id == "chip-single-peak-breakout":
        # 2026-09-13 语义修复（深审 A3-1）：单峰密集 = 90%成本带宽窄 (4,10]
        # （原 >=20 实为带宽≥20% 的分散股，方向反了）。取 8.0 命中 prefilter 与 setups。
        return _tradeable_stock(chip_concentration=8.0, breakout20=True, vol5_20=1.2, ma5=10.0, ma10=9.9)
    if skill_id == "trend-pullback-continuation":
        # prefilter include_any: vol5_20<=0.8 and pct<=0 and pct>=-5 (原始字段, 第N+15轮修复死锁)
        return _tradeable_stock(dsa_trend_score=22.0, vol5_20=0.7, pct=-1.0)
    if skill_id == "dragon-tiger-smart-money":
        return _tradeable_stock(
            dragon_tiger_is_on_list=True,
            dragon_tiger_net_buy_ratio=0.08,
            dragon_tiger_inst_buy_ratio=0.6,
            main_net_inflow_yi=0.5,
            capital_resonance_signal="主力共振",
        )
    if skill_id == "small-cap-thematic-momentum":
        # universe free_mv_yi<80 + 核心模式 free_mv_yi<50（默认 80.0 不满足 include）
        return _tradeable_stock(free_mv_yi=32.0)
    if skill_id == "overnight-holding-method":
        # universe 要求 pct∈[3,5]（隔夜持有需当日有涨幅），override 默认 1.5
        return _tradeable_stock(pct=4.0)
    return _tradeable_stock()


def test_common_tradeability_prefilter_rejects_low_amount():
    reason = common_tradeability_exclusion_reason(_tradeable_stock(amount_yi=0.8))
    assert reason == "low_amount"


def test_common_tradeability_prefilter_rejects_late_acceleration():
    reason = common_tradeability_exclusion_reason(_tradeable_stock(pos60=99.0, dist60=42.0))
    assert reason == "late_acceleration"


def test_skill_market_prefilters_accept_tradeable_mainline_stock():
    for selected in _selected_skills():
        # 2026-09-13（深审 A3-5）：loader 现按 manifest∧YAML 双源判定 enabled，
        # 已停用策略（如 main-line-leader-resonance，未验证）不参与线上选股，
        # 本测试只约束启用的生产策略。
        if not selected["skill"].enabled:
            continue
        stock = _positive_stock_for_skill(selected["skill_id"])
        passed, reason = evaluate_skill_prefilter(selected["skill"], stock, stage="market")
        assert passed is True, (selected["skill_id"], reason)


def test_apply_prefilters_keeps_union_and_fills_if_needed():
    selected_skills = _selected_skills()
    stocks = [
        _tradeable_stock(code="000001"),
        _tradeable_stock(
            code="000002",
            is_main_theme=False,
            industry_capacity_like_count=0,
            sector_ladder_completeness_score=0.0,
            breakout20=0,
            breakout60=False,
            pos120=40.0,
            off_high60_pct=-15.0,
            vol5_20=0.5,
            primary_excess20=0.0,
            support_ma5=False,
            support_ma10=False,
            pct=1.0,
            off_high_pct=-12.0,
        ),
    ]

    filtered, audit = apply_prefilters(stocks, selected_skills, min_candidate_count=2)

    assert [stock["code"] for stock in filtered] == ["000001", "000002"]
    assert audit["union_pass_count"] + audit["fill_count"] == len(filtered)
    assert audit["union_pass_count"] == 2
    assert audit["fill_count"] == 0
    assert audit["fallback_used"] is False


def _limit_up_skill():
    for s in _selected_skills():
        if s["skill_id"] == "limit-up-reseal-relay":
            return s
    raise AssertionError("limit-up-reseal-relay skill not found")


def test_limit_up_reseal_rejects_non_limit_up_stock():
    """非涨停股（is_limit_up=False）应被 universe 拒绝。"""
    stock = _positive_stock_for_skill("limit-up-reseal-relay")
    stock["is_limit_up"] = False
    passed, reason = evaluate_skill_prefilter(_limit_up_skill()["skill"], stock, stage="market")
    assert passed is False
    assert reason == "universe_include"


def test_prefilter_explanation_contains_failed_expression_and_observed_value():
    stock = _positive_stock_for_skill("limit-up-reseal-relay")
    stock["is_limit_up"] = False

    detail = explain_skill_prefilter(_limit_up_skill()["skill"], stock, stage="market")

    assert detail == {
        "passed": False,
        "scope": "universe_include",
        "code": "universe_include",
        "label": "涨停状态需为 是（当前 否）",
        "expression": "is_limit_up == true",
        "observed": {"is_limit_up": False},
    }


def test_prefilter_machine_reason_is_replaced_by_readable_condition():
    selected = next(
        item for item in _selected_skills()
        if item["skill_id"] == "chao-gu-yang-jia-skill"
    )
    detail = explain_skill_prefilter(
        selected["skill"],
        _tradeable_stock(turnover_rate=11.58),
        stage="market",
    )

    assert detail["code"] == "turnover_too_high"
    assert detail["label"] == "换手率（%）需不高于 10（当前 11.58）"
    assert detail["observed"] == {"turnover_rate": 11.58}


def test_limit_up_reseal_rejects_low_amount():
    """amount_yi < 3 应被 universe 拒绝（流动性门槛，第 87-94 轮修复的根因）。"""
    stock = _positive_stock_for_skill("limit-up-reseal-relay")
    stock["amount_yi"] = 2.5  # < 3
    passed, reason = evaluate_skill_prefilter(_limit_up_skill()["skill"], stock, stage="market")
    assert passed is False
    assert reason == "universe_include"


def test_limit_up_reseal_rejects_high_board():
    """limit_up_consecutive_days > 3 应被 prefilter 拒绝（板高过高）。"""
    stock = _positive_stock_for_skill("limit-up-reseal-relay")
    stock["limit_up_consecutive_days"] = 5  # > 3
    passed, reason = evaluate_skill_prefilter(_limit_up_skill()["skill"], stock, stage="market")
    assert passed is False
    assert reason == "board_height_too_high"


def test_limit_up_reseal_rejects_weak_seal_no_reseal():
    """一字板（open_times=0）且封单弱（fd<0.5）应被 prefilter exclude 拒绝。"""
    stock = _positive_stock_for_skill("limit-up-reseal-relay")
    stock["limit_up_open_times"] = 0
    stock["limit_up_fd_amount_yi"] = 0.3  # < 0.5
    passed, reason = evaluate_skill_prefilter(_limit_up_skill()["skill"], stock, stage="market")
    assert passed is False
    assert reason == "one_word_or_weak_seal"


def test_limit_up_reseal_accepts_strong_first_board():
    """首板强封（consecutive_days=1, fd>=0.5, open_times=1-8）应通过 prefilter。"""
    stock = _positive_stock_for_skill("limit-up-reseal-relay")
    # 已是正向场景：is_limit_up=True, consecutive=1, fd=0.5, open_times=1, pct=9.8
    passed, reason = evaluate_skill_prefilter(_limit_up_skill()["skill"], stock, stage="market")
    assert passed is True, reason
