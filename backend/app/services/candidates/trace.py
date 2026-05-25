from __future__ import annotations

from typing import Any


def candidate_trace_row(
    *,
    code: str,
    stage: str,
    passed: bool,
    reason: str = "",
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": str(code),
        "stage": str(stage),
        "passed": bool(passed),
        "reason": str(reason or ""),
        "metrics": dict(metrics or {}),
    }
