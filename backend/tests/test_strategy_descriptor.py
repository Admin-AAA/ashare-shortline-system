"""S2 StrategyDescriptor integrity, identity, and compatibility tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.engine.skill_loader import StrategySkill, load_skills
from app.engine.strategy import (
    DeclarativeStrategy,
    StrategyContext,
    StrategyError,
    StrategyIdentityError,
    StrategyMode,
    StrategyRegistryFrozenError,
    StrategyStatus,
    StrategyUnavailableError,
    build_registry_from_skills,
    build_strategy_descriptor,
)

_UNSET = object()


def _skill(
    strategy_id: str = "descriptor-strategy",
    *,
    name: str = "Descriptor 测试策略",
    manifest: dict | None = None,
    contract: dict | None | object = _UNSET,
) -> StrategySkill:
    manifest_extra = dict(manifest or {})
    imperative = bool(manifest_extra.get("impl_class"))
    status = str(manifest_extra.get("status") or "active")
    manifest_value = {
        "id": strategy_id,
        "name": name,
        "version": "1.2.3",
        "mode": "imperative" if imperative else "declarative",
        "status": status,
        "requirements": ["stock_feature_context"],
        "capabilities": ["signal_evaluation"],
        "enabled": status == "active",
        **manifest_extra,
    }
    contract_value = (
        {"id": strategy_id, "name": name, "scoring": {"base": 50, "rules": []}}
        if contract is _UNSET
        else contract
    )
    return StrategySkill(
        skill_id=strategy_id,
        name=name,
        version="1.2.3",
        path=Path(f"/nonexistent/{strategy_id}"),
        manifest=manifest_value,
        skill_frontmatter={},
        skill_body="descriptor fixture",
        skill_hash="skill-content-hash",
        reference_files=[],
        contract=contract_value,
        contract_hash="contract-content-hash" if contract_value is not None else None,
        enabled=bool(manifest_value["enabled"]),
    )


def test_declarative_registry_uses_immutable_descriptor_and_preserves_signal_behavior():
    skill = _skill()
    registry = build_registry_from_skills([skill])
    descriptor, strategy = registry.resolve(skill.skill_id)

    assert descriptor.strategy_id == skill.skill_id
    assert descriptor.name == descriptor.display_name == skill.name
    assert descriptor.mode is StrategyMode.DECLARATIVE
    assert descriptor.status is StrategyStatus.ACTIVE
    assert descriptor.declared_version == "1.2.3"
    assert descriptor.requirements == ("stock_feature_context",)
    assert descriptor.capabilities == ("signal_evaluation",)
    assert len(descriptor.manifest_hash) == len(descriptor.implementation_hash) == 64
    assert descriptor.effective_version == descriptor.effective_fingerprint
    assert isinstance(strategy, DeclarativeStrategy)
    assert strategy.descriptor is descriptor

    projection = descriptor.to_projection()
    assert projection["strategy_id"] == projection["skill_id"] == skill.skill_id
    assert projection["display_name"] == skill.name
    assert projection["enabled"] is True

    signal = strategy.evaluate(
        {"code": "600000", "amount_yi": 5.0, "market_sample_count": 200,
         "data_completeness_score": 100, "evidence_quality_score": 100},
        ctx=StrategyContext(),
    )
    assert signal["strategy_id"] == descriptor.strategy_id
    assert signal["version_id"] == descriptor.effective_version
    skill.contract["scoring"]["base"] = 99
    frozen_signal = strategy.evaluate(
        {"code": "600000", "amount_yi": 5.0, "market_sample_count": 200,
         "data_completeness_score": 100, "evidence_quality_score": 100},
        ctx=StrategyContext(),
    )
    assert frozen_signal["score"] == signal["score"] == 50.0
    with pytest.raises(FrozenInstanceError):
        descriptor.name = "不可变"  # type: ignore[misc]
    with pytest.raises(StrategyRegistryFrozenError, match="已冻结"):
        registry.unregister(skill.skill_id)


def test_manifest_or_contract_changes_change_effective_strategy_version():
    original = _skill()
    changed_contract = _skill(contract={"id": "descriptor-strategy", "name": "Descriptor 测试策略", "scoring": {"base": 51, "rules": []}})
    changed_manifest = _skill(manifest={"capabilities": ["signal_evaluation", "research_backtest"]})

    original_descriptor = build_strategy_descriptor(original)
    contract_descriptor = build_strategy_descriptor(changed_contract)
    manifest_descriptor = build_strategy_descriptor(changed_manifest)

    assert contract_descriptor.manifest_hash != original_descriptor.manifest_hash
    assert contract_descriptor.effective_version != original_descriptor.effective_version
    assert manifest_descriptor.manifest_hash != original_descriptor.manifest_hash
    assert manifest_descriptor.effective_version != original_descriptor.effective_version
    assert original_descriptor.implementation_hash == contract_descriptor.implementation_hash


def test_bootstrap_rejects_declarative_manifest_contract_identity_mismatch():
    bad_skill = _skill(contract={"id": "different-strategy-id", "name": "Descriptor 测试策略", "scoring": {"base": 50, "rules": []}})
    with pytest.raises(StrategyIdentityError, match="strategy.yaml.id"):
        build_registry_from_skills([bad_skill])


def test_imperative_registry_injects_manifest_identity_into_signal():
    skill = _skill(
        strategy_id="manifest-owned-id",
        name="manifest-owned-name",
        manifest={"impl_class": "_impl_class_fixtures:FixtureImperativeStrategy"},
        contract=None,
    )
    registry = build_registry_from_skills([skill])
    descriptor, strategy = registry.resolve("manifest-owned-id")
    signal = strategy.evaluate({"code": "600000"}, StrategyContext())

    assert descriptor.mode is StrategyMode.IMPERATIVE
    assert signal["strategy_id"] == "manifest-owned-id"
    assert signal["strategy_name"] == "manifest-owned-name"
    assert signal["version_id"] == descriptor.effective_version


def test_bootstrap_rejects_imperative_identity_declared_outside_manifest():
    skill = _skill(
        strategy_id="manifest-owned-id",
        manifest={"impl_class": "_impl_class_fixtures:MismatchedIdentityStrategy"},
        contract=None,
    )
    with pytest.raises(StrategyIdentityError, match="strategy_id='implementation-owned-id'"):
        build_registry_from_skills([skill])


def test_disabled_descriptor_is_projected_but_rejected_for_new_resolution():
    skill = _skill(manifest={"status": "disabled", "enabled": False})
    registry = build_registry_from_skills([skill])
    descriptor = registry.get_descriptor(skill.skill_id)

    assert descriptor.status is StrategyStatus.DISABLED
    assert descriptor.enabled is False
    assert descriptor.to_projection()["enabled"] is False
    with pytest.raises(StrategyUnavailableError, match="disabled"):
        registry.resolve(skill.skill_id)
    assert registry.resolve(skill.skill_id, allow_unavailable=True)[0] is descriptor


def test_real_manifests_supply_complete_descriptor_contracts():
    skills = load_skills(get_settings().skills_dir)
    registry = build_registry_from_skills(skills)

    assert registry.is_frozen is True
    assert len(registry.list_descriptors()) == len([skill for skill in skills if skill.executable])
    for descriptor in registry.list_descriptors():
        assert descriptor.strategy_id
        assert descriptor.display_name
        assert descriptor.requirements
        assert descriptor.capabilities
        assert descriptor.status in StrategyStatus
        assert len(descriptor.manifest_hash) == 64
        assert len(descriptor.implementation_hash) == 64
        assert len(descriptor.effective_version) == 64


def test_registry_rejects_a_standalone_string_key():
    skill = _skill()
    registry = build_registry_from_skills([])
    with pytest.raises(StrategyError, match="StrategyDescriptor"):
        registry.register(skill.skill_id, DeclarativeStrategy(skill))  # type: ignore[arg-type]
