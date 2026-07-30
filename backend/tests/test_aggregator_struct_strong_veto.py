"""T-struct-strong-veto 回归：结构性强势(climax) regime 入场硬否决。

背景（si_struct_strong_veto.py 复验 + self-improve 发布）：
  LIVE formal clean>=72 口径实证结构性强势 7 笔全败(WR0%/PF0.0, 跨 5 交易日非单日簇)，
  原 +4 门槛仅抬高分数线但放行高分毒区票(动态 base 可低至 67→eff 71, 72 分仍过线)。
  改为硬否决：_snapshot_regime=="结构性强势" 时 effective_score_line=999，彻底阻断入场。

锁定：
  1. 结构性强势卡片（即便 final_score 远高于地板）不得进入推荐结果；
  2. 其 score_line_meta 携带 regime_struct_strong_hard_veto=True、regime_strong_penalty=4.0、
     passed=False（归因一致，L-013 决策时口径）；
  3. 弱势对照卡片同分应正常过线（否决是 regime 定向，不误伤其他 regime）。
"""
import pytest

from app.engine.aggregator import apply_pool_rules


def _card(code: str, final_score: float = 95.0) -> dict:
    return {
        "code": code,
        "final_score": final_score,
        "evidence_quality_score": 92.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
    }


def _snapshot(regime: str) -> dict:
    return {
        "sample_count": 100,
        "up_count": 45,
        "limit_up_count": 0,
        "limit_down_count": 0,
        "market_regime": regime,
    }


def test_struct_strong_regime_hard_veto_blocks_recommendation():
    struct_card = _card("A", final_score=95.0)
    res = apply_pool_rules(
        [struct_card], output_count=6, score_line=72,
        market_snapshot=_snapshot("结构性强势"),
    )
    assert not res, "结构性强势必须硬否决，高分票也不得进入推荐"
    meta = struct_card["score_line_meta"]
    assert meta["regime_struct_strong_hard_veto"] is True
    assert meta["regime_strong_penalty"] == 4.0
    assert meta["passed"] is False
    assert meta["decision_regime"] == "结构性强势"


def test_weak_regime_control_passes_same_score():
    weak_card = _card("B", final_score=95.0)
    res = apply_pool_rules(
        [weak_card], output_count=6, score_line=72,
        market_snapshot=_snapshot("弱势"),
    )
    assert res, "弱势对照同分必须过线，否决是 regime 定向不误伤"
    assert weak_card["score_line_meta"]["regime_struct_strong_hard_veto"] is not True
    assert weak_card["score_line_meta"]["passed"] is True


def test_struct_strong_hard_veto_beats_floor_penalty():
    """即便把地板抬高到 90，结构性强势仍应被硬否决（验证 999 覆盖任何动态 base）。"""
    struct_card = _card("C", final_score=98.0)
    res = apply_pool_rules(
        [struct_card], output_count=6, score_line=90,
        market_snapshot=_snapshot("结构性强势"),
    )
    assert not res
    assert struct_card["score_line_meta"]["regime_struct_strong_hard_veto"] is True
