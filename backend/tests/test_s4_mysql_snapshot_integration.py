"""Explicit, rollback-only real-MySQL proof for S4 snapshot composition.

This opt-in suite targets only the configured local MySQL database.  Its schema and
stored-data checks are read-only.  Every fixture write uses a caller-owned transaction
and rolls back; assertions expose only schema names and counts, never connection data
or provider/business rows.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from datetime import date, datetime, timezone
from queue import Empty, Queue
from threading import Event, Thread
from typing import Any

import pytest
from sqlalchemy import bindparam, create_engine, func, inspect, select, text

from app.core.config import get_settings
from app.db.migrations.versions.v024_auxiliary_partition_snapshots import (
    BATCH_TABLE,
    POINTER_TABLE,
    ROW_TABLE,
    VERSION,
)
from app.models.tables import CapitalFlow
from app.services.auxiliary.composition import (
    EVENT_SNAPSHOT_CONTRACTS,
    INSTITUTIONAL_SURVEY_AUDIT_CONTRACT,
    build_auxiliary_production_composition,
)
from app.services.auxiliary.contracts import AuxiliaryDatasetRequest, AuxiliaryInstrument
from app.services.auxiliary.mysql_repository import MySQLAtomicUpsertRepository, MySQLUpsertPolicy
from app.services.auxiliary.production import (
    AUXILIARY_DATASET_INVENTORY,
    AUXILIARY_UNIQUE_MIGRATION_PLANS,
)
from app.services.auxiliary.event_snapshot import (
    EventSnapshotAuditCaptureService,
    EventSnapshotPilot,
    EventSnapshotRequest,
    TushareSinglePageEventProviderClient,
)
from app.services.auxiliary.snapshot_repository import (
    SQLAlchemyImmutablePartitionSnapshotRepository,
    active_partition_snapshots,
    snapshot_batches,
)
from app.services.tushare_client import TushareRawEnvelope


NOW = datetime(2026, 5, 18, 9, 30, tzinfo=timezone.utc)
TRADE_DATE = date(2026, 5, 18)


class _RawProvider:
    def __init__(self, responses: Mapping[str, object]) -> None:
        self._responses = dict(responses)
        self.calls: list[tuple[str, dict[str, object], str]] = []

    def fetch_raw(self, api_name: str, *, params=None, fields: str = "") -> TushareRawEnvelope:
        self.calls.append((api_name, dict(params or {}), fields))
        response = self._responses[api_name]
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            response = response(api_name, dict(params or {}), fields)
        assert isinstance(response, TushareRawEnvelope)
        return response


class _SchemaReadiness:
    def assert_ready(self, entry: object) -> None:
        del entry


class _SignalBeforeExecute:
    """Transaction-bound executor that tells the test when the contending DML starts."""

    def __init__(self, connection, attempting: Event) -> None:
        self._connection = connection
        self._attempting = attempting

    def execute(self, statement: Any) -> Any:
        self._attempting.set()
        return self._connection.execute(statement)


def _contract(contract_key: str):
    return (
        INSTITUTIONAL_SURVEY_AUDIT_CONTRACT
        if contract_key == "institutional_survey"
        else EVENT_SNAPSHOT_CONTRACTS[contract_key]
    )


def _record(contract_key: str) -> dict[str, object]:
    contract = _contract(contract_key)
    values: dict[str, object] = {
        "ts_code": "S4FIX.SZ",
        "trade_date": "20991231",
        "ann_date": "20991231",
        "float_date": "20991231",
        "surv_date": "20991231",
        "name": "fixture",
        "title": "fixture",
        "url": "https://example.invalid/s4-fixture",
        "rec_time": "2099-12-31 00:00:00",
        "price": 1.0,
        "vol": 1.0,
        "amount": 1.0,
        "buyer": "fixture",
        "seller": "fixture",
        "float_share": 1.0,
        "float_ratio": 1.0,
        "holder_name": "fixture",
        "share_type": "fixture",
        "holder_type": "fixture",
        "in_de": "IN",
        "change_vol": 1.0,
        "change_ratio": 1.0,
        "after_share": 1.0,
        "after_ratio": 1.0,
        "avg_price": 1.0,
        "total_share": 1.0,
        "begin_date": "20990101",
        "close_date": "20991231",
        "fund_visitors": 1,
        "rece_place": "fixture",
        "rece_mode": "fixture",
        "rece_org": "fixture",
        "org_type": "fixture",
        "comp_rece": "fixture",
    }
    return {field: values[field] for field in contract.documented_fields}


def _envelope(contract_key: str, rows: tuple[Mapping[str, object], ...] = ()) -> TushareRawEnvelope:
    contract = _contract(contract_key)
    return TushareRawEnvelope.from_envelope(
        api_name=contract.endpoint,
        requested_fields="",
        envelope={
            "code": 0,
            "data": {
                "fields": list(contract.documented_fields),
                "items": [[row[field] for field in contract.documented_fields] for row in rows],
            },
        },
    )


def _event_inventory():
    return tuple(entry for entry in AUXILIARY_DATASET_INVENTORY if entry.is_snapshot_dataset)


def _state_inventory():
    entries = tuple(entry for entry in AUXILIARY_DATASET_INVENTORY if entry.is_state_dataset)
    assert len(entries) == 13
    assert set(AUXILIARY_UNIQUE_MIGRATION_PLANS) == {entry.key for entry in entries}
    return entries


def _assert_v024_shapes(engine) -> None:
    inspector = inspect(engine)
    assert {BATCH_TABLE, ROW_TABLE, POINTER_TABLE} <= set(inspector.get_table_names())
    assert tuple(inspector.get_pk_constraint(BATCH_TABLE)["constrained_columns"]) == ("batch_id",)
    assert tuple(inspector.get_pk_constraint(ROW_TABLE)["constrained_columns"]) == (
        "batch_id",
        "row_ordinal",
    )
    assert tuple(inspector.get_pk_constraint(POINTER_TABLE)["constrained_columns"]) == (
        "dataset",
        "provider",
        "endpoint",
        "partition_hash",
    )
    batch_uniques = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(BATCH_TABLE)}
    pointer_uniques = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(POINTER_TABLE)}
    assert ("dataset", "provider", "endpoint", "partition_hash", "batch_id") in batch_uniques
    assert ("active_batch_id",) in pointer_uniques
    row_indexes = {item["name"]: item for item in inspector.get_indexes(ROW_TABLE)}
    assert row_indexes["idx_aux_snapshot_rows_payload_hash"]["column_names"] == ["payload_hash"]
    assert not bool(row_indexes["idx_aux_snapshot_rows_payload_hash"].get("unique"))
    assert not any(
        tuple(item["column_names"]) == ("payload_hash",)
        for item in inspector.get_unique_constraints(ROW_TABLE)
    )
    assert any(
        item["constrained_columns"] == ["batch_id"]
        and item["referred_table"] == BATCH_TABLE
        and item["referred_columns"] == ["batch_id"]
        for item in inspector.get_foreign_keys(ROW_TABLE)
    )
    assert any(
        item["constrained_columns"]
        == ["dataset", "provider", "endpoint", "partition_hash", "active_batch_id"]
        and item["referred_table"] == BATCH_TABLE
        and item["referred_columns"] == ["dataset", "provider", "endpoint", "partition_hash", "batch_id"]
        for item in inspector.get_foreign_keys(POINTER_TABLE)
    )


def _assert_state_unique_shapes(engine) -> None:
    inspector = inspect(engine)
    for entry in _state_inventory():
        plan = AUXILIARY_UNIQUE_MIGRATION_PLANS[entry.key]
        declaration = plan.declaration
        assert declaration.table == entry.table_name
        assert declaration.natural_key == entry.natural_key
        shapes = {
            (item["name"], tuple(item["column_names"]), bool(item.get("unique")))
            for item in inspector.get_indexes(declaration.table)
        }
        shapes.update(
            (item["name"], tuple(item["column_names"]), True)
            for item in inspector.get_unique_constraints(declaration.table)
        )
        assert (declaration.index, declaration.natural_key, True) in shapes, entry.key


def _assert_state_duplicate_key_preflight(connection) -> None:
    """Read every frozen state natural key with a bound duplicate-group threshold."""
    for entry in _state_inventory():
        plan = AUXILIARY_UNIQUE_MIGRATION_PLANS[entry.key]
        assert connection.scalar(
            text("SELECT COUNT(*) FROM schema_migrations WHERE version = :version"),
            {"version": plan.version},
        ) == 1
        table = entry.model.__table__
        natural_columns = tuple(table.c[field] for field in entry.natural_key)
        duplicate_groups = (
            select(*natural_columns)
            .group_by(*natural_columns)
            .having(func.count() > bindparam("minimum_group_size"))
            .subquery()
        )
        count = connection.scalar(
            select(func.count()).select_from(duplicate_groups),
            {"minimum_group_size": 1},
        )
        assert int(count or 0) == 0, entry.key


def _capital_flow_repository(executor) -> MySQLAtomicUpsertRepository[dict[str, object]]:
    return MySQLAtomicUpsertRepository[dict[str, object]](
        executor=executor,
        table=CapitalFlow.__table__,
        natural_key=("code", "trade_date"),
        row_encoder=lambda value: value,
        policy=MySQLUpsertPolicy(overwrite_fields=("main_net_inflow_yi",)),
        batch_size=1,
    )


def _set_innodb_lock_wait_timeout(connection, seconds: int) -> None:
    """Constrain a test-only waiting connection, then finish its SET transaction."""
    connection.execute(
        text("SET SESSION innodb_lock_wait_timeout = :seconds"),
        {"seconds": seconds},
    )
    connection.commit()


def _assert_two_connection_same_key_contention(engine, fixture_code: str) -> None:
    """Prove the second upsert blocks on T1, then rolls back after T1 releases it."""
    request = AuxiliaryDatasetRequest(
        dataset_key="capital_flow",
        instruments=(AuxiliaryInstrument(fixture_code, "SZ"),),
        trade_date=date(2099, 12, 31),
        refresh_enabled=True,
        requested_at=NOW,
    )
    row = {
        "code": fixture_code,
        "trade_date": "2099-12-31",
        "main_net_inflow_yi": 1.0,
    }
    attempting = Event()
    finished = Event()
    outcome: Queue[tuple[str, int | None]] = Queue(maxsize=1)

    def second_writer() -> None:
        connection = None
        transaction = None
        try:
            connection = engine.connect()
            _set_innodb_lock_wait_timeout(connection, seconds=10)
            transaction = connection.begin()
            _capital_flow_repository(_SignalBeforeExecute(connection, attempting)).upsert((row,), request)
            logical_count = connection.scalar(
                select(func.count()).select_from(CapitalFlow.__table__).where(
                    CapitalFlow.code == fixture_code,
                    CapitalFlow.trade_date == "2099-12-31",
                )
            )
            outcome.put(("count", int(logical_count or 0)))
        except BaseException:  # pragma: no cover - failure is asserted from a safe marker below
            outcome.put(("error", None))
        finally:
            if transaction is not None and transaction.is_active:
                transaction.rollback()
            if connection is not None:
                connection.close()
            finished.set()

    first_connection = engine.connect()
    first_transaction = None
    thread = Thread(target=second_writer, name="s4-same-key-upsert", daemon=True)
    try:
        first_transaction = first_connection.begin()
        first_metrics = _capital_flow_repository(first_connection).upsert((row,), request)
        assert first_metrics.execute_count == 1
        thread.start()
        assert attempting.wait(timeout=3)
        # The second execute has started but cannot return while T1 retains the unique-key lock.
        assert not finished.wait(timeout=0.4)
        first_transaction.rollback()
        first_transaction = None
        assert finished.wait(timeout=12)
        thread.join(timeout=1)
        try:
            assert outcome.get_nowait() == ("count", 1)
        except Empty as exc:  # pragma: no cover - protected by the finished event above
            raise AssertionError("contending writer finished without an outcome marker") from exc
    finally:
        if first_transaction is not None and first_transaction.is_active:
            first_transaction.rollback()
        first_connection.close()
        if thread.is_alive():
            thread.join(timeout=12)

    with engine.connect() as verifier:
        assert verifier.scalar(
            select(func.count()).select_from(CapitalFlow.__table__).where(
                CapitalFlow.code == fixture_code,
                CapitalFlow.trade_date == "2099-12-31",
            )
        ) == 0


def test_s4_real_mysql_schema_ledger_and_duplicate_preflight_are_read_only() -> None:
    if os.environ.get("RUN_REAL_MYSQL_S4") != "1":
        pytest.skip("set RUN_REAL_MYSQL_S4=1 to run the read-only local MySQL S4 proof")

    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        with engine.connect() as preflight:
            assert preflight.scalar(
                text("SELECT COUNT(*) FROM schema_migrations WHERE version = :version"),
                {"version": VERSION},
            ) == 1
            _assert_v024_shapes(engine)
            _assert_state_unique_shapes(engine)
            _assert_state_duplicate_key_preflight(preflight)
    finally:
        engine.dispose()


def test_s4_real_mysql_snapshot_and_atomic_upsert_proof_is_rollback_only() -> None:
    if os.environ.get("RUN_REAL_MYSQL_S4") != "1":
        pytest.skip("set RUN_REAL_MYSQL_S4=1 to run the rollback-only local MySQL proof")

    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    prefix = f"s4-fixture-{uuid.uuid4().hex[:20]}"
    fixture_code = f"{uuid.uuid4().int % 1_000_000:06d}"
    try:
        def response_for(api_name: str, _params: dict[str, object], _fields: str) -> TushareRawEnvelope:
            key = next(item for item, contract in EVENT_SNAPSHOT_CONTRACTS.items() if contract.endpoint == api_name)
            return _envelope(key)

        raw_provider = _RawProvider(
            {contract.endpoint: response_for for contract in EVENT_SNAPSHOT_CONTRACTS.values()}
        )
        with engine.connect() as writer, engine.connect() as observer:
            transaction = writer.begin()
            composition = build_auxiliary_production_composition(
                connection=writer,
                raw_provider=raw_provider,
                state_adapters={},
                schema_readiness=_SchemaReadiness(),
                inventory=_event_inventory(),
                event_contracts=EVENT_SNAPSHOT_CONTRACTS,
                clock=lambda: NOW,
                batch_id_factory=lambda dataset, _request: f"{prefix}-{dataset}-{len(raw_provider.calls)}",
            )
            result = composition.orchestrator.prepare(
                datasets=tuple(EVENT_SNAPSHOT_CONTRACTS),
                instruments=(AuxiliaryInstrument("000001", "SZ"),),
                trade_date=TRADE_DATE,
                refresh=True,
            )
            by_dataset = result.by_dataset
            assert set(by_dataset) == set(EVENT_SNAPSHOT_CONTRACTS)
            assert all(not by_dataset[key].errors for key in EVENT_SNAPSHOT_CONTRACTS)
            assert all(call[0] != "stk_surv" for call in raw_provider.calls)

            audit_repository = SQLAlchemyImmutablePartitionSnapshotRepository(writer, clock=lambda: NOW)
            audit_service = EventSnapshotAuditCaptureService(
                EventSnapshotPilot(
                    INSTITUTIONAL_SURVEY_AUDIT_CONTRACT,
                    TushareSinglePageEventProviderClient(
                        _RawProvider({"stk_surv": _envelope("institutional_survey", (_record("institutional_survey"),))}),
                        INSTITUTIONAL_SURVEY_AUDIT_CONTRACT,
                    ),
                ),
                audit_repository,
            )
            audit_request = EventSnapshotRequest.from_mapping({"ts_code": "000001.SZ"})
            audit_success = audit_service.capture(
                batch_id=f"{prefix}-survey-success",
                request=audit_request,
                requested_as_of=NOW,
                fetched_at=NOW,
            )
            audit_failure = EventSnapshotAuditCaptureService(
                EventSnapshotPilot(
                    INSTITUTIONAL_SURVEY_AUDIT_CONTRACT,
                    TushareSinglePageEventProviderClient(
                        _RawProvider({"stk_surv": RuntimeError("fixture provider failure")}),
                        INSTITUTIONAL_SURVEY_AUDIT_CONTRACT,
                    ),
                ),
                audit_repository,
            ).capture(
                batch_id=f"{prefix}-survey-failure",
                request=audit_request,
                requested_as_of=NOW,
                fetched_at=NOW,
            )
            assert audit_success.persisted.created is True
            assert audit_success.active_pointer_published is False
            assert audit_failure.provider_result.failure is not None
            assert audit_failure.persisted.created is True
            assert audit_failure.active_pointer_published is False
            assert writer.scalar(
                select(func.count()).select_from(active_partition_snapshots).where(
                    active_partition_snapshots.c.dataset == "institutional_survey"
                )
            ) == 0
            assert observer.scalar(
                select(func.count()).select_from(snapshot_batches).where(snapshot_batches.c.batch_id.like(f"{prefix}%"))
            ) == 0
            assert observer.scalar(
                select(func.count()).select_from(active_partition_snapshots).where(
                    active_partition_snapshots.c.active_batch_id.like(f"{prefix}%")
                )
            ) == 0
            transaction.rollback()

        with engine.connect() as verifier:
            assert verifier.scalar(
                select(func.count()).select_from(snapshot_batches).where(snapshot_batches.c.batch_id.like(f"{prefix}%"))
            ) == 0
            assert verifier.scalar(
                select(func.count()).select_from(active_partition_snapshots).where(
                    active_partition_snapshots.c.active_batch_id.like(f"{prefix}%")
                )
            ) == 0

        _assert_two_connection_same_key_contention(engine, fixture_code)
    finally:
        engine.dispose()
