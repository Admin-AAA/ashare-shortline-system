from __future__ import annotations

from pathlib import Path

from app.engine.skill_loader import load_skill, validate_skill


SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "xiaoshi-quant-expert"


def test_xiaoshi_quant_skill_is_pinned_and_starts_disabled() -> None:
    skill = load_skill(SKILL_DIR)

    assert validate_skill(skill) == []
    assert skill.skill_id == "xiaoshi-quant-expert"
    assert skill.status == "disabled"
    assert skill.enabled is False
    assert skill.manifest["source_ref"] == "2026-08-14.1"
    assert skill.manifest["upstream_sha256"] == (
        "a6683e89da671b78ec04456f12b18a463cf9874707f81ee26f65c64ca043b1f7"
    )
    assert skill.contract is not None
    assert skill.contract["enabled"] is False
    assert skill.contract["meta"]["lifecycle_stage"] == "research"
    assert "formal_opening" not in skill.contract["meta"].get("default_preset_groups", {})
