"""命令式策略行为协议与 descriptor adapter 回归测试。

**Validates: Requirements 4.2, 4.3, 4.5**
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from app.core.config import get_settings
from app.engine.imperative_strategy import (
    DescriptorBoundImperativeStrategy,
    ImperativeStrategy,
    ImperativeStrategyProtocolError,
    StrategyDecision,
)
from app.engine.signal import SIGNAL_FIELDS
from app.engine.skill_loader import StrategySkill, load_skill
from app.engine.strategy import StrategyContext, StrategyIdentityError, build_strategy_descriptor, instantiate_strategy
from app.skills_impl.high_position_volume_stall_warning import HighPositionVolumeStallWarning


class _DecisionOnlyStrategy:
    """No identity or metadata: only a decision and the optional universe hook."""

    universe: ClassVar[dict[str, Any]] = {
        "include": ["amount_yi >= 1"],
        "exclude": ["is_st == true"],
    }

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, stock: dict[str, Any], ctx: StrategyContext) -> StrategyDecision:
        self.calls += 1
        return StrategyDecision(
            score=70.0,
            phase="放量上涨",
            score_cap=95.0,
            risk_flags=["示例风险提示"],
            setup_match="示例买点",
            entry_rule="示例入场规则",
            invalid_rule="示例失效规则",
            setup_quality=80.0,
            evidence=["示例证据"],
        )


class _ReturnsSignalInsteadOfDecision:
    def evaluate(self, stock: dict[str, Any], ctx: StrategyContext) -> dict[str, Any]:
        return {"score": 100}


def _skill(*, strategy_id: str = "manifest-owned-id", manifest: dict[str, Any] | None = None) -> StrategySkill:
    extra = dict(manifest or {})
    manifest_value = {
        "id": strategy_id,
        "name": "manifest-owned-name",
        "version": "1.2.3",
        "mode": "imperative",
        "status": "active",
        "requirements": ["stock_feature_context"],
        "capabilities": ["signal_evaluation"],
        "enabled": True,
        "impl_class": "_impl_class_fixtures:FixtureImperativeStrategy",
        "signal_metadata": {"weight": 1.5, "strategy_independence": 0.8},
        **extra,
    }
    return StrategySkill(
        skill_id=strategy_id,
        name="manifest-owned-name",
        version="1.2.3",
        path=Path(f"/nonexistent/{strategy_id}"),
        manifest=manifest_value,
        skill_frontmatter={},
        skill_body="fixture",
        skill_hash="fixture-skill-hash",
        reference_files=[],
        contract=None,
        contract_hash=None,
        enabled=True,
    )


def _stock(**extra: Any) -> dict[str, Any]:
    stock: dict[str, Any] = {
        "code": "600000",
        "name": "测试股",
        "amount_yi": 5.0,
        "is_st": False,
        "market_sample_count": 200,
        "data_completeness_score": 100.0,
        "evidence_quality_score": 100.0,
    }
    stock.update(extra)
    return stock


def test_behavior_only_implementation_satisfies_imperative_protocol():
    implementation = _DecisionOnlyStrategy()

    assert isinstance(implementation, ImperativeStrategy)
    assert all(not hasattr(implementation, attribute) for attribute in (
        "strategy_id", "strategy_name", "version", "mode", "weight", "strategy_independence",
    ))


def test_descriptor_adapter_owns_signal_identity_version_and_declared_metadata():
    implementation = _DecisionOnlyStrategy()
    descriptor = build_strategy_descriptor(_skill(), implementation)
    strategy = DescriptorBoundImperativeStrategy(implementation, descriptor)

    signal = strategy.evaluate(_stock(), StrategyContext())

    assert list(signal) == list(SIGNAL_FIELDS)
    assert signal["strategy_id"] == descriptor.strategy_id
    assert signal["strategy_name"] == descriptor.name
    assert signal["version_id"] == descriptor.effective_version
    assert signal["weight"] == 1.5
    assert signal["strategy_independence"] == 0.8
    assert signal["phase"] == "放量上涨"
    assert signal["score"] == 70.0
    assert signal["score_cap"] == 95.0


def test_descriptor_adapter_preserves_universe_short_circuit_and_evidence_cap():
    implementation = _DecisionOnlyStrategy()
    strategy = DescriptorBoundImperativeStrategy(
        implementation,
        build_strategy_descriptor(_skill(), implementation),
    )

    vetoed = strategy.evaluate(_stock(amount_yi=0.5), StrategyContext())
    capped = strategy.evaluate(_stock(market_sample_count=10), StrategyContext())

    assert vetoed["hard_veto"] is True
    assert vetoed["veto_reason"] == "不在策略股票池"
    assert implementation.calls == 1
    assert capped["score"] == capped["score_cap"] == 68.0
    assert "样本或证据覆盖不足，评分已降权" in capped["risk_flags"]
    assert "诚实性上限: 68.0" in capped["evidence"]


def test_adapter_rejects_non_decision_return_with_stable_message():
    implementation = _ReturnsSignalInsteadOfDecision()
    strategy = DescriptorBoundImperativeStrategy(
        implementation,
        build_strategy_descriptor(_skill(), implementation),
    )

    with pytest.raises(ImperativeStrategyProtocolError, match="必须返回 StrategyDecision"):
        strategy.evaluate(_stock(), StrategyContext())


@pytest.mark.parametrize(
    ("impl_class", "expected_declaration"),
    [
        ("_impl_class_fixtures:MismatchedIdentityStrategy", "strategy_id='implementation-owned-id'"),
        ("_impl_class_fixtures:MetadataDeclaringStrategy", "weight=2.0"),
        ("_impl_class_fixtures:RunModeDeclaringStrategy", "run_mode='formal'"),
    ],
)
def test_bootstrap_rejects_duplicate_identity_and_metadata_declarations(
    impl_class: str,
    expected_declaration: str,
):
    with pytest.raises(StrategyIdentityError, match=expected_declaration):
        instantiate_strategy(_skill(manifest={"impl_class": impl_class}))


def test_bootstrap_rejects_legacy_prefilter_hook_and_incompatible_evaluate_signature():
    from app.engine.strategy import StrategyAssemblyError

    with pytest.raises(StrategyAssemblyError, match="仅可声明可选 universe hook"):
        instantiate_strategy(
            _skill(manifest={"impl_class": "_impl_class_fixtures:PrefilterDeclaringStrategy"})
        )
    with pytest.raises(StrategyAssemblyError, match="evaluate 必须能以 \\(stock, ctx\\) 两个位置参数调用"):
        instantiate_strategy(
            _skill(manifest={"impl_class": "_impl_class_fixtures:WrongEvaluateSignatureStrategy"})
        )


def test_real_imperative_strategy_regression_uses_manifest_descriptor_and_signal_contract():
    skill = load_skill(get_settings().skills_dir / "high-position-volume-stall-warning")
    strategy = instantiate_strategy(skill)

    assert isinstance(HighPositionVolumeStallWarning(), ImperativeStrategy)
    signal = strategy.evaluate(
        _stock(bias_ma20=10.0, vol5_20=1.5, pct=0.5, turnover_rate=10.0),
        StrategyContext(),
    )

    assert list(signal) == list(SIGNAL_FIELDS)
    assert signal["strategy_id"] == skill.skill_id
    assert signal["strategy_name"] == skill.name
    assert signal["version_id"] == strategy.descriptor.effective_version
    assert signal["weight"] == 0.3
    assert signal["strategy_independence"] == 0.8
    assert signal["phase"] == "高位放量滞涨预警"
    assert signal["hard_veto"] is False
    assert signal["score"] == 32.0
