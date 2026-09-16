"""L-126 反向共识闸门（production_selection v13）：只保留背书数 ≤ N 的候选。

依据：`≥3 背书` 是 W1/W2 的强正信号、W3 的强负信号（已选样本 W3：<3 背书 WR 62% avg +1.57
vs ≥3 背书 WR 45% avg −1.31）。因果门控（regime/情绪/涨停家数/ivol/分数差）全部无效，
只能用"已结算的近 K 日表现"决定用正向侧（v10，≥3）还是反向侧（本规则，≤2）。

本测试锁：① 默认关闭；② ≤N 保留、>N 剔除并带归因；③ 与 v5 正向闸门可共存（v13 同时带
弱势/震荡 ≥2 的 v5 设定）。
"""
from __future__ import annotations

from app.engine.production_policy import (
    SelectionPolicyContext,
    evaluate_selection_policy,
    load_production_selection_policy,
)


def _card(code: str, score: float, signals: int) -> dict:
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
        "strategy_signals": [
            {"strategy_id": f"skill-{i}", "score": score - i, "hard_veto": False}
            for i in range(signals)
        ],
    }


def _bull_active() -> dict:
    # up_ratio=0.75 → 强牛（v5 正向闸门只在弱势/震荡生效，故此处不叠加）
    return {"sample_count": 5000, "up_count": 3750, "limit_up_count": 35, "limit_down_count": 0}


def test_v13_declares_max_signals_and_others_do_not() -> None:
    assert load_production_selection_policy(version=13).consensus_max_signals == 2
    for version in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
        assert load_production_selection_policy(version=version).consensus_max_signals is None


def test_anti_consensus_drops_high_signal_cards() -> None:
    v13 = load_production_selection_policy(version=13)
    cards = [
        _card("600000", 90.0, 4),  # >2 → 剔除
        _card("600001", 80.0, 2),  # ≤2 → 保留
        _card("600002", 70.0, 1),  # ≤2 → 保留
    ]
    res = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(score_line=60.0, output_count=10, market_snapshot=_bull_active()),
        v13,
    )
    codes = [c["code"] for c in res.recommendations]
    assert codes == ["600001", "600002"], codes
    verdicts = {v.code: v for v in res.verdicts}
    assert verdicts["600000"].drop_owner == "anti_consensus_gate"


def test_v7_keeps_high_signal_card() -> None:
    """同一批卡在 v7 下 90 分的 4 背书卡照常入选（证明差异来自本规则）。"""
    v7 = load_production_selection_policy(version=7)
    cards = [_card("600000", 90.0, 4), _card("600001", 80.0, 2)]
    res = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(score_line=60.0, output_count=10, market_snapshot=_bull_active()),
        v7,
    )
    assert [c["code"] for c in res.recommendations] == ["600000", "600001"]
