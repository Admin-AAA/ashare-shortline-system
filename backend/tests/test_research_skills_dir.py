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
