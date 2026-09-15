"""L-122 配额槽（production_selection v9）：给"指定技能当日分最高的那张卡"加分抬进 top-N。

动机（用户 2026-09-16 提出）：单策略口径下若干技能胜率/盈亏比明显好于三大主力
（trend-pullback W1 WR61.5%/W2 65.0%，overnight W1 PF2.11/W3 PF2.68，sector-relative W1 WR59.9%/n514），
但它们在组合里每窗只被选中个位数笔——从不赢每日 top-N 排序。逐技能信号权重对**单信号卡**
无效（w 在 Σ(score·w)/Σw 里约掉；实测 sigw_tp15 与基线逐位相同），所以只有直接抬分能改变出票量。

本测试锁三件事：① 默认关闭时逐位不动（v1-v8 语义不被隐式改变）；② 只抬"当日该技能最高分"那一张，
且 final_score/selection_score/portfolio_rank_score 三处同步（L-119：只改 final_score 会静默失效）；
③ 未点名的技能不受影响。
"""
from __future__ import annotations

from app.engine.production_policy import (
    SelectionPolicyContext,
    evaluate_selection_policy,
    load_production_selection_policy,
)


def _card(code: str, score: float, skill: str) -> dict:
    return {
        "code": code,
        "bucket": skill,
        "final_score": score,
        "selection_score": score,
        "portfolio_rank_score": score,
        "evidence_quality_score": 90.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
        "strategy_signals": [
            {"strategy_id": skill, "score": score, "hard_veto": False},
            {"strategy_id": "chao-gu-yang-jia-skill", "score": 70.0, "hard_veto": False},
        ],
    }


def _market() -> dict:
    # up_ratio=0.55 → 震荡 基态（不触发强牛/高潮输出上限）
    return {"sample_count": 5000, "up_count": 2750, "limit_up_count": 40, "limit_down_count": 3}


def test_v9_declares_slot_reserve_and_older_versions_do_not() -> None:
    assert load_production_selection_policy(version=9).slot_reserve == (
        ("overnight-holding-method", 20.0),
    )
    for version in (1, 2, 3, 4, 5, 6, 7, 8):
        assert load_production_selection_policy(version=version).slot_reserve == ()


def test_slot_reserve_boosts_only_best_card_of_named_skill() -> None:
    policy = load_production_selection_policy(version=9)
    cards = [
        _card("600000", 80.0, "chen-xiaoqun-skill"),
        _card("600001", 66.0, "overnight-holding-method"),
        _card("600002", 64.0, "overnight-holding-method"),
    ]
    res = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(score_line=60.0, output_count=10, market_snapshot=_market()),
        policy,
    )
    by_code = {c["code"]: c for c in cards}
    # 当日该技能最高分那张 +20，另一张不动
    assert by_code["600001"]["final_score"] == 86.0
    assert by_code["600001"]["selection_score"] == 86.0
    assert by_code["600001"]["portfolio_rank_score"] == 86.0
    assert by_code["600002"]["final_score"] == 64.0
    # 未点名的技能不受影响
    assert by_code["600000"]["final_score"] == 80.0
    steps = [s for s in by_code["600001"]["policy_verdict_steps"] if s["rule_id"] == "slot_reserve"]
    assert len(steps) == 1 and steps[0]["action"] == "boost"
    assert "overnight-holding-method" in steps[0]["reason"]
    assert [c["code"] for c in res.recommendations][:1] == ["600001"]


def test_slot_reserve_lifts_card_over_score_line() -> None:
    """加分必须能真正改变"是否出票"：66 分在 70 线下、+20 后过线。"""
    v7 = load_production_selection_policy(version=7)
    v9 = load_production_selection_policy(version=9)

    def _run(policy):
        cards = [
            _card("600010", 80.0, "chen-xiaoqun-skill"),
            _card("600011", 66.0, "overnight-holding-method"),
        ]
        return [
            c["code"]
            for c in evaluate_selection_policy(
                cards,
                SelectionPolicyContext(score_line=70.0, output_count=10, market_snapshot=_market()),
                policy,
            ).recommendations
        ]

    assert _run(v7) == ["600010"]
    # 加分后 600011（86）排在 600010（80）之前 —— 这正是"抬进 top-N"的机制
    assert _run(v9) == ["600011", "600010"]
