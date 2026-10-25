"""Local, receipt-backed deployment adapter for policy canary tests.

The adapter never changes the live production artifact.  In its default
``dry_run`` mode it only stages a content-addressed candidate pointer, which
lets the release state machine verify deployment/canary/rollback semantics
without an external deployment service.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
from uuid import uuid4

from app.engine.backtest import canonical_fingerprint


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DeploymentReceipt:
    deployment_id: str
    deployment_adapter: str
    deployment_fingerprint: str
    deployed_policy_fingerprint: str
    previous_policy_fingerprint: str | None
    repo_commit: str
    mode: str
    created_at: str

    def payload(self) -> dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "deployment_adapter": self.deployment_adapter,
            "deployed_policy_fingerprint": self.deployed_policy_fingerprint,
            "previous_policy_fingerprint": self.previous_policy_fingerprint,
            "repo_commit": self.repo_commit,
            "mode": self.mode,
            "created_at": self.created_at,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.payload())

    def as_dict(self) -> dict[str, Any]:
        return {**self.payload(), "deployment_fingerprint": self.fingerprint}


class LocalPolicyDeploymentAdapter:
    adapter_id = "local-policy-adapter:v1"

    def __init__(self, root: Path | None = None, *, mode: str = "dry_run") -> None:
        if mode not in {"dry_run", "active"}:
            raise ValueError("deployment adapter mode must be dry_run or active")
        default_root = Path(
            os.environ.get(
                "SELF_IMPROVE_DEPLOYMENT_DIR",
                str(Path.home() / ".claude/projects/-Users-wangao/memory/policy-deployments"),
            )
        )
        self.root = Path(root or default_root)
        self.mode = mode

    @property
    def candidate_pointer(self) -> Path:
        return self.root / "candidate-policy.json"

    @property
    def current_pointer(self) -> Path:
        return self.root / "current-policy.json"

    def stage(
        self,
        *,
        policy_fingerprint: str,
        repo_commit: str = "",
    ) -> dict[str, Any]:
        previous = self._read_pointer(self.current_pointer)
        receipt = DeploymentReceipt(
            deployment_id=f"deploy_{uuid4().hex}",
            deployment_adapter=self.adapter_id,
            deployment_fingerprint="",
            deployed_policy_fingerprint=str(policy_fingerprint),
            previous_policy_fingerprint=(
                str(previous.get("policy_fingerprint")) if previous else None
            ),
            repo_commit=str(repo_commit),
            mode=self.mode,
            created_at=_now_iso(),
        )
        value = receipt.as_dict()
        self._atomic_write(self.candidate_pointer, value)
        return value

    def activate(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        verified = self.verify_receipt(receipt)
        if verified["mode"] == "active":
            self._atomic_write(
                self.current_pointer,
                {
                    "policy_fingerprint": verified["deployed_policy_fingerprint"],
                    "deployment_fingerprint": verified["deployment_fingerprint"],
                },
            )
        return verified

    def rollback(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        verified = self.verify_receipt(receipt)
        previous = verified.get("previous_policy_fingerprint")
        if previous:
            self._atomic_write(
                self.current_pointer,
                {"policy_fingerprint": previous, "rolled_back_from": verified["deployment_fingerprint"]},
            )
        elif self.current_pointer.exists():
            self.current_pointer.unlink()
        if self.candidate_pointer.exists():
            self.candidate_pointer.unlink()
        return {"ok": True, "rolled_back_to": previous}

    @staticmethod
    def verify_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(receipt)
        expected = value.pop("deployment_fingerprint", None)
        if not expected or canonical_fingerprint(value) != expected:
            raise ValueError("invalid deployment receipt fingerprint")
        if value.get("deployment_adapter") != LocalPolicyDeploymentAdapter.adapter_id:
            raise ValueError("unknown deployment adapter")
        if value.get("mode") not in {"dry_run", "active"}:
            raise ValueError("invalid deployment receipt mode")
        return {**value, "deployment_fingerprint": expected}

    @staticmethod
    def _read_pointer(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None

    @staticmethod
    def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(dict(value), handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise


__all__ = ["DeploymentReceipt", "LocalPolicyDeploymentAdapter"]
