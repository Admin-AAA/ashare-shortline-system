"""L-013 回归：决策时 regime 标签必须持久化进 score_line_meta。

背景：下游 regime 否决 / χ² 分桶 / 最差桶归因 一律应以「决策时实际施加的 regime」
为准。旧记录允许读取逐笔 market_context_json（它保存了送入 apply_pool_rules 的同一份
snapshot）；禁止读可能被事后重建的 feature_snapshot_json.market_regime。

本测试锁定 aggregator 在构建 score_line_meta 时显式落：
  - decision_regime      = 聚合时实际使用的 _snapshot_regime
  - regime_strong_penalty = 结构性强势 +4 / 强势 +8 / 其他 0.0

2026-08-02 用户决策适配：结构性强势/震荡 hard_veto 默认关闭——
结构性强势仅 +4 提线（不再 999 全拦）、震荡走普通线；veto 开启时的
回归行为由 test_aggregator_struct_strong_veto.py / test_struct_strong_plus4_decoupled.py
用 with_rule_patch 显式覆盖。
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
        ("结构性强势", 4.0, False),  # 2026-08-02：veto 默认关闭，仅 +4 提线
        ("强势", 8.0, False),
        ("震荡", 0.0, False),  # 2026-08-02：veto 默认关闭，走普通线
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
        # 结构性强势默认关闭 veto：无 hard_veto 标记（开启时的标记由
        # test_aggregator_struct_strong_veto.py 显式 patch 覆盖）
        if regime == "结构性强势":
            assert (
                meta.get("regime_struct_strong_hard_veto", False) is False
            ), "veto 默认关闭，结构性强势不应携带硬否决标记"


def test_decision_regime_matches_breadth_regime_source():
    """_snapshot_regime 也可经 breadth.regime 来源解析，decision_regime 应跟随。"""
    snapshot = {
        "sample_count": 100,
        "up_count": 45,
        "limit_up_count": 0,
        "limit_down_count": 0,
        "breadth": {"regime": "弱势"},
    }
    cards = [_card("A")]
    result = apply_pool_rules(
        cards, output_count=6, score_line=72, market_snapshot=snapshot
    )
    assert result[0]["score_line_meta"]["decision_regime"] == "弱势"
    assert result[0]["score_line_meta"]["regime_strong_penalty"] == 0.0
