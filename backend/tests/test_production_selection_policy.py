from __future__ import annotations

from app.engine.production_policy import (
    DEFAULT_PRODUCTION_SELECTION_POLICY,
    ProductionSelectionPolicy,
    SelectionPolicyContext,
    evaluate_selection_policy,
)
from app.services.runs.filter_audit import build_filter_audit


def _card(code: str, score: float = 90.0, *, bucket: str = "chen-xiaoqun-skill") -> dict:
    return {
        "code": code,
        "bucket": bucket,
        "final_score": score,
        "evidence_quality_score": 92.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
    }


def test_default_policy_covers_main_board_and_20cm_markets() -> None:
    assert DEFAULT_PRODUCTION_SELECTION_POLICY.board_universe is None
    cards = [
        _card("300001"),
        _card("600001", 99.0),
        _card("300002", 98.0, bucket="daily-stock-analysis-skill"),
    ]

    result = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(
            score_line=72.0,
            output_count=6,
            market_snapshot={
                "sample_count": 100,
                "up_count": 60,
                "limit_up_count": 0,
                "limit_down_count": 0,
                "market_regime": "结构性强势",
            },
        ),
    )

    assert result.policy_fingerprint == DEFAULT_PRODUCTION_SELECTION_POLICY.fingerprint
    verdicts = {item.code: item for item in result.verdicts}
    assert verdicts["600001"].drop_owner is None
    assert verdicts["300002"].drop_owner is None
    # 2026-08-02 用户决策：默认不保留结构性强势 hard_veto，高分候选按线通过
    assert verdicts["300001"].drop_owner is None
    assert [card["code"] for card in result.recommendations] == [
        "600001",
        "300002",
        "300001",
    ]
    assert all(item.steps for item in result.verdicts)


def test_policy_constructor_defaults_to_all_a_share_boards() -> None:
    assert ProductionSelectionPolicy().board_universe is None


def test_daily_top10_policy_counts_recommendations_from_earlier_same_day_runs() -> None:
    cards = [_card(f"300{index:03d}", 90.0) for index in range(12, 0, -1)]

    result = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(
            score_line=72.0,
            output_count=20,
            market_snapshot={
                "sample_count": 100,
                "up_count": 50,
                "limit_up_count": 0,
                "limit_down_count": 0,
            },
            same_day_recommendation_count=3,
        ),
    )

    assert [card["code"] for card in result.recommendations] == [
        "300001",
        "300002",
        "300003",
        "300004",
        "300005",
        "300006",
        "300007",
    ]
    assert sum(item.drop_owner == "daily_top_n" for item in result.verdicts) == 5


def test_one_rule_patch_changes_only_structural_veto_and_keeps_same_input_cards() -> None:
    champion_cards = [_card("300001")]
    challenger_cards = [_card("300001")]
    snapshot = {
        "sample_count": 100,
        "up_count": 60,
        "limit_up_count": 0,
        "limit_down_count": 0,
        "market_regime": "结构性强势",
    }
    context = SelectionPolicyContext(
        score_line=72.0,
        output_count=6,
        market_snapshot=snapshot,
    )

    # 2026-08-02 用户决策：默认关闭 hard_veto → 默认 policy 产出；
    # patch 开启 veto 后全拦（验证单规则 patch 只影响 veto 开关）。
    champion = evaluate_selection_policy(champion_cards, context)
    challenger_policy = DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch(
        "structural_strong_hard_veto", True
    )
    challenger = evaluate_selection_policy(
        challenger_cards, context, challenger_policy
    )

    assert [card["code"] for card in champion.recommendations] == ["300001"]
    assert challenger.recommendations == ()
    assert champion.policy_fingerprint != challenger.policy_fingerprint


def test_filter_audit_names_policy_drop_owner_instead_of_missing_metadata() -> None:
    cards = [_card("600001", 99.0)]
    board_limited_policy = DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch(
        "board_universe", "20cm"
    )
    result = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(
            score_line=72.0,
            output_count=6,
            market_snapshot={"sample_count": 100, "market_regime": "弱势"},
        ),
        board_limited_policy,
    )

    audit = build_filter_audit(
        prefilter_audit={},
        skill_gate_audit={},
        scored_cards=cards,
        recommended_cards=list(result.recommendations),
        pipeline={},
    )

    score_row = audit["rows"][0]["score_line"]
    assert score_row["policy_drop_owner"] == "board_universe"
    assert score_row["reason"] == "production_policy: board_universe"
    assert audit["empty_reason"].startswith("production_policy_blocked:")


def test_regime_hard_veto_is_persisted_as_policy_drop_owner() -> None:
    cards = [_card("300001", 99.0)]
    # 2026-08-02 用户决策：默认关闭震荡 hard_veto；归因代码仍须可用，
    # 用 with_rule_patch 开启后验证 drop_owner 归因链路。
    # 基线固定 v4：DEFAULT 已是 v5（L-117 共识闸门），它也会在震荡剔掉单背书合成卡，
    # 会污染本条"归因链路"断言；两条轴分别锁定。
    from app.engine.production_policy import load_production_selection_policy

    policy = load_production_selection_policy(version=4).with_rule_patch(
        "oscillation_hard_veto", True
    )
    result = evaluate_selection_policy(
        cards,
        SelectionPolicyContext(
            score_line=72.0,
            output_count=6,
            market_snapshot={
                "sample_count": 100,
                "market_regime": "震荡",
            },
        ),
        policy,
    )
    audit = build_filter_audit(
        prefilter_audit={},
        skill_gate_audit={},
        scored_cards=cards,
        recommended_cards=list(result.recommendations),
        pipeline={},
    )
    assert cards[0]["policy_drop_owner"] == "oscillation_hard_veto"
    assert audit["empty_reason"].startswith("production_policy_blocked:")


def test_regime_sentiment_line_overrides_precision() -> None:
    """组合线精细放行，其余组合继续保持抬线（v2：非强牛高潮组合硬排除）。

    防止 regime_sentiment_line_overrides 被误改导致负期望组合(偏多/震荡/弱势·高潮)被放行。
    依据双窗口因果重放（L-070）：唯有强牛·高潮两窗口为正，其余高潮组合两窗口皆负。
    """
    from app.engine.aggregator import resolve_score_line_context

    def _snap(up: int, lu: int, ld: int) -> dict:
        return {
            "sample_count": 5000, "up_count": up, "down_count": 5000 - up,
            "limit_up_count": lu, "limit_down_count": ld,
        }

    from app.engine.production_policy import load_production_selection_policy
    # 本测试钉住 v2（L-070）组合线契约；v3 原样继承这些 override，另加 L-093 ivol 否决。
    policy = load_production_selection_policy(version=2)
    assert policy.version == 2

    # 强牛·高潮: up 76% + 涨停95 → override 64（唯一保留的高潮放行）
    c = resolve_score_line_context(72.0, market_snapshot=_snap(3800, 95, 3), selection_policy=policy)
    assert (c["market_base"], c["market_sentiment"]) == ("强牛", "高潮")
    assert c["effective_score_line"] == 64.0 and c["regime_sentiment_override_applied"] is True
    # 偏多·高潮: v2 硬排除到 120（最高分 100 不可达），override 生效
    c2 = resolve_score_line_context(72.0, market_snapshot=_snap(2600, 95, 3), selection_policy=policy)
    assert (c2["market_base"], c2["market_sentiment"]) == ("偏多", "高潮")
    assert c2["effective_score_line"] == 120.0 and c2["regime_sentiment_override_applied"] is True
    # 弱势·高潮: v2 同样硬排除到 120（不再受 weak_regime_score_cap=60 放行）
    c3 = resolve_score_line_context(72.0, market_snapshot=_snap(1500, 95, 3), selection_policy=policy)
    assert c3["market_base"] == "弱势" and c3["effective_score_line"] == 120.0
    # 震荡·高潮: v2 硬排除到 120（up 40% + 涨停95）
    c4 = resolve_score_line_context(72.0, market_snapshot=_snap(2000, 95, 3), selection_policy=policy)
    assert (c4["market_base"], c4["market_sentiment"]) == ("震荡", "高潮")
    assert c4["effective_score_line"] == 120.0


def test_v1_policy_artifact_remains_reproducible() -> None:
    """v1 artifact 不可变：显式加载仍得到历史阈值，保证旧回测可复现。"""
    from app.engine.production_policy import load_production_selection_policy
    from app.engine.aggregator import resolve_score_line_context

    def _snap(up: int, lu: int, ld: int) -> dict:
        return {"sample_count": 5000, "up_count": up, "down_count": 5000 - up,
                "limit_up_count": lu, "limit_down_count": ld}

    v1 = load_production_selection_policy(version=1)
    assert v1.version == 1
    # v1：偏多·高潮抬线 85（非硬排除）、弱势·高潮被 weak cap 压到 60
    duo_climax = resolve_score_line_context(72.0, _snap(2600, 95, 3), selection_policy=v1)
    assert duo_climax["effective_score_line"] == 85.0
    weak_climax = resolve_score_line_context(72.0, _snap(1500, 95, 3), selection_policy=v1)
    assert weak_climax["effective_score_line"] == 60.0


def test_normal_active_market_overrides_restore_output_without_relaxing_weak_market() -> None:
    """正常市场放行规则票；弱势规则保持原样，不靠减少出票改善回测。"""
    from app.engine.aggregator import resolve_score_line_context

    def _snap(up: int, lu: int, ld: int) -> dict:
        return {
            "sample_count": 5000,
            "up_count": up,
            "down_count": 5000 - up,
            "limit_up_count": lu,
            "limit_down_count": ld,
        }

    policy = DEFAULT_PRODUCTION_SELECTION_POLICY
    strong_active = resolve_score_line_context(
        72.0,
        market_snapshot=_snap(3800, 50, 3),
        selection_policy=policy,
    )
    strong_climax = resolve_score_line_context(
        72.0,
        market_snapshot=_snap(3800, 95, 3),
        selection_policy=policy,
    )
    bullish_active = resolve_score_line_context(
        72.0,
        market_snapshot=_snap(2600, 50, 3),
        selection_policy=policy,
    )
    weak_active = resolve_score_line_context(
        72.0,
        market_snapshot=_snap(1500, 50, 3),
        selection_policy=policy,
    )

    # v2 相对 v1 的唯一变化是硬排除偏多/震荡/弱势·高潮；活跃档沿用 L-060 精细放行：
    # 强牛·活跃 64、强牛·高潮 64、偏多·活跃 71.4；弱势·活跃仍受 weak cap=60 放行。
    assert strong_active["effective_score_line"] == 64.0
    assert strong_climax["effective_score_line"] == 64.0
    assert bullish_active["effective_score_line"] == 71.4
    assert weak_active["effective_score_line"] == 60.0


# ---------------------------------------------------------------------------
# 通用横截面尾部否决（extra_tail_veto_*，L-095）：默认关闭、artifact 声明式开启，
# 与 L-093 的 strong_high_ivol_veto 同构但因子/方向/阈值参数化。
# ---------------------------------------------------------------------------


def _strong_snapshot() -> dict:
    """up 76% + 涨停 50 → _market_base = 强牛。"""
    return {
        "sample_count": 5000,
        "up_count": 3800,
        "down_count": 1200,
        "limit_up_count": 50,
        "limit_down_count": 3,
    }


def _oscillation_snapshot() -> dict:
    """up 40% → _market_base = 震荡。"""
    return {
        "sample_count": 5000,
        "up_count": 2000,
        "down_count": 3000,
        "limit_up_count": 50,
        "limit_down_count": 3,
    }


def _tail_policy(**overrides: object):
    """在 v4 artifact 上只打开 extra_tail_veto，其余规则不动。

    基线固定 v4 而非 DEFAULT：DEFAULT 已升到 v5（L-117 共识闸门会在弱势/震荡剔掉
    "只有 1 个策略背书"的合成卡），而本组用例锁定的是 extra_tail_veto 的 regime 条件
    与缺字段保守放行，两条轴不应互相污染。
    """
    from app.engine.production_policy import load_production_selection_policy

    policy = load_production_selection_policy(version=4).with_rule_patch(
        "extra_tail_veto_enabled", True
    )
    for rule_id, value in overrides.items():
        policy = policy.with_rule_patch(rule_id, value)
    return policy


def _tail_cards() -> list[dict]:
    """3 张卡：中位分数递减，分位 .20/.90/.10 —— 中间那张落在高尾。

    每次调用都返回全新对象：evaluate_selection_policy 会就地写回
    policy_drop_owner / policy_verdict_steps，复用同一批 dict 会让上一臂的剔除
    标记泄漏到下一臂（生产回测咽喉因此显式剥离这两个键，见 L-071/L-095）。
    """
    return [
        {**_card("600001", 99.0), "extra_tail_veto_pct": 0.20},
        {**_card("600002", 98.0), "extra_tail_veto_pct": 0.90},
        {**_card("600003", 97.0), "extra_tail_veto_pct": 0.10},
    ]


def test_extra_tail_veto_is_disabled_by_default_and_absent_from_frozen_artifacts() -> None:
    """默认策略不得启用 extra_tail_veto；且 v1-v4 artifact 不得含该键（保持不可变）。"""
    import json
    from pathlib import Path

    from app.engine.production_policy import load_production_selection_policy

    assert DEFAULT_PRODUCTION_SELECTION_POLICY.extra_tail_veto_enabled is False
    # 当前默认 = v7（L-119：强牛·高潮输出上限收到 2）；v1-v6 是 frozen artifact，语义必须逐位不变
    assert DEFAULT_PRODUCTION_SELECTION_POLICY.version == 7
    for version in (1, 2, 3, 4, 5, 6):
        policy = load_production_selection_policy(version=version)
        assert policy.extra_tail_veto_enabled is False
        # v5 引入共识闸门；v6 引入强牛·高潮输出上限 → 各版本只允许拥有自己那一代的新键
        assert policy.consensus_min_signals == (2 if version >= 5 else 0)
        assert policy.strong_climax_top_n == (3 if version == 6 else 0)
        payload = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "app"
                / "policies"
                / f"production_selection.v{version}.json"
            ).read_text(encoding="utf-8")
        )
        assert not any(key.startswith("extra_tail_veto") for key in payload)
        if version < 5:
            assert not any(key.startswith("consensus_") for key in payload)
        if version < 6:
            assert not any(key.startswith("strong_climax") for key in payload)


def test_extra_tail_veto_high_tail_drops_card_and_backfills_next_ranked() -> None:
    """direction=high：分位落高尾的卡被硬剔，其后按 final_score 的下一名自然递补。

    出票硬上限由 daily_output_limit 控制（output_count 只影响软底线），这里把它压到 2
    以便真正观察"剔一张 → 补一张"的递补行为。
    """
    context = SelectionPolicyContext(
        score_line=72.0, output_count=2, market_snapshot=_strong_snapshot()
    )
    base_policy = DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch("daily_output_limit", 2)
    base = evaluate_selection_policy(_tail_cards(), context, base_policy)
    assert [card["code"] for card in base.recommendations] == ["600001", "600002"]

    policy = _tail_policy(
        daily_output_limit=2,
        extra_tail_veto_direction="high",
        extra_tail_veto_pct_threshold=0.80,
        extra_tail_veto_regimes=("强牛",),
    )
    # evaluate_selection_policy 会就地写回 policy_drop_owner，故每臂都用全新的卡对象。
    treat = evaluate_selection_policy(_tail_cards(), context, policy)

    # 600002 被剔后，原本排第 3 的 600003 递补进 2 个名额。
    assert [card["code"] for card in treat.recommendations] == ["600001", "600003"]
    verdicts = {item.code: item for item in treat.verdicts}
    assert verdicts["600002"].drop_owner == "extra_tail_veto"
    assert verdicts["600003"].drop_owner is None
    assert any(step.rule_id == "extra_tail_veto" for step in verdicts["600002"].steps)


def test_extra_tail_veto_low_tail_direction_drops_low_pct_card() -> None:
    """direction=low：分位落低尾的卡被剔（镜像方向）。"""
    cards = [
        {**_card("600001", 99.0), "extra_tail_veto_pct": 0.50},
        {**_card("600002", 98.0), "extra_tail_veto_pct": 0.05},  # 低尾 → 剔
        {**_card("600003", 97.0), "extra_tail_veto_pct": 0.60},
    ]
    policy = _tail_policy(
        extra_tail_veto_direction="low",
        extra_tail_veto_pct_threshold=0.20,
        extra_tail_veto_regimes=("强牛",),
    )
    result = evaluate_selection_policy(
        list(cards),
        SelectionPolicyContext(
            score_line=72.0, output_count=2, market_snapshot=_strong_snapshot()
        ),
        policy,
    )

    assert [card["code"] for card in result.recommendations] == ["600001", "600003"]
    verdicts = {item.code: item for item in result.verdicts}
    assert verdicts["600002"].drop_owner == "extra_tail_veto"


def test_extra_tail_veto_respects_regime_gate() -> None:
    """声明 regimes=('强牛',) 时，震荡日不得触发（regime 条件是承重设计）。"""
    cards = [
        {**_card("600001", 99.0), "extra_tail_veto_pct": 0.20},
        {**_card("600002", 98.0), "extra_tail_veto_pct": 0.90},
    ]
    policy = _tail_policy(extra_tail_veto_regimes=("强牛",))
    result = evaluate_selection_policy(
        list(cards),
        SelectionPolicyContext(
            score_line=72.0, output_count=2, market_snapshot=_oscillation_snapshot()
        ),
        policy,
    )

    assert [card["code"] for card in result.recommendations] == ["600001", "600002"]
    assert all(item.drop_owner != "extra_tail_veto" for item in result.verdicts)


def test_extra_tail_veto_missing_percentile_is_conservative_pass() -> None:
    """缺分位字段（数据不全）的卡保守放行，保证旧版本行为逐位可复现。"""
    cards = [_card("600001", 99.0), _card("600002", 98.0)]
    policy = _tail_policy(extra_tail_veto_regimes=("强牛",))
    result = evaluate_selection_policy(
        list(cards),
        SelectionPolicyContext(
            score_line=72.0, output_count=2, market_snapshot=_strong_snapshot()
        ),
        policy,
    )

    assert [card["code"] for card in result.recommendations] == ["600001", "600002"]
    assert all(item.drop_owner != "extra_tail_veto" for item in result.verdicts)


def test_extra_tail_veto_regimes_patch_stays_hashable_tuple() -> None:
    """regimes 必须被规范成 tuple，否则 fingerprint 不可哈希。"""
    policy = _tail_policy(extra_tail_veto_regimes=["强牛", "偏多"])
    assert policy.extra_tail_veto_regimes == ("强牛", "偏多")
    assert isinstance(policy.fingerprint, str) and len(policy.fingerprint) == 64


def test_default_policy_fingerprint_is_not_pinned_to_legacy_value() -> None:
    """v3 规则行为未变：默认策略仍启用 L-093 ivol 否决，且 v3 未被改成 v4。"""
    assert DEFAULT_PRODUCTION_SELECTION_POLICY.strong_high_ivol_veto_enabled is True
    assert DEFAULT_PRODUCTION_SELECTION_POLICY.strong_high_ivol_regimes == ("强牛",)
    assert DEFAULT_PRODUCTION_SELECTION_POLICY.strong_high_ivol_pct_threshold == 0.80
