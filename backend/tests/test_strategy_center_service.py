from __future__ import annotations

import json
from pathlib import Path

from app.engine.skill_loader import StrategySkill
from app.engine.strategy import build_strategy_descriptor
from app.services.registry.skills import _strategy_snapshot
from app.services.strategy_center_service import _snapshot_diff


def _skill(contract: dict) -> StrategySkill:
    return StrategySkill(
        skill_id="versioned-strategy",
        name="版本策略",
        version="1.0.0",
        path=Path("/nonexistent/versioned-strategy"),
        manifest={
            "id": "versioned-strategy",
            "name": "版本策略",
            "version": "1.0.0",
            "mode": "declarative",
            "status": "active",
            "requirements": ["stock_feature_context"],
            "capabilities": ["signal_evaluation"],
            "enabled": True,
            "description": "测试策略",
        },
        skill_frontmatter={"name": "版本策略"},
        skill_body="按规则执行。",
        skill_hash="abc123",
        reference_files=[],
        contract=contract,
        contract_hash="def456",
        enabled=True,
    )


def test_strategy_snapshot_is_self_contained_and_deterministic():
    skill = _skill({"id": "versioned-strategy", "name": "版本策略", "scoring": {"base": 40}})
    descriptor = build_strategy_descriptor(skill)

    first_json, first_hash = _strategy_snapshot(skill, descriptor)
    second_json, second_hash = _strategy_snapshot(skill, descriptor)
    payload = json.loads(first_json)

    assert first_json == second_json
    assert first_hash == second_hash
    assert payload["contract"]["scoring"]["base"] == 40
    assert payload["skill_body"] == "按规则执行。"
    assert payload["effective_version"] == descriptor.effective_version


def test_snapshot_diff_reports_added_removed_and_changed_paths():
    before = {
        "declared_version": "1.0.0",
        "status": "active",
        "manifest": {"signal_metadata": {"weight": 1.0}},
        "contract": {
            "universe": {"include": ["amount_yi >= 2"], "exclude": ["is_st == true"]},
            "scoring": {"base": 40},
        },
        "skill_hash": "old",
    }
    after = {
        "declared_version": "1.1.0",
        "status": "active",
        "manifest": {"signal_metadata": {"weight": 1.1}},
        "contract": {
            "universe": {"include": ["amount_yi >= 3"], "exclude": []},
            "scoring": {"base": 45},
        },
        "skill_hash": "new",
    }

    changes = _snapshot_diff(before, after)
    paths = {change["path"] for change in changes}

    assert "declared_version" in paths
    assert "signal_metadata.weight" in paths
    assert "contract.universe.include[0]" in paths
    assert "contract.universe.exclude[0]" in paths
    assert "contract.scoring.base" in paths
    assert "skill_hash" in paths
