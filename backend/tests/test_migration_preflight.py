"""Focused contract coverage for strict online-DDL preflight and schema signatures."""
from __future__ import annotations

from sqlalchemy import create_engine, text

from app.db.migrations.contract import MigrationContext
from app.db.migrations.preflight import DdlOperation, DdlPreflight, DdlTarget, PreflightStatus


def _target() -> DdlTarget:
    return DdlTarget(
        migration_version="V003_add_model_route_max_tokens",
        operation=DdlOperation.ADD_COLUMN,
        table="model_route_config",
        column="max_tokens",
        expected_data_types=("int", "integer"),
        expected_nullable=True,
        expected_default=None,
    )


def test_column_signature_distinguishes_same_name_with_wrong_shape() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE model_route_config (max_tokens TEXT NOT NULL DEFAULT '0')"))
        context = MigrationContext(connection)

        signature = context.column_signature("model_route_config", "max_tokens")

        assert signature is not None
        assert signature.data_type == "text"
        assert not context.has_column_shape(
            "model_route_config",
            "max_tokens",
            data_types=("int", "integer"),
            nullable=True,
            default=None,
        )


def test_index_signature_preserves_column_order_and_uniqueness() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE signature_fixture (id INTEGER, code TEXT, trade_date TEXT)"))
        connection.execute(text("CREATE UNIQUE INDEX uq_signature_fixture ON signature_fixture (code, trade_date)"))
        context = MigrationContext(connection)

        signature = context.index_signature("signature_fixture", "uq_signature_fixture")

        assert signature is not None
        assert signature.columns == ("code", "trade_date")
        assert signature.unique is True
        assert context.has_index_shape(
            "signature_fixture",
            "uq_signature_fixture",
            columns=("code", "trade_date"),
            unique=True,
        )
        assert not context.has_index_shape(
            "signature_fixture",
            "uq_signature_fixture",
            columns=("trade_date", "code"),
            unique=True,
        )


def test_sqlite_preflight_is_safe_and_never_attempts_online_mysql_ddl() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        report = DdlPreflight(connection).run(_target())

    assert report.is_safe
    assert report.checks[0].status is PreflightStatus.NOT_APPLICABLE
    assert report.as_dict()["safe"] is True


class _FakeResult:
    def __init__(self, *, first=None, rows=()):
        self._first = first
        self._rows = tuple(rows)

    def mappings(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return list(self._rows)


class _FakeMysqlConnection:
    """Read-only MySQL-shaped connection for exercising every safety check."""

    dialect = type("Dialect", (), {"name": "mysql"})()
    engine = type("Engine", (), {"url": type("Url", (), {"host": "localhost"})()})()

    def __init__(self, *, duplicate_groups=0, blocker_threads=0, long_transactions=0, rows=100):
        self.duplicate_groups = duplicate_groups
        self.blocker_threads = blocker_threads
        self.long_transactions = long_transactions
        self.rows = rows
        self.executed: list[str] = []

    def execute(self, statement, params=None):
        del params
        sql = str(statement)
        self.executed.append(sql)
        if "information_schema.tables" in sql:
            return _FakeResult(
                first={
                    "engine": "InnoDB",
                    "estimated_rows": self.rows,
                    "data_bytes": 4096,
                    "index_bytes": 2048,
                    "free_bytes": 0,
                }
            )
        if "information_schema.columns" in sql:
            return _FakeResult(first=None)
        if "performance_schema.metadata_locks" in sql:
            return _FakeResult(
                first={
                    "pending_waits": self.blocker_threads,
                    "waiting_threads": self.blocker_threads,
                    "blocker_threads": self.blocker_threads,
                }
            )
        if "FROM `fixture_table`" in sql:
            rows = ({"code": "000001", "trade_date": "2026-07-14", "duplicate_count": 2},) if self.duplicate_groups else ()
            return _FakeResult(rows=rows)
        raise AssertionError(f"unexpected preflight SQL: {sql}")

    def scalar(self, statement, params=None):
        del params
        sql = str(statement)
        if "SELECT VERSION()" in sql:
            return "9.6.0"
        if "@@datadir" in sql:
            return "/tmp"
        if "information_schema.innodb_trx" in sql:
            return self.long_transactions
        if "duplicate_groups" in sql:
            return self.duplicate_groups
        raise AssertionError(f"unexpected preflight scalar SQL: {sql}")


def test_mysql_preflight_reports_blockers_and_duplicate_conflict_samples_without_writes() -> None:
    connection = _FakeMysqlConnection(duplicate_groups=1, blocker_threads=2, rows=10)
    target = DdlTarget(
        migration_version="V006_fixture_unique",
        operation=DdlOperation.ADD_UNIQUE,
        table="fixture_table",
        natural_key=("code", "trade_date"),
        algorithm="INPLACE",
        lock="NONE",
        minimum_free_bytes=0,
    )

    report = DdlPreflight(connection).run(target)

    assert not report.is_safe
    checks = {check.name: check for check in report.checks}
    assert checks["table_inventory"].status is PreflightStatus.PASS
    assert checks["online_capability"].status is PreflightStatus.PASS
    assert checks["metadata_lock_waits"].status is PreflightStatus.BLOCK
    assert checks["natural_key_duplicates"].status is PreflightStatus.BLOCK
    assert "000001" in checks["natural_key_duplicates"].detail_map["conflicts"]
    assert all("DROP" not in sql.upper() and "TRUNCATE" not in sql.upper() for sql in connection.executed)


def test_mysql_preflight_blocks_table_scale_and_long_transactions() -> None:
    connection = _FakeMysqlConnection(long_transactions=1, rows=101)
    target = DdlTarget(
        migration_version="V006_fixture_index",
        operation=DdlOperation.ADD_INDEX,
        table="fixture_table",
        natural_key=("code",),
        algorithm="INPLACE",
        lock="NONE",
        minimum_free_bytes=0,
        maximum_estimated_rows=100,
    )

    report = DdlPreflight(connection).run(target)
    checks = {check.name: check for check in report.checks}

    assert checks["table_inventory"].status is PreflightStatus.BLOCK
    assert checks["table_inventory"].detail_map["reason"] == "estimated_row_limit_exceeded"
    assert checks["long_transactions"].status is PreflightStatus.BLOCK


def test_mysql_preflight_keeps_permission_failures_unknown_and_unsafe() -> None:
    from sqlalchemy.exc import SQLAlchemyError

    class PermissionDenied(_FakeMysqlConnection):
        def execute(self, statement, params=None):
            if "performance_schema.metadata_locks" in str(statement):
                raise SQLAlchemyError("SELECT command denied to user 'configured'@'localhost'")
            return super().execute(statement, params)

        def scalar(self, statement, params=None):
            if "information_schema.innodb_trx" in str(statement):
                raise SQLAlchemyError("PROCESS privilege required")
            return super().scalar(statement, params)

    report = DdlPreflight(PermissionDenied()).run(_target())
    checks = {check.name: check for check in report.checks}

    assert checks["metadata_lock_waits"].status is PreflightStatus.UNKNOWN
    assert checks["long_transactions"].status is PreflightStatus.UNKNOWN
    assert not report.is_safe


def test_non_unique_index_does_not_treat_index_columns_as_a_natural_key() -> None:
    connection = _FakeMysqlConnection()
    target = DdlTarget(
        migration_version="V006_fixture_index",
        operation=DdlOperation.ADD_INDEX,
        table="fixture_table",
        index_columns=("status", "lease_expires_at"),
        algorithm="INPLACE",
        lock="NONE",
        minimum_free_bytes=0,
    )

    report = DdlPreflight(connection).run(target)
    checks = {check.name: check for check in report.checks}
    assert checks["natural_key_duplicates"].status is PreflightStatus.NOT_APPLICABLE
    assert report.is_safe
