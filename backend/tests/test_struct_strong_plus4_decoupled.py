"""结构性强势 regime 的 veto 与 +4 提线解耦测试（2026-08-02 review 跟进）。

用户决策：默认关闭 structural_strong_hard_veto / oscillation_hard_veto。
review 发现：+4 提线原埋在 veto 分支内，veto 关闭后结构性强势日会丢失
该门槛。修复：+4 与 veto 解耦——结构性强势日始终 +4 提线，veto 开启时
才 999 全拦。本测试锁定该行为。
"""
from app.engine.aggregator import apply_pool_rules
from app.engine.production_policy import DEFAULT_PRODUCTION_SELECTION_POLICY


def _struct_strong_snapshot() -> dict:
    return {
        "sample_count": 100,
        "up_count": 70,
        "limit_up_count": 0,
        "limit_down_count": 0,
        "breadth": {"regime": "结构性强势", "risk": "中"},
        "market_regime": "结构性强势",
        "market_sentiment": "平淡",
    }


def _card(score: float = 90.0) -> dict:
    return {
        "code": "300001",
        "bucket": "chen-xiaoqun-skill",
        "final_score": score,
        "evidence_quality_score": 92.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
    }


def test_struct_strong_keeps_plus4_when_veto_off():
    """默认（veto=False）：结构性强势日保留 +4 提线，但不再 999 全拦。"""
    cards = [_card(90.0)]
    apply_pool_rules(
        cards,
        score_line=72.0,
        market_snapshot=_struct_strong_snapshot(),
        selection_policy=DEFAULT_PRODUCTION_SELECTION_POLICY,
        display_limit=6,
    )
    meta = cards[0].get("score_line_meta") or {}
    # +4 提线保留（72 基础 + 强牛 +6 + 结构性强势 +4 = 82）
    assert meta.get("effective_score_line") == 82.0, meta
    # 无 veto 标记、不 999
    assert meta.get("regime_struct_strong_hard_veto") is False, meta
    assert not cards[0].get("policy_drop_owner"), cards[0]


def test_struct_strong_veto_on_still_999():
    """patch 开启 veto：结构性强势日 999 全拦（归因链路仍可用）。"""
    cards = [_card(90.0)]
    veto_policy = DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch(
        "structural_strong_hard_veto", True
    )
    apply_pool_rules(
        cards,
        score_line=72.0,
        market_snapshot=_struct_strong_snapshot(),
        selection_policy=veto_policy,
        display_limit=6,
    )
    meta = cards[0].get("score_line_meta") or {}
    assert meta.get("effective_score_line") == 999.0, meta
    assert meta.get("regime_struct_strong_hard_veto") is True, meta
    # policy_drop_owner 由 evaluate_selection_policy 层落卡（另有测试覆盖），
    # 此处锁定 apply_pool_rules 的 999 线 + veto 标记行为。
