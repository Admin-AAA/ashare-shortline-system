"""sync_skills 把盘上已删除的策略在 skill_registry 中强制 enabled=False（行保留供历史 run 引用）。"""
import app.services.registry.skills as reg
from app.models.strategy import SkillRegistry


def test_sync_disables_registry_rows_missing_on_disk(db, monkeypatch):
    db.add(SkillRegistry(skill_id="ghost-deleted-skill", name="ghost", installed_path="/nope", enabled=True))
    db.flush()
    monkeypatch.setattr(reg, "_last_skill_sync_time", 0.0)
    monkeypatch.setattr(reg, "_cached_skill_cache_key", None)
    skills = reg.sync_skills(db)
    assert db.get(SkillRegistry, "ghost-deleted-skill").enabled is False
    live = {s.skill_id for s in skills}
    assert "chao-gu-yang-jia-skill" in live
    assert db.get(SkillRegistry, "chao-gu-yang-jia-skill").enabled is True
