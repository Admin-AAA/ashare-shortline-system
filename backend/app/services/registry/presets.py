"""预设（Preset）CRUD 深模块。

预设增删查改（`list_presets` / `create_preset` / `update_preset` / `delete_preset`）
与策略替换（`_replace_preset_skills`）收口到本模块。

自愈逻辑（`_ensure_default_presets` / `repair_preset_registration` /
`_default_preset_skill_rows`）已移除：它们会在用户编辑预设后把删掉的策略
自动加回来，导致前端编辑永远无法生效。预设由用户手动管理，不需要机器自愈。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.tables import Preset, PresetSkill, SkillRegistry
from app.services.registry.data_source import (
    DEFAULT_RECOMMENDATION_DATA_SOURCE,
    normalize_data_source,
)
from app.services.registry.skills import sync_skills
from app.services.runs.bootstrap_cache import invalidate_bootstrap_cache


def _slugify(value: str) -> str:
    import hashlib

    return hashlib.md5(value.encode("utf-8")).hexdigest()[:12]


FULL_COMBO_PRESET_ID = "preset_formal_opening"


def _preset_description(preset: Preset, skill_count: int) -> str | None:
    """全策略组合的描述按实际成员数生成，增删策略后不会再过时（旧文案曾写死“12 个”）。"""
    if preset.preset_id == FULL_COMBO_PRESET_ID:
        return f"全部 {skill_count} 个在跑策略的组合预设，盘前 formal run 使用。"
    return preset.description


def _serialize_preset(db: Session, preset: Preset) -> dict[str, Any]:
    skills = db.scalars(
        select(PresetSkill).where(PresetSkill.preset_id == preset.preset_id).order_by(PresetSkill.sort_order.asc())
    ).all()
    return {
        "preset_id": preset.preset_id,
        "name": preset.name,
        "description": _preset_description(preset, len(skills)),
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
                "strategy_id": row.skill_id,
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
                "strategy_id": row.skill_id,
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
            "description": _preset_description(preset, len(skills_by_preset.get(preset.preset_id, []))),
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


PROTECTED_PRESET_PREFIX = "preset_formal_"


class ProtectedPresetError(Exception):
    """正式预设（preset_formal_*）被正式短线/盘前 formal run 依赖，删除需显式 confirm_formal=True。"""


def delete_preset(db: Session, preset_id: str, *, confirm_formal: bool = False) -> bool:
    import logging

    if str(preset_id).startswith(PROTECTED_PRESET_PREFIX) and not confirm_formal:
        # 正式预设删除需二次确认（用户可主动删除不再使用的正式方案）
        raise ProtectedPresetError(preset_id)

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
        strategy_id = str(item.get("strategy_id") or item.get("skill_id") or "").strip()
        if not strategy_id:
            raise ValueError("preset skill requires strategy_id or skill_id")
        db.add(
            PresetSkill(
                id=f"{preset_id}:{strategy_id}",
                preset_id=preset_id,
                skill_id=strategy_id,
                weight=float(item.get("weight") or 1.0),
                sort_order=int(item.get("sort_order") or index),
            )
        )
