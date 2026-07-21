"""T8 验证脚本纯函数单测（check_stance_action / review_quality_signals / aggregate_reviews）。

不碰 DB / 不碰网络；只验证质量体检逻辑。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_t8_llm import (  # noqa: E402
    aggregate_reviews,
    check_stance_action,
    review_quality_signals,
)


def test_check_stance_action_consistency():
    assert check_stance_action("strong_support", "确认后加仓") is True
    assert check_stance_action("support", "轻仓试错") is True
    assert check_stance_action("neutral", "观察") is True
    # 违反：oppose 不能给进攻性行动
    assert check_stance_action("oppose", "轻仓试错") is False
    assert check_stance_action("veto", "确认后加仓") is False
    # 未知 → 不误报
    assert check_stance_action(None, "观察") is True
    assert check_stance_action("support", None) is True


def test_review_quality_signals_basic():
    rev = {
        "stance": "support",
        "action": "轻仓试错",
        "score": 72,
        "summary": "均线多头，可轻仓试错",
        "evidence": ["MA5>MA10>MA20"],
        "risks": ["RSI=82 超买"],
    }
    sig = review_quality_signals(rev)
    assert sig["stance_action_ok"] is True
    assert sig["has_evidence"] is True
    assert sig["has_risks"] is True
    assert sig["summary_nonempty"] is True
    assert sig["neutral_with_high_score"] is False


def test_review_neutral_high_score_flagged():
    rev = {"stance": "neutral", "action": "观察", "score": 60,
           "summary": "x", "evidence": ["e"], "risks": ["r"]}
    sig = review_quality_signals(rev)
    assert sig["neutral_with_high_score"] is True


def test_review_pos20_hot_must_be_in_risks():
    # pos20=98 但 risks 未提过热 → 标记未标注
    rev = {"stance": "support", "action": "轻仓试错", "score": 70,
           "summary": "pos20=98 强势", "evidence": ["pos20=98"], "risks": ["流动性不足"]}
    sig = review_quality_signals(rev)
    assert sig["pos20_hot"] == 98.0
    assert sig["pos20_flagged_in_risks"] is False

    rev2 = dict(rev, risks=["pos20=98 过热风险"])
    sig2 = review_quality_signals(rev2)
    assert sig2["pos20_flagged_in_risks"] is True


def test_aggregate_reviews_per_skill():
    reviews = [
        {"_skill_id": "s1", "stance": "support", "action": "轻仓试错", "score": 70,
         "summary": "x", "evidence": ["e"], "risks": ["r"]},
        {"_skill_id": "s1", "stance": "oppose", "action": "轻仓试错", "score": 40,  # SA 违反
         "summary": "y", "evidence": ["e"], "risks": ["r"]},
        {"_skill_id": "s2", "stance": "support", "action": "轻仓试错", "score": 65,
         "summary": "z", "evidence": ["e"], "risks": ["r"]},
    ]
    agg = aggregate_reviews(reviews)
    assert agg["s1"]["n"] == 2
    assert agg["s1"]["stance_action_violations"] == 1
    assert agg["s1"]["stance_action_violation_rate"] == 0.5
    assert agg["s2"]["n"] == 1
    assert agg["s2"]["stance_action_violations"] == 0
    # 不含 _token_cost 的条目 → token 统计为 0
    assert agg["s1"]["n_with_token_cost"] == 0
    assert agg["s1"]["tokens_real_total"] == 0


def test_aggregate_reviews_token_sum():
    reviews = [
        {"_skill_id": "s1", "stance": "support", "action": "轻仓试错", "score": 70,
         "summary": "x", "evidence": ["e"], "risks": ["r"],
         "_token_cost": {"total_tokens_real": 180, "total_cache_hit_tokens": 30}},
        {"_skill_id": "s1", "stance": "support", "action": "轻仓试错", "score": 72,
         "summary": "y", "evidence": ["e"], "risks": ["r"],
         "_token_cost": {"total_tokens_real": 200, "total_cache_hit_tokens": 50}},
    ]
    agg = aggregate_reviews(reviews)
    assert agg["s1"]["n_with_token_cost"] == 2
    assert agg["s1"]["tokens_real_total"] == 380
    assert agg["s1"]["cache_hit_tokens_total"] == 80
