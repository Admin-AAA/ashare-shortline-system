"""Contract tests for the forward-only schema migration foundation."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from app.db.migrations import all_migrations
from app.db.migrations.contract import (
    MigrationBaselineError,
    MigrationChecksumMismatchError,
    MigrationDefinitionError,
    MigrationLockError,
    MigrationOperation,
    MigrationSafetyError,
    OperationState,
    define_migration,
)
from app.db.migrations.runner import MigrationRunner


def _table_operation(table: str, *, checksum_material: str | None = None) -> MigrationOperation:
    def state(context):
        return OperationState.SATISFIED if context.has_table(table) else OperationState.PENDING

    def apply(context):
        context.create_table(table, (("id", "INTEGER PRIMARY KEY"),))

    return MigrationOperation(
        name=f"create_{table}",
        checksum_material=checksum_material or f"create table {table} with integer primary key",
        precondition=state,
        apply=apply,
        postcondition=state,
    )


def _migration(version: str, table: str, *, checksum_material: str | None = None):
    return define_migration(
        version=version,
        description=f"Create {table}",
        operations=(_table_operation(table, checksum_material=checksum_material),),
    )


def test_runner_applies_once_then_records_a_noop() -> None:
    engine = create_engine("sqlite:///:memory:")
    migration = _migration("V101", "migration_once")
    runner = MigrationRunner(engine, (migration,))

    first = runner.run()
    second = runner.run()

    assert first.applied_versions == ("V101",)
    assert second.applied_versions == ()
    assert second.skipped_versions == ("V101",)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM schema_migrations")) == 1
        assert connection.scalar(text("SELECT checksum FROM schema_migrations WHERE version = 'V101'")) == migration.checksum


def test_checksum_mismatch_stops_before_a_new_operation_runs() -> None:
    engine = create_engine("sqlite:///:memory:")
    original = _migration("V102", "checksum_fixture", checksum_material="first immutable definition")
    MigrationRunner(engine, (original,)).run()
    changed = _migration("V102", "checksum_fixture", checksum_material="changed immutable definition")

    with pytest.raises(MigrationChecksumMismatchError) as error:
        MigrationRunner(engine, (changed,)).run()

    assert error.value.version == "V102"
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM schema_migrations")) == 1


def test_operation_is_reentrant_after_failure_before_ledger_write() -> None:
    engine = create_engine("sqlite:///:memory:")
    attempts = 0
    first = _table_operation("reentry_first")

    def second_state(context):
        return OperationState.SATISFIED if context.has_table("reentry_second") else OperationState.PENDING

    def fail_once_then_create(context):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected failure after first DDL")
        context.create_table("reentry_second", (("id", "INTEGER PRIMARY KEY"),))

    second = MigrationOperation(
        name="create_reentry_second",
        checksum_material="create reentry_second after independently verified reentry_first",
        precondition=second_state,
        apply=fail_once_then_create,
        postcondition=second_state,
    )
    migration = define_migration(
        version="V103",
        description="Verify reentrant multi-operation migration",
        operations=(first, second),
    )
    runner = MigrationRunner(engine, (migration,))

    with pytest.raises(RuntimeError, match="injected failure"):
        runner.run()

    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM schema_migrations WHERE version = 'V103'")) == 0

    result = runner.run()
    assert result.applied_versions == ("V103",)
    assert attempts == 2
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM schema_migrations WHERE version = 'V103'")) == 1
        tables = set(connection.dialect.get_table_names(connection))
        assert {"reentry_first", "reentry_second"} <= tables


def test_runner_sorts_versions_and_rejects_duplicates() -> None:
    engine = create_engine("sqlite:///:memory:")
    newer = _migration("V105", "sorted_newer")
    older = _migration("V104", "sorted_older")

    assert MigrationRunner(engine, (newer, older)).run().applied_versions == ("V104", "V105")

    with pytest.raises(MigrationDefinitionError, match="duplicate migration version"):
        MigrationRunner(engine, (older, older))


def test_context_rejects_unsafe_identifiers_and_unbounded_data_changes() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        from app.db.migrations.contract import MigrationContext

        context = MigrationContext(connection)
        context.create_table("safe_fixture", (("id", "INTEGER PRIMARY KEY"), ("value", "INTEGER")))
        assert not hasattr(context, "connection")
        with pytest.raises(MigrationSafetyError):
            context.create_table("unsafe; DROP TABLE safe_fixture", (("id", "INTEGER"),))
        with pytest.raises(MigrationSafetyError):
            context.update_rows("safe_fixture", {"value": 1}, where_sql="")
        with pytest.raises(MigrationSafetyError):
            context.update_rows("safe_fixture", {"value": 1}, where_sql="1 = 1")


def test_mysql_get_and_release_lock_use_the_same_connection() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class RecordingConnection:
        dialect = SimpleNamespace(name="mysql")

        def scalar(self, statement, params):
            calls.append((str(statement), dict(params)))
            if "GET_LOCK" in str(statement):
                return 1
            if "RELEASE_LOCK" in str(statement):
                return 1
            raise AssertionError(f"unexpected scalar statement: {statement}")

    runner = MigrationRunner(object(), ())
    connection = RecordingConnection()
    runner._acquire_mysql_lock(connection)
    runner._release_mysql_lock(connection)

    assert ["GET_LOCK" in sql for sql, _ in calls] == [True, False]
    assert ["RELEASE_LOCK" in sql for sql, _ in calls] == [False, True]
    assert all(params["lock_name"] == "ashare_schema_migration" for _, params in calls)


def test_mysql_lock_rejection_stops_the_runner() -> None:
    class UnavailableLockConnection:
        dialect = SimpleNamespace(name="mysql")

        @staticmethod
        def scalar(statement, params):
            del statement, params
            return 0

    with pytest.raises(MigrationLockError, match="unavailable"):
        MigrationRunner(object(), ())._acquire_mysql_lock(UnavailableLockConnection())


def _create_baseline_inventory(connection) -> None:
    from app.db.migrations.versions.v000_baseline import (
        BASELINE_REQUIRED_COLUMNS,
        BASELINE_REQUIRED_INDEXES,
    )

    for table, columns in BASELINE_REQUIRED_COLUMNS.items():
        definitions = ["`id` INTEGER"]
        definitions.extend(f"`{column}` TEXT" for column in columns)
        connection.execute(text(f"CREATE TABLE IF NOT EXISTS `{table}` ({', '.join(definitions)})"))
        existing_columns = {
            row[1]
            for row in connection.execute(text(f"PRAGMA table_info(`{table}`)"))
        }
        for column in columns:
            if column not in existing_columns:
                connection.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{column}` TEXT"))
    for table, indexes in BASELINE_REQUIRED_INDEXES.items():
        for index in indexes:
            connection.execute(text(f"CREATE INDEX IF NOT EXISTS `{index}` ON `{table}` (`id`)"))


def test_existing_schema_baseline_applies_owned_p0_research_schema_forward_only() -> None:
    engine = create_engine("sqlite:///:memory:")

    with pytest.raises(MigrationBaselineError) as error:
        MigrationRunner(engine, all_migrations()).run()

    assert error.value.version == "V000"
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE recommendation_runs (id INTEGER PRIMARY KEY)"))
    with pytest.raises(MigrationBaselineError, match="incomplete existing schema baseline"):
        MigrationRunner(engine, all_migrations()).run()

    with engine.begin() as connection:
        _create_baseline_inventory(connection)

    result = MigrationRunner(engine, all_migrations()).run()
    expected_versions = (
        "V000",
        "V001_p0_research",
        "V002_workbench_run_lifecycle",
        "V003_add_model_route_max_tokens",
        "V004_add_model_route_temperature",
        "V005_add_model_route_timeout_seconds",
    )
    assert result.applied_versions == expected_versions
    assert [migration.version for migration in all_migrations()] == list(expected_versions)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM schema_migrations")) == len(expected_versions)
        assert connection.scalar(
            text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'backtest_%'")
        ) == 8


def test_init_db_uses_create_all_only_for_sqlite(monkeypatch) -> None:
    import importlib

    init_db_module = importlib.import_module("app.db.init_db")
    calls: list[str] = []
    monkeypatch.setattr(init_db_module.Base.metadata, "create_all", lambda **_: calls.append("create_all"))
    monkeypatch.setattr(init_db_module, "run_migrations", lambda _: calls.append("migrate"))

    monkeypatch.setattr(init_db_module, "_is_sqlite", True)
    init_db_module.init_db()
    monkeypatch.setattr(init_db_module, "_is_sqlite", False)
    init_db_module.init_db()

    assert calls == ["create_all", "migrate", "migrate"]


def test_health_distinguishes_migration_error_from_database_unavailable(monkeypatch) -> None:
    import importlib

    main = importlib.import_module("app.main")
    from app.db.migrations.health import MigrationHealth

    monkeypatch.setattr(main, "_startup_db_error", "database connection refused")
    monkeypatch.setattr(
        main,
        "get_migration_health",
        lambda: MigrationHealth(
            status="error",
            error_code="migration_checksum_mismatch",
            failed_version="V102",
        ),
    )
    migration_error = main.health()

    assert migration_error["ok"] is False
    assert migration_error["database"] == "migration_error"
    assert migration_error["migration"]["error_code"] == "migration_checksum_mismatch"

    monkeypatch.setattr(main, "get_migration_health", lambda: MigrationHealth(status="not_run"))
    unavailable = main.health()

    assert unavailable["ok"] is False
    assert unavailable["database"] == "unavailable"
    assert unavailable["application"] == "degraded"


def test_init_db_bootstraps_an_in_memory_sqlite_schema(monkeypatch) -> None:
    import importlib

    init_db_module = importlib.import_module("app.db.init_db")
    sqlite_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(init_db_module, "engine", sqlite_engine)
    monkeypatch.setattr(init_db_module, "_is_sqlite", True)

    init_db_module.init_db()

    with sqlite_engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM schema_migrations WHERE version = 'V000'")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM schema_migrations WHERE version = 'V001_p0_research'")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM schema_migrations WHERE version = 'V002_workbench_run_lifecycle'")) == 1
