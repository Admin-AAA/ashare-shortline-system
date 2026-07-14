"""预设（Preset）CRUD 深模块。

任务 7.4（需求 6.1）落地：把此前散落在 `workbench_service.py` 的预设增删查改
（`list_presets` / `create_preset` / `update_preset` / `delete_preset`）与默认
预设自愈逻辑（`_ensure_default_presets` / `repair_preset_registration` /
`_default_preset_skill_rows` / `_replace_preset_skills`）收口到本模块。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.engine.skill_loader import StrategySkill
from app.models.tables import Preset, PresetSkill, SkillRegistry
from app.services.registry.data_source import (
    DATA_SOURCE_LOCAL_POOL,
    DEFAULT_PRESETS,
    DEFAULT_RECOMMENDATION_DATA_SOURCE,
    normalize_data_source,
)
from app.services.registry.skills import sync_skills
from app.services.runs.bootstrap_cache import invalidate_bootstrap_cache


def _slugify(value: str) -> str:
    import hashlib

    return hashlib.md5(value.encode("utf-8")).hexdigest()[:12]


def _serialize_preset(db: Session, preset: Preset) -> dict[str, Any]:
    skills = db.scalars(
        select(PresetSkill).where(PresetSkill.preset_id == preset.preset_id).order_by(PresetSkill.sort_order.asc())
    ).all()
    return {
        "preset_id": preset.preset_id,
        "name": preset.name,
        "description": preset.description,
        "skip_aux_refresh": preset.skip_aux_refresh,
        "market_scope": preset.market_scope,
        "market_scope_value": preset.market_scope_value,
        "risk_template": preset.risk_template,
        "score_line": preset.score_line,
        "output_count": preset.output_count,
        "data_source": preset.data_source,
        "hide_recent_repeats": preset.hide_recent_repeats,
        "status": preset.status,
        "skills": [
            {
                "skill_id": row.skill_id,
                "weight": row.weight,
                "sort_order": row.sort_order,
                "name": (registry.name if (registry := db.get(SkillRegistry, row.skill_id)) else row.skill_id),
            }
            for row in skills
        ],
    }


def _serialize_presets_batched(db: Session, presets: list[Preset]) -> list[dict[str, Any]]:
    if not presets:
        return []
    preset_ids = [preset.preset_id for preset in presets]
    skill_rows = db.scalars(
        select(PresetSkill)
        .where(PresetSkill.preset_id.in_(preset_ids))
        .order_by(PresetSkill.preset_id.asc(), PresetSkill.sort_order.asc())
    ).all()
    skill_ids = {row.skill_id for row in skill_rows}
    skill_name_map = {}
    if skill_ids:
        skill_name_map = {
            row.skill_id: row.name
            for row in db.scalars(select(SkillRegistry).where(SkillRegistry.skill_id.in_(skill_ids))).all()
        }
    skills_by_preset: dict[str, list[dict[str, Any]]] = {}
    for row in skill_rows:
        skills_by_preset.setdefault(row.preset_id, []).append(
            {
                "skill_id": row.skill_id,
                "weight": row.weight,
                "sort_order": row.sort_order,
                "name": skill_name_map.get(row.skill_id, row.skill_id),
            }
        )

    return [
        {
            "preset_id": preset.preset_id,
            "name": preset.name,
            "description": preset.description,
            "skip_aux_refresh": preset.skip_aux_refresh,
            "market_scope": preset.market_scope,
            "market_scope_value": preset.market_scope_value,
            "risk_template": preset.risk_template,
            "score_line": preset.score_line,
            "output_count": preset.output_count,
            "data_source": preset.data_source,
            "hide_recent_repeats": preset.hide_recent_repeats,
            "status": preset.status,
            "skills": skills_by_preset.get(preset.preset_id, []),
        }
        for preset in presets
    ]


def list_presets(db: Session) -> list[dict[str, Any]]:
    sync_skills(db)
    presets = db.scalars(select(Preset).order_by(Preset.created_at.asc())).all()
    return _serialize_presets_batched(db, presets)


def create_preset(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    preset_id = str(payload.get("preset_id") or _slugify(payload.get("name") or f"preset-{uuid.uuid4().hex[:6]}"))
    if db.get(Preset, preset_id) is not None:
        preset_id = f"{preset_id}-{uuid.uuid4().hex[:4]}"
    preset = Preset(
        preset_id=preset_id,
        name=str(payload.get("name") or "未命名预设"),
        description=payload.get("description"),
        run_mode="formal",
        skip_aux_refresh=bool(payload.get("skip_aux_refresh", False)),
        market_scope=str(payload.get("market_scope") or "all"),
        market_scope_value=payload.get("market_scope_value"),
        risk_template=str(payload.get("risk_template") or "balanced"),
        score_line=float(payload.get("score_line") or 72.0),
        output_count=int(payload.get("output_count") or 6),
        bucket_limits_json="{}",
        data_source=normalize_data_source(payload.get("data_source") or DEFAULT_RECOMMENDATION_DATA_SOURCE),
        hide_recent_repeats=bool(payload.get("hide_recent_repeats", True)),
        status=str(payload.get("status") or "active"),
    )
    db.add(preset)
    db.flush()
    _replace_preset_skills(db, preset.preset_id, payload.get("skills") or [])
    db.commit()
    invalidate_bootstrap_cache()
    return _serialize_preset(db, preset)


def update_preset(db: Session, preset_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    preset = db.get(Preset, preset_id)
    if preset is None:
        return None
    for field in [
        "name",
        "description",
        "market_scope",
        "market_scope_value",
        "risk_template",
        "score_line",
        "data_source",
        "status",
    ]:
        if field in payload:
            value = normalize_data_source(payload[field]) if field == "data_source" else payload[field]
            setattr(preset, field, value)
    if "output_count" in payload:
        preset.output_count = int(payload["output_count"])
    if "skip_aux_refresh" in payload:
        preset.skip_aux_refresh = bool(payload["skip_aux_refresh"])
    if "hide_recent_repeats" in payload:
        preset.hide_recent_repeats = bool(payload["hide_recent_repeats"])
    preset.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if "skills" in payload:
        _replace_preset_skills(db, preset.preset_id, payload["skills"] or [])
    db.commit()
    invalidate_bootstrap_cache()
    return _serialize_preset(db, preset)


def delete_preset(db: Session, preset_id: str) -> bool:
    import logging

    logger = logging.getLogger(__name__)
    preset = db.get(Preset, preset_id)
    if preset is None:
        logger.warning("delete_preset: preset_id=%s not found, treating as idempotent success", preset_id)
        invalidate_bootstrap_cache()
        return True
    db.execute(text("DELETE FROM preset_skills WHERE preset_id = :pid"), {"pid": preset_id})
    db.delete(preset)
    db.commit()
    invalidate_bootstrap_cache()
    return True


def _replace_preset_skills(db: Session, preset_id: str, skills: list[dict[str, Any]]) -> None:
    existing = db.scalars(select(PresetSkill).where(PresetSkill.preset_id == preset_id)).all()
    for row in existing:
        db.delete(row)
    # 显式 flush：确保 DELETE 先落库，否则同事务内 INSERT 与待删旧行主键/唯一键冲突（uq_preset_skill）。
    db.flush()
    for index, item in enumerate(skills):
        db.add(
            PresetSkill(
                id=f"{preset_id}:{item['skill_id']}",
                preset_id=preset_id,
                skill_id=str(item["skill_id"]),
                weight=float(item.get("weight") or 1.0),
                sort_order=int(item.get("sort_order") or index),
            )
        )


def _ensure_default_presets(db: Session, skills: list[StrategySkill]) -> None:
    has_changes = False
    for item in DEFAULT_PRESETS:
        preset = db.get(Preset, item["preset_id"])
        configured_data_source = normalize_data_source(item.get("data_source") or DEFAULT_RECOMMENDATION_DATA_SOURCE)
        if preset is None:
            preset = Preset(
                preset_id=item["preset_id"],
                name=item["name"],
                description=item["description"],
                market_scope=item["market_scope"],
                market_scope_value=item["market_scope_value"],
                risk_template=item["risk_template"],
                score_line=item["score_line"],
                output_count=item["output_count"],
                bucket_limits_json="{}",
                data_source=configured_data_source,
                hide_recent_repeats=item["hide_recent_repeats"],
                skip_aux_refresh=item.get("skip_aux_refresh", False),
                status="active",
            )
            db.add(preset)
            db.flush()
            desired_skills = _default_preset_skill_rows(item, skills)
            _replace_preset_skills(db, item["preset_id"], desired_skills)
            has_changes = True
            continue

        if (
            not str(preset.data_source or "").strip()
            or (
                preset.preset_id == item["preset_id"]
                and preset.data_source == DATA_SOURCE_LOCAL_POOL
                and configured_data_source == DEFAULT_RECOMMENDATION_DATA_SOURCE
            )
        ):
            preset.data_source = configured_data_source
            has_changes = True
        if float(preset.score_line or 0) <= 0:
            preset.score_line = float(item["score_line"])
            has_changes = True
        has_skill_rows = bool(
            db.scalar(
                select(PresetSkill.id)
                .where(PresetSkill.preset_id == item["preset_id"])
                .limit(1)
            )
        )
        if not has_skill_rows:
            desired_skills = _default_preset_skill_rows(item, skills)
            if desired_skills:
                _replace_preset_skills(db, item["preset_id"], desired_skills)
                has_changes = True
    if has_changes:
        db.commit()


def repair_preset_registration(db: Session, skills: list[StrategySkill]) -> None:
    """T1 策略注册自愈（仅加法，绝不删除）。

    根因：``_ensure_default_presets`` 仅在预设「无任何 skill 行」时才用
    ``_default_preset_skill_rows`` 全量填充；一旦预设已有部分 skill 行
    （如 ``preset_formal_opening`` 长期只有 2/9 策略），后续新声明该组的策略
    （含 ``trend-pullback-continuation``）永远不会被加入，导致其从未在每日 formal run
    中被调用，违反北极星 C「每个策略有独立可查战绩」。

    本函数做加法修复：仅为缺失项 INSERT ``PresetSkill`` 行，绝不 DELETE 任何已有行
    （保留人工策展的权重/排序），幂等可重复调用，接入 ``sync_skills`` 后每次同步自愈。
    """
    has_changes = False
    for item in DEFAULT_PRESETS:
        preset_id = item["preset_id"]
        desired = _default_preset_skill_rows(item, skills)
        if not desired:
            continue
        existing_ids = set(
            db.scalars(
                select(PresetSkill.skill_id).where(PresetSkill.preset_id == preset_id)
            ).all()
        )
        for row in desired:
            sid = str(row["skill_id"])
            if sid in existing_ids:
                continue
            db.add(
                PresetSkill(
                    id=f"{preset_id}:{sid}",
                    preset_id=preset_id,
                    skill_id=sid,
                    weight=float(row.get("weight") or 1.0),
                    sort_order=int(row.get("sort_order") or 0),
                )
            )
            has_changes = True
    if has_changes:
        db.commit()


def _default_preset_skill_rows(preset_config: dict[str, Any], skills: list[StrategySkill]) -> list[dict[str, Any]]:
    groups = {str(group) for group in preset_config.get("skill_groups", []) if str(group).strip()}
    if not groups:
        return []
    rows: list[dict[str, Any]] = []
    for skill in skills:
        meta = (skill.contract or {}).get("meta") or {}
        group_config = meta.get("default_preset_groups") or {}
        if not isinstance(group_config, dict):
            continue
        for group in groups:
            if group not in group_config:
                continue
            spec = group_config[group]
            if isinstance(spec, dict):
                weight = float(spec.get("weight") or 1.0)
                sort_order = int(spec.get("sort_order") or len(rows))
            elif isinstance(spec, (int, float)):
                weight = float(spec)
                sort_order = len(rows)
            elif spec is True:
                weight = float((skill.contract or {}).get("weight") or 1.0)
                sort_order = len(rows)
            else:
                continue
            rows.append({"skill_id": skill.skill_id, "weight": weight, "sort_order": sort_order})
            break
    return sorted(rows, key=lambda row: (int(row.get("sort_order") or 0), str(row.get("skill_id") or "")))
