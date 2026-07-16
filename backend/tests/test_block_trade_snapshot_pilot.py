from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from app.services.auxiliary.block_trade_snapshot import (
    ActiveBlockTradeProjection,
    BlockTradeNativeResponse,
    BlockTradeProviderRequestError,
    BlockTradeSnapshotPilot,
    DataFrameBlockTradeProviderClient,
    block_trade_request_partition,
)
from app.services.auxiliary.provider_results import (
    FieldSetEvidence,
    LimitEvidence,
    PaginationEvidence,
    ResponseCompletenessEvidence,
    SnapshotPublishBlockedError,
)
from app.services.auxiliary.snapshot_codec import decode_canonical_payload
from app.services.auxiliary.snapshot_contracts import PartitionSnapshotStatus


NOW = datetime(2026, 5, 18, 1, tzinfo=timezone.utc)
FIELDS = (
    "ts_code",
    "trade_date",
    "price",
    "vol",
    "amount",
    "buyer",
    "seller",
    "provider_only",
    "nullable",
    "empty_text",
    "zero_value",
)


def _evidence(row_count: int, fields=FIELDS) -> ResponseCompletenessEvidence:
    return ResponseCompletenessEvidence(
        pagination=PaginationEvidence(page_count=1, all_pages_fetched=True),
        limit=LimitEvidence(
            returned_rows=row_count,
            truncation_ruled_out=True,
            provider_reported_total=row_count,
        ),
        field_set=FieldSetEvidence(
            response_fields=tuple(fields),
            all_returned_fields_captured=True,
            requested_all_fields=True,
        ),
    )


class FakeProvider:
    def __init__(self, response: BlockTradeNativeResponse) -> None:
        self.response = response
        self.requests = []

    def fetch_block_trade(self, request):
        self.requests.append(request)
        return self.response


def test_pilot_captures_lossless_full_rows_duplicates_and_fetch_order() -> None:
    row = {
        "ts_code": "000001.SZ",
        "trade_date": "20260518",
        "price": Decimal("0"),
        "vol": None,
        "amount": "",
        "buyer": "",
        "seller": None,
        "provider_only": {"desk_rank": 0},
        "nullable": None,
        "empty_text": "",
        "zero_value": 0,
    }
    provider = FakeProvider(BlockTradeNativeResponse(rows=(row, row), completeness=_evidence(2)))
    result = BlockTradeSnapshotPilot(provider).capture(
        batch_id="block-batch",
        trade_date="2026-05-18",
        requested_as_of=NOW,
        fetched_at=NOW,
    )
    snapshot = result.to_snapshot()

    assert provider.requests[0].trade_date == "20260518"
    assert result.status is PartitionSnapshotStatus.COMPLETE
    result.require_publishable()
    assert [item.row_ordinal for item in snapshot.rows] == [0, 1]
    assert snapshot.rows[0].payload_hash == snapshot.rows[1].payload_hash
    decoded = decode_canonical_payload(snapshot.rows[0].lossless_payload)
    assert decoded["provider_only"] == {"desk_rank": 0}
    assert decoded["nullable"] is None
    assert decoded["empty_text"] == ""
    assert decoded["zero_value"] == 0


def test_partition_hash_does_not_depend_on_dataframe_or_fetch_order() -> None:
    first = {"ts_code": "000001.SZ", "price": 10}
    second = {"ts_code": "000002.SZ", "price": 11}
    left = BlockTradeSnapshotPilot(
        FakeProvider(BlockTradeNativeResponse((first, second), _evidence(2, ("ts_code", "price"))))
    ).capture(
        batch_id="left",
        trade_date="20260518",
        requested_as_of=NOW,
        fetched_at=NOW,
    ).to_snapshot()
    right = BlockTradeSnapshotPilot(
        FakeProvider(BlockTradeNativeResponse((second, first), _evidence(2, ("ts_code", "price"))))
    ).capture(
        batch_id="right",
        trade_date="20260518",
        requested_as_of=NOW,
        fetched_at=NOW,
    ).to_snapshot()

    assert left.request_partition.partition_hash == right.request_partition.partition_hash
    assert left.content_hash == right.content_hash
    assert decode_canonical_payload(left.rows[0].lossless_payload)["ts_code"] == "000001.SZ"
    assert decode_canonical_payload(right.rows[0].lossless_payload)["ts_code"] == "000002.SZ"


def test_dataframe_client_captures_provider_mapping_before_legacy_projection() -> None:
    native = {
        "ts_code": "000001.SZ",
        "trade_date": "20260518",
        "price": None,
        "vol": Decimal("0.00"),
        "amount": "",
        "buyer": None,
        "seller": "",
        "provider_only": "retained",
        "nullable": None,
        "empty_text": "",
        "zero_value": 0,
    }

    class API:
        def block_trade(self, **params):
            assert params == {"trade_date": "20260518"}
            return pd.DataFrame([native])

    factory_calls = []

    def evidence_factory(request, row_count, fields):
        factory_calls.append((request.trade_date, row_count, fields))
        return _evidence(row_count, fields)

    client = DataFrameBlockTradeProviderClient(API(), completeness_factory=evidence_factory)
    result = BlockTradeSnapshotPilot(client).capture(
        batch_id="dataframe-batch",
        trade_date="20260518",
        requested_as_of=NOW,
        fetched_at=NOW,
    )
    decoded = decode_canonical_payload(result.to_snapshot().rows[0].lossless_payload)

    assert decoded["price"] is None
    assert decoded["vol"] == Decimal("0")
    assert decoded["amount"] == ""
    assert decoded["buyer"] is None
    assert decoded["provider_only"] == "retained"
    assert factory_calls == [("20260518", 1, FIELDS)]


def test_missing_completeness_evidence_is_not_inferred_by_pilot() -> None:
    provider = FakeProvider(
        BlockTradeNativeResponse(rows=({"ts_code": "000001.SZ"},), completeness=None)
    )
    result = BlockTradeSnapshotPilot(provider).capture(
        batch_id="missing-evidence",
        trade_date="20260518",
        requested_as_of=NOW,
        fetched_at=NOW,
    )

    with pytest.raises(SnapshotPublishBlockedError, match="missing"):
        result.require_publishable()


def test_provider_error_becomes_stable_failure_result() -> None:
    class FailingProvider:
        def fetch_block_trade(self, request):
            del request
            raise BlockTradeProviderRequestError("provider_timeout", "request timed out")

    result = BlockTradeSnapshotPilot(FailingProvider()).capture(
        batch_id="failed-batch",
        trade_date="20260518",
        requested_as_of=NOW,
        fetched_at=NOW,
    )

    assert result.status is PartitionSnapshotStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "provider_timeout"
    with pytest.raises(SnapshotPublishBlockedError, match="FAILURE"):
        result.require_publishable()


def test_active_projection_matches_current_block_trade_view_and_keeps_duplicates() -> None:
    rows = (
        {
            "ts_code": "000001.SZ",
            "trade_date": "20260518",
            "price": "10.5",
            "vol": "20",
            "amount": "30000",
            "buyer": "buyer-a",
            "seller": "seller-a",
        },
        {
            "ts_code": "000001.SZ",
            "trade_date": "20260518",
            "price": "10.5",
            "vol": "20",
            "amount": "30000",
            "buyer": "buyer-a",
            "seller": "seller-a",
        },
        {"ts_code": "000002.SZ", "trade_date": "20260518", "price": None},
    )
    fields = ("ts_code", "trade_date", "price", "vol", "amount", "buyer", "seller")
    snapshot = BlockTradeSnapshotPilot(
        FakeProvider(BlockTradeNativeResponse(rows, _evidence(3, fields)))
    ).capture(
        batch_id="projection-batch",
        trade_date="20260518",
        requested_as_of=NOW,
        fetched_at=NOW,
    ).to_snapshot()

    class PointerOnlyRepository:
        def __init__(self, active):
            self.active = active
            self.calls = []

        def load_active(self, **kwargs):
            self.calls.append(kwargs)
            return self.active

    repository = PointerOnlyRepository(snapshot)
    projection = ActiveBlockTradeProjection(repository)  # type: ignore[arg-type]
    view = projection.load(codes=("000001",), trade_date="2026-05-18")

    assert view == {
        "000001": [
            {"price": 10.5, "vol": 20.0, "amount_yi": 3.0, "buyer": "buyer-a", "seller": "seller-a"},
            {"price": 10.5, "vol": 20.0, "amount_yi": 3.0, "buyer": "buyer-a", "seller": "seller-a"},
        ]
    }
    assert repository.calls == [
        {
            "dataset": "block_trade",
            "provider": "tushare_pro",
            "endpoint": "block_trade",
            "request_partition": block_trade_request_partition("20260518"),
        }
    ]


def test_projection_has_no_legacy_or_source_fallback_when_pointer_is_missing() -> None:
    class MissingPointerRepository:
        def __init__(self):
            self.load_count = 0

        def load_active(self, **kwargs):
            del kwargs
            self.load_count += 1
            return None

    repository = MissingPointerRepository()
    projection = ActiveBlockTradeProjection(repository)  # type: ignore[arg-type]

    assert projection.load(codes=("000001",), trade_date="20260518") == {}
    assert repository.load_count == 1


def test_dataframe_none_response_is_failure_not_successful_empty() -> None:
    class API:
        def block_trade(self, **params):
            del params
            return None

    client = DataFrameBlockTradeProviderClient(
        API(),
        completeness_factory=lambda request, row_count, fields: _evidence(row_count, fields),
    )
    result = BlockTradeSnapshotPilot(client).capture(
        batch_id="none-response",
        trade_date="20260518",
        requested_as_of=NOW,
        fetched_at=NOW,
    )

    assert result.status is PartitionSnapshotStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "block_trade_provider_empty_response"
    with pytest.raises(SnapshotPublishBlockedError, match="FAILURE"):
        result.require_publishable()


def test_provider_exception_is_failure_not_successful_empty() -> None:
    class FailingProvider:
        def fetch_block_trade(self, request):
            del request
            raise RuntimeError("provider exploded")

    result = BlockTradeSnapshotPilot(FailingProvider()).capture(
        batch_id="provider-exception",
        trade_date="20260518",
        requested_as_of=NOW,
        fetched_at=NOW,
    )

    assert result.status is PartitionSnapshotStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "block_trade_provider_contract_error"


def test_empty_dataframe_without_evidence_is_failure() -> None:
    class API:
        def block_trade(self, **params):
            del params
            return pd.DataFrame(columns=FIELDS)

    client = DataFrameBlockTradeProviderClient(
        API(),
        completeness_factory=lambda request, row_count, fields: None,
    )
    result = BlockTradeSnapshotPilot(client).capture(
        batch_id="empty-without-evidence",
        trade_date="20260518",
        requested_as_of=NOW,
        fetched_at=NOW,
    )

    assert result.status is PartitionSnapshotStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "provider_completeness_evidence_missing"


def test_explicit_successful_empty_requires_and_accepts_complete_evidence() -> None:
    class API:
        def block_trade(self, **params):
            del params
            return pd.DataFrame(columns=FIELDS)

    client = DataFrameBlockTradeProviderClient(
        API(),
        completeness_factory=lambda request, row_count, fields: _evidence(row_count, fields),
    )
    result = BlockTradeSnapshotPilot(client).capture(
        batch_id="explicit-empty",
        trade_date="20260518",
        requested_as_of=NOW,
        fetched_at=NOW,
    )

    assert result.status is PartitionSnapshotStatus.SUCCESSFUL_EMPTY
    result.require_publishable()


@pytest.mark.parametrize(
    ("evidence", "message"),
    (
        (_evidence(2, ("ts_code", "price")), "row count"),
        (
            ResponseCompletenessEvidence(
                pagination=PaginationEvidence(page_count=1, all_pages_fetched=True, terminal_cursor="next"),
                limit=LimitEvidence(returned_rows=1, truncation_ruled_out=True, provider_reported_total=1),
                field_set=FieldSetEvidence(
                    response_fields=("ts_code", "price"),
                    all_returned_fields_captured=True,
                    requested_all_fields=True,
                ),
            ),
            "pagination",
        ),
        (
            ResponseCompletenessEvidence(
                pagination=PaginationEvidence(page_count=1, all_pages_fetched=True),
                limit=LimitEvidence(returned_rows=1, truncation_ruled_out=False, provider_reported_total=1),
                field_set=FieldSetEvidence(
                    response_fields=("ts_code", "price"),
                    all_returned_fields_captured=True,
                    requested_all_fields=True,
                ),
            ),
            "truncation",
        ),
        (
            ResponseCompletenessEvidence(
                pagination=PaginationEvidence(page_count=1, all_pages_fetched=True),
                limit=LimitEvidence(returned_rows=1, truncation_ruled_out=True, provider_reported_total=2),
                field_set=FieldSetEvidence(
                    response_fields=("ts_code", "price"),
                    all_returned_fields_captured=True,
                    requested_all_fields=True,
                ),
            ),
            "reported total",
        ),
        (_evidence(1, ("ts_code",)), "field evidence"),
        (_evidence(1, ("ts_code", "price", "provider_only")), "field evidence"),
    ),
)
def test_incomplete_provider_evidence_blocks_non_empty_publish(
    evidence: ResponseCompletenessEvidence,
    message: str,
) -> None:
    row = {"ts_code": "000001.SZ", "price": 10}
    result = BlockTradeSnapshotPilot(
        FakeProvider(BlockTradeNativeResponse(rows=(row,), completeness=evidence))
    ).capture(
        batch_id=f"incomplete-{message}",
        trade_date="20260518",
        requested_as_of=NOW,
        fetched_at=NOW,
    )

    with pytest.raises(SnapshotPublishBlockedError, match=message):
        result.require_publishable()


def test_each_returned_row_must_match_the_declared_field_set() -> None:
    rows = (
        {"ts_code": "000001.SZ", "price": 10},
        {"ts_code": "000002.SZ"},
    )
    result = BlockTradeSnapshotPilot(
        FakeProvider(
            BlockTradeNativeResponse(
                rows=rows,
                completeness=_evidence(2, ("ts_code", "price")),
            )
        )
    ).capture(
        batch_id="row-field-mismatch",
        trade_date="20260518",
        requested_as_of=NOW,
        fetched_at=NOW,
    )

    with pytest.raises(SnapshotPublishBlockedError, match="every returned row"):
        result.require_publishable()


RAW_SCHEMA_FINGERPRINT = "c241bafd13099ab748ed5bc215e14de41430f2142c19da585486619162af4e2b"


def _documented_raw_item(index: int = 1) -> list[object]:
    return [
        f"{index:06d}.SZ",
        "20260709",
        10.5,
        20.0,
        30000.0,
        "buyer-a",
        "seller-a",
    ]


def _raw_envelope(*, items: list[list[object]], fields: tuple[str, ...] | None = None):
    from app.services.auxiliary.block_trade_snapshot import BLOCK_TRADE_DOCUMENTED_FIELDS
    from app.services.tushare_client import TushareRawEnvelope

    schema = fields or BLOCK_TRADE_DOCUMENTED_FIELDS
    return TushareRawEnvelope.from_envelope(
        api_name="block_trade",
        requested_fields="",
        envelope={
            "code": 0,
            "msg": "",
            "detail": "",
            "request_id": "fixture-request",
            "data": {
                "fields": list(schema),
                "items": items,
                "count": len(items),
                "has_more": False,
            },
        },
    )


class FakeRawProvider:
    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object], str]] = []

    def fetch_raw(self, api_name, *, params=None, fields=""):
        self.calls.append((api_name, dict(params or {}), fields))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def test_raw_envelope_maps_exact_provider_fields_without_dataframe_normalization() -> None:
    raw = _raw_envelope(items=[_documented_raw_item()])

    assert raw.envelope_keys == ("code", "msg", "detail", "request_id", "data")
    assert raw.data_keys == ("fields", "items", "count", "has_more")
    assert raw.fields == (
        "ts_code",
        "trade_date",
        "price",
        "vol",
        "amount",
        "buyer",
        "seller",
    )
    assert raw.field_fingerprint == RAW_SCHEMA_FINGERPRINT
    assert raw.items == (
        {
            "ts_code": "000001.SZ",
            "trade_date": "20260709",
            "price": 10.5,
            "vol": 20.0,
            "amount": 30000.0,
            "buyer": "buyer-a",
            "seller": "seller-a",
        },
    )


def test_raw_provider_requests_all_fields_and_retains_every_schema_field() -> None:
    from app.services.auxiliary.block_trade_snapshot import (
        BlockTradeProviderRequest,
        TushareBlockTradeProviderClient,
    )

    raw = _raw_envelope(items=[_documented_raw_item()])
    provider = FakeRawProvider(raw)
    native = TushareBlockTradeProviderClient(provider).fetch_block_trade(
        BlockTradeProviderRequest("20260709")
    )

    assert provider.calls == [("block_trade", {"trade_date": "20260709"}, "")]
    assert native.completeness is not None
    assert native.completeness.field_set.response_fields == raw.fields
    assert native.completeness.field_set.all_returned_fields_captured is True
    assert native.completeness.field_set.requested_all_fields is True
    assert tuple(native.rows[0]) == raw.fields


@pytest.mark.parametrize(
    ("row_count", "publishable"),
    ((999, True), (1000, False), (1001, False)),
)
def test_documented_single_page_cap_only_publishes_strictly_below_cap(
    row_count: int,
    publishable: bool,
) -> None:
    from app.services.auxiliary.block_trade_snapshot import TushareBlockTradeProviderClient

    raw = _raw_envelope(items=[_documented_raw_item()] * row_count)
    result = BlockTradeSnapshotPilot(TushareBlockTradeProviderClient(FakeRawProvider(raw))).capture(
        batch_id=f"cap-{row_count}",
        trade_date="20260709",
        requested_as_of=NOW,
        fetched_at=NOW,
    )

    assert result.status is PartitionSnapshotStatus.COMPLETE
    assert result.completeness is not None
    assert result.completeness.limit.returned_rows == row_count
    assert result.completeness.limit.truncation_ruled_out is publishable
    if publishable:
        result.require_publishable()
    else:
        with pytest.raises(SnapshotPublishBlockedError, match="truncation"):
            result.require_publishable()


def test_raw_zero_response_is_proven_successful_empty() -> None:
    from app.services.auxiliary.block_trade_snapshot import TushareBlockTradeProviderClient

    result = BlockTradeSnapshotPilot(
        TushareBlockTradeProviderClient(FakeRawProvider(_raw_envelope(items=[])))
    ).capture(
        batch_id="raw-empty",
        trade_date="20260709",
        requested_as_of=NOW,
        fetched_at=NOW,
    )

    assert result.status is PartitionSnapshotStatus.SUCCESSFUL_EMPTY
    result.require_publishable()


def test_raw_duplicate_rows_preserve_multiplicity_and_complete_schema() -> None:
    from app.services.auxiliary.block_trade_snapshot import TushareBlockTradeProviderClient

    raw = _raw_envelope(items=[_documented_raw_item(), _documented_raw_item()])
    result = BlockTradeSnapshotPilot(TushareBlockTradeProviderClient(FakeRawProvider(raw))).capture(
        batch_id="raw-duplicates",
        trade_date="20260709",
        requested_as_of=NOW,
        fetched_at=NOW,
    )
    snapshot = result.to_snapshot()

    assert result.status is PartitionSnapshotStatus.COMPLETE
    assert snapshot.row_count == 2
    assert snapshot.rows[0].payload_hash == snapshot.rows[1].payload_hash
    assert result.completeness is not None
    assert result.completeness.field_set.response_fields == raw.fields
    assert set(decode_canonical_payload(snapshot.rows[0].lossless_payload)) == set(raw.fields)


def test_unknown_raw_schema_is_a_failure_not_a_partial_publish() -> None:
    from app.services.auxiliary.block_trade_snapshot import (
        BLOCK_TRADE_DOCUMENTED_FIELDS,
        TushareBlockTradeProviderClient,
    )

    fields = (*BLOCK_TRADE_DOCUMENTED_FIELDS, "unverified_field")
    raw = _raw_envelope(items=[[*_documented_raw_item(), "unexpected"]], fields=fields)
    result = BlockTradeSnapshotPilot(TushareBlockTradeProviderClient(FakeRawProvider(raw))).capture(
        batch_id="unknown-schema",
        trade_date="20260709",
        requested_as_of=NOW,
        fetched_at=NOW,
    )

    assert result.status is PartitionSnapshotStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "block_trade_provider_unknown_schema"


def test_malformed_raw_envelope_and_raw_provider_error_are_failures() -> None:
    from app.services.auxiliary.block_trade_snapshot import TushareBlockTradeProviderClient
    from app.services.tushare_client import TushareRawEnvelope, TushareRawEnvelopeError

    with pytest.raises(TushareRawEnvelopeError, match="item does not match"):
        TushareRawEnvelope.from_envelope(
            api_name="block_trade",
            requested_fields="",
            envelope={"code": 0, "data": {"fields": ["ts_code"], "items": [[]]}},
        )

    result = BlockTradeSnapshotPilot(
        TushareBlockTradeProviderClient(
            FakeRawProvider(TushareRawEnvelopeError("provider_error", "fixture failure"))
        )
    ).capture(
        batch_id="raw-provider-error",
        trade_date="20260709",
        requested_as_of=NOW,
        fetched_at=NOW,
    )

    assert result.status is PartitionSnapshotStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "block_trade_provider_request_failed"


def test_injected_refresh_service_persists_all_outcomes_and_only_publishes_proven_complete() -> None:
    from sqlalchemy import create_engine, func, select

    from app.services.auxiliary.block_trade_snapshot import (
        build_block_trade_snapshot_refresh_service,
    )
    from app.services.auxiliary.snapshot_repository import (
        active_partition_snapshots,
        snapshot_batches,
        snapshot_metadata,
    )
    from app.services.tushare_client import TushareRawEnvelopeError

    engine = create_engine("sqlite:///:memory:")
    snapshot_metadata.create_all(engine)
    with engine.begin() as connection:
        failure = build_block_trade_snapshot_refresh_service(
            connection,
            raw_provider=FakeRawProvider(TushareRawEnvelopeError("provider_error", "fixture failure")),
            clock=lambda: NOW,
        ).refresh(
            batch_id="persisted-failure",
            trade_date="20260709",
            requested_as_of=NOW,
            fetched_at=NOW,
        )
        cap_bound = build_block_trade_snapshot_refresh_service(
            connection,
            raw_provider=FakeRawProvider(_raw_envelope(items=[_documented_raw_item()] * 1000)),
            clock=lambda: NOW,
        ).refresh(
            batch_id="persisted-cap-bound",
            trade_date="20260709",
            requested_as_of=NOW,
            fetched_at=NOW,
        )
        complete = build_block_trade_snapshot_refresh_service(
            connection,
            raw_provider=FakeRawProvider(_raw_envelope(items=[_documented_raw_item()])),
            clock=lambda: NOW,
        ).refresh(
            batch_id="published-complete",
            trade_date="20260709",
            requested_as_of=NOW,
            fetched_at=NOW,
        )

        assert failure.provider_result.status is PartitionSnapshotStatus.FAILURE
        assert failure.persisted.created is True
        assert failure.was_published is False
        assert failure.publish_block_reason is not None
        assert cap_bound.provider_result.status is PartitionSnapshotStatus.COMPLETE
        assert cap_bound.persisted.created is True
        assert cap_bound.was_published is False
        assert cap_bound.publish_block_reason is not None
        assert complete.provider_result.status is PartitionSnapshotStatus.COMPLETE
        assert complete.persisted.created is True
        assert complete.was_published is True
        assert connection.scalar(select(func.count()).select_from(snapshot_batches)) == 3
        assert connection.execute(select(active_partition_snapshots.c.active_batch_id)).scalar_one() == "published-complete"
