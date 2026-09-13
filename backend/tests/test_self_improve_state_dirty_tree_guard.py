"""执行树守卫测试：policy 指纹只在「main 生产点」树上重绑治理状态。

背景：policy fingerprint 由导入的 app 包（脚本所在树）计算。多实例共享
state 时，两类执行树都不得重绑治理指纹/重算 mismatch/重锚：

1. 脏树（T-102 / L-102）：主工作树常停在 feature 分支且带未提交 WIP，
   WIP 指纹不是任何已提交 artifact（2026-09-13 实测 9707a4ca vs 8c4821b9）。
2. 干净但非 main 生产点的树（T-104）：worktree 停在落后/分叉分支上虽然
   干净，但其 identity 反映该分支代码而非生产真值（2026-09-13 实测：
   feat/si-fina-indicator-pit @31759cf 用合并前代码重算 v3 指纹，把
   policy_artifact_mismatch 翻回 true，重新打开 T-098 死锁）。

规则：只有 backend 干净且 HEAD 恰为 main tip 的执行树才允许重算 mismatch、
重绑治理指纹或重锚；其余情况只记录 identity_context 供审计。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import self_improve_state as sis

BASE_CHAMPION_FP = "fp-champion"
BASE_CHAMPION = {
    "policy_id": "production-selection",
    "version": 3,
    "fingerprint": BASE_CHAMPION_FP,
}
DIRTY_CTX = {
    "tree_root": "/somewhere/dirty",
    "commit": "deadbeef",
    "backend_dirty": True,
    "main_tip": "c0ffee00",
    "production_owned": False,
    "recorded_at": "2026-09-13T00:00:00+00:00",
}
CLEAN_CTX = {
    "tree_root": "/somewhere/clean",
    "commit": "c0ffee00",
    "backend_dirty": False,
    "main_tip": "c0ffee00",
    "production_owned": True,
    "recorded_at": "2026-09-13T00:00:00+00:00",
}
# 干净但停在分叉分支：HEAD != main tip（T-104 场景）
STALE_BRANCH_CTX = {
    "tree_root": "/somewhere/stale-branch",
    "commit": "31759cf",
    "backend_dirty": False,
    "main_tip": "c0ffee00",
    "production_owned": False,
    "recorded_at": "2026-09-13T00:00:00+00:00",
}


def _write_base_state(memory_dir: Path) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": 4,
        "canonical_production": {"selection_policy": dict(BASE_CHAMPION)},
        "selection_policy_governance": {
            "mode": "autonomous",
            "phase": "audit",
            "audit_only": True,
            "policy_artifact_fingerprint": BASE_CHAMPION_FP,
            "policy_artifact_mismatch": False,
        },
        "release_state": {"status": "draft"},
        "holdout_ledger": {"consumed": {}},
    }
    (memory_dir / "self-improve-state.json").write_text(json.dumps(state))


def _read_state(memory_dir: Path) -> dict:
    return json.loads((memory_dir / "self-improve-state.json").read_text())


def _loader(fp: str):
    def load():
        return {"policy_id": "production-selection", "version": 3, "fingerprint": fp}

    return load


def test_migrate_state_dirty_tree_does_not_rebind_governance(tmp_path, monkeypatch):
    memory_dir = tmp_path / "memory"
    _write_base_state(memory_dir)
    monkeypatch.setattr(sis, "_identity_context", lambda: dict(DIRTY_CTX))

    sis.migrate_state(memory_dir, policy_loader=_loader("fp-wip"))

    state = _read_state(memory_dir)
    gov = state["selection_policy_governance"]
    # 指纹与 mismatch 保持原值，不被 WIP 指纹污染
    assert gov["policy_artifact_fingerprint"] == BASE_CHAMPION_FP
    assert gov["policy_artifact_mismatch"] is False
    assert state["canonical_production"]["selection_policy"]["fingerprint"] == BASE_CHAMPION_FP
    # 执行树上下文仍被记录，供审计
    assert gov["identity_context"]["backend_dirty"] is True


def test_migrate_state_clean_tree_recomputes_mismatch(tmp_path, monkeypatch):
    memory_dir = tmp_path / "memory"
    _write_base_state(memory_dir)
    monkeypatch.setattr(sis, "_identity_context", lambda: dict(CLEAN_CTX))

    # 干净树的新 identity 与已注册 champion 不同 → mismatch 必须如实报 true
    sis.migrate_state(memory_dir, policy_loader=_loader("fp-new"))

    state = _read_state(memory_dir)
    gov = state["selection_policy_governance"]
    assert gov["policy_artifact_fingerprint"] == BASE_CHAMPION_FP  # setdefault 只在缺失时
    assert gov["policy_artifact_mismatch"] is True
    assert gov["identity_context"]["backend_dirty"] is False


def test_refresh_policy_health_clean_tree_self_heals_mismatch(tmp_path, monkeypatch):
    memory_dir = tmp_path / "memory"
    _write_base_state(memory_dir)
    # 模拟脏树运行留下的污染：mismatch 被翻成 true
    state = _read_state(memory_dir)
    state["selection_policy_governance"]["policy_artifact_mismatch"] = True
    (memory_dir / "self-improve-state.json").write_text(json.dumps(state))
    monkeypatch.setattr(sis, "_identity_context", lambda: dict(CLEAN_CTX))
    monkeypatch.setattr(
        sis,
        "_current_policy_identity",
        lambda: dict(BASE_CHAMPION),
    )

    result = sis.refresh_policy_health(
        memory_dir, health_loader=lambda: {"strategy_inactive": False}
    )
    assert result["policy_artifact_mismatch"] is False


def test_refresh_policy_health_dirty_tree_leaves_mismatch_untouched(tmp_path, monkeypatch):
    memory_dir = tmp_path / "memory"
    _write_base_state(memory_dir)
    state = _read_state(memory_dir)
    state["selection_policy_governance"]["policy_artifact_mismatch"] = True
    (memory_dir / "self-improve-state.json").write_text(json.dumps(state))
    monkeypatch.setattr(sis, "_identity_context", lambda: dict(DIRTY_CTX))

    result = sis.refresh_policy_health(
        memory_dir, health_loader=lambda: {"strategy_inactive": False}
    )
    assert result["policy_artifact_mismatch"] is True
    assert result["identity_context"]["backend_dirty"] is True


def test_reanchor_policy_dirty_tree_rejected(tmp_path, monkeypatch):
    memory_dir = tmp_path / "memory"
    _write_base_state(memory_dir)
    monkeypatch.setattr(sis, "_identity_context", lambda: dict(DIRTY_CTX))

    result = sis.reanchor_policy_identity(
        memory_dir,
        from_fingerprint=BASE_CHAMPION_FP,
        reason="test",
        researcher_id="si-test",
        policy_loader=_loader("fp-wip"),
    )
    assert result["ok"] is False
    assert result["error"] == "DIRTY_TREE"
    # 状态完全不变
    state = _read_state(memory_dir)
    gov = state["selection_policy_governance"]
    assert gov["policy_artifact_fingerprint"] == BASE_CHAMPION_FP
    assert state["canonical_production"]["selection_policy"]["fingerprint"] == BASE_CHAMPION_FP


def test_migrate_state_stale_branch_tree_does_not_flip_mismatch(tmp_path, monkeypatch):
    """T-104：干净但非 main 生产点的树不得重算 mismatch。"""
    memory_dir = tmp_path / "memory"
    _write_base_state(memory_dir)
    monkeypatch.setattr(sis, "_identity_context", lambda: dict(STALE_BRANCH_CTX))

    # 分叉树上的 identity 与治理注册不同——但该树不是生产真值，不得翻 flag
    sis.migrate_state(memory_dir, policy_loader=_loader("fp-stale-branch"))

    state = _read_state(memory_dir)
    gov = state["selection_policy_governance"]
    assert gov["policy_artifact_fingerprint"] == BASE_CHAMPION_FP
    assert gov["policy_artifact_mismatch"] is False
    assert state["canonical_production"]["selection_policy"]["fingerprint"] == BASE_CHAMPION_FP
    # 执行树上下文仍被记录，供审计
    assert gov["identity_context"]["production_owned"] is False
    assert gov["identity_context"]["backend_dirty"] is False


def test_refresh_policy_health_stale_branch_tree_leaves_mismatch_untouched(tmp_path, monkeypatch):
    """T-104：refresh 的自愈重算同样只在 main 生产点树上发生。"""
    memory_dir = tmp_path / "memory"
    _write_base_state(memory_dir)
    monkeypatch.setattr(sis, "_identity_context", lambda: dict(STALE_BRANCH_CTX))
    # 用与治理注册不同的 identity，模拟分叉树算出的指纹
    monkeypatch.setattr(
        sis,
        "_current_policy_identity",
        lambda: {"policy_id": "production-selection", "version": 3, "fingerprint": "fp-stale-branch"},
    )

    result = sis.refresh_policy_health(
        memory_dir, health_loader=lambda: {"strategy_inactive": False}
    )
    assert result["policy_artifact_mismatch"] is False  # 未被分叉树指纹翻 true
    assert result["identity_context"]["production_owned"] is False


def test_identity_context_marks_production_owned(tmp_path):
    """_identity_context 实测：main tip 工作树 → production_owned=True。"""
    import subprocess

    # 用临时 git 仓库模拟 worktree：main 与 HEAD 同点
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    # 在仓库内建 backend/scripts/（镜像真实布局 backend/scripts/*.py，
    # 使 parents[2] = 仓库根），先复制再提交保持树干净
    scripts = repo / "backend" / "scripts"
    scripts.mkdir(parents=True)
    import shutil
    shutil.copy(Path(sis.__file__), scripts / "self_improve_state.py")
    git("add", "-A")
    git("commit", "-qm", "init")

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "sis_ctx", scripts / "self_improve_state.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    ctx = mod._identity_context()
    assert ctx["production_owned"] is True
    assert ctx["backend_dirty"] is False
    assert ctx["main_tip"] == ctx["commit"]
