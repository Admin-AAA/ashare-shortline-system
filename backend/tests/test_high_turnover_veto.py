"""L-124 高换手闸门（production_selection v11）：turnover_rate ≥ 阈值的候选卡直接剔除。

依据：在**已选中**样本上按换手分层，[8,12) 这一档在三个窗口都是低质量区
（W1 53% vs 60%、W2 47% vs 54–59%、W3 50% vs 61–65%）。
端到端：W2 五项全改善（PF 1.169→1.191、Σ +190→+204、MDD −27.15→−23.66），
W1/W3 未过闸门 → 候选 artifact，默认不启用。

本测试锁三件事：① 默认关闭（v7=v10 无该字段，逐位不变）；② ≥阈值的卡被剔除且带归因；
③ 缺 turnover_rate 的卡保守放行（与 v8/L-121 同一纪律）。
"""
from __future__ import annotations

from app.engine.production_policy import (
    SelectionPolicyContext,
    evaluate_selection_policy,
    load_production_selection_policy,
)


def _card(code: str, score: float, turnover: float | None) -> dict:
    snapshot = {"turnover_rate": turnover} if turnover is not None else {}
    return {
        "code": code,
        "bucket": "chen-xiaoqun-skill",
        "final_score": score,
        "selection_score": score,
        "portfolio_rank_score": score,
        "evidence_quality_score": 90.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
        "feature_snapshot": snapshot,
        "turnover_rate": turnover,
        "strategy_signals": [
            {"strategy_id": "chen-xiaoqun-skill", "score": score, "hard_veto": False},
            {"strategy_id": "chao-gu-yang-jia-skill", "score": 70.0, "hard_veto": False},
        ],
    }


def _market() -> dict:
    return {"sample_count": 5000, "up_count": 2750, "limit_up_count": 40, "limit_down_count": 3}


def test_v11_declares_threshold_and_default_versions_do_not() -> None:
    assert load_production_selection_policy(version=11).high_turnover_veto_pct == 8.0
    assert load_production_selection_policy(version=12).high_turnover_veto_pct == 10.0
    for version in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
        assert load_production_selection_policy(version=version).high_turnover_veto_pct is None


def test_high_turnover_card_is_dropped_and_promotes_next() -> None:
    v11 = load_production_selection_policy(version=11)
    cards = [
        _card("600000", 90.0, 9.5),   # 换手 9.5 ≥ 8 → 剔除
        _card("600001", 80.0, 4.0),   # 保留
        _card("600002", 75.0, None),  # 缺字段 → 保守放行
    ]
    res = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(score_line=60.0, output_count=10, market_snapshot=_market()),
        v11,
    )
    codes = [c["code"] for c in res.recommendations]
    assert codes == ["600001", "600002"], codes
    verdicts = {v.code: v for v in res.verdicts}
    assert verdicts["600000"].drop_owner == "high_turnover_veto"
    assert verdicts["600002"].drop_owner is None


def test_v7_keeps_high_turnover_card() -> None:
    """同一天同一批卡，v7 无该规则 → 90 分的高换手卡照常入选（证明差异来自本规则）。"""
    v7 = load_production_selection_policy(version=7)
    cards = [_card("600000", 90.0, 9.5), _card("600001", 80.0, 4.0)]
    res = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(score_line=60.0, output_count=10, market_snapshot=_market()),
        v7,
    )
    assert [c["code"] for c in res.recommendations] == ["600000", "600001"]
