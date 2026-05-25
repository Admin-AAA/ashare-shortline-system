from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AcceptanceMode:
    dry_run: bool = True
    execute: bool = False

    @property
    def can_write(self) -> bool:
        return bool(self.execute and not self.dry_run)


def build_batch_id(now: datetime | None = None) -> str:
    current = now or datetime.now()
    return f"acceptance-{current.strftime('%Y%m%d-%H%M%S')}"


def tag_acceptance_name(name: str, batch_id: str) -> str:
    base = str(name).strip() or "验收对象"
    return f"{base} [{batch_id}]"


def _is_production_database(database_url: str) -> bool:
    lowered = str(database_url or "").lower()
    if "ashare_test" in lowered or "127.0.0.1" in lowered or "localhost" in lowered:
        return False
    return "ashare_workbench" in lowered or "mysql" in lowered


def assert_destructive_allowed(*, database_url: str, requested: bool) -> None:
    if not requested:
        raise RuntimeError("destructive operation blocked: explicit flag is required")
    if _is_production_database(database_url) and os.environ.get("ASHARE_ALLOW_PRODUCTION_DESTRUCTIVE") != "1":
        raise RuntimeError("destructive operation blocked: production database requires ASHARE_ALLOW_PRODUCTION_DESTRUCTIVE=1")
