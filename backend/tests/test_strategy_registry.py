"""Descriptor-only StrategyRegistry regression tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.imperative_strategy import DescriptorBoundImperativeStrategy, StrategyDecision
from app.engine.signal import SIGNAL_FIELDS
from app.engine.skill_loader import StrategySkill, validate_skill
from app.engine.strategy import (
    DeclarativeStrategy,
    DuplicateStrategyError,
    Strategy,
    StrategyContext,
    StrategyError,
    StrategyNotFoundError,
    StrategyRegistry,
    build_registry_from_skills,
    build_strategy_descriptor,
)
from app.engine.strategy_runner import run_strategy

_CONTRACT: dict = {
    "id": "registry-equiv-strategy",
    "name": "注册表等价测试策略",
    "weight": 1.1,
    "meta": {"strategy_independence": 0.88},
    "universe": {"include": ["amount_yi >= 1"], "exclude": ["is_st == true"]},
    "prefilter": {"market": {"exclude": ["avg_cost > 200"]}},
    "phases": [{"when": "pct >= 5", "name": "上涨", "score_cap": 90, "hard_veto": False, "risk_flags": ["涨幅偏高"]}],
    "scoring": {"base": 50, "score_fields": [{"field": "pct", "multiplier": 2.0, "cap": 20, "reason": "涨幅贡献"}], "rules": [{"when": "vol5_20 >= 1", "delta": 10, "reason": "量比放大"}]},
    "setups": [{"when": "breakout20 == true", "name": "突破买点", "score_delta": 6, "entry_mode": "next_open", "entry_rule": "突破确认后进场", "invalid_rule": "跌破 MA20"}],
    "risk": {"rules": [{"when": "bias_ma20 >= 15", "flag": "乖离率偏高", "level": "high"}]},
    "require_setup": False,
}


def _make_skill(skill_id: str = "registry-equiv-strategy", contract: dict | None = None) -> StrategySkill:
    contract_value = dict(contract or _CONTRACT)
    contract_value["id"] = skill_id
    name = str(contract_value.get("name", skill_id))
    return StrategySkill(
        skill_id=skill_id,
        name=name,
        version="1.0.0",
        path=Path(f"/nonexistent/{skill_id}"),
        manifest={"id": skill_id, "name": name, "version": "1.0.0", "mode": "declarative", "status": "active", "requirements": ["stock_feature_context"], "capabilities": ["signal_evaluation"], "enabled": True},
        skill_frontmatter={}, skill_body="", skill_hash=None, reference_files=[],
        contract=contract_value, contract_hash="fedcba9876543210fedcba9876543210", enabled=True,
    )


def _make_imperative_skill(skill_id: str = "registry-imperative-strategy") -> StrategySkill:
    return StrategySkill(
        skill_id=skill_id,
        name=skill_id,
        version="1.0.0",
        path=Path(f"/nonexistent/{skill_id}"),
        manifest={
            "id": skill_id,
            "name": skill_id,
            "version": "1.0.0",
            "mode": "imperative",
            "status": "active",
            "requirements": ["stock_feature_context"],
            "capabilities": ["signal_evaluation"],
            "enabled": True,
            "impl_class": "tests.fixtures:ImperativeStrategy",
        },
        skill_frontmatter={},
        skill_body="",
        skill_hash=None,
        reference_files=[],
        contract=None,
        contract_hash=None,
        enabled=True,
    )


def _full_quality(**extra) -> dict:
    base = {"code": "600000", "name": "测试股", "market_sample_count": 200, "data_completeness_score": 100, "evidence_quality_score": 100}
    base.update(extra)
    return base


class _StubStrategy:
    def __init__(self, tag: str, descriptor) -> None:  # noqa: ANN001 - local test double
        self.tag = tag
        self.descriptor = descriptor

    def evaluate(self, stock, ctx):  # noqa: ANN001 - local test double
        return {"tag": self.tag}


class _ImperativeImplementation:
    def evaluate(self, stock, ctx):  # noqa: ANN001 - local test double
        return StrategyDecision(score=50.0, phase="fixture")


def _register_stub(registry: StrategyRegistry, strategy_id: str, tag: str) -> _StubStrategy:
    descriptor = build_strategy_descriptor(_make_skill(strategy_id))
    stub = _StubStrategy(tag, descriptor)
    assert registry.register(descriptor, stub) is stub
    return stub


def test_register_and_get_roundtrip_uses_descriptor_key():
    registry = StrategyRegistry()
    stub = _register_stub(registry, "skill-a", "a")
    assert registry.get("skill-a") is stub
    assert registry.get_descriptor("skill-a") is stub.descriptor
    assert registry.ids() == ("skill-a",)


def test_register_rejects_standalone_string_key_and_duplicate_descriptor_id():
    registry = StrategyRegistry()
    with pytest.raises(StrategyError, match="StrategyDescriptor"):
        registry.register("skill-a", object())  # type: ignore[arg-type]
    _register_stub(registry, "skill-a", "a")
    with pytest.raises(DuplicateStrategyError):
        _register_stub(registry, "skill-a", "b")


def test_register_requires_descriptor_bound_adapter_for_imperative_strategies():
    descriptor = build_strategy_descriptor(_make_imperative_skill())
    registry = StrategyRegistry()

    legacy_signal_strategy = _StubStrategy("legacy-signal", descriptor)
    with pytest.raises(StrategyError, match="DescriptorBoundImperativeStrategy"):
        registry.register(descriptor, legacy_signal_strategy)

    other_descriptor = build_strategy_descriptor(_make_imperative_skill())
    mismatched_adapter = DescriptorBoundImperativeStrategy(_ImperativeImplementation(), other_descriptor)
    with pytest.raises(StrategyError, match="同一 Descriptor 对象"):
        registry.register(descriptor, mismatched_adapter)

    bound_adapter = DescriptorBoundImperativeStrategy(_ImperativeImplementation(), descriptor)
    assert registry.register(descriptor, bound_adapter) is bound_adapter


def test_get_unknown_strategy_id_raises():
    with pytest.raises(StrategyNotFoundError):
        StrategyRegistry().get("does-not-exist")


def test_list_and_unregister_preserve_descriptor_registration_order():
    registry = StrategyRegistry()
    stubs = [_register_stub(registry, key, key[-1]) for key in ("skill-a", "skill-b", "skill-c")]
    assert registry.list_strategies() == stubs
    assert [item.strategy_id for item in registry.list_descriptors()] == ["skill-a", "skill-b", "skill-c"]
    registry.unregister("skill-a")
    assert "skill-a" not in registry
    registry.unregister("skill-a")


def test_declarative_strategy_direct_use_preserves_canonical_signal_contract():
    skill = _make_skill()
    strategy = DeclarativeStrategy(skill)
    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    stock = _full_quality(amount_yi=5, is_st=False, avg_cost=50, pct=8, vol5_20=1.5, breakout20=True, bias_ma20=20)
    direct = run_strategy(skill, dict(stock), main_industries=[], skip_universe_check=False)
    via_strategy = strategy.evaluate(dict(stock), ctx)
    assert via_strategy == direct
    assert list(via_strategy.keys()) == list(direct.keys()) == list(SIGNAL_FIELDS)
    assert via_strategy["entry_mode"] == "next_open"
    assert isinstance(strategy, Strategy)


def test_declarative_setup_rejects_unknown_entry_mode():
    contract = {**_CONTRACT, "setups": [{**_CONTRACT["setups"][0], "entry_mode": "guess_open"}]}
    errors = validate_skill(_make_skill(contract=contract))

    assert any("entry_mode must be one of" in error for error in errors)


def test_build_registry_from_skills_binds_every_signal_to_descriptor_version():
    skills = [_make_skill(skill_id="skill-1"), _make_skill(skill_id="skill-2")]
    registry = build_registry_from_skills(skills)
    assert registry.ids() == ("skill-1", "skill-2")
    stock = _full_quality(amount_yi=5, is_st=False, avg_cost=50, pct=8, vol5_20=1.5, breakout20=True, bias_ma20=5)
    for skill in skills:
        descriptor, strategy = registry.resolve(skill.skill_id)
        assert isinstance(strategy, DeclarativeStrategy)
        signal = strategy.evaluate(dict(stock), StrategyContext(main_industries=["半导体"]))
        assert signal["strategy_id"] == descriptor.strategy_id
        assert signal["version_id"] == descriptor.effective_version


def test_empty_bootstrap_produces_a_frozen_empty_registry():
    registry = build_registry_from_skills([])
    assert registry.is_frozen
    assert registry.ids() == ()
