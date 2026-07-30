"""Contract tests for the forward-only schema migration foundation."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text

from app.db.migrations import all_migrations
from app.db.migrations.contract import (
    MigrationBaselineError,
    MigrationChecksumMismatchError,
    MigrationDefinitionError,
    MigrationExecutionTimeoutError,
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

    # The runner now discovers all V006–V021 state-key migrations.  This fixture
    # represents a compatible legacy schema, so it supplies their declared natural
    # key columns without pre-creating the UNIQUE shapes those migrations own.
    from app.services.auxiliary.production import AUXILIARY_UNIQUE_MIGRATION_PLANS

    for plan in AUXILIARY_UNIQUE_MIGRATION_PLANS.values():
        declaration = plan.declaration
        existing_tables = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
        if declaration.table not in existing_tables:
            definitions = ", ".join(
                ("`id` INTEGER", *(f"`{column}` TEXT" for column in declaration.natural_key))
            )
            connection.execute(text(f"CREATE TABLE `{declaration.table}` ({definitions})"))
            continue
        existing_columns = {
            row[1]
            for row in connection.execute(text(f"PRAGMA table_info(`{declaration.table}`)"))
        }
        for column in declaration.natural_key:
            if column not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE `{declaration.table}` ADD COLUMN `{column}` TEXT")
                )


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
        "V006_auxiliary_limit_up_board_unique",
        "V007_auxiliary_concept_sector_unique",
        "V008_auxiliary_concept_sector_stock_unique",
        "V009_auxiliary_capital_flow_unique",
        "V010_auxiliary_dragon_tiger_unique",
        "V012_auxiliary_margin_detail_unique",
        "V014_auxiliary_hsgt_flow_unique",
        "V015_auxiliary_hsgt_top10_unique",
        "V016_auxiliary_pledge_stat_unique",
        "V017_auxiliary_broker_recommend_unique",
        "V019_auxiliary_fina_indicator_unique",
        "V020_auxiliary_auction_unique",
        "V021_auxiliary_holder_number_unique",
        "V024_auxiliary_partition_snapshots",
        "V025_strategy_version_snapshots",
        "V026_recommendation_execution_states",
        "V027_cancelled_before_entry",
            "V028_recommendation_evaluation_scope",
            "V029_recommendation_source_evidence",
            "V030_outcome_version_ledger",
        )
    assert result.applied_versions == expected_versions
    assert [migration.version for migration in all_migrations()] == list(expected_versions)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM schema_migrations")) == len(expected_versions)
        assert connection.scalar(
            text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'backtest_%'")
        ) == 8
        assert connection.scalar(
            text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name = 'strategy_version_snapshots'")
        ) == 1
        assert connection.scalar(
            text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name = 'recommendation_execution_states'")
        ) == 1
        execution_columns = {
            column["name"] for column in inspect(connection).get_columns("recommendation_execution_states")
        }
        assert {
            "cancelled_before_entry",
            "first_cancelled_at",
            "first_cancelled_price",
            "first_cancel_reason",
        } <= execution_columns
        run_columns = {
            column["name"] for column in inspect(connection).get_columns("recommendation_runs")
        }
        assert "evaluation_scope" in run_columns


def test_init_db_always_uses_alembic_runtime(monkeypatch) -> None:
    import importlib

    init_db_module = importlib.import_module("app.db.init_db")
    calls: list[str] = []
    monkeypatch.setattr(init_db_module, "upgrade_database", lambda _: calls.append("upgrade"))
    init_db_module.init_db()

    assert calls == ["upgrade"]


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

    init_db_module.init_db()

    with sqlite_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001_demo_baseline"
        assert inspect(connection).has_table("recommendation_runs")


def test_runner_configures_bounded_mysql_wait_and_query_limits() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class SessionConnection:
        dialect = SimpleNamespace(name="mysql")

        def execute(self, statement, params):
            calls.append((str(statement), dict(params)))

    MigrationRunner(
        object(),
        (),
        lock_wait_timeout_seconds=7,
        innodb_lock_wait_timeout_seconds=9,
        preflight_query_timeout_milliseconds=1111,
    )._configure_mysql_session(SessionConnection())

    assert [(sql, params) for sql, params in calls] == [
        ("SET SESSION lock_wait_timeout = :seconds", {"seconds": 7}),
        ("SET SESSION innodb_lock_wait_timeout = :seconds", {"seconds": 9}),
        ("SET SESSION max_execution_time = :milliseconds", {"milliseconds": 1111}),
    ]


def test_runner_rejects_a_slow_operation_before_success_ledger(monkeypatch) -> None:
    import app.db.migrations.runner as runner_module

    engine = create_engine("sqlite:///:memory:")
    operation = _table_operation("timeout_fixture")
    migration = _migration("V106", "timeout_fixture")
    runner = MigrationRunner(engine, (migration,))
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(runner_module, "perf_counter", lambda: next(ticks))

    from app.db.migrations.contract import MigrationContext
    connection = engine.connect()
    try:
        with pytest.raises(MigrationExecutionTimeoutError, match="execution timeout"):
            runner._apply_migration(
                MigrationContext(connection),
                migration,
                ddl_execution_timeout_seconds=1,
            )
    finally:
        connection.close()


@pytest.mark.parametrize("failure_step", (1, 2, 3))
def test_multi_operation_failure_at_each_step_reenters_without_duplicate_ledger(failure_step: int) -> None:
    """A fault after any applied operation must resume from its postcondition."""
    from app.db.migrations.contract import MigrationContext

    engine = create_engine("sqlite:///:memory:")
    table = f"reentry_data_{failure_step}"
    index = f"idx_{table}_value"
    with engine.begin() as connection:
        connection.execute(
            text(
                f"CREATE TABLE `{table}` ("
                "`id` INTEGER PRIMARY KEY, `value` INTEGER NOT NULL)"
            )
        )
        connection.execute(text(f"INSERT INTO `{table}` (`id`, `value`) VALUES (1, 7)"))

    injected = False

    def fail_after(step: int) -> None:
        nonlocal injected
        if step == failure_step and not injected:
            injected = True
            raise RuntimeError(f"injected failure at operation {step}")

    def marker_state(context: MigrationContext) -> OperationState:
        return OperationState.SATISFIED if context.has_column(table, "marker") else OperationState.PENDING

    def add_marker(context: MigrationContext) -> None:
        context.add_column(table, "marker", "INTEGER NULL")
        fail_after(1)

    def marked_row_state(context: MigrationContext) -> OperationState:
        return (
            OperationState.SATISFIED
            if context.count_rows(
                table,
                where_sql="id = :row_id AND marker = :marker",
                params={"row_id": 1, "marker": 9},
            )
            == 1
            else OperationState.PENDING
        )

    def mark_row(context: MigrationContext) -> None:
        context.update_rows(
            table,
            {"marker": 9},
            where_sql="id = :row_id",
            where_params={"row_id": 1},
        )
        fail_after(2)

    def index_state(context: MigrationContext) -> OperationState:
        return OperationState.SATISFIED if context.has_index(table, index) else OperationState.PENDING

    def create_value_index(context: MigrationContext) -> None:
        context.create_index(table, index, ("value",))
        fail_after(3)

    migration = define_migration(
        version=f"V20{failure_step}",
        description="Re-enter a three-operation migration after each operation boundary",
        operations=(
            MigrationOperation(
                name="add_marker",
                checksum_material="add nullable marker column",
                precondition=marker_state,
                apply=add_marker,
                postcondition=marker_state,
            ),
            MigrationOperation(
                name="mark_seed_row",
                checksum_material="bounded update of the seed row marker",
                precondition=marked_row_state,
                apply=mark_row,
                postcondition=marked_row_state,
            ),
            MigrationOperation(
                name="create_value_index",
                checksum_material="create value index",
                precondition=index_state,
                apply=create_value_index,
                postcondition=index_state,
            ),
        ),
    )
    runner = MigrationRunner(engine, (migration,))

    with pytest.raises(RuntimeError, match=f"operation {failure_step}"):
        runner.run()

    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM schema_migrations WHERE version = :version"),
            {"version": migration.version},
        ) == 0

    result = runner.run()

    assert result.applied_versions == (migration.version,)
    assert result.skipped_versions == ()
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM schema_migrations WHERE version = :version"),
            {"version": migration.version},
        ) == 1
        assert connection.scalar(text(f"SELECT marker FROM `{table}` WHERE id = 1")) == 9
        assert connection.dialect.get_indexes(connection, table)[0]["name"] == index


@pytest.mark.parametrize("definition", ("INTEGER; DROP TABLE safe_sql_fixture", "INTEGER TRUNCATE"))
def test_context_rejects_disabled_destructive_sql_fragments(definition: str) -> None:
    from app.db.migrations.contract import MigrationContext

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        context = MigrationContext(connection)
        context.create_table("safe_sql_fixture", (("id", "INTEGER PRIMARY KEY"),))
        with pytest.raises(MigrationSafetyError):
            context.add_column("safe_sql_fixture", "value", definition)
        assert not hasattr(context, "delete_rows")
        assert not hasattr(context, "execute_sql")


def test_request_expand_columns_are_nullable_and_have_no_default_one() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_baseline_inventory(connection)
    MigrationRunner(engine, all_migrations()).run()

    with engine.connect() as connection:
        columns = {
            item["name"]: item
            for item in inspect(connection).get_columns("recommendation_runs")
        }
    for name in ("request_schema_version", "request_json", "execution_version"):
        assert columns[name]["nullable"] is True
        assert columns[name]["default"] is None
