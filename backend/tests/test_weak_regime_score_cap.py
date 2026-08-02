"""测试弱势 regime 分数线封顶（P3.1 产出链路修复）。

背景（2026-08-02 诊断）：7 月 formal 连续 0 产出，根因之一为
FORMAL_SCORE_LINE_FLOOR=72 + 弱势 +2 调整把线抬到 74，而弱市 gate 分普遍
<58。修复：production policy 新增 weak_regime_score_cap=60，弱势 regime
下 effective_score_line 封顶 60，其他 regime 不受影响。
"""
from app.engine.aggregator import resolve_score_line_context
from app.engine.production_policy import DEFAULT_PRODUCTION_SELECTION_POLICY


def _snapshot(market_base: str, sentiment: str = "平淡") -> dict:
    # _market_base 按 up_ratio=up_count/sample_count 判定（aggregator.py:160-172）
    ratio = {"强牛": 0.70, "偏多": 0.55, "震荡": 0.40, "弱势": 0.25}[market_base]
    return {
        "sample_count": 100,
        "up_count": int(100 * ratio),
        "breadth": {"regime": market_base, "risk": "低"},
        "market_regime": market_base,
        "market_sentiment": sentiment,
    }


def test_weak_regime_score_line_capped_at_60():
    ctx = resolve_score_line_context(72.0, market_snapshot=_snapshot("弱势"), selection_policy=DEFAULT_PRODUCTION_SELECTION_POLICY)
    assert ctx["effective_score_line"] == 60.0, ctx


def test_weak_regime_cap_respects_minimum_floor():
    # 弱势·冰点：基础 72 - 3 = 69 → 封顶 60；验证封顶不把线压到 minimum 之下
    ctx = resolve_score_line_context(72.0, market_snapshot=_snapshot("弱势", "冰点"), selection_policy=DEFAULT_PRODUCTION_SELECTION_POLICY)
    assert ctx["effective_score_line"] == 60.0, ctx


def test_non_weak_regime_not_capped():
    for regime in ("强牛", "偏多", "震荡"):
        ctx = resolve_score_line_context(72.0, market_snapshot=_snapshot(regime), selection_policy=DEFAULT_PRODUCTION_SELECTION_POLICY)
        assert ctx["effective_score_line"] >= 72.0, (regime, ctx)


def test_weak_cap_none_disables_cap():
    from dataclasses import replace

    policy = replace(DEFAULT_PRODUCTION_SELECTION_POLICY, weak_regime_score_cap=None)
    ctx = resolve_score_line_context(72.0, market_snapshot=_snapshot("弱势"), selection_policy=policy)
    assert ctx["effective_score_line"] == 71.0, ctx  # 无封顶时保持调整后原线（72 + 弱势调整 - 1）


def test_policy_artifact_contains_weak_cap():
    from app.engine.production_policy import load_production_selection_policy

    policy = load_production_selection_policy()
    assert policy.weak_regime_score_cap == 60.0
    assert "weak_regime_score_cap" in policy.canonical_payload()


def test_soft_floor_base_follows_weak_cap():
    """弱势封顶后软降线 base_floor 应跟随有效线（52），否则 top<64 弱市候选永远无法放行。"""
    from app.engine.aggregator import apply_pool_rules

    top_card = {
        "code": "300001",
        "name": "特锐德",
        "final_score": 56.0,
        "chg20": 18.0,  # ≥15：避免弱势×chg20<15 硬否决（半强股不兜底是设计行为）
        "evidence_quality_score": 92.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
        "score_line_meta": {"effective_score_line": 60.0},
        "execution_viability": {"status": "ok"},
        "strategy_signals": [{"strategy_id": "chip-single-peak-breakout", "score": 56.0, "hard_veto": False}],
    }
    market = _snapshot("弱势")
    # 无候选过线（top 56 < 60），软降线应把线放到 max(top-1, base_floor)=55 放行
    result = apply_pool_rules(
        [top_card],
        output_count=2,
        min_score=72.0,
        market_snapshot=market,
        risk_template="balanced",
        selection_policy=DEFAULT_PRODUCTION_SELECTION_POLICY,
    )
    cards = result if isinstance(result, list) else result.get("recommendations", [])
    assert any(c["code"] == "300001" for c in cards), f"弱市 top=56 应经软降线放行: {result}"


def test_soft_floor_weak_low_chg20_still_blocked():
    """弱势×chg20<15 硬否决不参与软降线兜底（设计行为，防止半强股混入）。"""
    from app.engine.aggregator import apply_pool_rules

    market = {
        "sample_count": 100,
        "up_count": 25,
        "breadth": {"regime": "弱势", "risk": "低"},
        "market_regime": "弱势",
        "market_sentiment": "平淡",
    }
    card = {
        "code": "300001",
        "final_score": 56.0,
        "chg20": 8.0,  # <15 触发弱势×chg20 惩罚
        "evidence_quality_score": 92.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
    }
    result = apply_pool_rules(
        [card], output_count=2, min_score=72.0, market_snapshot=market, selection_policy=DEFAULT_PRODUCTION_SELECTION_POLICY,
    )
    cards = result if isinstance(result, list) else result.get("recommendations", [])
    assert cards == [], f"chg20=8 的半强股不应被软降线放行: {cards}"
