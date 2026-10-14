"""把 preset_formal_opening 的成员对齐到当前单策略独立运行清单（standalone_strategy_ids）。

新启用的策略声明了 formal_opening 组但不在 preset_skills 里时，注册诊断会让所有正式 run 失败；
已删除/退役的策略留在 preset 里也没用。已有成员保留原权重，新成员权重取 yaml 声明值（默认 0.8）。
用法：python scripts/sync_formal_preset_members.py [--apply]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.tables import PresetSkill
from app.services.registry.presets import _replace_preset_skills
from app.services.standalone_strategies import standalone_strategy_ids
from app.services.workbench_service import sync_skills

PRESET = "preset_formal_opening"
with SessionLocal() as db:
    skills = sync_skills(db)
    target = standalone_strategy_ids(skills)
    cur = {r.skill_id: r for r in db.scalars(select(PresetSkill).where(PresetSkill.preset_id == PRESET))}
    rows = []
    for i, sid in enumerate(target):
        w = cur[sid].weight if sid in cur else 0.8
        rows.append({"skill_id": sid, "weight": w, "sort_order": i})
    print("before:", sorted(cur)); print("after: ", target)
    if "--apply" in sys.argv:
        _replace_preset_skills(db, PRESET, rows)
        db.commit()
        try:
            from app.services.runs.bootstrap_cache import invalidate_bootstrap_cache
            invalidate_bootstrap_cache()
        except Exception:
            pass
        print("applied")
