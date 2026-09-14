from pathlib import Path

from sqlalchemy import select

from app.models.tables import Preset, PresetSkill, SkillRegistry
from app.services.registry import skills as skills_registry


def test_sync_skills_disables_removed_local_skill_and_prunes_presets(db):
    retired_id = "synthetic-retired-local-skill"
    preset_id = "synthetic-retirement-preset"
    db.add(
        SkillRegistry(
            skill_id=retired_id,
            name="已退役测试策略",
            source_type="local",
            installed_path=str(Path("skills") / retired_id),
            current_version="1.0.0",
            mode="declarative",
            enabled=True,
            has_executable_contract=True,
            manifest_json="{}",
        )
    )
    db.add(
        Preset(
            preset_id=preset_id,
            name="退役同步测试预设",
            run_mode="formal",
            market_scope="all",
            risk_template="balanced",
            score_line=72.0,
            output_count=6,
            bucket_limits_json="{}",
            data_source="local_pool",
            status="active",
        )
    )
    db.add(
        PresetSkill(
            id=f"{preset_id}:{retired_id}",
            preset_id=preset_id,
            skill_id=retired_id,
            weight=1.0,
            sort_order=1,
        )
    )
    db.commit()

    skills_registry._last_skill_sync_time = 0.0
    skills_registry._cached_skill_cache_key = None
    skills_registry._cached_skill_list = None
    skills_registry.sync_skills(db)

    retired = db.get(SkillRegistry, retired_id)
    assert retired is not None
    assert retired.enabled is False
    assert retired.has_executable_contract is False
    assert db.scalar(
        select(PresetSkill).where(PresetSkill.skill_id == retired_id)
    ) is None
