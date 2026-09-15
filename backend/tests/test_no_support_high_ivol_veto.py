"""L-121 无支撑高波动否决（production_selection v8）。

规则：`ivol20_pct >= 阈值`（默认 0.70）且当日**既无 MA5 也无 MA10 支撑**的候选直接剔除，
缺 ivol 分位（数据不全）时保守放行——与 v3/v4 尾部否决同形的"缺字段保守放行"。

依据（把 DSA 的回撤特征迁移成跨策略条件，用户 2026-09-15 建议）：
- W2 里 DSA 相关选中卡的共同点是"有 MA 支撑"（sup5 占比 0.37 vs 无 DSA 卡 0.16），
  DSA 相关卡 WR 62.6% / avg +0.68%，无 DSA 卡只有 WR 48.4% / avg −0.05%。
- W3 里亏损卡集中在高 ivol(0.80)+高位+无支撑，赢家是低位低波动+有支撑。
"""
from __future__ import annotations

from app.engine.production_policy import (
    SelectionPolicyContext,
    evaluate_selection_policy,
    load_production_selection_policy,
)


def _card(code: str, score: float, ivol, sup5: bool = False, sup10: bool = False) -> dict:
    return {
        "code": code,
        "final_score": score,
        "selection_score": score,
        "portfolio_rank_score": score,
        "evidence_quality_score": 90.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
        "ivol20_pct": ivol,
        "support_ma5": sup5,
        "support_ma10": sup10,
        "strategy_signals": [
            {"strategy_id": f"skill-{i}", "score": score - i, "hard_veto": False} for i in range(2)
        ],
    }


def _snapshot() -> dict:
    return {"sample_count": 5000, "up_count": 2000, "limit_up_count": 40, "limit_down_count": 0}


def _evaluate(policy, cards):
    return evaluate_selection_policy(
        [dict(c) for c in cards],
        SelectionPolicyContext(score_line=40.0, output_count=10, market_snapshot=_snapshot()),
        policy,
    )


def test_v8_drops_high_ivol_without_support() -> None:
    v8 = load_production_selection_policy(version=8)
    assert v8.no_support_high_ivol_veto_enabled is True
    cards = [
        _card("600001", 99.0, 0.90),               # 高 ivol 无支撑 → 剔
        _card("600002", 98.0, 0.90, sup5=True),    # 高 ivol 有 MA5 支撑 → 留
        _card("600003", 97.0, 0.50),               # 低 ivol 无支撑 → 留
        _card("600004", 96.0, None),               # 缺 ivol 分位 → 保守放行
    ]
    res = _evaluate(v8, cards)
    assert [c["code"] for c in res.recommendations] == ["600002", "600003", "600004"]
    assert {v.code: v.drop_owner for v in res.verdicts}["600001"] == "no_support_high_ivol_veto"


def test_v8_threshold_boundary_inclusive_high_side() -> None:
    v8 = load_production_selection_policy(version=8)
    at = _evaluate(v8, [_card("600001", 99.0, 0.70)])
    above = _evaluate(v8, [_card("600001", 99.0, 0.69)])
    assert at.recommendations == ()          # 恰好等于阈值 → 剔（含等号）
    assert [c["code"] for c in above.recommendations] == ["600001"]


def test_v8_missing_support_fields_are_treated_as_no_support() -> None:
    """support_ma5/ma10 缺失视为无支撑（保守方向 = 更严格风控），与 ivol 缺字段相反。"""
    v8 = load_production_selection_policy(version=8)
    card = _card("600001", 99.0, 0.90)
    card.pop("support_ma5")
    card.pop("support_ma10")
    res = _evaluate(v8, [card])
    assert res.recommendations == ()


def test_older_versions_do_not_apply_the_veto() -> None:
    for version in (4, 5, 6, 7):
        policy = load_production_selection_policy(version=version)
        assert policy.no_support_high_ivol_veto_enabled is False
        res = _evaluate(policy, [_card("600001", 99.0, 0.90)])
        assert [c["code"] for c in res.recommendations] == ["600001"], version
