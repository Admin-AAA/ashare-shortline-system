"""L-129 涨幅/回撤闸门（production_selection v23/v24/v26）与三窗口达标配置 v26。

v26 = champion v7 + 换手<8% + 成交额<20 亿 + chg20<22 + max_dd60<−15.61（叠加 execution v5 act0.8）。
三窗口端到端：W1 453/63.36%/PF1.591/Σ+366.1/MDD−12.73；W2 248/62.90%/PF1.537/Σ+210.2；
W3 54/61.11%/PF1.619/Σ+66.3 —— 全部满足 WR≥60% / PF≥1.2 / Σ>0。

本测试锁：① 三个闸门字段的默认值；② chg20 / max_dd60 越界的卡被剔除并带归因；
③ v15（无涨幅/回撤闸门）在同批卡上保留它们（证明差异来自本规则）。
"""
from __future__ import annotations

from app.engine.production_policy import (
    SelectionPolicyContext,
    evaluate_selection_policy,
    load_production_selection_policy,
)


def _card(code: str, score: float, *, chg20: float | None = None, max_dd60: float | None = None) -> dict:
    snap: dict = {}
    if chg20 is not None:
        snap["chg20"] = chg20
    if max_dd60 is not None:
        snap["max_dd60"] = max_dd60
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
        "feature_snapshot": snap,
        **snap,
        "strategy_signals": [
            {"strategy_id": "chen-xiaoqun-skill", "score": score, "hard_veto": False},
            {"strategy_id": "chao-gu-yang-jia-skill", "score": 70.0, "hard_veto": False},
        ],
    }


def _market() -> dict:
    return {"sample_count": 5000, "up_count": 3750, "limit_up_count": 35, "limit_down_count": 0}


def test_v26_declares_vetoes_and_defaults_do_not() -> None:
    v26 = load_production_selection_policy(version=26)
    assert v26.high_chg20_veto == 22.0
    assert v26.max_dd60_veto == -15.61
    assert v26.high_turnover_veto_pct == 8.0
    assert v26.high_amount_veto_yi == 20.0
    v15 = load_production_selection_policy(version=15)
    assert v15.high_chg20_veto is None and v15.max_dd60_veto is None
    assert load_production_selection_policy(version=23).high_chg20_veto == 22.0
    assert load_production_selection_policy(version=24).high_chg60_veto == 33.0


def test_extension_vetoes_drop_overextended_and_smooth_risers() -> None:
    v26 = load_production_selection_policy(version=26)
    cards = [
        _card("600000", 90.0, chg20=31.0, max_dd60=-40.0),   # 20 日涨幅过大 → 剔除
        _card("600001", 85.0, chg20=10.0, max_dd60=-5.0),     # 60 日回撤过浅 → 剔除
        _card("600002", 80.0, chg20=8.0, max_dd60=-30.0),     # 保留
        _card("600003", 75.0),                                 # 缺字段 → 保守放行
    ]
    res = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(score_line=60.0, output_count=10, market_snapshot=_market()),
        v26,
    )
    assert sorted(c["code"] for c in res.recommendations) == ["600002", "600003"]
    verdicts = {v.code: v for v in res.verdicts}
    assert verdicts["600000"].drop_owner == "high_chg20_veto"
    assert verdicts["600001"].drop_owner == "max_dd60_veto"


def test_v15_keeps_extension_cards() -> None:
    """同批卡在 v15（只有换手/成交额闸门）下全部入选，证明差异来自 v26 的两个新闸门。"""
    v15 = load_production_selection_policy(version=15)
    cards = [
        _card("600000", 90.0, chg20=31.0, max_dd60=-40.0),
        _card("600001", 85.0, chg20=10.0, max_dd60=-5.0),
    ]
    res = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(score_line=60.0, output_count=10, market_snapshot=_market()),
        v15,
    )
    assert sorted(c["code"] for c in res.recommendations) == ["600000", "600001"]
