from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.skill_loader import StrategySkill
from app.services.registry.diagnostics import (
    RegistryDiagnosticsError,
    UnknownRegistryValueError,
    build_registry_diagnostics,
)


def _skill(
    skill_id: str,
    *,
    groups: tuple[str, ...] = ("formal_opening",),
    status: str = "active",
    executable: bool = True,
) -> StrategySkill:
    return StrategySkill(
        skill_id=skill_id,
        name=skill_id,
        version="1.0.0",
        path=Path(f"/tmp/{skill_id}"),
        manifest={
            "id": skill_id,
            "name": skill_id,
            "version": "1.0.0",
            "mode": "declarative",
            "status": status,
            "requirements": [],
            "capabilities": [],
            "enabled": status == "active",
        },
        skill_frontmatter={},
        skill_body="",
        skill_hash=None,
        reference_files=[],
        contract={
            "id": skill_id,
            "name": skill_id,
            "contract": {"executable": executable},
            "meta": {"default_preset_groups": {group: True for group in groups}},
        },
        contract_hash=None,
        enabled=status == "active",
    )


_CANONICAL = ({"preset_id": "preset_formal_opening", "skill_groups": ["formal_opening"]},)


def test_registry_diagnostics_reports_registration_failures_without_repairing_rows():
    skills = [_skill("alpha"), _skill("alpha"), _skill("inactive", groups=("missing_group",), status="disabled")]
    presets = [
        {"preset_id": "preset_formal_opening", "status": "active", "skills": [{"strategy_id": "ghost"}]},
        {"preset_id": "preset_formal_opening", "status": "active", "skills": []},
    ]

    report = build_registry_diagnostics(skills, presets, canonical_presets=_CANONICAL)
    codes = {issue.code for issue in report.errors}

    assert "duplicate_strategy_id" in codes
    assert "duplicate_preset_id" in codes
    assert "preset_references_unknown_strategy" in codes
    assert any(item.code == "inactive_strategy_unknown_declared_group" for item in report.informational)
    assert report.strategy_presets["alpha"] == ()
    assert report.to_dict()["available_preset_ids"] == ["preset_formal_opening"]
    assert all(item.available_values == tuple(sorted(item.available_values)) for item in report.errors)


def test_formal_preflight_checks_target_closure_but_ignores_unrelated_experiment_orphans():
    skills = [
        _skill("opening-a"),
        _skill("opening-b"),
        _skill("experiment-orphan", groups=("experiment_only",)),
    ]
    presets = [
        {
            "preset_id": "preset_formal_opening",
            "status": "active",
            "skills": [
                {"strategy_id": "opening-a"},
                {"strategy_id": "opening-b"},
            ],
        }
    ]
    report = build_registry_diagnostics(skills, presets, canonical_presets=_CANONICAL)

    # The experiment-only unknown group and global unassigned diagnostic must not
    # make a complete formal-opening closure fail.
    report.require_executable("preset_formal_opening")
    assert "experiment-orphan" in report.unassigned_strategy_ids
    assert report.strategy_presets["opening-a"] == ("preset_formal_opening",)

    incomplete = build_registry_diagnostics(
        skills,
        [{"preset_id": "preset_formal_opening", "status": "active", "skills": [{"strategy_id": "opening-a"}]}],
        canonical_presets=_CANONICAL,
    )
    with pytest.raises(RegistryDiagnosticsError) as caught:
        incomplete.require_executable("preset_formal_opening")
    assert any(item.code == "active_strategy_not_in_target_preset" for item in caught.value.report.errors)


def test_unknown_registry_values_expose_sorted_available_values():
    report = build_registry_diagnostics(
        [_skill("zeta"), _skill("alpha")],
        [{"preset_id": "preset_formal_opening", "skills": [{"strategy_id": "alpha"}]}],
        canonical_presets=_CANONICAL,
    )

    with pytest.raises(UnknownRegistryValueError) as strategy_error:
        report.require_strategy("missing")
    assert strategy_error.value.available_values == ("alpha", "zeta")

    with pytest.raises(UnknownRegistryValueError) as preset_error:
        report.require_preset("missing")
    assert preset_error.value.available_values == ("preset_formal_opening",)

    with pytest.raises(UnknownRegistryValueError) as group_error:
        report.require_group("missing")
    assert group_error.value.available_values == ("formal_opening",)
