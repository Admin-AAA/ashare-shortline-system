from pathlib import Path

from app.engine.skill_loader import load_skill, validate_skill


SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def test_chao_gu_and_dsa_use_next_open_as_strategy_default():
    for skill_id in (
        "chao-gu-yang-jia-skill",
        "daily-stock-analysis-skill",
    ):
        skill = load_skill(SKILLS_DIR / skill_id)

        assert validate_skill(skill) == []
        assert skill.contract is not None
        assert skill.contract["default_entry_mode"] == "next_open"
        assert skill.version == "0.2.0"
