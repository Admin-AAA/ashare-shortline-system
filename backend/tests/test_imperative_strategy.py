"""``ImperativeStrategy`` 模板方法基类单测（任务 3.3，需求 4.3）。

覆盖范围：
1. universe 否决路径：门槛不过 → 提前返回 ``hard_veto=True`` 的 Signal，子类
   ``score(...)`` 不会被调用。
2. 正常打分路径：子类 ``score(...)`` 被调用，返回值被基类正确组装进 ``Signal``，
   键集合 == ``SIGNAL_FIELDS``。
3. evidence 覆盖度降权路径：低样本量触发 ``_evidence_score_cap`` 收紧，score_cap
   被下调，且追加对应的风险提示 / 证据说明（与声明式 ``run_strategy`` 的
   coverage_cap 降权路径逐字段对齐同一套阈值逻辑）。
4. ``ImperativeStrategy`` 具体子类满足 ``Strategy`` 协议（``isinstance`` 为真）。

纯 in-process：只依赖 ``app.engine.imperative_strategy`` / ``app.engine.strategy`` /
``app.engine.signal``，不吃 DB / 网络。
"""
from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.engine.imperative_strategy import ImperativeScoreResult, ImperativeStrategy
from app.engine.signal import SIGNAL_FIELDS, Signal
from app.engine.strategy import Strategy, StrategyContext


# ══════════════════════════════════════════════════════════════════════════
# Dummy 子类
# ══════════════════════════════════════════════════════════════════════════
class _FixedScoreStrategy(ImperativeStrategy):
    """最小 dummy 子类：universe 门槛 + 固定打分逻辑，便于覆盖三条路径。"""

    strategy_id: ClassVar[str] = "dummy-imperative"
    strategy_name: ClassVar[str] = "假人命令式策略"
    version: ClassVar[str] = "1.0.0"
    weight: ClassVar[float] = 1.5
    strategy_independence: ClassVar[float] = 0.8

    universe: ClassVar[dict[str, Any]] = {
        "include": ["amount_yi >= 1"],
        "exclude": ["is_st == true"],
    }

    def score(self, stock: dict[str, Any], ctx: StrategyContext) -> ImperativeScoreResult:
        return ImperativeScoreResult(
            score=70.0,
            phase="放量上涨",
            score_cap=95.0,
            hard_veto=False,
            veto_reason="",
            risk_flags=["示例风险提示"],
            setup_match="示例买点",
            entry_rule="示例入场规则",
            invalid_rule="示例失效规则",
            setup_quality=80.0,
            evidence=["示例证据"],
        )


class _VetoingStrategy(ImperativeStrategy):
    """dummy 子类：score() 自身判定否决（如「观察」类信号），无 universe 门槛。"""

    strategy_id: ClassVar[str] = "dummy-vetoing"
    strategy_name: ClassVar[str] = "假人观察否决策略"

    def score(self, stock: dict[str, Any], ctx: StrategyContext) -> ImperativeScoreResult:
        return ImperativeScoreResult(
            score=40.0,
            phase="观察",
            hard_veto=True,
            veto_reason="观察类信号，不建议入场",
        )


def _stock(**extra) -> dict[str, Any]:
    base: dict[str, Any] = {
        "code": "600000",
        "name": "测试股",
        "market_sample_count": 200,
        "data_completeness_score": 100,
        "evidence_quality_score": 100,
    }
    base.update(extra)
    return base


# ══════════════════════════════════════════════════════════════════════════
# 1. universe 否决路径
# ══════════════════════════════════════════════════════════════════════════
def test_universe_veto_short_circuits_before_score_hook_is_called():
    calls: list[Any] = []

    class _TrackingStrategy(_FixedScoreStrategy):
        def score(self, stock, ctx):  # noqa: ANN001
            calls.append(stock)
            return super().score(stock, ctx)

    strategy = _TrackingStrategy()
    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    # amount_yi=0.5 未过 include 门槛 → universe 否决。
    stock = _stock(amount_yi=0.5)

    signal = strategy.evaluate(stock, ctx)

    assert signal["hard_veto"] is True
    assert signal["veto_reason"] == "不在策略股票池"
    assert signal["phase"] == "排除"
    assert signal["score"] == 0
    assert signal["risk_flags"] == ["不符合股票池"]
    assert calls == [], "universe 否决路径下，score() 钩子不应被调用"


def test_universe_veto_can_be_skipped_via_context():
    strategy = _FixedScoreStrategy()
    ctx = StrategyContext(main_industries=[], skip_universe_check=True)
    # amount_yi=0.5 本应被 universe 拒绝，但 skip_universe_check=True 时应跳过校验。
    stock = _stock(amount_yi=0.5)

    signal = strategy.evaluate(stock, ctx)

    assert signal["hard_veto"] is False
    assert signal["phase"] == "放量上涨"


# ══════════════════════════════════════════════════════════════════════════
# 2. 正常打分路径：score() 返回值被正确组装进 Signal
# ══════════════════════════════════════════════════════════════════════════
def test_normal_scoring_path_assembles_signal_from_score_hook():
    strategy = _FixedScoreStrategy()
    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    stock = _stock(amount_yi=5, is_st=False)

    signal = strategy.evaluate(stock, ctx)

    # 键集合与顺序均对齐规范 Signal 契约。
    assert list(signal.keys()) == list(SIGNAL_FIELDS)
    assert type(signal) is dict

    assert signal["strategy_id"] == "dummy-imperative"
    assert signal["strategy_name"] == "假人命令式策略"
    assert signal["version_id"] == "dummy-imperative:1.0.0:imperative"
    assert signal["strategy_independence"] == 0.8
    assert signal["weight"] == 1.5
    assert signal["phase"] == "放量上涨"
    assert signal["score"] == 70.0
    assert signal["score_cap"] == 95.0
    assert signal["confidence"] == round(min(0.95, max(0.2, 70.0 / 100)), 2)
    assert signal["setup_match"] == "示例买点"
    assert signal["entry_rule"] == "示例入场规则"
    assert signal["invalid_rule"] == "示例失效规则"
    assert signal["setup_quality"] == 80.0
    assert signal["hard_veto"] is False
    assert signal["veto_reason"] == ""
    assert signal["risk_flags"] == ["示例风险提示"]
    assert signal["evidence"] == ["示例证据"]


def test_score_hook_self_determined_veto_is_preserved():
    strategy = _VetoingStrategy()
    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    stock = _stock()

    signal = strategy.evaluate(stock, ctx)

    assert signal["hard_veto"] is True
    assert signal["veto_reason"] == "观察类信号，不建议入场"
    assert signal["phase"] == "观察"
    assert signal["score"] == 40.0
    # 未声明的字段使用 ImperativeScoreResult 默认值。
    assert signal["setup_match"] == ""
    assert signal["risk_flags"] == []


# ══════════════════════════════════════════════════════════════════════════
# 3. evidence 覆盖度降权路径
# ══════════════════════════════════════════════════════════════════════════
def test_low_sample_count_tightens_score_cap_and_appends_evidence():
    strategy = _FixedScoreStrategy()
    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    # market_sample_count < 20 → _evidence_score_cap 收紧为 68.0（对齐声明式阈值）。
    stock = _stock(amount_yi=5, is_st=False, market_sample_count=10)

    signal = strategy.evaluate(stock, ctx)

    assert signal["score_cap"] == 68.0
    assert signal["score"] == 68.0  # min(子类原始 70.0, min(95.0, 68.0))
    assert "样本或证据覆盖不足，评分已降权" in signal["risk_flags"]
    assert "诚实性上限: 68.0" in signal["evidence"]


def test_full_evidence_quality_does_not_trigger_downgrade():
    strategy = _FixedScoreStrategy()
    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    stock = _stock(amount_yi=5, is_st=False, market_sample_count=200)

    signal = strategy.evaluate(stock, ctx)

    assert signal["score_cap"] == 95.0
    assert "样本或证据覆盖不足，评分已降权" not in signal["risk_flags"]


# ══════════════════════════════════════════════════════════════════════════
# 4. Strategy 协议
# ══════════════════════════════════════════════════════════════════════════
def test_imperative_strategy_subclass_satisfies_strategy_protocol():
    strategy = _FixedScoreStrategy()
    assert isinstance(strategy, Strategy)


def test_evaluate_cannot_be_overridden_by_subclass():
    """``evaluate`` 标记为 ``@final``：子类若尝试覆写，静态检查工具（mypy）应报错；
    运行时 Python 不强制拦截，这里改为断言 ``evaluate`` 就是基类定义的同一个函数
    对象，作为「未被子类意外覆写」的回归防线。"""
    assert _FixedScoreStrategy.evaluate is ImperativeStrategy.evaluate
    assert _VetoingStrategy.evaluate is ImperativeStrategy.evaluate


def test_imperative_strategy_cannot_be_instantiated_directly():
    """未实现 score() 的抽象基类不可直接实例化。"""
    with pytest.raises(TypeError):
        ImperativeStrategy()  # type: ignore[abstract]
