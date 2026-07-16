"""Unit tests for auxiliary UNIQUE migration declarations.

These tests exercise only the restricted migration interface.  They do not run DDL
against MySQL or present SQLite as evidence for MySQL atomicity or online-DDL safety.
"""
from __future__ import annotations

import pytest

from app.db.migrations.auxiliary_unique import AuxiliaryUniqueConstraintDeclaration
from app.db.migrations.contract import IndexSignature, MigrationSafetyError, OperationState
from app.db.migrations.preflight import DdlOperation, DdlTarget


class RecordingMigrationContext:
    """A no-connection context double proving the declaration's allowed calls."""

    def __init__(self) -> None:
        self.columns = {"code", "trade_date"}
        self.signature = None
        self.created: list[tuple[str, str, tuple[str, ...], bool, DdlTarget | None]] = []

    def has_column(self, table: str, column: str) -> bool:
        assert table == "auxiliary_fixture"
        return column in self.columns

    def index_signature(self, table: str, index: str) -> IndexSignature | None:
        assert (table, index) == ("auxiliary_fixture", "uq_auxiliary_fixture_code_trade_date")
        if self.signature is None:
            return None
        columns, unique = self.signature
        return IndexSignature(name=index, columns=columns, unique=unique)

    def has_index_shape(
        self,
        table: str,
        index: str,
        *,
        columns: tuple[str, ...],
        unique: bool,
    ) -> bool:
        assert (table, index, columns, unique) == (
            "auxiliary_fixture",
            "uq_auxiliary_fixture_code_trade_date",
            ("code", "trade_date"),
            True,
        )
        return self.signature == (columns, unique)

    def create_index(
        self,
        table: str,
        index: str,
        columns: tuple[str, ...],
        *,
        unique: bool,
        ddl_target: DdlTarget | None,
    ) -> None:
        self.created.append((table, index, columns, unique, ddl_target))
        self.signature = (columns, unique)


def _declaration() -> AuxiliaryUniqueConstraintDeclaration:
    return AuxiliaryUniqueConstraintDeclaration(
        version="V006_auxiliary_fixture_unique",
        dataset_key="auxiliary_fixture",
        table="auxiliary_fixture",
        index="uq_auxiliary_fixture_code_trade_date",
        natural_key=("code", "trade_date"),
        minimum_free_bytes=0,
    )


def test_auxiliary_unique_declaration_uses_restricted_preflighted_context_path() -> None:
    context = RecordingMigrationContext()
    declaration = _declaration()
    migration = declaration.as_migration()
    operation = migration.operations[0]

    assert operation.precondition(context) is OperationState.PENDING
    operation.apply(context)
    assert operation.postcondition(context) is OperationState.SATISFIED
    migration.verify(context)
    assert context.created == [
        (
            "auxiliary_fixture",
            "uq_auxiliary_fixture_code_trade_date",
            ("code", "trade_date"),
            True,
            DdlTarget(
                migration_version="V006_auxiliary_fixture_unique",
                operation=DdlOperation.ADD_UNIQUE,
                table="auxiliary_fixture",
                natural_key=("code", "trade_date"),
                algorithm="INPLACE",
                lock="NONE",
                minimum_free_bytes=0,
            ),
        )
    ]


def test_auxiliary_unique_declaration_rejects_missing_columns_and_mismatched_existing_index() -> None:
    missing_column_context = RecordingMigrationContext()
    missing_column_context.columns.remove("trade_date")
    state = _declaration().as_migration().operations[0].precondition

    with pytest.raises(MigrationSafetyError, match="missing columns=trade_date"):
        state(missing_column_context)

    mismatched_index_context = RecordingMigrationContext()
    mismatched_index_context.signature = (("trade_date", "code"), True)
    with pytest.raises(MigrationSafetyError, match="incompatible index"):
        state(mismatched_index_context)


@pytest.mark.parametrize(
    "dataset_key",
    ("block_trade", "share_float", "holder_trade", "institutional_survey", "news_announcement"),
)
def test_event_snapshot_dataset_can_never_materialize_a_per_row_unique_declaration(
    dataset_key: str,
) -> None:
    with pytest.raises(ValueError, match="immutable partition snapshots"):
        AuxiliaryUniqueConstraintDeclaration(
            version=f"V999_forbidden_{dataset_key}_unique",
            dataset_key=dataset_key,
            table="event_rows",
            index=f"uq_forbidden_{dataset_key}",
            natural_key=("code", "event_date"),
        )



def _discovered_state_unique_migrations():
    from app.db.migrations import all_migrations
    from app.services.auxiliary.production import AUXILIARY_UNIQUE_MIGRATION_PLANS

    expected_versions = tuple(
        plan.version
        for plan in sorted(AUXILIARY_UNIQUE_MIGRATION_PLANS.values(), key=lambda item: item.version)
    )
    discovered = tuple(
        migration for migration in all_migrations() if migration.version in expected_versions
    )
    return expected_versions, discovered


def _create_already_satisfied_state_unique_shapes(connection) -> None:
    from sqlalchemy import text
    from app.services.auxiliary.production import AUXILIARY_UNIQUE_MIGRATION_PLANS

    for plan in sorted(AUXILIARY_UNIQUE_MIGRATION_PLANS.values(), key=lambda item: item.version):
        declaration = plan.declaration
        columns = ", ".join(f"`{column}` TEXT NOT NULL" for column in declaration.natural_key)
        connection.execute(text(f"CREATE TABLE `{declaration.table}` ({columns})"))
        connection.execute(
            text(
                f"CREATE UNIQUE INDEX `{declaration.index}` "
                f"ON `{declaration.table}` ({', '.join(f'`{column}`' for column in declaration.natural_key)})"
            )
        )


def test_all_state_unique_migrations_are_discovered_in_order_and_noop_when_shapes_exist() -> None:
    from sqlalchemy import create_engine, text
    from app.db.migrations.runner import MigrationRunner

    expected_versions, discovered = _discovered_state_unique_migrations()

    assert tuple(migration.version for migration in discovered) == expected_versions
    assert tuple(migration.version for migration in discovered) == tuple(sorted(expected_versions))

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_already_satisfied_state_unique_shapes(connection)

    runner = MigrationRunner(engine, discovered)
    first = runner.run()
    second = runner.run()

    assert first.applied_versions == expected_versions
    assert second.applied_versions == ()
    assert second.skipped_versions == expected_versions
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM schema_migrations")) == len(expected_versions)


def test_discovered_state_unique_migration_fails_closed_for_duplicate_natural_keys() -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import IntegrityError
    from app.db.migrations.runner import MigrationRunner

    expected_versions, discovered = _discovered_state_unique_migrations()
    migration = discovered[0]
    declaration = next(
        item.declaration
        for item in __import__("app.services.auxiliary.production", fromlist=["AUXILIARY_UNIQUE_MIGRATION_PLANS"])
        .AUXILIARY_UNIQUE_MIGRATION_PLANS.values()
        if item.version == migration.version
    )
    engine = create_engine("sqlite:///:memory:")
    columns = ", ".join(f"`{column}` TEXT NOT NULL" for column in declaration.natural_key)
    values = {column: "duplicate" for column in declaration.natural_key}
    with engine.begin() as connection:
        connection.execute(text(f"CREATE TABLE `{declaration.table}` ({columns})"))
        connection.execute(
            text(
                f"INSERT INTO `{declaration.table}` ({', '.join(f'`{column}`' for column in declaration.natural_key)}) "
                f"VALUES ({', '.join(f':{column}' for column in declaration.natural_key)})"
            ),
            values,
        )
        connection.execute(
            text(
                f"INSERT INTO `{declaration.table}` ({', '.join(f'`{column}`' for column in declaration.natural_key)}) "
                f"VALUES ({', '.join(f':{column}' for column in declaration.natural_key)})"
            ),
            values,
        )

    with pytest.raises(IntegrityError):
        MigrationRunner(engine, (migration,)).run()

    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM schema_migrations WHERE version = :version"),
            {"version": expected_versions[0]},
        ) == 0
        assert connection.scalar(text(f"SELECT COUNT(*) FROM `{declaration.table}`")) == 2


def test_discovered_state_unique_migration_fails_closed_for_same_name_wrong_shape() -> None:
    from sqlalchemy import create_engine, text
    from app.db.migrations.contract import MigrationSafetyError
    from app.db.migrations.runner import MigrationRunner

    expected_versions, discovered = _discovered_state_unique_migrations()
    migration = discovered[0]
    declaration = next(
        item.declaration
        for item in __import__("app.services.auxiliary.production", fromlist=["AUXILIARY_UNIQUE_MIGRATION_PLANS"])
        .AUXILIARY_UNIQUE_MIGRATION_PLANS.values()
        if item.version == migration.version
    )
    engine = create_engine("sqlite:///:memory:")
    columns = ", ".join(f"`{column}` TEXT NOT NULL" for column in declaration.natural_key)
    with engine.begin() as connection:
        connection.execute(text(f"CREATE TABLE `{declaration.table}` ({columns})"))
        connection.execute(
            text(
                f"CREATE INDEX `{declaration.index}` "
                f"ON `{declaration.table}` ({', '.join(f'`{column}`' for column in declaration.natural_key)})"
            )
        )

    with pytest.raises(MigrationSafetyError, match="incompatible index"):
        MigrationRunner(engine, (migration,)).run()

    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM schema_migrations WHERE version = :version"),
            {"version": expected_versions[0]},
        ) == 0
