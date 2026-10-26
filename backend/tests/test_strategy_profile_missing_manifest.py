"""画像补跑：缺 manifest.json 的策略目录跳过并告警，不让整个任务 failed；主链路加载器仍严格。"""
from __future__ import annotations

import shutil

import pytest

from app.core.config import get_settings
from app.engine import skill_loader as sl
from app.services import scheduler_service


@pytest.fixture()
def skills_with_bad_dir(tmp_path):
    real = get_settings().skills_dir
    good = next(p for p in sorted(real.iterdir()) if p.is_dir() and (p / "manifest.json").is_file())
    dst = tmp_path / "skills"
    shutil.copytree(good, dst / good.name)
    (dst / "retired-leftover").mkdir()
    (dst / "retired-leftover" / "notes.txt").write_text("残留文件，没有 manifest")
    return dst, good.name


def test_strict_loader_still_raises_on_missing_manifest(skills_with_bad_dir):
    dst, _ = skills_with_bad_dir
    with pytest.raises(sl.SkillManifestError):
        sl.load_skills(dst)


def test_lenient_loader_skips_missing_manifest_only(skills_with_bad_dir):
    dst, good = skills_with_bad_dir
    skipped: list[str] = []
    with sl.lenient_missing_manifest(skipped):
        skills = sl.load_skills(dst)
    assert [s.skill_id for s in skills] == [good] or good in {s.skill_id for s in skills}
    assert [p.rsplit("/", 1)[-1] for p in skipped] == ["retired-leftover"]
    with pytest.raises(sl.SkillManifestError):  # with 块外恢复严格
        sl.load_skills(dst)


def test_lenient_loader_does_not_swallow_invalid_manifest(skills_with_bad_dir):
    dst, _ = skills_with_bad_dir
    bad = dst / "broken"
    bad.mkdir()
    (bad / "manifest.json").write_text("{not json")
    with sl.lenient_missing_manifest([]):
        with pytest.raises(Exception):
            sl.load_skills(dst)


def test_profile_task_skips_bad_dir_and_alerts(monkeypatch, skills_with_bad_dir):
    import app.services.backtest.strategy_profile as sp

    dst, _ = skills_with_bad_dir
    alerts: list[tuple] = []
    monkeypatch.setattr("app.core.alerts.emit_alert", lambda kind, key, msg, **k: alerts.append((kind, key, msg)) or True)
    monkeypatch.setattr("app.core.alerts.resolve_alert", lambda *a, **k: True)

    class T:
        strategy_id, strategy_version = "s1", "v1"

    class FakeProfiles:
        def __init__(self, db):
            pass

        def current_targets(self):
            sl.load_skills(dst)  # 真实走加载器：在宽容上下文里不应抛错
            return [T()]

        def read_rolled(self, t):
            return None

        def refresh(self, t):
            return {"status": "completed", "reused": False}

    monkeypatch.setattr(sp, "StrategyBacktestProfiles", FakeProfiles)
    out = scheduler_service._run_strategy_profile_refresh(None, {"max_runs": 1})
    assert "status" not in out and out["executed"] == 1
    assert [p.rsplit("/", 1)[-1] for p in out["skipped_missing_manifest"]] == ["retired-leftover"]
    assert "跳过缺 manifest 目录 1" in out["summary_text"]
    assert alerts and alerts[0][0] == "strategy_profile_missing_manifest" and "retired-leftover" in alerts[0][2]
