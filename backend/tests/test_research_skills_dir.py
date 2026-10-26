from app.core.config import get_settings
from app.engine.skill_loader import load_skills


def test_research_skills_not_loaded_in_production(monkeypatch):
    monkeypatch.delenv("ASHARE_RESEARCH_EXTRA_SKILLS", raising=False)
    ids = {s.skill_id for s in load_skills(get_settings().skills_dir)}
    assert not any(i.startswith("research-") for i in ids)


def test_research_skills_loaded_only_when_named(monkeypatch):
    monkeypatch.setenv("ASHARE_RESEARCH_EXTRA_SKILLS", "research-ladder-relay")
    ids = [s.skill_id for s in load_skills(get_settings().skills_dir)]
    assert "research-ladder-relay" in ids
    assert "research-first-board" not in ids


def test_empty_skill_dir_is_skipped_but_incomplete_dir_still_fails(tmp_path):
    import pytest

    from app.engine.skill_loader import SkillManifestError

    skills_dir = tmp_path / "skills"
    (skills_dir / "retired-leftover").mkdir(parents=True)
    (skills_dir / "retired-leftover" / ".DS_Store").write_text("")
    assert load_skills(skills_dir) == []

    broken = skills_dir / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("body", encoding="utf-8")
    with pytest.raises(SkillManifestError):
        load_skills(skills_dir)
