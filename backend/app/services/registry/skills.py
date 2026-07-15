"""Skill 注册表 CRUD 深模块。

任务 7.4（需求 6.1）落地：把此前散落在 `workbench_service.py` 的 Skill 同步/
增删查改（`sync_skills` / `list_skills` / `get_skill_detail` / `set_skill_enabled` /
`skill_performance_summary` / `batch_skill_performance_summaries` /
`backtest_skill` / `install_skill`）收口到本模块，编排层（`workbench_service.py`）
只保留对外名字的 re-export，方便 `app/api/*` 等既有调用点零改动。

模块状态（skill 同步缓存 TTL / 锁）也一并下沉，避免编排层继续持有这部分状态。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.skill_loader import StrategySkill, load_skills, validate_skill
from app.models.tables import Preset, PresetSkill, SkillRegistry, SkillVersion, StrategyVersion
from app.services.runs.bootstrap_cache import invalidate_bootstrap_cache


class PathLike(str):
    """把字符串路径包一层 `joinpath`，兼容既有代码里对 `installed_path` 的用法。"""

    def joinpath(self, *parts: str):
        return Path(self).joinpath(*parts)


_last_skill_sync_time: float = 0.0
_skill_sync_ttl: float = 5.0  # seconds — avoid redundant disk I/O on concurrent requests
_cached_skill_list: list[StrategySkill] | None = None
_cached_skill_cache_key: tuple[str, str] | None = None
_skill_sync_lock = threading.Lock()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _is_skill_cache_fresh(now: float, cache_key: tuple[str, str]) -> bool:
    return (
        _cached_skill_list is not None
        and _cached_skill_cache_key == cache_key
        and (now - _last_skill_sync_time) < _skill_sync_ttl
    )


def _sync_registry_row(
    registry: SkillRegistry,
    skill: StrategySkill,
    descriptor: "StrategyDescriptor",
    manifest_json: str,
) -> bool:
    """Refresh the database cache strictly as a descriptor projection."""
    changed = False
    updates = {
        "name": descriptor.name,
        "source_type": skill.manifest.get("source_type", registry.source_type),
        "source_url": skill.manifest.get("source_url", registry.source_url),
        "installed_path": str(skill.path),
        "current_version": descriptor.declared_version,
        "mode": descriptor.mode.value,
        "enabled": descriptor.enabled,
        "has_executable_contract": skill.executable,
        "manifest_json": manifest_json,
    }
    for field, value in updates.items():
        if getattr(registry, field) != value:
            setattr(registry, field, value)
            changed = True
    if changed:
        registry.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return changed


def _strategy_backtest_adapter_manifest(skill: StrategySkill) -> dict[str, Any] | None:
    adapter = ((skill.contract or {}).get("meta") or {}).get("backtest_adapter")
    if not isinstance(adapter, dict):
        return None
    actions = adapter.get("actions") or {}
    if not isinstance(actions, dict):
        actions = {}
    return {
        "default_action": str(adapter.get("default_action") or "run"),
        "actions": sorted(str(action) for action in actions if str(action).strip()),
    }


def _descriptor_map(skills: list[StrategySkill]) -> dict[str, "StrategyDescriptor"]:
    # This invokes the same official bootstrap used by execution; it also verifies
    # every impl_class before the registry cache can advertise a strategy version.
    from app.engine.strategy import build_registry_from_skills

    registry = build_registry_from_skills(skills)
    return {descriptor.strategy_id: descriptor for descriptor in registry.list_descriptors()}


def sync_skills(db: Session) -> list[StrategySkill]:
    global _last_skill_sync_time, _cached_skill_cache_key, _cached_skill_list
    settings = get_settings()
    cache_key = (settings.database_url, str(settings.skills_dir.resolve()))
    now = time.time()
    if _is_skill_cache_fresh(now, cache_key):
        return _cached_skill_list

    with _skill_sync_lock:
        now = time.time()
        if _is_skill_cache_fresh(now, cache_key):
            return _cached_skill_list

        skills = load_skills(settings.skills_dir)
        descriptors = _descriptor_map(skills)
        has_changes = False
        for skill in skills:
            descriptor = descriptors.get(skill.skill_id)
            if descriptor is None:
                # Non-executable agent skills are intentionally not strategies and
                # therefore cannot be used as a second identity source.
                continue
            registry = db.get(SkillRegistry, descriptor.strategy_id)
            manifest_data = dict(skill.manifest)
            manifest_data["_strategy_descriptor"] = descriptor.to_projection()
            manifest_data["_skill_md_available"] = skill.agent_enabled
            manifest_data["_skill_md_hash"] = skill.skill_hash
            manifest_data["_skill_frontmatter"] = _json_safe(skill.skill_frontmatter)
            manifest_data["_skill_reference_files"] = _json_safe(skill.reference_files)
            manifest_data["_skill_runtime"] = "dual" if skill.contract is not None and skill.agent_enabled else "agent" if skill.agent_enabled else "rule"
            manifest_data["_strategy_backtest_adapter"] = _strategy_backtest_adapter_manifest(skill)
            manifest_json = json.dumps(manifest_data, ensure_ascii=False, sort_keys=True)
            if registry is None:
                registry = SkillRegistry(
                    skill_id=descriptor.strategy_id,
                    name=descriptor.name,
                    source_type=skill.manifest.get("source_type", "local"),
                    source_url=skill.manifest.get("source_url"),
                    installed_path=str(skill.path),
                    current_version=descriptor.declared_version,
                    mode=descriptor.mode.value,
                    enabled=descriptor.enabled,
                    has_executable_contract=skill.executable,
                    manifest_json=manifest_json,
                )
                db.add(registry)
                has_changes = True
            else:
                has_changes = _sync_registry_row(registry, skill, descriptor, manifest_json) or has_changes

            version_id = skill_version_id(skill, descriptor)
            if db.get(SkillVersion, version_id) is None:
                db.add(
                    SkillVersion(
                        id=version_id,
                        skill_id=descriptor.strategy_id,
                        version=descriptor.declared_version,
                        source_ref=skill.manifest.get("source_ref"),
                        installed_path=str(skill.path),
                        manifest_json=manifest_json,
                        contract_hash=descriptor.manifest_hash,
                        status=descriptor.status.value,
                    )
                )
                has_changes = True
            if db.get(StrategyVersion, version_id) is None:
                db.add(
                    StrategyVersion(
                        version_id=version_id,
                        strategy_id=descriptor.strategy_id,
                        name=descriptor.name,
                        enabled=descriptor.enabled,
                        skill_repo=skill.manifest.get("source_url"),
                        skill_ref=skill.manifest.get("source_ref"),
                        skill_path=str(skill.path),
                        yaml_path=str(skill.path / "strategy.yaml"),
                        yaml_hash=descriptor.manifest_hash,
                        manifest_json=manifest_json,
                    )
                )
                has_changes = True
        if has_changes:
            db.commit()
        from app.services.registry.presets import _ensure_default_presets, repair_preset_registration

        _ensure_default_presets(db, skills)
        repair_preset_registration(db, skills)
        _last_skill_sync_time = time.time()
        _cached_skill_cache_key = cache_key
        _cached_skill_list = skills
        return skills


def skill_version_id(skill: StrategySkill, descriptor: "StrategyDescriptor" | None = None) -> str:
    """Return a fixed-width version-row key derived from the effective descriptor."""
    if descriptor is None:
        from app.engine.strategy import build_strategy_descriptor

        descriptor = build_strategy_descriptor(skill)
    seed = f"{descriptor.strategy_id}:{descriptor.effective_version}"
    return f"sd1:{hashlib.sha256(seed.encode('utf-8')).hexdigest()}"


def list_skills(db: Session) -> list[dict[str, Any]]:
    sync_skills(db)
    preset_rows = db.execute(
        select(PresetSkill.skill_id, Preset.name).join(Preset, Preset.preset_id == PresetSkill.preset_id)
    ).all()
    preset_map: dict[str, list[str]] = {}
    for skill_id, preset_name in preset_rows:
        preset_map.setdefault(skill_id, []).append(preset_name)
    rows = db.scalars(select(SkillRegistry).order_by(SkillRegistry.skill_id.asc())).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        manifest = _loads(row.manifest_json, {})
        items.append(
            {
                "skill_id": row.skill_id,
                "name": row.name,
                "source_type": row.source_type,
                "source_url": row.source_url,
                "installed_path": row.installed_path,
                "current_version": row.current_version,
                "enabled": row.enabled,
                "has_executable_contract": row.has_executable_contract,
                "has_agent_contract": bool(manifest.get("_skill_md_available")),
                "agent_runtime": manifest.get("_skill_runtime", "unknown"),
                "has_backtest_adapter": bool(manifest.get("_strategy_backtest_adapter")),
                "backtest_adapter_actions": (manifest.get("_strategy_backtest_adapter") or {}).get("actions", []),
                "reference_count": len(manifest.get("_skill_reference_files", [])),
                "manifest": manifest,
                "assigned_presets": preset_map.get(row.skill_id, []),
            }
        )
    return items


def get_skill_detail(db: Session, skill_id: str) -> dict[str, Any] | None:
    sync_skills(db)
    row = db.get(SkillRegistry, skill_id)
    if row is None:
        return None
    path = PathLike(row.installed_path)
    skill = load_skills(get_settings().skills_dir)
    skill_obj = next((item for item in skill if item.skill_id == skill_id), None)
    skill_md = ""
    errors: list[str] = []
    if path.joinpath("SKILL.md").exists():
        skill_md = path.joinpath("SKILL.md").read_text(encoding="utf-8")
    if skill_obj is not None:
        errors = validate_skill(skill_obj)
    performance = skill_performance(db, skill_id)
    assigned_presets = [
        {"preset_id": preset_id, "name": db.get(Preset, preset_id).name}
        for preset_id in {row.preset_id for row in db.scalars(select(PresetSkill).where(PresetSkill.skill_id == skill_id)).all()}
        if db.get(Preset, preset_id) is not None
    ]
    manifest = _loads(row.manifest_json, {})
    adapter_manifest = manifest.get("_strategy_backtest_adapter") or {}
    return {
        "skill_id": row.skill_id,
        "name": row.name,
        "enabled": row.enabled,
        "current_version": row.current_version,
        "has_executable_contract": row.has_executable_contract,
        "has_agent_contract": bool(skill_md.strip()),
        "agent_runtime": manifest.get("_skill_runtime", "unknown"),
        "has_backtest_adapter": bool(adapter_manifest),
        "backtest_adapter_actions": adapter_manifest.get("actions", []),
        "reference_files": manifest.get("_skill_reference_files", []),
        "manifest": manifest,
        "skill_md": skill_md,
        "errors": errors,
        "assigned_presets": assigned_presets,
        "performance": performance,
    }


def set_skill_enabled(db: Session, skill_id: str, enabled: bool) -> dict[str, Any] | None:
    """Change status at the manifest source, then refresh all descriptor projections."""
    row = db.get(SkillRegistry, skill_id)
    if row is None:
        return None
    manifest_path = Path(row.installed_path) / "manifest.json"
    manifest = _loads(manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else None, {})
    if not isinstance(manifest, dict):
        raise ValueError(f"策略 '{skill_id}' 的 manifest 无法读取。")
    if manifest.get("status") == "deprecated" and enabled:
        raise ValueError(f"已弃用策略 '{skill_id}' 不能通过 enabled 兼容入口重新启用。")
    manifest["status"] = "active" if enabled else "disabled"
    manifest["enabled"] = enabled
    temp_path = manifest_path.with_name(f".{manifest_path.name}.tmp")
    try:
        temp_path.write_text(json.dumps(_json_safe(manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(manifest_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    global _last_skill_sync_time, _cached_skill_cache_key, _cached_skill_list
    _last_skill_sync_time = 0.0
    _cached_skill_cache_key = None
    _cached_skill_list = None
    sync_skills(db)
    refreshed = db.get(SkillRegistry, skill_id)
    invalidate_bootstrap_cache()
    if refreshed is None:
        return None
    descriptor = (_loads(refreshed.manifest_json, {}).get("_strategy_descriptor") or {})
    return {
        "strategy_id": descriptor.get("strategy_id", refreshed.skill_id),
        "skill_id": refreshed.skill_id,
        "enabled": bool(descriptor.get("enabled", refreshed.enabled)),
        "status": descriptor.get("status", "active" if refreshed.enabled else "disabled"),
    }


def skill_performance_summary(db: Session, skill_id: str) -> dict[str, Any] | None:
    # 直接走轻量路径，避免 get_skill_detail 的重复 load_skills / SKILL.md 读取 /
    # validate_skill / assigned_presets 查询——这些字段 summary 不需要。
    # 实测：8 skills × 2 modes 从 3-6s 并发降至 <0.5s（攻击 UX 红色缺口"关键操作 <2s"）。
    skills = sync_skills(db)
    skill_obj = next((item for item in skills if item.skill_id == skill_id), None)
    if skill_obj is None:
        return None
    errors = validate_skill(skill_obj)
    performance = skill_performance(db, skill_id)
    return {
        "skill_id": skill_id,
        "status": "completed",
        "summary_type": "historical_performance",
        "source": "recommendation_outcomes",
        "runs_backtest": False,
        "sample_count": performance["sample_count"],
        "hit_rate": performance["hit_rate"],
        "avg_return": performance["avg_return"],
        "errors": errors,
    }


def batch_skill_performance_summaries(db: Session) -> list[dict[str, Any]]:
    """批量获取所有 skill 的 performance summary，避免前端并发 8 次请求。

    优化：sync_skills 只调用一次（5s TTL 缓存），validate_skill 对每个 skill 独立调用。
    """
    skills = sync_skills(db)
    results: list[dict[str, Any]] = []
    for skill_obj in skills:
        errors = validate_skill(skill_obj)
        performance = skill_performance(db, skill_obj.skill_id)
        results.append({
            "skill_id": skill_obj.skill_id,
            "status": "completed",
            "summary_type": "historical_performance",
            "source": "recommendation_outcomes",
            "runs_backtest": False,
            "sample_count": performance["sample_count"],
            "hit_rate": performance["hit_rate"],
            "avg_return": performance["avg_return"],
            "errors": errors,
        })
    return results


def backtest_skill(db: Session, skill_id: str) -> dict[str, Any] | None:
    result = skill_performance_summary(db, skill_id)
    if result is not None:
        result["deprecated_endpoint"] = "/api/skills/{skill_id}/backtest"
        result["replacement_endpoint"] = "/api/skills/{skill_id}/performance-summary"
    return result


def install_skill(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("source") or payload.get("url") or "").strip()
    if not source:
        raise ValueError("source is required")
    settings = get_settings()
    settings.skills_dir.mkdir(parents=True, exist_ok=True)
    if source.startswith("http://") or source.startswith("https://") or source.endswith(".git"):
        target_name = str(payload.get("skill_id") or Path(source.rstrip("/").removesuffix(".git")).name)
        target = settings.skills_dir / target_name
        if target.exists():
            shutil.rmtree(target)
        subprocess.run(["git", "clone", "--depth", "1", source, str(target)], check=True, capture_output=True, text=True)
    else:
        src = Path(source).expanduser().resolve()
        if not src.exists() or not src.is_dir():
            raise ValueError("local skill path does not exist")
        target_name = str(payload.get("skill_id") or src.name)
        target = settings.skills_dir / target_name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
    _write_install_manifest(target, target_name, source)
    sync_skills(db)
    skill = load_skills(settings.skills_dir)
    installed = next((item for item in skill if item.path == target), None)
    if installed is None:
        raise ValueError("skill install failed")
    detail = get_skill_detail(db, installed.skill_id)
    if detail is None:
        raise ValueError("skill registry failed")
    invalidate_bootstrap_cache()
    return detail


def _write_install_manifest(target: Path, skill_id: str, source: str) -> None:
    manifest_path = target / "manifest.json"
    manifest = _loads(manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else None, {})
    if not isinstance(manifest, dict):
        manifest = {}
    manifest.setdefault("name", skill_id)
    manifest["id"] = skill_id
    manifest.setdefault("mode", "imperative" if manifest.get("impl_class") else "declarative")
    manifest.setdefault("status", "active" if manifest.get("enabled", True) else "disabled")
    # status is canonical; enabled is retained only as its public compatibility projection.
    manifest["enabled"] = manifest["status"] == "active"
    manifest.setdefault("requirements", ["stock_feature_context"])
    manifest.setdefault("capabilities", ["signal_evaluation"])
    manifest["source_type"] = "github" if source.startswith(("http://", "https://")) else "local"
    manifest["source_url"] = source
    manifest_path.write_text(json.dumps(_json_safe(manifest), ensure_ascii=False, indent=2), encoding="utf-8")


def skill_performance(db: Session, skill_id: str) -> dict[str, Any]:
    from sqlalchemy import text

    from app.services.outcome_service import _ADDITIONAL_COST_PCT

    _EMPTY: dict[str, Any] = {
        "sample_count": 0, "triggered_count": 0, "trigger_rate": 0.0,
        "hit_rate": 0.0, "avg_return": 0.0, "avg_score": 0.0, "failure_reasons": [],
    }
    # JSON_TABLE 把 skill_breakdown_json 展开为行，过滤下推到 MySQL，
    # 避免拉取全量 runs → recs → Python JSON 解析（~1.2s/call → <50ms）。
    agg_sql = text("""
        WITH matched AS (
            SELECT
                COALESCE(jt.scr, 0)                              AS signal_score,
                COALESCE(o.entry_triggered, 0)                   AS entry_triggered,
                COALESCE(o.executed_return, o.close_return, 0)   AS resolved_return
            FROM recommendation_runs rr
            JOIN recommendations r          ON r.run_id = rr.run_id
            JOIN JSON_TABLE(
                r.skill_breakdown_json,
                '$[*]' COLUMNS (
                    sid VARCHAR(100) PATH '$.strategy_id',
                    scr DOUBLE        PATH '$.score'
                )
            ) jt
            LEFT JOIN recommendation_outcomes o
                ON o.recommendation_id = r.recommendation_id
            WHERE jt.sid = :skill_id
        )
        SELECT
            COUNT(*)                                                      AS sample_count,
            SUM(entry_triggered)                                          AS triggered_count,
            SUM(CASE WHEN entry_triggered THEN resolved_return ELSE 0 END) AS total_return,
            SUM(CASE WHEN entry_triggered AND resolved_return > :cost
                     THEN 1 ELSE 0 END)                                   AS success_count,
            SUM(signal_score)                                             AS total_score
        FROM matched
    """)
    row = db.execute(agg_sql, {
        "skill_id": skill_id, "cost": _ADDITIONAL_COST_PCT,
    }).first()
    if not row or (row.sample_count or 0) == 0:
        return _EMPTY

    sample_count: int = int(row.sample_count)
    triggered_count: int = int(row.triggered_count or 0)
    success_count: int = int(row.success_count or 0)
    total_return: float = float(row.total_return or 0)
    total_score: float = float(row.total_score or 0)

    # 失败原因明细：仅在有失败记录时执行第二次查询
    failures: dict[str, int] = {}
    if triggered_count > success_count:
        fail_sql = text("""
            SELECT COALESCE(o.outcome_reason, '未达标') AS reason, COUNT(*) AS cnt
            FROM recommendation_runs rr
            JOIN recommendations r          ON r.run_id = rr.run_id
            JOIN JSON_TABLE(
                r.skill_breakdown_json,
                '$[*]' COLUMNS (
                    sid VARCHAR(100) PATH '$.strategy_id'
                )
            ) jt
            JOIN recommendation_outcomes o  ON o.recommendation_id = r.recommendation_id
            WHERE jt.sid = :skill_id
              AND o.entry_triggered = 1
              AND COALESCE(o.executed_return, o.close_return, 0) <= :cost
            GROUP BY reason
            ORDER BY cnt DESC
            LIMIT 5
        """)
        for frow in db.execute(fail_sql, {
            "skill_id": skill_id, "cost": _ADDITIONAL_COST_PCT,
        }):
            failures[frow.reason] = frow.cnt

    return {
        "sample_count": sample_count,
        "triggered_count": triggered_count,
        "trigger_rate": round(triggered_count / sample_count, 3) if sample_count else 0.0,
        "hit_rate": round(success_count / triggered_count, 3) if triggered_count else 0.0,
        "avg_return": round(total_return / max(1, triggered_count), 2) if triggered_count else 0.0,
        "avg_score": round(total_score / sample_count, 2) if sample_count else 0.0,
        "failure_reasons": sorted(
            [{"reason": key, "count": count} for key, count in failures.items()],
            key=lambda item: item["count"],
            reverse=True,
        )[:5],
    }
