"""Version-aware strategy detail read model for the strategy center."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.engine.strategy import build_registry_from_skills
from app.models.tables import (
    Recommendation,
    RecommendationRun,
    SkillRegistry,
    StrategyVersion,
    StrategyVersionSnapshot,
)
from app.services.registry.skills import skill_version_id, sync_skills


class StrategyVersionNotFoundError(LookupError):
    pass


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _usage_by_version(db: Session, strategy_id: str) -> dict[str, dict[str, Any]]:
    usage: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"run_count": 0, "recommendation_count": 0, "latest_run_at": None, "run_ids": []}
    )
    run_stmt = select(
        RecommendationRun.run_id,
        RecommendationRun.selected_skills_json,
        RecommendationRun.started_at,
    )
    if db.get_bind().dialect.name == "mysql":
        run_stmt = run_stmt.where(
            text(
                "JSON_SEARCH(recommendation_runs.selected_skills_json,'one',:strategy_id,"
                "NULL,'$[*].skill_id') IS NOT NULL"
            ).bindparams(strategy_id=strategy_id)
        )
    rows = db.execute(run_stmt).all()
    for run_id, selected_skills_json, started_at in rows:
        selected = _loads(selected_skills_json, [])
        if not isinstance(selected, list):
            continue
        version_id = None
        for item in selected:
            if isinstance(item, dict) and str(item.get("skill_id") or "") == strategy_id:
                version_id = str(item.get("version_id") or "") or None
                break
        if version_id is None:
            continue
        entry = usage[version_id]
        entry["run_count"] += 1
        entry["run_ids"].append(str(run_id))
        if started_at is not None and (
            entry["latest_run_at"] is None or started_at > entry["latest_run_at"]
        ):
            entry["latest_run_at"] = started_at

    all_run_ids = [run_id for entry in usage.values() for run_id in entry["run_ids"]]
    if all_run_ids:
        rec_counts: dict[str, int] = defaultdict(int)
        for run_id, skill_breakdown_json in db.execute(
            select(Recommendation.run_id, Recommendation.skill_breakdown_json)
            .where(Recommendation.run_id.in_(all_run_ids))
        ).all():
            breakdown = _loads(skill_breakdown_json, [])
            if isinstance(breakdown, list) and any(
                isinstance(item, dict) and str(item.get("strategy_id") or "") == strategy_id
                for item in breakdown
            ):
                rec_counts[str(run_id)] += 1
        for entry in usage.values():
            entry["recommendation_count"] = sum(int(rec_counts.get(run_id, 0)) for run_id in entry["run_ids"])
    return usage


def _diff_value(before: Any, after: Any, path: str, changes: list[dict[str, Any]], limit: int) -> None:
    if len(changes) >= limit or before == after:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}.{key}" if path else str(key)
            if key not in before:
                changes.append({"path": child_path, "change_type": "added", "before": None, "after": after[key]})
            elif key not in after:
                changes.append({"path": child_path, "change_type": "removed", "before": before[key], "after": None})
            else:
                _diff_value(before[key], after[key], child_path, changes, limit)
            if len(changes) >= limit:
                return
        return
    if isinstance(before, list) and isinstance(after, list):
        for index in range(max(len(before), len(after))):
            child_path = f"{path}[{index}]"
            if index >= len(before):
                changes.append({"path": child_path, "change_type": "added", "before": None, "after": after[index]})
            elif index >= len(after):
                changes.append({"path": child_path, "change_type": "removed", "before": before[index], "after": None})
            else:
                _diff_value(before[index], after[index], child_path, changes, limit)
            if len(changes) >= limit:
                return
        return
    changes.append({"path": path or "root", "change_type": "changed", "before": before, "after": after})


def _diff_projection(snapshot: dict[str, Any]) -> dict[str, Any]:
    manifest = snapshot.get("manifest") if isinstance(snapshot.get("manifest"), dict) else {}
    return {
        "declared_version": snapshot.get("declared_version"),
        "status": snapshot.get("status"),
        "signal_metadata": manifest.get("signal_metadata"),
        "contract": snapshot.get("contract"),
        "skill_hash": snapshot.get("skill_hash"),
    }


def _snapshot_diff(before: dict[str, Any], after: dict[str, Any], limit: int = 400) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    _diff_value(_diff_projection(before), _diff_projection(after), "", changes, limit)
    return changes


def get_strategy_center_detail(
    db: Session,
    strategy_id: str,
    *,
    version_id: str | None = None,
) -> dict[str, Any] | None:
    skills = sync_skills(db)
    skill = next((item for item in skills if item.skill_id == strategy_id), None)
    if skill is None:
        return None

    descriptor = build_registry_from_skills([skill]).get_descriptor(strategy_id)
    current_version_id = skill_version_id(skill, descriptor)
    registry = db.get(SkillRegistry, strategy_id)
    versions = list(
        db.scalars(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy_id)
            .order_by(StrategyVersion.created_at.desc(), StrategyVersion.version_id.desc())
        ).all()
    )
    snapshots = {
        row.version_id: row
        for row in db.scalars(
            select(StrategyVersionSnapshot).where(StrategyVersionSnapshot.strategy_id == strategy_id)
        ).all()
    }
    selected_version_id = version_id or current_version_id
    selected_version = next((row for row in versions if row.version_id == selected_version_id), None)
    if selected_version is None:
        raise StrategyVersionNotFoundError(selected_version_id)

    usage = _usage_by_version(db, strategy_id)
    timeline = []
    for row in versions:
        snapshot = snapshots.get(row.version_id)
        version_usage = usage.get(row.version_id, {})
        timeline.append(
            {
                "version_id": row.version_id,
                "declared_version": (_loads(row.manifest_json, {}).get("version") or ""),
                "yaml_hash": row.yaml_hash,
                "created_at": _iso(row.created_at),
                "snapshot_available": snapshot is not None,
                "content_hash": snapshot.content_hash if snapshot else None,
                "is_current": row.version_id == current_version_id,
                "run_count": int(version_usage.get("run_count", 0)),
                "recommendation_count": int(version_usage.get("recommendation_count", 0)),
                "latest_run_at": _iso(version_usage.get("latest_run_at")),
            }
        )

    selected_snapshot_row = snapshots.get(selected_version_id)
    selected_snapshot = _loads(selected_snapshot_row.snapshot_json, {}) if selected_snapshot_row else None
    previous_row = None
    selected_index = next(index for index, row in enumerate(versions) if row.version_id == selected_version_id)
    for candidate in versions[selected_index + 1 :]:
        if candidate.version_id in snapshots:
            previous_row = candidate
            break
    previous_snapshot = (
        _loads(snapshots[previous_row.version_id].snapshot_json, {}) if previous_row is not None else None
    )

    return {
        "strategy_id": strategy_id,
        "name": descriptor.name,
        "description": str(skill.manifest.get("description") or ""),
        "mode": descriptor.mode.value,
        "status": descriptor.status.value,
        "enabled": descriptor.enabled,
        "current_version_id": current_version_id,
        "selected_version_id": selected_version_id,
        "selected_is_current": selected_version_id == current_version_id,
        "effective_version": descriptor.effective_version,
        "registry_updated_at": _iso(registry.updated_at if registry else None),
        "snapshot": selected_snapshot,
        "snapshot_available": selected_snapshot is not None,
        "timeline": timeline,
        "diff": {
            "base_version_id": previous_row.version_id if previous_row else None,
            "target_version_id": selected_version_id,
            "changes": _snapshot_diff(previous_snapshot, selected_snapshot)
            if previous_snapshot is not None and selected_snapshot is not None
            else [],
        },
    }
