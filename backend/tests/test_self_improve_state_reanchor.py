"""reanchor-policy 子命令测试（发布状态机指纹重锚）。

背景：policy fingerprint 由 canonical_payload() 在当前代码版本下计算，
dataclass 演化后历史 champion 指纹不可复现；migrate-state 只 setdefault
governance 指纹、publish-champion 又被 policy_artifact_identity 门禁卡住，
状态机在任何改变 payload 的落地（如 v3 合并）之后死锁。reanchor-policy
是 CAS 守卫的显式逃生门。
"""

import json
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import self_improve_state as sis


def _loader(fingerprint: str, version: int = 1):
    def _load():
        return {
            "policy_id": "production-selection",
            "version": version,
            "fingerprint": fingerprint,
        }

    return _load


def test_reanchor_updates_identity_and_records_history(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    sis.migrate_state(memory_dir, policy_loader=_loader("fp-old"))

    result = sis.reanchor_policy_identity(
        memory_dir,
        from_fingerprint="fp-old",
        reason="v3 merged to main; champion fingerprint from older code revision",
        researcher_id="si-test",
        policy_loader=_loader("fp-new", version=3),
    )
    assert result["ok"] is True
    assert result["policy_artifact_mismatch"] is False

    state = json.loads((memory_dir / "self-improve-state.json").read_text())
    champ = state["canonical_production"]
    assert champ["selection_policy"]["fingerprint"] == "fp-new"
    assert champ["selection_policy"]["version"] == 3
    # champion 的其它字段（commit/metrics 等）不被触碰
    assert "policy_reanchor" in champ
    record = champ["policy_reanchor"]
    assert record["from_fingerprint"] == "fp-old"
    assert record["to_fingerprint"] == "fp-new"
    assert record["reason"]
    gov = state["selection_policy_governance"]
    assert gov["policy_artifact_fingerprint"] == "fp-new"
    assert gov["policy_artifact_mismatch"] is False
    history = state["state_migration"]["policy_reanchor_history"]
    assert len(history) == 1 and history[0]["to_fingerprint"] == "fp-new"

    # migrate-state 之后不再回退为 mismatch
    sis.migrate_state(memory_dir, policy_loader=_loader("fp-new", version=3))
    state = json.loads((memory_dir / "self-improve-state.json").read_text())
    assert state["selection_policy_governance"]["policy_artifact_mismatch"] is False


def test_reanchor_cas_rejects_wrong_fingerprint(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    sis.migrate_state(memory_dir, policy_loader=_loader("fp-old"))

    result = sis.reanchor_policy_identity(
        memory_dir,
        from_fingerprint="fp-wrong",
        reason="should fail",
        policy_loader=_loader("fp-new"),
    )
    assert result["ok"] is False
    assert result["error"] == "CAS_MISMATCH"

    state = json.loads((memory_dir / "self-improve-state.json").read_text())
    assert state["canonical_production"]["selection_policy"]["fingerprint"] == "fp-old"
    assert "policy_reanchor" not in state["canonical_production"]


def test_reanchor_rejects_when_already_anchored(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    sis.migrate_state(memory_dir, policy_loader=_loader("fp-same"))

    result = sis.reanchor_policy_identity(
        memory_dir,
        from_fingerprint="fp-same",
        reason="no-op",
        policy_loader=_loader("fp-same"),
    )
    assert result["ok"] is False
    assert result["error"] == "ALREADY_ANCHORED"
