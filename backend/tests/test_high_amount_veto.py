"""L-127 大成交额闸门（production_selection v15/v17/v18）：amount_yi ≥ 阈值直接剔除。

依据：在 V2 自适应闸门的**端到端**选中样本上剔除 amount_yi 最高 20%，三窗胜率同向改善
（W2 59.6→61.9%、W1 +1.0pp、W3 +2.2pp）——本轮唯一端到端三窗同号的特征。
与 DSA 自身 universe（`amount_yi < 15`，"大成交额=机构博弈盘"）同源，本规则把它推广到全部技能。

端到端三窗（V2 闸门 + amount<20 + act0.8）：W3 54 笔 / 63.0% / PF1.867 / Σ+74.6 / MDD−3.65（全达标）；
W2 282 笔 / 60.3% / PF1.616 / Σ+284.4 / MDD−17.31（全达标）。
"""
from __future__ import annotations

from app.engine.production_policy import (
    SelectionPolicyContext,
    evaluate_selection_policy,
    load_production_selection_policy,
)


def _card(code: str, score: float, amount_yi: float | None) -> dict:
    snapshot = {"amount_yi": amount_yi} if amount_yi is not None else {}
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
        "amount_yi": amount_yi,
        "strategy_signals": [
            {"strategy_id": "chen-xiaoqun-skill", "score": score, "hard_veto": False},
            {"strategy_id": "chao-gu-yang-jia-skill", "score": 70.0, "hard_veto": False},
        ],
    }


def _market() -> dict:
    # 强牛（不触发弱势/震荡的 v5 共识闸门，聚焦本规则）
    return {"sample_count": 5000, "up_count": 3750, "limit_up_count": 35, "limit_down_count": 0}


def test_amount_veto_versions_declare_threshold() -> None:
    for version in (15, 17, 18):
        assert load_production_selection_policy(version=version).high_amount_veto_yi == 20.0
    for version in (1, 5, 7, 11, 13, 14):
        assert load_production_selection_policy(version=version).high_amount_veto_yi is None


def test_large_amount_card_is_dropped_and_small_amount_kept() -> None:
    v15 = load_production_selection_policy(version=15)
    cards = [
        _card("600000", 90.0, 25.0),   # ≥20 亿 → 剔除
        _card("600001", 80.0, 9.0),    # 保留
        _card("600002", 75.0, None),   # 缺字段 → 保守放行
    ]
    res = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(score_line=60.0, output_count=10, market_snapshot=_market()),
        v15,
    )
    codes = [c["code"] for c in res.recommendations]
    assert codes == ["600001", "600002"], codes
    verdicts = {v.code: v for v in res.verdicts}
    assert verdicts["600000"].drop_owner == "high_amount_veto"


def test_v11_keeps_large_amount_card() -> None:
    """同批卡在 v11（只有换手闸门）下 90 分的大成交额卡照常入选。"""
    v11 = load_production_selection_policy(version=11)
    cards = [_card("600000", 90.0, 25.0), _card("600001", 80.0, 9.0)]
    res = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(score_line=60.0, output_count=10, market_snapshot=_market()),
        v11,
    )
    assert [c["code"] for c in res.recommendations] == ["600000", "600001"]
