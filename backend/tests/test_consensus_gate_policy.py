"""L-117 共识闸门（production_selection v5）：弱势/震荡下要求至少 2 个策略背书。

锁定四件事：
1. v5 在弱势/震荡把"只有 1 个非否决策略背书"的卡剔除，并由下一名自然递补；
2. 强牛/偏多不动（强趋势里单策略动量有效，施加会削掉 W1/W2 收益）；
3. 硬否决卡不参与该规则的计数（不因 signals 少而被"二次剔除"）；
4. v4 及更早版本行为不变（frozen artifact 语义 + 默认关闭）。
"""
from __future__ import annotations

from app.engine.production_policy import (
    SelectionPolicyContext,
    evaluate_selection_policy,
    load_production_selection_policy,
)


def _card(code: str, score: float, n_signals: int) -> dict:
    return {
        "code": code,
        "bucket": "chen-xiaoqun-skill",
        "final_score": score,
        "evidence_quality_score": 90.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
        "strategy_signals": [
            {"strategy_id": f"skill-{i}", "score": 70.0 - i, "hard_veto": False}
            for i in range(n_signals)
        ],
    }


def _weak() -> dict:
    # up_ratio=0.25 < 0.35 → 弱势；涨停率 0.3% ∈ [0.1%,0.5%] → 平淡（避开冰点 2 只限额）
    return {"sample_count": 1000, "up_count": 250, "limit_up_count": 3, "limit_down_count": 0}


def _range_mkt() -> dict:
    # up_ratio=0.40 ∈ [0.35,0.50) → 震荡
    return {"sample_count": 1000, "up_count": 400, "limit_up_count": 3, "limit_down_count": 0}


def _bull() -> dict:
    # up_ratio=0.80 ≥ 0.65 → 强牛
    return {"sample_count": 1000, "up_count": 800, "limit_up_count": 3, "limit_down_count": 0}


def _cards() -> list[dict]:
    """11 张：最高分那张只有 1 个背书，其余都 ≥2 个（用于观察递补）。"""
    cards = [_card(f"{600000 + i:06d}", 100.0 - i, 2) for i in range(11)]
    cards[0]["strategy_signals"] = cards[0]["strategy_signals"][:1]
    return cards


def test_v5_drops_low_consensus_card_in_weak_regime_and_backfills() -> None:
    v5 = load_production_selection_policy(version=5)
    res = evaluate_selection_policy(
        _cards(), SelectionPolicyContext(score_line=40.0, output_count=10,
                                         market_snapshot=_weak()), v5)
    codes = [c["code"] for c in res.recommendations]
    assert "600000" not in codes           # 仅 1 个背书 → 剔
    assert "600010" in codes               # 第 11 名递补
    assert len(codes) == 10
    verdicts = {v.code: v for v in res.verdicts}
    assert verdicts["600000"].drop_owner == "consensus_gate"


def test_v5_applies_in_oscillating_regime_too() -> None:
    v5 = load_production_selection_policy(version=5)
    res = evaluate_selection_policy(
        _cards(), SelectionPolicyContext(score_line=40.0, output_count=10,
                                         market_snapshot=_range_mkt()), v5)
    assert "600000" not in [c["code"] for c in res.recommendations]


def test_v5_does_not_touch_strong_regime() -> None:
    """强牛里单策略动量有效：v5 不得剔除低背书卡（否则会削掉 W1/W2 的动量收益）。"""
    v5 = load_production_selection_policy(version=5)
    res = evaluate_selection_policy(
        _cards(), SelectionPolicyContext(score_line=40.0, output_count=10,
                                         market_snapshot=_bull()), v5)
    assert [c["code"] for c in res.recommendations][0] == "600000"
    assert {v.code: v for v in res.verdicts}["600000"].drop_owner != "consensus_gate"


def test_v4_and_earlier_keep_low_consensus_card() -> None:
    for version in (1, 2, 3, 4):
        policy = load_production_selection_policy(version=version)
        assert policy.consensus_min_signals == 0
        res = evaluate_selection_policy(
            _cards(), SelectionPolicyContext(score_line=40.0, output_count=10,
                                             market_snapshot=_weak()), policy)
        assert [c["code"] for c in res.recommendations][0] == "600000", version


def test_hard_veto_card_keeps_its_own_drop_owner() -> None:
    """卡片级硬否决由上游处置：共识规则不得把它的 drop_owner 改写成 consensus_gate。"""
    v5 = load_production_selection_policy(version=5)
    cards = [_card("600000", 100.0, 1), _card("600001", 99.0, 2)]
    cards[0]["hard_veto"] = True
    res = evaluate_selection_policy(
        cards, SelectionPolicyContext(score_line=40.0, output_count=10,
                                      market_snapshot=_weak()), v5)
    verdict = {v.code: v for v in res.verdicts}["600000"]
    assert verdict.drop_owner != "consensus_gate"
