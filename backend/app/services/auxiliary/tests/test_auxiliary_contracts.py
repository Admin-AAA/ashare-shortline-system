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
            overwrite_fields=("value",),
            preserve_fields=("source_version",),
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
