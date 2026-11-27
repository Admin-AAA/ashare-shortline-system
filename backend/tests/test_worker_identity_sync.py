"""worker 身份文件：写/读往返；重建脚本入队前 revision 预检；sync 脚本只在漂移且空闲时重启。"""
from __future__ import annotations

import os

import pytest

from app.core import deployment_identity as di


def test_worker_identity_roundtrip(tmp_path):
    p = tmp_path / "ident.json"
    di.write_worker_identity("a" * 40, path=p)
    got = di.read_worker_identity(p)
    assert got["revision"] == "a" * 40 and got["pid"] == os.getpid() and got["started_at"]
    assert di.read_worker_identity(tmp_path / "missing.json") is None


def test_replay_precheck_rejects_drifted_worker(monkeypatch):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("replay_all_days", Path(__file__).resolve().parents[1] / "scripts" / "replay_all_days.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("ASHARE_APPLICATION_REVISION", "b" * 40)
    monkeypatch.setattr(di, "read_worker_identity", lambda path=None: {"revision": "a" * 40, "started_at": "2026-11-25T01:00:00+00:00"})
    with pytest.raises(SystemExit) as exc:
        mod._assert_worker_revision_matches()
    assert "不一致" in str(exc.value) and "kickstart" in str(exc.value)
    monkeypatch.setattr(di, "read_worker_identity", lambda path=None: {"revision": "b" * 40})
    mod._assert_worker_revision_matches()  # 一致 → 通过
    monkeypatch.setattr(di, "read_worker_identity", lambda path=None: None)
    mod._assert_worker_revision_matches()  # 没有文件 → 只警告


def test_sync_script_restarts_only_on_drift_and_idle(monkeypatch):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("sync_worker_revision", Path(__file__).resolve().parents[1] / "scripts" / "sync_worker_revision.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls: list[list[str]] = []
    monkeypatch.setattr(mod, "_git_head_revision", lambda: "c" * 40)
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)) or type("R", (), {"stdout": "501\n"})())
    # 同版本：不动
    monkeypatch.setattr(mod, "read_worker_identity", lambda path=None: {"revision": "c" * 40})
    assert mod.main() == 0 and calls == []
    # 漂移但有任务在跑：不动
    monkeypatch.setattr(mod, "read_worker_identity", lambda path=None: {"revision": "a" * 40})
    monkeypatch.setattr(mod.subprocess, "call", lambda cmd, **kw: 2)
    assert mod.main() == 0 and calls == []
    # 漂移且空闲：kickstart worker + 重启后端
    monkeypatch.setattr(mod.subprocess, "call", lambda cmd, **kw: 0)
    assert mod.main() == 0
    assert any("kickstart" in c for c in calls) and any(str(mod.BACKEND_RESTART) in c for c in calls)
