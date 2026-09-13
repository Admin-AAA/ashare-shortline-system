"""identity_context 记录测试：状态工具执行的树上下文必须落 governance。

背景：policy fingerprint 由导入的 app 包（脚本所在树）计算；多实例共享
state 时，从带未提交 WIP 的树跑 migrate-state/refresh-policy-health 会让
policy_artifact_mismatch 翻动（2026-09-13 实测：main eb78093 identity
8c4821b9 vs 主树 feature+WIP 9707a4ca）。每次健康检查/迁移记录执行树上下文
使翻动可审计。
"""

import json
import subprocess
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import self_improve_state as sis

TREE_ROOT = Path(sis.__file__).resolve().parents[2]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(TREE_ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_identity_context_reports_commit_and_dirty_flag():
    context = sis._identity_context()
    assert context["tree_root"] == str(TREE_ROOT)
    assert context["commit"] == _git("rev-parse", "HEAD")
    expected_dirty = bool(_git("status", "--porcelain", "--", "backend"))
    assert context["backend_dirty"] is expected_dirty
    assert context["recorded_at"]


def test_refresh_policy_health_records_identity_context(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    result = sis.refresh_policy_health(
        memory_dir, health_loader=lambda: {"strategy_inactive": False}
    )
    context = result["identity_context"]
    assert context["commit"] == _git("rev-parse", "HEAD")
    state = json.loads((memory_dir / "self-improve-state.json").read_text())
    assert state["selection_policy_governance"]["identity_context"]["commit"] == context["commit"]


def test_migrate_state_records_identity_context(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    def loader():
        return {"policy_id": "production-selection", "version": 3, "fingerprint": "fp-x"}

    sis.migrate_state(memory_dir, policy_loader=loader)
    state = json.loads((memory_dir / "self-improve-state.json").read_text())
    context = state["selection_policy_governance"]["identity_context"]
    assert context["commit"] == _git("rev-parse", "HEAD")
