from __future__ import annotations

from typing import Any


def queued_run_item(run_id: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return str(run_id), dict(payload)
