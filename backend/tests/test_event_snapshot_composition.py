"""No-network contracts for S4 generic immutable event snapshots.

These tests use only injected raw-envelope fakes and caller-owned SQLite transactions.
They never acquire a provider singleton, open a global Session, or read legacy event
projection tables.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine

from app.services.auxiliary.composition import (
    EVENT_SNAPSHOT_CONTRACTS,
    INSTITUTIONAL_SURVEY_AUDIT_CONTRACT,
    build_auxiliary_production_composition,
)
from app.services.auxiliary.contracts import (
    AuxiliaryDatasetRequest,
    AuxiliaryInstrument,
    AuxiliaryLoadedView,
    AuxiliaryPersistResult,
    AuxiliaryRepositorySnapshot,
)
from app.services.auxiliary.event_snapshot import (
    ActiveEventSnapshotReader,
    EventSnapshotAuditCaptureService,
    EventSnapshotPilot,
    EventSnapshotRefreshService,
    EventSnapshotRequest,
    TushareSinglePageEventProviderClient,
)
from app.services.auxiliary.production import (
    AUXILIARY_DATASET_INVENTORY,
    StandardAuxiliaryDatasetAdapters,
)
from app.services.auxiliary.snapshot_contracts import PartitionSnapshotStatus
from app.services.auxiliary.snapshot_repository import (
    SQLAlchemyImmutablePartitionSnapshotRepository,
    snapshot_metadata,
)
from app.services.tushare_client import TushareRawEnvelope, TushareRawEnvelopeError


NOW = datetime(2026, 5, 18, 9, 30, tzinfo=timezone.utc)
TRADE_DATE = date(2026, 5, 18)
INSTRUMENTS = (AuxiliaryInstrument("000001", "SZ"),)


class FakeRawProvider:
    """Injected raw provider that exposes calls but retains no client/global state."""

    def __init__(self, responses: Mapping[str, object]) -> None:
        self._responses = dict(responses)
        self.calls: list[tuple[str, dict[str, object], str]] = []

    def fetch_raw(self, api_name: str, *, params=None, fields: str = "") -> TushareRawEnvelope:
        self.calls.append((api_name, dict(params or {}), fields))
        result = self._responses[api_name]
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            result = result(api_name, dict(params or {}), fields)
        assert isinstance(result, TushareRawEnvelope)
        return result


def _contract(contract_key: str):
    return (
        INSTITUTIONAL_SURVEY_AUDIT_CONTRACT
        if contract_key == "institutional_survey"
        else EVENT_SNAPSHOT_CONTRACTS[contract_key]
    )


def _record(contract_key: str) -> dict[str, object]:
    contract = _contract(contract_key)
    value_by_field: dict[str, object] = {
        "ts_code": "000001.SZ",
        "trade_date": "20260518",
        "ann_date": "20260518",
        "float_date": "20260618",
        "surv_date": "20260518",
        "name": "fixture-name",
        "title": "fixture-title",
        "url": "https://example.invalid/fixture",
        "rec_time": "2026-05-18 09:30:00",
        "ann_time": "2026-05-18 09:30:00",
        "symbol": "000001",
        "ann_type": "fixture-type",
        "price": 10.5,
        "vol": 1.0,
        "amount": 2.0,
        "buyer": "fixture-buyer",
        "seller": "fixture-seller",
        "float_share": 1.0,
        "float_ratio": 0.1,
        "holder_name": "fixture-holder",
        "share_type": "fixture-type",
        "holder_type": "fixture-holder-type",
        "in_de": "IN",
        "change_vol": 1.0,
        "change_ratio": 0.1,
        "after_share": 1.0,
        "after_ratio": 0.1,
        "avg_price": 10.0,
        "total_share": 2.0,
        "begin_date": "20260501",
        "close_date": "20260518",
        "fund_visitors": 1,
        "rece_place": "fixture-place",
        "rece_mode": "fixture-mode",
        "rece_org": "fixture-org",
        "org_type": "fixture-org-type",
        "comp_rece": "fixture-company",
    }
    return {field: value_by_field[field] for field in contract.documented_fields}


def _envelope(
    contract_key: str,
    rows: tuple[Mapping[str, object], ...] = (),
    *,
    fields: tuple[str, ...] | None = None,
    requested_fields: str = "",
) -> TushareRawEnvelope:
    contract = _contract(contract_key)
    schema = fields if fields is not None else contract.documented_fields
    return TushareRawEnvelope.from_envelope(
        api_name=contract.endpoint,
        requested_fields=requested_fields,
        envelope={
            "code": 0,
            "data": {
                "fields": list(schema),
                "items": [[row[field] for field in schema] for row in rows],
            },
        },
    )


def _request(contract_key: str) -> EventSnapshotRequest:
    if contract_key in {"block_trade", "news_announcement"}:
        return EventSnapshotRequest.from_mapping(
            {"trade_date" if contract_key == "block_trade" else "ann_date": "20991231"}
        )
    return EventSnapshotRequest.from_mapping({"ts_code": "000001.SZ"})


def _refresh_service(connection, contract_key: str, raw_provider: FakeRawProvider) -> EventSnapshotRefreshService:
    contract = _contract(contract_key)
    repository = SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=lambda: NOW)
    return EventSnapshotRefreshService(
        EventSnapshotPilot(contract, TushareSinglePageEventProviderClient(raw_provider, contract)),
        repository,
    )


@pytest.mark.parametrize(
    ("contract_key", "cap"),
    (
        ("block_trade", 1000),
        ("share_float", 6000),
        ("holder_trade", 3000),
        ("news_announcement", 2000),
    ),
)
def test_documented_single_page_contract_uses_strict_less_than_cap(contract_key: str, cap: int) -> None:
    contract = _contract(contract_key)
    one_row = _record(contract_key)

    complete = contract.completeness_for(_envelope(contract_key, (one_row,)))
    cap_bound = contract.completeness_for(_envelope(contract_key, (one_row,) * cap))

    assert contract.documentation_url is not None
    assert contract.documented_single_request_cap == cap
    assert complete.limit.truncation_ruled_out is True
    assert cap_bound.limit.returned_rows == cap
    assert cap_bound.limit.truncation_ruled_out is False


def test_raw_adapter_requires_verified_schema_all_fields_and_preserves_duplicates() -> None:
    contract = EVENT_SNAPSHOT_CONTRACTS["share_float"]
    row = _record("share_float")
    raw_provider = FakeRawProvider({"share_float": _envelope("share_float", (row, row))})
    client = TushareSinglePageEventProviderClient(raw_provider, contract)
    response = client.fetch_event(_request("share_float"))

    assert raw_provider.calls == [("share_float", {"ts_code": "000001.SZ"}, "")]
    assert response.rows == (row, row)
    assert response.completeness is not None
    assert response.completeness.field_set.response_fields == contract.documented_fields
    assert response.completeness.field_set.requested_all_fields is True


def test_provider_failure_and_unknown_schema_become_nonpublishable_snapshot_failures() -> None:
    contract = EVENT_SNAPSHOT_CONTRACTS["holder_trade"]
    request = _request("holder_trade")

    failed = EventSnapshotPilot(
        contract,
        TushareSinglePageEventProviderClient(
            FakeRawProvider({"stk_holdertrade": RuntimeError("fixture provider failure")}),
            contract,
        ),
    ).capture(
        batch_id="event-test-provider-failure",
        request=request,
        requested_as_of=NOW,
        fetched_at=NOW,
    )
    unknown_fields = (*contract.documented_fields, "unverified_field")
    unknown = EventSnapshotPilot(
        contract,
        TushareSinglePageEventProviderClient(
            FakeRawProvider(
                {
                    "stk_holdertrade": _envelope(
                        "holder_trade",
                        ({**_record("holder_trade"), "unverified_field": "fixture"},),
                        fields=unknown_fields,
                    )
                }
            ),
            contract,
        ),
    ).capture(
        batch_id="event-test-unknown-schema",
        request=request,
        requested_as_of=NOW,
        fetched_at=NOW,
    )

    assert failed.status is PartitionSnapshotStatus.FAILURE
    assert failed.failure is not None and failed.failure.code == "event_provider_request_failed"
    assert unknown.status is PartitionSnapshotStatus.FAILURE
    assert unknown.failure is not None and unknown.failure.code == "event_provider_unknown_schema"


def test_refresh_persists_duplicate_failure_and_empty_outcomes_but_only_publishes_proven_responses() -> None:
    engine = create_engine("sqlite:///:memory:")
    snapshot_metadata.create_all(engine)
    with engine.begin() as connection:
        duplicate = _record("share_float")
        share_outcome = _refresh_service(
            connection,
            "share_float",
            FakeRawProvider({"share_float": _envelope("share_float", (duplicate, duplicate))}),
        ).refresh(
            batch_id="event-test-duplicates",
            request=_request("share_float"),
            requested_as_of=NOW,
            fetched_at=NOW,
        )
        failure_outcome = _refresh_service(
            connection,
            "holder_trade",
            FakeRawProvider({"stk_holdertrade": RuntimeError("fixture provider failure")}),
        ).refresh(
            batch_id="event-test-failure",
            request=_request("holder_trade"),
            requested_as_of=NOW,
            fetched_at=NOW,
        )
        empty_outcome = _refresh_service(
            connection,
            "news_announcement",
            FakeRawProvider({"anns_d": _envelope("news_announcement")}),
        ).refresh(
            batch_id="event-test-empty",
            request=_request("news_announcement"),
            requested_as_of=NOW,
            fetched_at=NOW,
        )
        reader = ActiveEventSnapshotReader(
            EVENT_SNAPSHOT_CONTRACTS["share_float"],
            SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=lambda: NOW),
        )
        active = reader.load(_request("share_float"))

        assert share_outcome.was_published is True
        assert active is not None and active.row_count == 2
        assert active.rows[0].payload_hash == active.rows[1].payload_hash
        assert failure_outcome.provider_result.status is PartitionSnapshotStatus.FAILURE
        assert failure_outcome.was_published is False
        assert failure_outcome.publish_block_reason is not None
        assert empty_outcome.provider_result.status is PartitionSnapshotStatus.SUCCESSFUL_EMPTY
        assert empty_outcome.was_published is True


def test_audit_only_institutional_survey_persists_validated_raw_evidence_without_publication() -> None:
    from app.services.auxiliary.snapshot_repository import SnapshotAuditOnlyAccessError

    engine = create_engine("sqlite:///:memory:")
    snapshot_metadata.create_all(engine)
    with engine.begin() as connection:
        contract = INSTITUTIONAL_SURVEY_AUDIT_CONTRACT
        service = EventSnapshotAuditCaptureService(
            EventSnapshotPilot(
                contract,
                TushareSinglePageEventProviderClient(
                    FakeRawProvider({"stk_surv": _envelope("institutional_survey", (_record("institutional_survey"),))}),
                    contract,
                ),
            ),
            SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=lambda: NOW),
        )
        outcome = service.capture(
            batch_id="event-test-survey-audit",
            request=_request("institutional_survey"),
            requested_as_of=NOW,
            fetched_at=NOW,
        )
        with pytest.raises(SnapshotAuditOnlyAccessError, match="audit-only"):
            ActiveEventSnapshotReader(
                contract,
                SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=lambda: NOW),
            )

        assert outcome.provider_result.status is PartitionSnapshotStatus.COMPLETE
        assert outcome.provider_result.completeness is None
        assert outcome.persisted.created is True
        assert outcome.active_pointer_published is False


def test_audit_only_survey_empty_envelope_is_persisted_without_inferred_completeness() -> None:
    engine = create_engine("sqlite:///:memory:")
    snapshot_metadata.create_all(engine)
    envelope = TushareRawEnvelope.from_envelope(
        api_name="stk_surv",
        requested_fields="",
        envelope={"code": 0, "data": {"items": []}},
    )
    with engine.begin() as connection:
        contract = INSTITUTIONAL_SURVEY_AUDIT_CONTRACT
        outcome = EventSnapshotAuditCaptureService(
            EventSnapshotPilot(
                contract,
                TushareSinglePageEventProviderClient(FakeRawProvider({"stk_surv": envelope}), contract),
            ),
            SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=lambda: NOW),
        ).capture(
            batch_id="event-test-survey-empty-audit",
            request=_request("institutional_survey"),
            requested_as_of=NOW,
            fetched_at=NOW,
        )

        assert outcome.provider_result.status is PartitionSnapshotStatus.SUCCESSFUL_EMPTY
        assert outcome.provider_result.completeness is None
        assert outcome.persisted.row_count == 0
        assert outcome.active_pointer_published is False


class _StateFetcher:
    def fetch(self, request: AuxiliaryDatasetRequest) -> tuple[dict[str, object], ...]:
        return ({"code": request.instruments[0].code if request.instruments else "000001"},)


class _StateNormalizer:
    def normalize(self, raw: Mapping[str, object], request: AuxiliaryDatasetRequest) -> dict[str, object]:
        return {**raw, "trade_date": request.trade_date}


class _StateRepository:
    def inspect(self, request: AuxiliaryDatasetRequest) -> AuxiliaryRepositorySnapshot:
        del request
        return AuxiliaryRepositorySnapshot(0)

    def upsert(
        self,
        records: tuple[Mapping[str, object], ...],
        request: AuxiliaryDatasetRequest,
    ) -> AuxiliaryPersistResult:
        del request
        return AuxiliaryPersistResult(
            attempted_rows=len(records),
            affected_rows=None,
            failed_rows=0,
            execute_count=1,
            duration_ms=0.0,
            batch_size=1,
            batch_statement_bound=1,
        )


class _StateLoader:
    def __init__(self, view_key: str) -> None:
        self._view_key = view_key

    def load(self, request: AuxiliaryDatasetRequest) -> AuxiliaryLoadedView[dict[str, object]]:
        return AuxiliaryLoadedView(self._view_key, {"dataset": request.dataset_key})


class _ReadySchema:
    def __init__(self) -> None:
        self.checked: list[str] = []

    def assert_ready(self, entry) -> None:
        self.checked.append(entry.key)


def _state_adapters() -> dict[str, StandardAuxiliaryDatasetAdapters]:
    return {
        entry.key: StandardAuxiliaryDatasetAdapters(
            fetcher=_StateFetcher(),
            normalizer=_StateNormalizer(),
            repository=_StateRepository(),
            loader=_StateLoader(entry.view_key),
        )
        for entry in AUXILIARY_DATASET_INVENTORY
        if entry.is_state_dataset
    }


def test_injected_composition_freezes_full_inventory_and_routes_events_through_generic_handlers() -> None:
    engine = create_engine("sqlite:///:memory:")
    snapshot_metadata.create_all(engine)

    def response_for(api_name: str, _params: dict[str, object], _fields: str) -> TushareRawEnvelope:
        contract_key = next(key for key, item in EVENT_SNAPSHOT_CONTRACTS.items() if item.endpoint == api_name)
        return _envelope(contract_key)

    raw_provider = FakeRawProvider({contract.endpoint: response_for for contract in EVENT_SNAPSHOT_CONTRACTS.values()})
    readiness = _ReadySchema()
    # 本测试验证的是装配冻结与事件路由，每个数据集只喂一行样本；生产规模的行数下限
    # （例如 margin_detail 的两融全市场约 4450 只）在这种样本量下必然判不足，与
    # 被验证的行为无关，因此注入一份去掉该门槛的 inventory，让 no-errors 断言仍然严格。
    inventory = tuple(
        replace(entry, minimum_record_count=0) if entry.minimum_record_count else entry
        for entry in AUXILIARY_DATASET_INVENTORY
    )
    with engine.begin() as connection:
        composition = build_auxiliary_production_composition(
            connection=connection,
            raw_provider=raw_provider,
            state_adapters=_state_adapters(),
            schema_readiness=readiness,
            clock=lambda: NOW,
            batch_id_factory=lambda dataset, _request: f"event-test-{dataset}-{len(raw_provider.calls)}",
            inventory=inventory,
        )
        result = composition.orchestrator.prepare(
            datasets=tuple(entry.key for entry in AUXILIARY_DATASET_INVENTORY),
            instruments=INSTRUMENTS,
            trade_date=TRADE_DATE,
            refresh=True,
        )

        by_dataset = result.by_dataset
        state_keys = {entry.key for entry in AUXILIARY_DATASET_INVENTORY if entry.is_state_dataset}
        event_keys = set(EVENT_SNAPSHOT_CONTRACTS)
        assert composition.registry.is_frozen is True
        assert set(by_dataset) == state_keys | event_keys
        assert len(by_dataset) == 17
        assert readiness.checked == [entry.key for entry in AUXILIARY_DATASET_INVENTORY if entry.is_state_dataset]
        assert all(not by_dataset[key].errors for key in state_keys | event_keys)
        assert all(call[2] == "" for call in raw_provider.calls)
        assert {call[0] for call in raw_provider.calls} == {
            contract.endpoint for contract in EVENT_SNAPSHOT_CONTRACTS.values()
        }
        assert all(call[0] != "stk_surv" for call in raw_provider.calls)


def test_news_snapshot_projection_never_synthesizes_article_identity() -> None:
    engine = create_engine("sqlite:///:memory:")
    snapshot_metadata.create_all(engine)
    entry = next(item for item in AUXILIARY_DATASET_INVENTORY if item.key == "news_announcement")
    contract = EVENT_SNAPSHOT_CONTRACTS["news_announcement"]
    raw_provider = FakeRawProvider({"anns_d": _envelope("news_announcement", (_record("news_announcement"),))})

    with engine.begin() as connection:
        composition = build_auxiliary_production_composition(
            connection=connection,
            raw_provider=raw_provider,
            state_adapters={},
            schema_readiness=_ReadySchema(),
            inventory=(entry,),
            event_contracts={entry.key: contract},
            clock=lambda: NOW,
        )
        result = composition.orchestrator.prepare(
            datasets=(entry.key,),
            instruments=INSTRUMENTS,
            trade_date=TRADE_DATE,
            refresh=True,
        ).result_for(entry.key)

        assert result.errors == ()
        assert result.view is not None and result.view["000001"]
        assert all("article_id" not in item for item in result.view["000001"])


def test_caller_owned_transaction_rolls_back_snapshot_batch_and_pointer_without_residue() -> None:
    engine = create_engine("sqlite:///:memory:")
    snapshot_metadata.create_all(engine)
    entry = next(item for item in AUXILIARY_DATASET_INVENTORY if item.key == "block_trade")
    contract = EVENT_SNAPSHOT_CONTRACTS["block_trade"]
    raw_provider = FakeRawProvider({"block_trade": _envelope("block_trade")})

    with engine.connect() as connection:
        transaction = connection.begin()
        composition = build_auxiliary_production_composition(
            connection=connection,
            raw_provider=raw_provider,
            state_adapters={},
            schema_readiness=_ReadySchema(),
            inventory=(entry,),
            event_contracts={entry.key: contract},
            clock=lambda: NOW,
            batch_id_factory=lambda _dataset, _request: "event-test-rollback-batch",
        )
        result = composition.orchestrator.prepare(
            datasets=(entry.key,),
            instruments=INSTRUMENTS,
            trade_date=TRADE_DATE,
            refresh=True,
        ).result_for(entry.key)
        assert result.errors == ()
        assert result.view == {}
        transaction.rollback()

    with engine.connect() as connection:
        repository = SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=lambda: NOW)
        assert repository.load_active(
            dataset=contract.dataset,
            provider=contract.provider,
            endpoint=contract.endpoint,
            request_partition=contract.request_partition(_request("block_trade")),
        ) is None


@pytest.mark.parametrize(
    "data",
    (
        {"items": []},
        {"fields": [], "items": []},
        {"fields": None, "items": []},
    ),
)
def test_raw_adapter_accepts_tushare_compatible_empty_envelopes_without_schema(
    data: Mapping[str, object],
) -> None:
    """Successful empty raw replies remain publishable for documented capped feeds."""
    contract = EVENT_SNAPSHOT_CONTRACTS["news_announcement"]
    envelope = TushareRawEnvelope.from_envelope(
        api_name=contract.endpoint,
        requested_fields="",
        envelope={"code": 0, "data": data},
    )
    response = TushareSinglePageEventProviderClient(
        FakeRawProvider({contract.endpoint: envelope}),
        contract,
    ).fetch_event(_request("news_announcement"))

    assert envelope.fields == ()
    assert envelope.items == ()
    assert response.rows == ()
    assert response.completeness is not None
    assert response.completeness.limit.truncation_ruled_out is True


@pytest.mark.parametrize(
    "data",
    (
        {"items": [["unmapped-value"]]},
        {"fields": [], "items": [[]]},
    ),
)
def test_raw_adapter_rejects_nonempty_envelopes_without_exact_schema(
    data: Mapping[str, object],
) -> None:
    with pytest.raises(TushareRawEnvelopeError, match="non-empty raw provider items"):
        TushareRawEnvelope.from_envelope(
            api_name="stk_surv",
            requested_fields="",
            envelope={"code": 0, "data": data},
        )
