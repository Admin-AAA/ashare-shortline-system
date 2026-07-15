"""Manifest-backed imperative implementation assembly tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.engine.imperative_strategy import ImperativeStrategy
from app.engine.skill_loader import SkillManifestError, StrategySkill, load_skill, validate_skill
from app.engine.strategy import DeclarativeStrategy, Strategy, StrategyAssemblyError, StrategyContext, build_registry_from_skills, instantiate_strategy


def _make_skill(skill_id: str = "some-skill", *, manifest: dict[str, Any] | None = None, contract: dict[str, Any] | None = None, path: Path | None = None) -> StrategySkill:
    extra = dict(manifest or {})
    imperative = bool(extra.get("impl_class"))
    mode = "imperative" if imperative else "declarative"
    values = {"id": skill_id, "name": skill_id, "version": "1.0.0", "mode": mode, "status": "active", "requirements": ["stock_feature_context"], "capabilities": ["signal_evaluation"], "enabled": True, **extra}
    return StrategySkill(skill_id=skill_id, name=skill_id, version="1.0.0", path=path or Path(f"/nonexistent/{skill_id}"), manifest=values, skill_frontmatter={}, skill_body="fixture", skill_hash=None, reference_files=[], contract=contract, contract_hash=None, enabled=bool(values["enabled"]))


def _write_command_skill_dir(tmp_path: Path, *, skill_id: str, impl_class: str | None) -> Path:
    skill_dir = tmp_path / skill_id
    skill_dir.mkdir(parents=True)
    manifest: dict[str, Any] = {"id": skill_id, "name": skill_id, "version": "1.0.0", "mode": "imperative", "status": "active", "requirements": ["stock_feature_context"], "capabilities": ["signal_evaluation"], "enabled": True}
    if impl_class is not None:
        manifest["impl_class"] = impl_class
    (skill_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(f"# {skill_id}\n\n命令式策略。", encoding="utf-8")
    return skill_dir


def test_impl_class_property_reads_from_manifest():
    assert _make_skill(manifest={"impl_class": "pkg.module:MyStrategy"}).impl_class == "pkg.module:MyStrategy"


def test_instantiate_strategy_with_valid_impl_class_injects_descriptor_identity():
    skill = _make_skill(skill_id="fixture-skill", manifest={"impl_class": "_impl_class_fixtures:FixtureImperativeStrategy"})
    strategy = instantiate_strategy(skill)
    assert isinstance(strategy, ImperativeStrategy)
    assert isinstance(strategy, Strategy)
    assert strategy.descriptor.strategy_id == "fixture-skill"
    assert strategy.descriptor.name == "fixture-skill"
    signal = strategy.evaluate({"code": "600000"}, StrategyContext(skip_universe_check=True))
    assert signal["strategy_id"] == strategy.descriptor.strategy_id
    assert signal["strategy_name"] == strategy.descriptor.name
    assert signal["phase"] == "fixture-phase"
    assert signal["version_id"] == strategy.descriptor.effective_version


@pytest.mark.parametrize("impl_class, expected", [
    ("_impl_class_fixtures:NotAStrategyAtAll", "Strategy 协议"),
    ("app.engine.this_module_does_not_exist:Whatever", "无法 import 模块"),
    ("_impl_class_fixtures:ThisClassDoesNotExist", "找不到类"),
    ("no-colon-in-here", "格式不合法"),
    ("_impl_class_fixtures:RaisesOnInit", "实例化"),
])
def test_instantiate_strategy_reports_invalid_impl_class(impl_class: str, expected: str):
    skill = _make_skill(skill_id="bad-skill", manifest={"impl_class": impl_class})
    with pytest.raises(StrategyAssemblyError, match=expected):
        instantiate_strategy(skill)


def test_declarative_path_is_still_bound_to_a_descriptor():
    contract = {"id": "declarative-skill", "name": "declarative-skill", "scoring": {"base": 50, "rules": []}}
    strategy = instantiate_strategy(_make_skill(skill_id="declarative-skill", contract=contract))
    assert isinstance(strategy, DeclarativeStrategy)
    assert strategy.descriptor.strategy_id == "declarative-skill"


def test_build_registry_dispatches_mixed_manifest_modes():
    declarative = _make_skill(skill_id="decl", contract={"id": "decl", "name": "decl", "scoring": {"base": 50, "rules": []}})
    imperative = _make_skill(skill_id="impl", manifest={"impl_class": "_impl_class_fixtures:FixtureImperativeStrategy"})
    registry = build_registry_from_skills([declarative, imperative])
    assert isinstance(registry.get("decl"), DeclarativeStrategy)
    assert isinstance(registry.get("impl"), ImperativeStrategy)


def test_load_skill_rejects_missing_canonical_manifest_fields(tmp_path: Path):
    skill_dir = tmp_path / "invalid"
    skill_dir.mkdir()
    (skill_dir / "manifest.json").write_text(json.dumps({"id": "invalid", "name": "invalid", "version": "1.0.0", "enabled": True}), encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("# invalid", encoding="utf-8")
    with pytest.raises(SkillManifestError, match="manifest.mode"):
        load_skill(skill_dir)


def test_load_skill_command_style_directory_requires_and_projects_manifest_contract(tmp_path: Path):
    skill = load_skill(_write_command_skill_dir(tmp_path, skill_id="cmd-skill", impl_class="_impl_class_fixtures:FixtureImperativeStrategy"))
    assert skill.skill_id == "cmd-skill"
    assert skill.mode == "imperative"
    assert skill.requirements == ("stock_feature_context",)
    assert skill.capabilities == ("signal_evaluation",)
    assert skill.contract is None
    assert skill.executable is True
    assert validate_skill(skill) == []
    assert isinstance(instantiate_strategy(skill), ImperativeStrategy)


def test_load_skill_rejects_status_enabled_drift(tmp_path: Path):
    skill_dir = _write_command_skill_dir(tmp_path, skill_id="drift", impl_class="_impl_class_fixtures:FixtureImperativeStrategy")
    manifest_path = skill_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "disabled"
    manifest["enabled"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SkillManifestError, match="兼容投影"):
        load_skill(skill_dir)
