from pathlib import Path

from sqlalchemy import select

from app.engine.skill_loader import load_skills
from app.models.tables import PresetSkill
from app.services.registry.data_source import DEFAULT_PRESETS
from app.services.registry.presets import _default_preset_skill_rows, repair_preset_registration

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

# 所有在 strategy.yaml 的 meta.default_preset_groups 中声明 formal_opening 的策略。
# 此集合与 9 个策略的声明逐一核对，是 preset_formal_opening 应有的完整成员。
FORMAL_OPENING_EXPECTED = {
    "chao-gu-yang-jia-skill",
    "chen-xiaoqun-skill",
    "daily-stock-analysis-skill",
    "dragon-tiger-smart-money",
    "high-range-breakout-momentum",
    "limit-up-reseal-relay",
    "overnight-holding-method",
    "sector-relative-strength-leader",
    "trend-pullback-continuation",
}


def _load_skills():
    return load_skills(SKILLS_DIR)


def _formal_opening_members(db):
    return set(
        db.scalars(
            select(PresetSkill.skill_id).where(PresetSkill.preset_id == "preset_formal_opening")
        ).all()
    )


def test_default_preset_skill_rows_formal_opening_includes_all_declared():
    """意图校验：_default_preset_skill_rows 应为 preset_formal_opening 产出全部 9 个声明策略。"""
    skills = _load_skills()
    cfg = next(p for p in DEFAULT_PRESETS if p["preset_id"] == "preset_formal_opening")
    rows = _default_preset_skill_rows(cfg, skills)
    ids = {r["skill_id"] for r in rows}
    assert FORMAL_OPENING_EXPECTED.issubset(ids), ids
    # trend-pullback-continuation 必须被包含——此前因注册缺陷从未进入每日 formal run
    assert "trend-pullback-continuation" in ids


def test_repair_preset_registration_is_additive_and_idempotent(db):
    """修复是加法的、幂等的：补齐缺失项、保留原有项、重复调用不产生副作用。"""
    skills = _load_skills()
    before = _formal_opening_members(db)

    repair_preset_registration(db, skills)
    after_first = _formal_opening_members(db)

    # 加法：原有成员一个不少
    assert before.issubset(after_first)
    # 补齐到声明全集（含此前缺失的 trend-pullback-continuation 等）
    assert FORMAL_OPENING_EXPECTED.issubset(after_first)

    # 幂等：第二次调用不应新增行也不应报错
    repair_preset_registration(db, skills)
    after_second = _formal_opening_members(db)
    assert after_first == after_second
