"""脏执行树守卫测试：policy 指纹只在干净树上重绑治理状态。

背景：policy fingerprint 由导入的 app 包（脚本所在树）计算。多实例共享
state 时，主工作树常停在 feature 分支且带未提交 WIP；旧版 migrate-state
从脏树执行时会把 WIP 指纹当 identity，把 policy_artifact_mismatch 翻成
true（2026-09-13 实测：干净 main 树 v3=8c4821b9，主树 feature+WIP
=9707a4ca）。规则：脏树运行只记录 identity_context，不得重绑治理指纹、
不得重算 mismatch、不得重锚。
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
    "recorded_at": "2026-09-13T00:00:00+00:00",
}
CLEAN_CTX = {
    "tree_root": "/somewhere/clean",
    "commit": "c0ffee00",
    "backend_dirty": False,
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
