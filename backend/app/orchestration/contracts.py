"""Stable application contracts for Prefect-backed orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OrchestrationRunRef:
    run_id: str
    deployment_name: str


@dataclass(frozen=True, slots=True)
class DeploymentSyncResult:
    scheduled_task_count: int
    deployment_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OrchestrationRunView:
    run_id: str
    state: str
    name: str
    parameters: dict[str, Any]
