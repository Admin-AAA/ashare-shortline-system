"""L-013 回归：决策时 regime 标签必须持久化进 score_line_meta。

背景：下游 regime 否决 / χ² 分桶 / 最差桶归因 一律应以「决策时实际施加的 regime」
为准，禁止读 feature_snapshot_json.market_regime 或 run.market_snapshot_json
（feature_builder 可能按 trade_date 回溯重算，导致标签与决策时不一致）。

本测试锁定 aggregator 在构建 score_line_meta 时显式落：
  - decision_regime      = 聚合时实际使用的 _snapshot_regime
  - regime_strong_penalty = 结构性强势 +4 / 强势 +8 / 其他 0.0

覆盖中性/结构性强势/强势/震荡/弱势 五分支，确保两个字段始终存在且自洽。
"""
import pytest

from app.engine.aggregator import apply_pool_rules


def _card(code: str, final_score: float = 90.0) -> dict:
    return {
        "code": code,
        "final_score": final_score,
        "evidence_quality_score": 92.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
    }


@pytest.mark.parametrize(
    "regime,expected_penalty,expected_vetoed",
    [
        ("结构性强势", 4.0, True),   # T-struct-strong-veto：硬否决，不进推荐
        ("强势", 8.0, False),
        ("震荡", 0.0, False),
        ("弱势", 0.0, False),
        ("", 0.0, False),
    ],
)
def test_score_line_meta_persists_decision_regime_and_strong_penalty(
    regime, expected_penalty, expected_vetoed
):
    snapshot = {
        "sample_count": 100,
        "up_count": 45,
        "limit_up_count": 0,
        "limit_down_count": 0,
        "market_regime": regime,
    }
    cards = [_card("A"), _card("B")]
    result = apply_pool_rules(
        cards, output_count=6, score_line=72, market_snapshot=snapshot
    )
    if expected_vetoed:
        assert not result, "结构性强势必须硬否决，高分票也不得进推荐"
    else:
        assert result, "至少应有卡片产出以校验 score_line_meta"
    for card in cards:
        meta = card["score_line_meta"]
        assert "decision_regime" in meta, "decision_regime 必须持久化进 score_line_meta"
        assert (
            meta["decision_regime"] == regime
        ), "decision_regime 必须等于决策时实际 regime"
        assert isinstance(meta["decision_regime"], str)
        assert (
            meta["regime_strong_penalty"] == expected_penalty
        ), "regime_strong_penalty 必须与 regime 分支一致"
        assert (
            meta.get("regime_struct_strong_hard_veto", False) is expected_vetoed
        ), "结构性强势必须携带硬否决标记"


def test_decision_regime_matches_breadth_regime_source():
    """_snapshot_regime 也可经 breadth.regime 来源解析，decision_regime 应跟随。"""
    snapshot = {
        "sample_count": 100,
        "up_count": 45,
        "limit_up_count": 0,
        "limit_down_count": 0,
        "breadth": {"regime": "震荡"},
    }
    cards = [_card("A")]
    result = apply_pool_rules(
        cards, output_count=6, score_line=72, market_snapshot=snapshot
    )
    assert result[0]["score_line_meta"]["decision_regime"] == "震荡"
    assert result[0]["score_line_meta"]["regime_strong_penalty"] == 0.0
