"""Connection-scoped, forward-only migration runner."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from time import perf_counter
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .contract import (
    Migration,
    MigrationChecksumMismatchError,
    MigrationConditionError,
    MigrationDefinitionError,
    MigrationError,
    MigrationLockError,
    MigrationContext,
    OperationState,
)
from .health import mark_migration_error, mark_migration_healthy, mark_migration_running

logger = logging.getLogger(__name__)

_LEDGER_TABLE = "schema_migrations"
_DEFAULT_LOCK_NAME = "ashare_schema_migration"


@dataclass(frozen=True)
class MigrationRunResult:
    applied_versions: tuple[str, ...]
    skipped_versions: tuple[str, ...]


class MigrationRunner:
    """Run all migrations on one connection and hold its MySQL advisory lock until done."""

    def __init__(
        self,
        engine: Engine,
        migrations: Iterable[Migration],
        *,
        lock_name: str = _DEFAULT_LOCK_NAME,
        lock_timeout_seconds: int = 30,
        lock_wait_timeout_seconds: int = 15,
        innodb_lock_wait_timeout_seconds: int = 15,
        preflight_query_timeout_milliseconds: int = 30_000,
    ) -> None:
        if not lock_name:
            raise MigrationDefinitionError("migration lock name is required")
        if lock_timeout_seconds < 0:
            raise MigrationDefinitionError("migration lock timeout must be non-negative")
        if lock_wait_timeout_seconds < 1 or innodb_lock_wait_timeout_seconds < 1:
            raise MigrationDefinitionError("migration lock wait timeouts must be positive")
        if preflight_query_timeout_milliseconds < 1:
            raise MigrationDefinitionError("preflight query timeout must be positive")
        self._engine = engine
        self._migrations = self._validate_migrations(migrations)
        self._lock_name = lock_name
        self._lock_timeout_seconds = lock_timeout_seconds
        self._lock_wait_timeout_seconds = lock_wait_timeout_seconds
        self._innodb_lock_wait_timeout_seconds = innodb_lock_wait_timeout_seconds
        self._preflight_query_timeout_milliseconds = preflight_query_timeout_milliseconds

    def run(self) -> MigrationRunResult:
        """Execute in version order; any failure propagates and leaves no success ledger row."""
        mark_migration_running()
        connection = None
        lock_acquired = False
        pending_error: Exception | None = None
        release_error: Exception | None = None
        result: MigrationRunResult | None = None
        try:
            connection = self._engine.connect()
            if connection.dialect.name == "mysql":
                self._acquire_mysql_lock(connection)
                lock_acquired = True
                self._configure_mysql_session(connection)
            context = MigrationContext(connection)
            self._ensure_ledger(context, connection)
            ledger = self._read_ledger(connection)
            applied: list[str] = []
            skipped: list[str] = []
            for migration in self._migrations:
                recorded_checksum = ledger.get(migration.version)
                if recorded_checksum is not None:
                    if recorded_checksum != migration.checksum:
                        raise MigrationChecksumMismatchError(
                            f"migration checksum mismatch for {migration.version}",
                            version=migration.version,
                        )
                    skipped.append(migration.version)
                    self._log_migration(migration, status="noop", execution_ms=0)
                    continue
                started = perf_counter()
                self._apply_migration(context, migration)
                execution_ms = max(0, int((perf_counter() - started) * 1000))
                self._write_ledger(connection, migration, execution_ms)
                ledger[migration.version] = migration.checksum
                applied.append(migration.version)
                self._log_migration(migration, status="applied", execution_ms=execution_ms)
            result = MigrationRunResult(tuple(applied), tuple(skipped))
        except Exception as error:  # The caller must receive every schema failure.
            pending_error = error
            if connection is not None:
                self._rollback_if_possible(connection)
        finally:
            if connection is not None:
                if lock_acquired:
                    try:
                        self._release_mysql_lock(connection)
                    except Exception as error:  # Do not silently lose a connection-scoped lock release failure.
                        release_error = error
                connection.close()

        if pending_error is not None:
            mark_migration_error(pending_error)
            raise pending_error.with_traceback(pending_error.__traceback__)
        if release_error is not None:
            mark_migration_error(release_error)
            raise release_error.with_traceback(release_error.__traceback__)
        assert result is not None
        mark_migration_healthy(
            applied_versions=result.applied_versions,
            skipped_versions=result.skipped_versions,
        )
        return result

    def _ensure_ledger(self, context: MigrationContext, connection) -> None:
        context.create_table(
            _LEDGER_TABLE,
            (
                ("version", "VARCHAR(64) PRIMARY KEY"),
                ("description", "VARCHAR(255) NOT NULL"),
                ("checksum", "CHAR(64) NOT NULL"),
                ("applied_at", "DATETIME(6) NOT NULL"),
                ("execution_ms", "INTEGER NOT NULL"),
            ),
        )
        connection.commit()

    @staticmethod
    def _read_ledger(connection) -> dict[str, str]:
        rows = connection.execute(text(f"SELECT version, checksum FROM {_LEDGER_TABLE}")).all()
        return {str(version): str(checksum) for version, checksum in rows}

    @staticmethod
    def _write_ledger(connection, migration: Migration, execution_ms: int) -> None:
        connection.execute(
            text(
                f"INSERT INTO {_LEDGER_TABLE} "
                "(version, description, checksum, applied_at, execution_ms) "
                "VALUES (:version, :description, :checksum, :applied_at, :execution_ms)"
            ),
            {
                "version": migration.version,
                "description": migration.description,
                "checksum": migration.checksum,
                "applied_at": datetime.now(timezone.utc).replace(tzinfo=None),
                "execution_ms": execution_ms,
            },
        )
        connection.commit()

    @staticmethod
    def _apply_migration(context: MigrationContext, migration: Migration) -> None:
        for operation in migration.operations:
            state = operation.precondition(context)
            if state not in (OperationState.PENDING, OperationState.SATISFIED):
                raise MigrationConditionError(
                    f"invalid precondition result for {migration.version}:{operation.name}",
                    version=migration.version,
                )
            if state is OperationState.PENDING:
                operation.apply(context)
            post_state = operation.postcondition(context)
            if post_state is not OperationState.SATISFIED:
                raise MigrationConditionError(
                    f"postcondition not satisfied for {migration.version}:{operation.name}",
                    version=migration.version,
                )
        migration.verify(context)

    def _acquire_mysql_lock(self, connection) -> None:
        acquired = connection.scalar(
            text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
            {"lock_name": self._lock_name, "timeout_seconds": self._lock_timeout_seconds},
        )
        if acquired != 1:
            raise MigrationLockError(
                f"migration lock unavailable: {self._lock_name}",
            )

    def _configure_mysql_session(self, connection) -> None:
        """Bound metadata/InnoDB lock waits and the preflight SELECT time budget.

        MySQL's ``max_execution_time`` applies to read statements, not DDL.  DDL
        lock waits are therefore bounded through the two lock-wait session values;
        a timed-out DDL is never force-killed because its final schema state must
        be inspected before any safe retry.
        """
        connection.execute(
            text("SET SESSION lock_wait_timeout = :seconds"),
            {"seconds": self._lock_wait_timeout_seconds},
        )
        connection.execute(
            text("SET SESSION innodb_lock_wait_timeout = :seconds"),
            {"seconds": self._innodb_lock_wait_timeout_seconds},
        )
        connection.execute(
            text("SET SESSION max_execution_time = :milliseconds"),
            {"milliseconds": self._preflight_query_timeout_milliseconds},
        )

    def _release_mysql_lock(self, connection) -> None:
        released = connection.scalar(
            text("SELECT RELEASE_LOCK(:lock_name)"),
            {"lock_name": self._lock_name},
        )
        if released != 1:
            raise MigrationLockError(
                f"migration lock release failed: {self._lock_name}",
            )

    @staticmethod
    def _rollback_if_possible(connection) -> None:
        try:
            if connection.in_transaction():
                connection.rollback()
        except Exception:
            logger.exception("migration rollback failed")

    @staticmethod
    def _validate_migrations(migrations: Iterable[Migration]) -> tuple[Migration, ...]:
        ordered = tuple(sorted(migrations, key=lambda migration: migration.version))
        versions = [migration.version for migration in ordered]
        if len(versions) != len(set(versions)):
            raise MigrationDefinitionError("duplicate migration version")
        return ordered

    @staticmethod
    def _log_migration(migration: Migration, *, status: str, execution_ms: int) -> None:
        logger.info(
            "schema migration version=%s checksum=%s status=%s execution_ms=%d",
            migration.version,
            migration.checksum,
            status,
            execution_ms,
        )
