"""端到端命令式样例策略 descriptor 分发回归。

覆盖真实命令式策略从 manifest 加载、bootstrap 绑定不可变
``StrategyDescriptor``，并由 adapter 输出与声明式策略一致的 ``Signal``。实现类
只负责 ``evaluate() -> StrategyDecision``，不暴露业务身份。
"""
from __future__ import annotations

from app.core.config import get_settings
from app.engine.imperative_strategy import DescriptorBoundImperativeStrategy, ImperativeStrategy
from app.engine.signal import SIGNAL_FIELDS
from app.engine.skill_loader import load_skill, validate_skill
from app.engine.strategy import (
    Strategy,
    StrategyContext,
    build_registry_from_skills,
    instantiate_strategy,
)
from app.skills_impl.high_position_volume_stall_warning import HighPositionVolumeStallWarning

_SKILL_ID = "high-position-volume-stall-warning"


def _skill_dir():
    return get_settings().skills_dir / _SKILL_ID


def _base_stock(**extra) -> dict:
    base: dict = {
        "code": "600000",
        "name": "测试股",
        "amount_yi": 5.0,
        "is_st": False,
        "market_sample_count": 200,
        "data_completeness_score": 100,
        "evidence_quality_score": 100,
    }
    base.update(extra)
    return base


# ══════════════════════════════════════════════════════════════════════════
# 1. load_skill(真实目录)：impl_class 正确解析
# ══════════════════════════════════════════════════════════════════════════
def test_load_skill_from_real_directory_resolves_impl_class():
    skill = load_skill(_skill_dir())

    assert skill.skill_id == _SKILL_ID
    assert skill.name == "高位放量滞涨预警"
    assert skill.impl_class == (
        "app.skills_impl.high_position_volume_stall_warning:HighPositionVolumeStallWarning"
    )
    # 命令式路径：没有 strategy.yaml，contract 应为 None。
    assert skill.contract is None
    assert skill.contract_hash is None
    assert skill.executable is True
    assert skill.agent_enabled is True
    assert skill.enabled is True
    assert validate_skill(skill) == []


# ══════════════════════════════════════════════════════════════════════════
# 2. instantiate_strategy(skill)：装配 Descriptor adapter，而不是身份自带实现类
# ══════════════════════════════════════════════════════════════════════════
def test_instantiate_strategy_wraps_behavior_only_implementation_with_descriptor():
    skill = load_skill(_skill_dir())

    strategy = instantiate_strategy(skill)
    implementation = HighPositionVolumeStallWarning()

    assert isinstance(strategy, DescriptorBoundImperativeStrategy)
    assert isinstance(strategy, Strategy)
    assert isinstance(implementation, ImperativeStrategy)
    assert not isinstance(strategy, HighPositionVolumeStallWarning)
    assert all(
        not hasattr(implementation, attribute)
        for attribute in ("strategy_id", "strategy_name", "version", "run_mode", "weight")
    )
    assert strategy.descriptor.strategy_id == _SKILL_ID
    assert strategy.descriptor.name == "高位放量滞涨预警"


# ══════════════════════════════════════════════════════════════════════════
# 3. 四个代表性 stock，对应 evaluate() 的四个业务分支
# ══════════════════════════════════════════════════════════════════════════
def test_branch_1_stall_warning_with_extreme_turnover_hard_vetoes():
    """高位 + 放量 + 滞涨 + 换手率极端 → 硬否决。"""
    skill = load_skill(_skill_dir())
    strategy = instantiate_strategy(skill)
    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    stock = _base_stock(bias_ma20=12.0, vol5_20=1.8, pct=0.3, turnover_rate=20.0)

    signal = strategy.evaluate(stock, ctx)

    assert list(signal.keys()) == list(SIGNAL_FIELDS)
    assert signal["hard_veto"] is True
    assert signal["phase"] == "高位放量滞涨-换手极端"
    assert "放量滞涨" in signal["risk_flags"]
    assert signal["score"] <= signal["score_cap"]


def test_branch_2_stall_warning_without_extreme_turnover_flags_risk_but_no_veto():
    """高位 + 放量 + 滞涨，换手率未到极端 → 低分 + 风险提示，不否决。"""
    skill = load_skill(_skill_dir())
    strategy = instantiate_strategy(skill)
    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    stock = _base_stock(bias_ma20=10.0, vol5_20=1.5, pct=0.5, turnover_rate=8.0)

    signal = strategy.evaluate(stock, ctx)

    assert signal["hard_veto"] is False
    assert signal["phase"] == "高位放量滞涨预警"
    assert "放量滞涨，警惕高位资金派发" in signal["risk_flags"]
    assert signal["score"] == 32.0


def test_branch_3_high_position_rising_in_sync_scores_moderately_high():
    """高位 + 放量，但价格同步上涨（未滞涨）→ 中等偏高分。"""
    skill = load_skill(_skill_dir())
    strategy = instantiate_strategy(skill)
    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    stock = _base_stock(bias_ma20=10.0, vol5_20=1.5, pct=4.0, turnover_rate=8.0)

    signal = strategy.evaluate(stock, ctx)

    assert signal["hard_veto"] is False
    assert signal["phase"] == "放量上涨未滞涨"
    assert signal["setup_match"] == "高位放量且价涨同步"
    assert signal["score"] == 62.0
    assert signal["risk_flags"] == []


def test_branch_4_below_high_position_threshold_yields_baseline_score():
    """未达到"高位"乖离率阈值 → 预警条件不成立，基础分。"""
    skill = load_skill(_skill_dir())
    strategy = instantiate_strategy(skill)
    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    stock = _base_stock(bias_ma20=2.0, vol5_20=1.5, pct=1.5, turnover_rate=5.0)

    signal = strategy.evaluate(stock, ctx)

    assert signal["hard_veto"] is False
    assert signal["phase"] == "未触发预警"
    assert signal["score"] == 45.0
    assert any("未达预警阈值" in item for item in signal["evidence"])


def test_universe_exclusion_still_applies_to_command_style_skill():
    """命令式策略仍由 adapter 执行 universe 校验，ST 股不进入实现计算。"""
    skill = load_skill(_skill_dir())
    strategy = instantiate_strategy(skill)
    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    stock = _base_stock(is_st=True, bias_ma20=12.0, vol5_20=1.8, pct=0.3)

    signal = strategy.evaluate(stock, ctx)

    assert signal["hard_veto"] is True
    assert signal["veto_reason"] == "不在策略股票池"
    assert signal["phase"] == "排除"


# ══════════════════════════════════════════════════════════════════════════
# 4. build_registry_from_skills([skill])：完整分发路径与直接装配一致
# ══════════════════════════════════════════════════════════════════════════
def test_run_pipeline_dispatch_via_registry_matches_direct_instantiation():
    """Registry 与直接装配必须都经 descriptor adapter，Signal 逐字段一致。"""
    skill = load_skill(_skill_dir())
    registry = build_registry_from_skills([skill])

    assert registry.ids() == (_SKILL_ID,)
    via_registry_strategy = registry.get(_SKILL_ID)
    assert isinstance(via_registry_strategy, DescriptorBoundImperativeStrategy)

    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    scenarios = [
        _base_stock(bias_ma20=12.0, vol5_20=1.8, pct=0.3, turnover_rate=20.0),
        _base_stock(bias_ma20=10.0, vol5_20=1.5, pct=0.5, turnover_rate=8.0),
        _base_stock(bias_ma20=10.0, vol5_20=1.5, pct=4.0, turnover_rate=8.0),
        _base_stock(bias_ma20=2.0, vol5_20=1.5, pct=1.5, turnover_rate=5.0),
    ]

    direct_strategy = instantiate_strategy(skill)
    for stock in scenarios:
        via_registry = via_registry_strategy.evaluate(dict(stock), ctx)
        direct = direct_strategy.evaluate(dict(stock), ctx)
        assert via_registry == direct
        assert list(via_registry.keys()) == list(SIGNAL_FIELDS)
        assert via_registry["strategy_id"] == via_registry_strategy.descriptor.strategy_id
        assert via_registry["version_id"] == via_registry_strategy.descriptor.effective_version


def test_build_registry_from_skills_alongside_declarative_skills_mixed_dispatch():
    """命令式与声明式策略经同一 registry 分发，调用方无需区分来源。"""
    imperative_skill = load_skill(_skill_dir())
    declarative_skill = load_skill(get_settings().skills_dir / "high-range-breakout-momentum")

    registry = build_registry_from_skills([imperative_skill, declarative_skill])

    assert set(registry.ids()) == {_SKILL_ID, "high-range-breakout-momentum"}

    ctx = StrategyContext(main_industries=[], skip_universe_check=True)
    stock = _base_stock(bias_ma20=10.0, vol5_20=1.5, pct=4.0, turnover_rate=8.0)

    imperative_signal = registry.get(_SKILL_ID).evaluate(dict(stock), ctx)
    declarative_signal = registry.get("high-range-breakout-momentum").evaluate(dict(stock), ctx)

    assert list(imperative_signal.keys()) == list(SIGNAL_FIELDS)
    assert list(declarative_signal.keys()) == list(SIGNAL_FIELDS)
    assert imperative_signal["strategy_id"] == _SKILL_ID
    assert declarative_signal["strategy_id"] == "high-range-breakout-momentum"
