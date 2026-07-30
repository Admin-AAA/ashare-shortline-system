"""Alembic-backed database initialization for the demo system."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.db.migrations.health import mark_migration_error, mark_migration_healthy, mark_migration_running


BASELINE_REVISION = "0001_demo_baseline"
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class AlembicUpgradeResult:
    current_revision: str
    adopted_existing_schema: bool


def _config(connection) -> Config:
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def upgrade_database(engine: Engine) -> AlembicUpgradeResult:
    """Upgrade an empty database or stamp the current demo schema as the baseline."""
    mark_migration_running()
    adopted = False
    try:
        with engine.begin() as connection:
            tables = set(inspect(connection).get_table_names())
            config = _config(connection)
            if tables and "alembic_version" not in tables:
                command.stamp(config, "head")
                adopted = True
            else:
                command.upgrade(config, "head")
            revision = MigrationContext.configure(connection).get_current_revision()
        if revision != BASELINE_REVISION:
            raise RuntimeError(f"unexpected Alembic revision: {revision!r}")
        mark_migration_healthy(
            applied_versions=(() if adopted else (revision,)),
            skipped_versions=((revision,) if adopted else ()),
        )
        return AlembicUpgradeResult(revision, adopted)
    except Exception as error:
        mark_migration_error(error)
        raise
