"""L-118 强牛·高潮日输出上限（production_selection v6）。

规则：`market_base == 强牛` 且 `sentiment == 高潮` 时，当日候选先按分数收到最高的 N 张
（v6 取 N=3），其余写入 `policy_drop_owner="strong_climax_top_n"` 归因。

依据（三窗全管线/面板口径）：W3 的亏损集中在强牛·高潮日（多笔 −5.66% 跳空止损）；
把高潮输出从现有 divisor 机制的 10→5 进一步收到 3 张，三窗 Δcohort +0.015/+0.419/−0.119
全为正、三窗均过 evaluate_challenger（W2 配对 CI 下界 +0.064、逐月同号 0.86）。
"""
from __future__ import annotations

from app.engine.production_policy import (
    SelectionPolicyContext,
    evaluate_selection_policy,
    load_production_selection_policy,
)


def _card(code: str, score: float, signals: int = 2) -> dict:
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


def _bull_climax() -> dict:
    # up_ratio=0.75 ≥0.65 → 强牛；涨停率 129/5000=2.58% >1.2% 且无跌停 → 高潮
    return {"sample_count": 5000, "up_count": 3750, "limit_up_count": 129, "limit_down_count": 0}


def _range_climax() -> dict:
    # up_ratio=0.40 → 震荡；涨停率仍 2.58% → 情绪同为高潮（用于验证"只限强牛"）
    return {"sample_count": 5000, "up_count": 2000, "limit_up_count": 129, "limit_down_count": 0}


def _bull_active() -> dict:
    # 强牛但涨停率 35/5000=0.7% → 活跃（非高潮）
    return {"sample_count": 5000, "up_count": 3750, "limit_up_count": 35, "limit_down_count": 0}


def _cards() -> list[dict]:
    return [_card(f"{600000 + i:06d}", 100.0 - i) for i in range(6)]


def test_v7_caps_strong_climax_day_to_top_n() -> None:
    v6 = load_production_selection_policy(version=6)
    assert v6.strong_climax_top_n == 3
    v7 = load_production_selection_policy(version=7)
    assert v7.strong_climax_top_n == 2
    res = evaluate_selection_policy(
        _cards(),
        SelectionPolicyContext(score_line=40.0, output_count=10, market_snapshot=_bull_climax()),
        v7,
    )
    codes = [c["code"] for c in res.recommendations]
    assert codes == ["600000", "600001"], codes
    verdicts = {v.code: v for v in res.verdicts}
    assert verdicts["600003"].drop_owner == "strong_climax_top_n"


def test_v7_only_applies_to_strong_bull_base() -> None:
    """震荡·高潮不触发 v6：该场景下 v6 与 v5 结果逐位一致，且没有任何 strong_climax_top_n 归因。

    （注：震荡·高潮本身被既有有效分数线整体拦掉，这是 v5 之前就存在的行为，与本规则无关。）
    """
    res_v5 = evaluate_selection_policy(
        _cards(),
        SelectionPolicyContext(score_line=40.0, output_count=10, market_snapshot=_range_climax()),
        load_production_selection_policy(version=5),
    )
    res_v6 = evaluate_selection_policy(
        _cards(),
        SelectionPolicyContext(score_line=40.0, output_count=10, market_snapshot=_range_climax()),
        load_production_selection_policy(version=6),
    )
    assert [c["code"] for c in res_v6.recommendations] == [c["code"] for c in res_v5.recommendations]
    assert all(v.drop_owner != "strong_climax_top_n" for v in res_v6.verdicts)


def test_v7_not_applied_outside_climax_sentiment() -> None:
    v6 = load_production_selection_policy(version=6)
    res = evaluate_selection_policy(
        _cards(),
        SelectionPolicyContext(score_line=40.0, output_count=10, market_snapshot=_bull_active()),
        v6,
    )
    assert len(res.recommendations) == 6


def test_older_versions_do_not_cap() -> None:
    """v4/v5 无本规则：高潮日仍按既有 divisor 机制输出 ceil(10/2)=5 张。"""
    for version in (4, 5):
        policy = load_production_selection_policy(version=version)
        assert policy.strong_climax_top_n == 0
        res = evaluate_selection_policy(
            _cards(),
            SelectionPolicyContext(score_line=40.0, output_count=10, market_snapshot=_bull_climax()),
            policy,
        )
        assert len(res.recommendations) == 5, version
        assert all(v.drop_owner != "strong_climax_top_n" for v in res.verdicts)
