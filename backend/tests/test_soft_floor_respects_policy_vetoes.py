"""软降线只在通过横截面否决的候选里降线，不能把已否决的卡重新放行。

主选择循环遍历 deduped（去重 + 十余道 policy 否决后的池），软降线兜底却遍历 ordered
（否决前的池），只重查 score_line_meta 里的 regime 惩罚。被 high_turnover_veto 剔除的卡
仍留在 ordered 里，降线后分数够就又被 append 进 result。
"""
from app.engine.aggregator_pure import apply_pool_rules
from app.engine.production_policy import load_production_selection_policy


def _weak_active_snapshot() -> dict:
    return {
        "sample_count": 5000,
        "up_count": 1500,
        "down_count": 3400,
        "limit_up_count": 40,
        "limit_down_count": 20,
        "breadth": {"regime": "弱势", "up_ratio": 0.30},
    }


def _limit_up_risky_card(code: str, final_score: float, turnover: float) -> dict:
    """涨停 + 弱封单 + 尾盘封板 → 单票执行惩罚把有效线抬到 top 之上。"""
    return {
        "code": code,
        "final_score": final_score,
        "evidence_quality_score": 92.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
        "is_limit_up": True,
        "limit_up_open_times": 3,
        "limit_up_fd_amount_yi": 0.3,
        "limit_up_last_time": 144500,
        "chg20": 25.0,
        "pos60": 80.0,
        "turnover_rate": turnover,
        "feature_snapshot": {"turnover_rate": turnover},
        "strategy_signals": [
            {"strategy_id": "chen-xiaoqun-skill", "score": final_score, "hard_veto": False},
            {"strategy_id": "chao-gu-yang-jia-skill", "score": 70.0, "hard_veto": False},
        ],
    }


def test_soft_floor_does_not_readmit_vetoed_card():
    policy = load_production_selection_policy(version=11)
    assert policy.high_turnover_veto_pct == 8.0
    cards = [
        _limit_up_risky_card("600000", 70.0, 9.5),  # 换手 9.5 ≥ 8 → 被否决
        _limit_up_risky_card("600001", 64.0, 4.0),  # 保留，但单票有效线 67 > 64
    ]
    result = apply_pool_rules(
        cards, output_count=6, score_line=72, market_snapshot=_weak_active_snapshot(),
        selection_policy=policy,
    )
    codes = [c["code"] for c in result]
    assert "600000" not in codes, f"高换手否决卡被软降线重新放行：{codes}"
    assert codes == ["600001"], codes
    assert result[0]["score_line_meta"]["dynamic_soft_floor_applied"] is True
    assert cards[0]["policy_drop_owner"] == "high_turnover_veto"


def test_soft_floor_does_not_emit_duplicate_codes():
    """ordered 含同 code 重复卡（去重前），软降线遍历它会把同一只票出两次。"""
    snapshot = _weak_active_snapshot()
    cards = [
        _limit_up_risky_card("600000", 82.0, 4.0),
        _limit_up_risky_card("600000", 81.0, 4.0),
        _limit_up_risky_card("600001", 79.0, 4.0),
    ]
    result = apply_pool_rules(cards, output_count=6, score_line=72, market_snapshot=snapshot)
    codes = [c["code"] for c in result]
    assert len(codes) == len(set(codes)), f"软降线输出重复代码：{codes}"
