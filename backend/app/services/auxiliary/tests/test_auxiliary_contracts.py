"""Pure contract tests for the isolated Auxiliary Dataset S4 foundation.

No fixture opens a database connection or executes DDL/DML against MySQL.  The MySQL
adapter test compiles SQL and captures statements using an in-memory executor only.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from typing import Any

import pytest
from sqlalchemy import Column, Date, Integer, MetaData, String, Table
from sqlalchemy.dialects import mysql

from app.services.auxiliary.contracts import (
    AuxiliaryAuditEvent,
    AuxiliaryAuditOutcome,
    AuxiliaryContractError,
    AuxiliaryDatasetDescriptor,
    AuxiliaryDatasetRequest,
    AuxiliaryDatasetResult,
    AuxiliaryErrorCode,
    AuxiliaryInstrument,
    AuxiliaryLoadedView,
    AuxiliaryPersistResult,
    AuxiliaryRepositorySnapshot,
    AuxiliaryStage,
    CoveragePolicy,
    RefreshDecision,
    RefreshMode,
    RefreshPolicy,
    RefreshReason,
)
from app.services.auxiliary.mysql_repository import (
    MySQLAtomicUpsertRepository,
    MySQLUpsertConfigurationError,
    MySQLUpsertPolicy,
)
from app.services.auxiliary.orchestrator import AuxiliaryDataOrchestrator
from app.services.auxiliary.registry import (
    AuxiliaryDatasetRegistry,
    AuxiliaryRegistryFrozenError,
    DuplicateAuxiliaryViewKeyError,
)


NOW = datetime(2026, 5, 18, 9, 30, tzinfo=timezone.utc)
TRADE_DATE = date(2026, 5, 18)
INSTRUMENTS = (AuxiliaryInstrument("000001", "SZ"), AuxiliaryInstrument("600000", "SH"))


class FakeFetcher:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls = 0

    def fetch(self, request: AuxiliaryDatasetRequest) -> list[dict[str, Any]]:
        self.calls += 1
        return self.rows


class FakeNormalizer:
    def normalize(self, raw: dict[str, Any], request: AuxiliaryDatasetRequest) -> dict[str, Any] | None:
        return {**raw, "trade_date": request.trade_date}


class FakeRepository:
    def __init__(self, snapshot: AuxiliaryRepositorySnapshot) -> None:
        self.snapshot = snapshot
        self.persisted: tuple[dict[str, Any], ...] = ()

    def inspect(self, request: AuxiliaryDatasetRequest) -> AuxiliaryRepositorySnapshot:
        return self.snapshot

    def upsert(
        self,
        records: tuple[dict[str, Any], ...],
        request: AuxiliaryDatasetRequest,
    ) -> AuxiliaryPersistResult:
        self.persisted = records
        return AuxiliaryPersistResult(
            attempted_rows=len(records),
            affected_rows=len(records),
            failed_rows=0,
            execute_count=1,
            duration_ms=0.2,
        )


class FakeLoader:
    def __init__(self, key: str = "moneyflow") -> None:
        self.key = key
        self.calls = 0

    def load(self, request: AuxiliaryDatasetRequest) -> AuxiliaryLoadedView[dict[str, str]]:
        self.calls += 1
        return AuxiliaryLoadedView(self.key, {"as_of": request.trade_date.isoformat()})


class RaisingFetcher:
    def fetch(self, request: AuxiliaryDatasetRequest) -> list[dict[str, Any]]:
        raise TimeoutError("upstream timed out")


class FailingCustomHandler:
    def prepare(self, request: AuxiliaryDatasetRequest) -> AuxiliaryDatasetResult[dict[str, str]]:
        raise RuntimeError("special adapter failure")


def _descriptor(
    *,
    key: str = "capital_flow",
    view_key: str = "moneyflow",
    fetcher: Any | None = None,
    repository: FakeRepository | None = None,
    loader: FakeLoader | None = None,
    dependencies: tuple[str, ...] = (),
) -> AuxiliaryDatasetDescriptor[dict[str, Any], dict[str, Any], dict[str, str]]:
    return AuxiliaryDatasetDescriptor(
        key=key,
        view_key=view_key,
        natural_key=("code", "trade_date"),
        fetcher=fetcher or FakeFetcher(
            [{"code": "000001", "value": 1}, {"code": "600000", "value": 2}]
        ),
        normalizer=FakeNormalizer(),
        repository=repository or FakeRepository(AuxiliaryRepositorySnapshot(0)),
        loader=loader or FakeLoader(view_key),
        coverage_policy=CoveragePolicy(
            minimum_record_count=2,
            minimum_instrument_coverage=1.0,
            instrument_extractor=lambda record: record["code"],
        ),
        refresh_policy=RefreshPolicy(RefreshMode.IF_STALE),
        freshness_field="trade_date",
        freshness_extractor=lambda record: record["trade_date"],
        dependencies=dependencies,
    )


def _orchestrator(*descriptors: AuxiliaryDatasetDescriptor[Any, Any, Any]) -> AuxiliaryDataOrchestrator:
    registry = AuxiliaryDatasetRegistry()
    for descriptor in descriptors:
        registry.register(descriptor)
    registry.freeze()
    return AuxiliaryDataOrchestrator(registry, clock=lambda: NOW)


def test_descriptor_is_immutable_and_rejects_incomplete_freshness_contract():
    descriptor = _descriptor()
    with pytest.raises(FrozenInstanceError):
        descriptor.key = "mutated"  # type: ignore[misc]

    with pytest.raises(AuxiliaryContractError, match="freshness_field"):
        AuxiliaryDatasetDescriptor(
            key="invalid",
            view_key="invalid_view",
            natural_key=("id",),
            fetcher=FakeFetcher([]),
            normalizer=FakeNormalizer(),
            repository=FakeRepository(AuxiliaryRepositorySnapshot(0)),
            loader=FakeLoader("invalid_view"),
            coverage_policy=CoveragePolicy(),
            refresh_policy=RefreshPolicy(RefreshMode.IF_STALE),
            freshness_field="trade_date",
        )
    with pytest.raises(AuxiliaryContractError, match="fetcher.fetch"):
        AuxiliaryDatasetDescriptor(
            key="invalid_handler",
            view_key="invalid_handler_view",
            natural_key=("id",),
            fetcher=object(),  # type: ignore[arg-type]
            normalizer=FakeNormalizer(),
            repository=FakeRepository(AuxiliaryRepositorySnapshot(0)),
            loader=FakeLoader("invalid_handler_view"),
            coverage_policy=CoveragePolicy(),
            refresh_policy=RefreshPolicy(RefreshMode.ALWAYS),
        )


def test_registry_validates_view_keys_dependencies_and_freezes_topological_snapshot():
    upstream = _descriptor(key="upstream", view_key="upstream_view")
    dependent = _descriptor(
        key="dependent",
        view_key="dependent_view",
        dependencies=("upstream",),
    )
    registry = AuxiliaryDatasetRegistry()
    registry.register(upstream)
    registry.register(dependent)
    registry.freeze()

    assert registry.is_frozen
    assert [item.key for item in registry.resolve_with_dependencies(("dependent",))] == [
        "upstream",
        "dependent",
    ]
    with pytest.raises(AuxiliaryRegistryFrozenError):
        registry.register(_descriptor(key="later", view_key="later_view"))

    duplicate_view_registry = AuxiliaryDatasetRegistry()
    duplicate_view_registry.register(_descriptor(key="one", view_key="same_view"))
    with pytest.raises(DuplicateAuxiliaryViewKeyError):
        duplicate_view_registry.register(_descriptor(key="two", view_key="same_view"))


def test_standard_descriptor_runs_refresh_normalize_persist_load_and_coverage_audit():
    repository = FakeRepository(AuxiliaryRepositorySnapshot(0))
    loader = FakeLoader()
    descriptor = _descriptor(repository=repository, loader=loader)

    result = _orchestrator(descriptor).prepare(
        datasets=("capital_flow",),
        instruments=INSTRUMENTS,
        trade_date=TRADE_DATE,
        refresh=True,
    ).result_for("capital_flow")

    assert result.is_usable
    assert result.refresh.required
    assert result.refresh.reason is RefreshReason.INSUFFICIENT_COVERAGE
    assert result.coverage.sufficient
    assert result.coverage.covered_instruments == ("000001", "600000")
    assert result.persistence is not None
    assert result.persistence.attempted_rows == 2
    assert len(repository.persisted) == 2
    assert loader.calls == 1
    assert [event.stage for event in result.audit] == [
        AuxiliaryStage.INSPECT,
        AuxiliaryStage.REFRESH,
        AuxiliaryStage.FETCH,
        AuxiliaryStage.NORMALIZE,
        AuxiliaryStage.PERSIST,
        AuxiliaryStage.LOAD,
        AuxiliaryStage.COVERAGE,
    ]


def test_fresh_cached_dataset_skips_refresh_but_loads_typed_view():
    fresh_state = AuxiliaryRepositorySnapshot(
        record_count=2,
        covered_instruments=frozenset({"000001", "600000"}),
        freshness=TRADE_DATE,
    )
    fetcher = FakeFetcher([])
    repository = FakeRepository(fresh_state)
    descriptor = _descriptor(fetcher=fetcher, repository=repository)

    result = _orchestrator(descriptor).prepare(
        datasets=("capital_flow",),
        instruments=INSTRUMENTS,
        trade_date=TRADE_DATE,
        refresh=True,
    ).result_for("capital_flow")

    assert result.is_usable
    assert result.refresh == RefreshDecision(False, RefreshReason.FRESH, TRADE_DATE)
    assert fetcher.calls == 0
    assert repository.persisted == ()
    assert result.persistence is None


def test_standard_and_custom_failures_share_audit_and_stable_error_model():
    normal_failure = _descriptor(fetcher=RaisingFetcher())
    custom = AuxiliaryDatasetDescriptor(
        key="special_news",
        view_key="news",
        natural_key=("article_id",),
        coverage_policy=CoveragePolicy(),
        refresh_policy=RefreshPolicy(RefreshMode.NEVER),
        custom_handler=FailingCustomHandler(),
    )
    result = _orchestrator(normal_failure, custom).prepare(
        datasets=("capital_flow", "special_news"),
        instruments=INSTRUMENTS,
        trade_date=TRADE_DATE,
        refresh=True,
    )

    fetch_result = result.result_for("capital_flow")
    custom_result = result.result_for("special_news")
    assert fetch_result.errors[0].code is AuxiliaryErrorCode.FETCH_FAILED
    assert fetch_result.errors[0].exception_type == "TimeoutError"
    assert fetch_result.audit[-1].outcome is AuxiliaryAuditOutcome.FAILED
    assert custom_result.errors[0].code is AuxiliaryErrorCode.CUSTOM_HANDLER_FAILED
    assert custom_result.audit[0].stage is AuxiliaryStage.CUSTOM
    assert custom_result.audit[-1].outcome is AuxiliaryAuditOutcome.FAILED


class CapturingExecutor:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        return type("Result", (), {"rowcount": 2})()


def _request() -> AuxiliaryDatasetRequest:
    return AuxiliaryDatasetRequest(
        dataset_key="capital_flow",
        instruments=INSTRUMENTS,
        trade_date=TRADE_DATE,
        refresh_enabled=True,
        requested_at=NOW,
    )


def test_mysql_repository_batches_mysql_atomic_upserts_without_database_access():
    metadata = MetaData()
    table = Table(
        "auxiliary_rows",
        metadata,
        Column("code", String(6), primary_key=True),
        Column("trade_date", Date, primary_key=True),
        Column("value", Integer, nullable=False),
        Column("source_version", Integer, nullable=False),
    )
    executor = CapturingExecutor()
    repository = MySQLAtomicUpsertRepository[dict[str, Any]](
        executor=executor,
        table=table,
        natural_key=("code", "trade_date"),
        row_encoder=lambda row: row,
        policy=MySQLUpsertPolicy(
            overwrite_fields=("value", "source_version"),
            latest_wins_field="source_version",
        ),
        batch_size=2,
    )
    rows = [
        {"code": "000001", "trade_date": TRADE_DATE, "value": 1, "source_version": 1},
        {"code": "600000", "trade_date": TRADE_DATE, "value": 2, "source_version": 1},
        {"code": "000002", "trade_date": TRADE_DATE, "value": 3, "source_version": 1},
    ]

    metrics = repository.upsert(rows, _request())

    assert metrics.attempted_rows == 3
    assert metrics.execute_count == 2
    assert metrics.affected_rows == 4
    assert metrics.inserted_rows is None and metrics.updated_rows is None
    assert metrics.batch_size == 2
    assert metrics.batch_statement_bound == 2
    assert metrics.fixed_statement_count == 0
    compiled = str(executor.statements[0].compile(dialect=mysql.dialect()))
    assert "ON DUPLICATE KEY UPDATE" in compiled
    assert "CASE WHEN" in compiled
    assert "source_version" in compiled


def test_mysql_repository_requires_predeclared_matching_unique_natural_key():
    table = Table(
        "unsafe_rows",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("code", String(6), nullable=False),
        Column("value", Integer, nullable=False),
    )
    with pytest.raises(MySQLUpsertConfigurationError, match="UNIQUE/PRIMARY KEY"):
        MySQLAtomicUpsertRepository[dict[str, Any]](
            executor=CapturingExecutor(),
            table=table,
            natural_key=("code",),
            row_encoder=lambda row: row,
            policy=MySQLUpsertPolicy(overwrite_fields=("value",)),
        )


class SuccessfulCustomHandler:
    def prepare(self, request: AuxiliaryDatasetRequest) -> AuxiliaryDatasetResult[dict[str, str]]:
        coverage = CoveragePolicy[dict[str, str]]().evaluate_records(
            (), request.instruments, source="custom_handler"
        )
        return AuxiliaryDatasetResult(
            dataset_key=request.dataset_key,
            view_key="special_view",
            view={"status": "ready"},
            coverage=coverage,
            refresh=RefreshDecision(False, RefreshReason.POLICY_NEVER, None),
            audit=(
                AuxiliaryAuditEvent.create(
                    AuxiliaryStage.CUSTOM,
                    AuxiliaryAuditOutcome.SUCCEEDED,
                    NOW,
                    {"implementation": "fixture"},
                ),
            ),
            errors=(),
        )


def test_successful_custom_handler_preserves_shared_typed_audit_result():
    descriptor = AuxiliaryDatasetDescriptor(
        key="special_dataset",
        view_key="special_view",
        natural_key=("id",),
        coverage_policy=CoveragePolicy(),
        refresh_policy=RefreshPolicy(RefreshMode.NEVER),
        custom_handler=SuccessfulCustomHandler(),
    )

    result = _orchestrator(descriptor).prepare(
        datasets=("special_dataset",),
        instruments=INSTRUMENTS,
        trade_date=TRADE_DATE,
        refresh=True,
    ).result_for("special_dataset")

    assert result.is_usable
    assert result.view == {"status": "ready"}
    assert [event.stage for event in result.audit] == [AuxiliaryStage.CUSTOM, AuxiliaryStage.CUSTOM]
    assert result.audit[0].detail_map["event"] == "dispatched"
    assert result.audit[1].detail_map["implementation"] == "fixture"


class FixtureSchemaReadiness:
    """Test-only schema evidence; production must use migration-backed evidence."""

    def assert_ready(self, entry: Any) -> None:
        del entry


def test_inventory_is_frozen_auditable_and_marks_identity_specific_work_as_blocked():
    from app.services.auxiliary.production import (
        AUDIT_ONLY_AUXILIARY_DATASET_KEYS,
        AUXILIARY_AUDIT_ONLY_DATASET_INVENTORY,
        AUXILIARY_DATASET_BY_KEY,
        AUXILIARY_DATASET_INVENTORY,
        IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS,
        REQUIRED_AUXILIARY_CATEGORIES,
        STATE_ATOMIC_UPSERT_DATASET_KEYS,
        AuxiliaryPersistenceIdentity,
        MySQLVerificationRequirement,
        SnapshotVerificationRequirement,
        validate_auxiliary_inventory,
    )

    validate_auxiliary_inventory()
    assert REQUIRED_AUXILIARY_CATEGORIES <= {entry.category for entry in AUXILIARY_DATASET_INVENTORY}
    assert len(AUXILIARY_DATASET_INVENTORY) == 17
    assert {entry.key for entry in AUXILIARY_AUDIT_ONLY_DATASET_INVENTORY} == AUDIT_ONLY_AUXILIARY_DATASET_KEYS
    assert set(AUXILIARY_DATASET_BY_KEY) == {entry.key for entry in AUXILIARY_DATASET_INVENTORY}
    state_entries = tuple(entry for entry in AUXILIARY_DATASET_INVENTORY if entry.is_state_dataset)
    snapshot_entries = tuple(entry for entry in AUXILIARY_DATASET_INVENTORY if entry.is_snapshot_dataset)
    assert {entry.key for entry in state_entries} == STATE_ATOMIC_UPSERT_DATASET_KEYS
    assert {entry.key for entry in snapshot_entries} == IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS
    assert all(
        entry.identity_semantics is AuxiliaryPersistenceIdentity.STATE_ATOMIC_UPSERT
        and set(entry.mysql_blockers) == set(MySQLVerificationRequirement)
        and not entry.snapshot_blockers
        for entry in state_entries
    )
    assert all(
        entry.identity_semantics is AuxiliaryPersistenceIdentity.IMMUTABLE_PARTITION_SNAPSHOT
        and not entry.natural_key
        and entry.row_identity == ("batch_id", "row_ordinal")
        and not entry.mysql_blockers
        and set(entry.snapshot_blockers) == set(SnapshotVerificationRequirement)
        for entry in snapshot_entries
    )
    assert all(entry.table_name == entry.model.__tablename__ for entry in AUXILIARY_DATASET_INVENTORY)
    with pytest.raises(TypeError):
        AUXILIARY_DATASET_BY_KEY["unexpected"] = AUXILIARY_DATASET_INVENTORY[0]  # type: ignore[index]


def test_declared_standard_adapter_runs_new_dataset_without_custom_handler():
    import dataclasses

    from app.services.auxiliary.production import (
        AUXILIARY_DATASET_INVENTORY,
        StandardAuxiliaryDatasetAdapters,
        build_production_auxiliary_orchestrator,
    )

    fixture = dataclasses.replace(
        next(entry for entry in AUXILIARY_DATASET_INVENTORY if entry.key == "capital_flow"),
        key="fixture_without_custom_handler",
        view_key="fixture.view",
    )
    repository = FakeRepository(AuxiliaryRepositorySnapshot(0))
    orchestrator = build_production_auxiliary_orchestrator(
        {
            fixture.key: StandardAuxiliaryDatasetAdapters(
                fetcher=FakeFetcher([
                    {"code": "000001", "value": 1},
                    {"code": "600000", "value": 2},
                ]),
                normalizer=FakeNormalizer(),
                repository=repository,
                loader=FakeLoader(fixture.view_key),
            )
        },
        schema_readiness=FixtureSchemaReadiness(),
        inventory=(fixture,),
        clock=lambda: NOW,
    )

    result = orchestrator.prepare(
        datasets=(fixture.key,),
        instruments=INSTRUMENTS,
        trade_date=TRADE_DATE,
        refresh=True,
    ).result_for(fixture.key)

    assert result.is_usable
    assert result.view_key == "fixture.view"
    assert result.persistence is not None
    assert repository.persisted == (
        {"code": "000001", "value": 1, "trade_date": TRADE_DATE},
        {"code": "600000", "value": 2, "trade_date": TRADE_DATE},
    )


def test_unverified_schema_gate_blocks_production_assembly_without_database_access():
    import dataclasses

    from app.services.auxiliary.production import (
        AUXILIARY_DATASET_INVENTORY,
        AuxiliarySchemaMigrationBlockedError,
        StandardAuxiliaryDatasetAdapters,
        UnverifiedMySQLSchemaReadiness,
        build_production_auxiliary_registry,
    )

    fixture = dataclasses.replace(
        next(entry for entry in AUXILIARY_DATASET_INVENTORY if entry.key == "capital_flow"),
        key="blocked_fixture",
        view_key="blocked.fixture",
    )
    with pytest.raises(AuxiliarySchemaMigrationBlockedError, match="两个连接|two_connection"):
        build_production_auxiliary_registry(
            {
                fixture.key: StandardAuxiliaryDatasetAdapters(
                    fetcher=FakeFetcher([]),
                    normalizer=FakeNormalizer(),
                    repository=FakeRepository(AuxiliaryRepositorySnapshot(0)),
                    loader=FakeLoader(fixture.view_key),
                )
            },
            schema_readiness=UnverifiedMySQLSchemaReadiness(),
            inventory=(fixture,),
        )


def test_mysql_repository_preflights_duplicate_keys_before_any_fake_execute():
    from app.services.auxiliary.mysql_repository import MySQLDuplicateNaturalKeyError

    table = Table(
        "duplicate_preflight_rows",
        MetaData(),
        Column("code", String(6), primary_key=True),
        Column("trade_date", Date, primary_key=True),
        Column("value", Integer, nullable=False),
    )
    executor = CapturingExecutor()
    repository = MySQLAtomicUpsertRepository[dict[str, Any]](
        executor=executor,
        table=table,
        natural_key=("code", "trade_date"),
        row_encoder=lambda row: row,
        policy=MySQLUpsertPolicy(overwrite_fields=("value",)),
        batch_size=1,
    )
    rows = [
        {"code": "000001", "trade_date": TRADE_DATE, "value": 1},
        {"code": "000001", "trade_date": TRADE_DATE, "value": 2},
    ]

    preflight = repository.preflight_duplicate_keys(rows)
    assert not preflight.is_clear
    assert preflight.attempted_rows == 2
    assert preflight.unique_key_count == 1
    with pytest.raises(MySQLDuplicateNaturalKeyError):
        repository.upsert(rows, _request())
    assert executor.statements == []


def test_mysql_repository_rejects_unknown_update_payload_and_exposes_batch_bound():
    table = Table(
        "whitelist_rows",
        MetaData(),
        Column("code", String(6), primary_key=True),
        Column("trade_date", Date, primary_key=True),
        Column("value", Integer, nullable=False),
    )
    repository = MySQLAtomicUpsertRepository[dict[str, Any]](
        executor=CapturingExecutor(),
        table=table,
        natural_key=("code", "trade_date"),
        row_encoder=lambda row: row,
        policy=MySQLUpsertPolicy(overwrite_fields=("value",)),
        batch_size=2,
    )

    with pytest.raises(MySQLUpsertConfigurationError, match="不存在的字段"):
        repository.build_statement(
            [{"code": "000001", "trade_date": TRADE_DATE, "value": 1, "unsafe": "nope"}]
        )
    assert repository.update_whitelist == ("value",)
    assert repository.fixed_statement_count == 0
    assert repository.dml_statement_upper_bound(0) == 0
    assert repository.dml_statement_upper_bound(3) == 2


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [
        ("natural_key", (), "natural_key"),
        ("fetcher", object(), "fetcher.fetch"),
        ("freshness_extractor", None, "freshness_field"),
    ],
)
def test_registry_bootstrap_revalidates_descriptor_contracts_before_freezing(
    attribute: str,
    value: object,
    message: str,
):
    descriptor = _descriptor()
    registry = AuxiliaryDatasetRegistry()
    registry.register(descriptor)

    # Frozen dataclasses protect normal callers.  This deliberately bypasses that
    # guard to prove bootstrap re-checks every descriptor before publication.
    object.__setattr__(descriptor, attribute, value)

    with pytest.raises(AuxiliaryContractError, match=message):
        registry.freeze()
    assert registry.is_frozen is False


def test_registry_bootstrap_rejects_duplicate_key_and_invalid_dependency_graph():
    from app.services.auxiliary.registry import (
        AuxiliaryDependencyError,
        DuplicateAuxiliaryDatasetError,
    )

    duplicate = AuxiliaryDatasetRegistry()
    duplicate.register(_descriptor(key="one", view_key="one_view"))
    with pytest.raises(DuplicateAuxiliaryDatasetError):
        duplicate.register(_descriptor(key="one", view_key="two_view"))

    missing_dependency = AuxiliaryDatasetRegistry()
    missing_dependency.register(
        _descriptor(key="dependent", view_key="dependent_view", dependencies=("missing",))
    )
    with pytest.raises(AuxiliaryDependencyError, match="未注册"):
        missing_dependency.freeze()
    assert missing_dependency.is_frozen is False

    cyclic = AuxiliaryDatasetRegistry()
    cyclic.register(_descriptor(key="first", view_key="first_view", dependencies=("second",)))
    cyclic.register(_descriptor(key="second", view_key="second_view", dependencies=("first",)))
    with pytest.raises(AuxiliaryDependencyError, match="循环"):
        cyclic.freeze()
    assert cyclic.is_frozen is False


class NoConnectionSchemaReadiness:
    """Records descriptor assembly without claiming real-MySQL evidence."""

    def __init__(self) -> None:
        self.entries: list[str] = []

    def assert_ready(self, entry: Any) -> None:
        self.entries.append(entry.key)


def test_s4_inventory_declares_unique_plans_only_for_state_datasets_and_registers_them():
    """State datasets have discovered UNIQUE migrations; event datasets never do."""
    from app.db.migrations.auxiliary_unique import AuxiliaryUniqueConstraintDeclaration
    from app.db.migrations.versions import all_migrations
    from app.services.auxiliary.production import (
        AUXILIARY_DATASET_COMPLETION,
        AUXILIARY_DATASET_INVENTORY,
        AUXILIARY_UNIQUE_MIGRATION_PLANS,
        IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS,
        STATE_ATOMIC_UPSERT_DATASET_KEYS,
        AuxiliaryCompletionState,
        AuxiliaryMigrationRegistrationState,
        AuxiliarySnapshotDatasetCompletion,
        AuxiliaryStateDatasetCompletion,
        MySQLVerificationRequirement,
        validate_auxiliary_migration_inventory,
    )

    validate_auxiliary_migration_inventory()
    inventory_keys = {entry.key for entry in AUXILIARY_DATASET_INVENTORY}
    registered_versions = {migration.version for migration in all_migrations()}
    unique_versions = {plan.version for plan in AUXILIARY_UNIQUE_MIGRATION_PLANS.values()}

    assert set(AUXILIARY_UNIQUE_MIGRATION_PLANS) == STATE_ATOMIC_UPSERT_DATASET_KEYS
    assert not (set(AUXILIARY_UNIQUE_MIGRATION_PLANS) & IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS)
    assert set(AUXILIARY_DATASET_COMPLETION) == inventory_keys
    assert len(unique_versions) == 13
    assert unique_versions <= registered_versions

    for dataset_key in STATE_ATOMIC_UPSERT_DATASET_KEYS:
        entry = next(item for item in AUXILIARY_DATASET_INVENTORY if item.key == dataset_key)
        plan = AUXILIARY_UNIQUE_MIGRATION_PLANS[dataset_key]
        completion = AUXILIARY_DATASET_COMPLETION[dataset_key]

        assert isinstance(plan.declaration, AuxiliaryUniqueConstraintDeclaration)
        assert plan.declaration.dataset_key == entry.key
        assert plan.declaration.table == entry.table_name
        assert plan.declaration.natural_key == entry.natural_key
        assert plan.duplicate_preflight is MySQLVerificationRequirement.STORED_DATA_DUPLICATE_PREFLIGHT
        assert plan.registration_state is AuxiliaryMigrationRegistrationState.REGISTERED
        assert isinstance(completion, AuxiliaryStateDatasetCompletion)
        assert completion.migration_version == plan.version
        assert completion.duplicate_preflight is AuxiliaryCompletionState.PENDING_REAL_MYSQL
        assert completion.unique_migration is AuxiliaryCompletionState.PENDING_REAL_MYSQL
        assert completion.two_connection_acceptance is AuxiliaryCompletionState.PENDING_REAL_MYSQL
        assert completion.overall is AuxiliaryCompletionState.BLOCKED_REAL_MYSQL
        assert not completion.is_production_ready

    for dataset_key in IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS:
        completion = AUXILIARY_DATASET_COMPLETION[dataset_key]
        assert isinstance(completion, AuxiliarySnapshotDatasetCompletion)
        assert completion.snapshot_schema is AuxiliaryCompletionState.PENDING_SNAPSHOT_SCHEMA
        assert completion.provider_contract is AuxiliaryCompletionState.PENDING_PROVIDER_CONTRACT
        assert completion.publish_acceptance is AuxiliaryCompletionState.PENDING_PUBLISH_ACCEPTANCE
        assert completion.overall is AuxiliaryCompletionState.BLOCKED_SNAPSHOT_ACCEPTANCE
        assert not completion.is_production_ready


def test_s4_inventory_exercises_every_state_descriptor_with_no_connection_fakes():
    """The thirteen state descriptors remain generically assemblable without a database."""
    from app.services.auxiliary.production import (
        AUXILIARY_DATASET_INVENTORY,
        StandardAuxiliaryDatasetAdapters,
        build_production_auxiliary_orchestrator,
    )

    state_inventory = tuple(entry for entry in AUXILIARY_DATASET_INVENTORY if entry.is_state_dataset)
    readiness = NoConnectionSchemaReadiness()
    adapters = {
        entry.key: StandardAuxiliaryDatasetAdapters(
            fetcher=FakeFetcher([
                {"code": "000001", "value": entry.key},
                {"code": "600000", "value": entry.key},
            ]),
            normalizer=FakeNormalizer(),
            repository=FakeRepository(AuxiliaryRepositorySnapshot(0)),
            loader=FakeLoader(entry.view_key),
        )
        for entry in state_inventory
    }
    orchestrator = build_production_auxiliary_orchestrator(
        adapters,
        schema_readiness=readiness,
        inventory=state_inventory,
        clock=lambda: NOW,
    )

    result = orchestrator.prepare(
        datasets=tuple(entry.key for entry in state_inventory),
        instruments=INSTRUMENTS,
        trade_date=TRADE_DATE,
        refresh=True,
    )

    assert readiness.entries == [entry.key for entry in state_inventory]
    assert [item.dataset_key for item in result.results] == [entry.key for entry in state_inventory]
    assert result.errors == ()
    assert all(item.is_usable and item.persistence is not None for item in result.results)


def test_s4_default_production_orchestrator_builds_the_full_inventory_without_io():
    """The public production builder routes default 13+4 inventory through composition."""
    from app.services.auxiliary.production import (
        AUXILIARY_DATASET_INVENTORY,
        StandardAuxiliaryDatasetAdapters,
        build_production_auxiliary_orchestrator,
    )

    class NoIOConnection:
        def execute(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("registry construction must not query the connection")

    class NoFetchRawProvider:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_raw(self, *args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            raise AssertionError("registry construction must not fetch the provider")

    state_inventory = tuple(entry for entry in AUXILIARY_DATASET_INVENTORY if entry.is_state_dataset)
    event_keys = {entry.key for entry in AUXILIARY_DATASET_INVENTORY if entry.is_snapshot_dataset}
    readiness = NoConnectionSchemaReadiness()
    adapters = {
        entry.key: StandardAuxiliaryDatasetAdapters(
            fetcher=FakeFetcher([{"code": "000001", "value": entry.key}]),
            normalizer=FakeNormalizer(),
            repository=FakeRepository(AuxiliaryRepositorySnapshot(0)),
            loader=FakeLoader(entry.view_key),
        )
        for entry in state_inventory
    }
    provider = NoFetchRawProvider()

    orchestrator = build_production_auxiliary_orchestrator(
        adapters,
        schema_readiness=readiness,
        connection=NoIOConnection(),
        raw_provider=provider,
        clock=lambda: NOW,
    )

    descriptors = orchestrator._registry.descriptors()
    assert {descriptor.key for descriptor in descriptors} == {
        entry.key for entry in AUXILIARY_DATASET_INVENTORY
    }
    assert {descriptor.key for descriptor in descriptors if descriptor.custom_handler is not None} == event_keys
    assert readiness.entries == [entry.key for entry in state_inventory]
    assert provider.calls == 0
