"""Process-local migration health state for startup and the health endpoint."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from .contract import MigrationError


@dataclass(frozen=True)
class MigrationHealth:
    status: str
    applied_versions: tuple[str, ...] = ()
    skipped_versions: tuple[str, ...] = ()
    error_code: str | None = None
    failed_version: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "applied_versions": list(self.applied_versions),
            "skipped_versions": list(self.skipped_versions),
            "error_code": self.error_code,
            "failed_version": self.failed_version,
        }


_lock = Lock()
_state = MigrationHealth(status="not_run")


def get_migration_health() -> MigrationHealth:
    with _lock:
        return _state


def mark_migration_running() -> None:
    global _state
    with _lock:
        _state = MigrationHealth(status="running")


def mark_migration_healthy(*, applied_versions: tuple[str, ...], skipped_versions: tuple[str, ...]) -> None:
    global _state
    with _lock:
        _state = MigrationHealth(
            status="ok",
            applied_versions=applied_versions,
            skipped_versions=skipped_versions,
        )


def mark_migration_error(error: Exception) -> None:
    global _state
    if isinstance(error, MigrationError):
        error_code = error.code
        failed_version = error.version
    else:
        error_code = "migration_execution_error"
        failed_version = None
    with _lock:
        _state = MigrationHealth(
            status="error",
            error_code=error_code,
            failed_version=failed_version,
        )
