"""Public migration runner API used by production database initialization."""
from __future__ import annotations

from sqlalchemy.engine import Engine

from .health import MigrationHealth, get_migration_health
from .runner import MigrationRunResult, MigrationRunner
from .versions import all_migrations


def run_migrations(engine: Engine) -> MigrationRunResult:
    """Run the complete built-in forward-only migration sequence."""
    return MigrationRunner(engine, all_migrations()).run()


__all__ = [
    "MigrationHealth",
    "MigrationRunResult",
    "MigrationRunner",
    "all_migrations",
    "get_migration_health",
    "run_migrations",
]
