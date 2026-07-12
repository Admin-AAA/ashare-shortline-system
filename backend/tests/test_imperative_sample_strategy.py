"""端到端命令式样例策略分发测试（任务 3.5，需求 4.6）。

验证目标：「新增命令式策略 = 建 skill 目录（``manifest.json`` + ``SKILL.md``）+
写一个 ``score()``，零改动分发管线」这条设计目标的最终闭环——

    load_skill(真实目录) → instantiate_strategy(skill) / build_registry_from_skills([skill])
        → registry.get(id).evaluate(stock, ctx) → 正常产出 Signal

样例策略：``app.skills_impl.high_position_volume_stall_warning.HighPositionVolumeStallWarning``
（「高位放量滞涨预警」，业务逻辑见该模块 docstring）。对应真实 skill 目录：
``backend/skills/high-position-volume-stall-warning/``（与其它声明式技能同级，
manifest.json 声明 ``impl_class``，无 ``strategy.yaml``）。

覆盖范围：
1. ``load_skill(真实目录路径)``：``impl_class`` 正确解析、``executable is True``、
   ``contract is None``（命令式路径没有 strategy.yaml）。
2. ``instantiate_strategy(skill)``：装配出的实例是该类的实例，满足 ``Strategy`` 协议。
3. 四个代表性 stock（对应 ``score()`` 内四个业务分支）逐一调用 ``evaluate``，断言
   ``Signal`` 结构正确、值符合策略打分/否决逻辑。
4. ``build_registry_from_skills([skill])`` 走完整分发路径，与直接实例化结果逐字段
   一致——这是本任务要验证的「run pipeline 正常分发」闭环。

纯 in-process：只依赖 ``app.engine.skill_loader`` / ``app.engine.strategy`` /
``app.engine.signal`` / ``app.core.config``，不吃 DB / 网络。
"""
from __future__ import annotations

from app.core.config import get_settings
from app.engine.imperative_strategy import ImperativeStrategy
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
    # 3.4 新增语义：impl_class 存在即可执行。
    assert skill.executable is True
    assert skill.agent_enabled is True
    assert skill.enabled is True

    # validate_skill 对命令式路径（contract is None）应提前返回，无报错。
    assert validate_skill(skill) == []


# ══════════════════════════════════════════════════════════════════════════
# 2. instantiate_strategy(skill)：装配出正确的类实例
# ══════════════════════════════════════════════════════════════════════════
def test_instantiate_strategy_assembles_the_correct_class_instance():
    skill = load_skill(_skill_dir())

    strategy = instantiate_strategy(skill)

    assert isinstance(strategy, HighPositionVolumeStallWarning)
    assert isinstance(strategy, ImperativeStrategy)
    assert isinstance(strategy, Strategy)
    assert strategy.strategy_id == _SKILL_ID
    assert strategy.strategy_name == "高位放量滞涨预警"


# ══════════════════════════════════════════════════════════════════════════
# 3. 四个代表性 stock，对应 score() 的四个业务分支
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
    """命令式策略仍走模板方法的 universe 校验：ST 股应被否决，score() 不被调用。"""
    skill = load_skill(_skill_dir())
    strategy = instantiate_strategy(skill)
    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    stock = _base_stock(is_st=True, bias_ma20=12.0, vol5_20=1.8, pct=0.3)

    signal = strategy.evaluate(stock, ctx)

    assert signal["hard_veto"] is True
    assert signal["veto_reason"] == "不在策略股票池"
    assert signal["phase"] == "排除"


# ══════════════════════════════════════════════════════════════════════════
# 4. build_registry_from_skills([skill])：完整分发路径与直接实例化一致
# ══════════════════════════════════════════════════════════════════════════
def test_run_pipeline_dispatch_via_registry_matches_direct_instantiation():
    """端到端闭环：build_registry_from_skills → registry.get(id).evaluate(...) 的
    产出必须与直接 instantiate_strategy(skill).evaluate(...) 逐字段一致——这正是
    「被启用后 run pipeline 正常产出 Signal」这一验收标准的直接验证。"""
    skill = load_skill(_skill_dir())
    registry = build_registry_from_skills([skill])

    assert registry.ids() == (_SKILL_ID,)
    via_registry_strategy = registry.get(_SKILL_ID)
    assert isinstance(via_registry_strategy, HighPositionVolumeStallWarning)

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


def test_build_registry_from_skills_alongside_declarative_skills_mixed_dispatch():
    """混合场景：本命令式 skill 与真实声明式 skill（从磁盘加载）共同经
    build_registry_from_skills 分发，二者互不干扰、各自产出合法 Signal——验证
    aggregator/调用方无需区分策略来源（需求 4.4/4.6 的联合闭环）。"""
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
