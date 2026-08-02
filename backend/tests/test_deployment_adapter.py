from __future__ import annotations

from pathlib import Path

import pytest

from app.services.backtest.deployment_adapter import LocalPolicyDeploymentAdapter


def test_local_adapter_stages_and_rolls_back_dry_run_pointer(tmp_path: Path) -> None:
    adapter = LocalPolicyDeploymentAdapter(tmp_path, mode="dry_run")
    receipt = adapter.stage(policy_fingerprint="a" * 64, repo_commit="abc")

    assert receipt["mode"] == "dry_run"
    assert receipt["deployment_adapter"] == adapter.adapter_id
    assert adapter.candidate_pointer.exists()
    adapter.activate(receipt)
    assert not adapter.current_pointer.exists()
    result = adapter.rollback(receipt)
    assert result == {"ok": True, "rolled_back_to": None}
    assert not adapter.candidate_pointer.exists()


def test_local_adapter_rejects_tampered_receipt(tmp_path: Path) -> None:
    adapter = LocalPolicyDeploymentAdapter(tmp_path)
    receipt = adapter.stage(policy_fingerprint="b" * 64)
    receipt["deployed_policy_fingerprint"] = "c" * 64
    with pytest.raises(ValueError, match="receipt fingerprint"):
        adapter.verify_receipt(receipt)
