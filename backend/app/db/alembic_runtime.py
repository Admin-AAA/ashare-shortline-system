"""Alembic-backed database initialization for the demo system."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

BASELINE_REVISION = "0001_demo_baseline"
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class AlembicUpgradeResult:
    current_revision: str
    head_revision: str
    adopted_existing_schema: bool


@dataclass(frozen=True, slots=True)
class AlembicHealth:
    status: Literal["not_run", "running", "ok", "error"] = "not_run"
    current_revision: str | None = None
    head_revision: str | None = None
    error_code: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status,
            "current_revision": self.current_revision,
            "head_revision": self.head_revision,
            "error_code": self.error_code,
        }


_health_lock = Lock()
_health = AlembicHealth()


def get_alembic_health() -> AlembicHealth:
    with _health_lock:
        return _health


def _set_health(value: AlembicHealth) -> None:
    global _health
    with _health_lock:
        _health = value


def _config(connection) -> Config:
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def upgrade_database(engine: Engine) -> AlembicUpgradeResult:
    """Upgrade an empty database or stamp the current demo schema as the baseline."""
    _set_health(AlembicHealth(status="running"))
    adopted = False
    try:
        with engine.begin() as connection:
            tables = set(inspect(connection).get_table_names())
            config = _config(connection)
            if tables and "alembic_version" not in tables:
                command.stamp(config, BASELINE_REVISION)
                adopted = True
            command.upgrade(config, "head")
            revision = MigrationContext.configure(connection).get_current_revision()
            script = config.attributes.get("script")
            if script is None:
                from alembic.script import ScriptDirectory

                script = ScriptDirectory.from_config(config)
            heads = script.get_heads()
        if len(heads) != 1 or revision != heads[0]:
            raise RuntimeError(f"database revision {revision!r} does not match Alembic head {heads!r}")
        head = heads[0]
        _set_health(AlembicHealth(status="ok", current_revision=revision, head_revision=head))
        return AlembicUpgradeResult(revision, head, adopted)
    except Exception as error:
        _set_health(AlembicHealth(status="error", error_code=type(error).__name__))
        raise
