"""block_trade immutable-snapshot pilot with an injected provider boundary.

The pilot captures each complete provider row mapping before the legacy ``_f``,
``_fmt_date`` and ``str`` projection.  It does not call or write the legacy
``block_trades`` table.  The read projection resolves only an active snapshot pointer.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import math
import re
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.engine import Connection

from app.adapters.market_source.dataframe_contracts import validate_market_frame
from app.services.tushare_client import (
    TushareRawEnvelope,
    TushareRawEnvelopeError,
)
from app.services.auxiliary.provider_results import (
    FieldSetEvidence,
    LimitEvidence,
    PaginationEvidence,
    ProviderPartitionResult,
    ResponseCompletenessEvidence,
    SnapshotPublishBlockedError,
)
from app.services.auxiliary.snapshot_codec import decode_canonical_payload
from app.services.auxiliary.snapshot_contracts import (
    CanonicalRequestPartition,
    ImmutablePartitionSnapshot,
)
from app.services.auxiliary.snapshot_repository import (
    ImmutablePartitionSnapshotRepositoryPort,
    SQLAlchemyImmutablePartitionSnapshotRepository,
    SnapshotPersistResult,
    SnapshotPublishResult,
)


BLOCK_TRADE_DATASET = "block_trade"
BLOCK_TRADE_PROVIDER = "tushare_pro"
BLOCK_TRADE_ENDPOINT = "block_trade"
BLOCK_TRADE_DOCUMENTED_SINGLE_REQUEST_CAP = 1000
BLOCK_TRADE_DOCUMENTED_FIELDS = (
    "ts_code",
    "trade_date",
    "price",
    "vol",
    "amount",
    "buyer",
    "seller",
)
_TRADE_DATE_PATTERN = re.compile(r"^\d{8}$")


class BlockTradePilotError(ValueError):
    """Invalid pilot request or provider response."""


class BlockTradeProviderRequestError(RuntimeError):
    """Stable provider failure safe to persist in snapshot metadata."""

    def __init__(self, code: str, message: str) -> None:
        if not code.strip() or not message.strip():
            raise BlockTradePilotError("provider error code and message must not be blank")
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class BlockTradeProviderRequest:
    trade_date: str

    def __post_init__(self) -> None:
        if not _TRADE_DATE_PATTERN.fullmatch(self.trade_date):
            raise BlockTradePilotError("block_trade trade_date must use YYYYMMDD")


@dataclass(frozen=True, slots=True)
class BlockTradeNativeResponse:
    """Provider-native mappings plus externally established completeness evidence."""

    rows: tuple[Mapping[str, Any], ...]
    completeness: ResponseCompletenessEvidence | None
    source_ids: tuple[str | None, ...] = ()

    def __post_init__(self) -> None:
        if any(not isinstance(row, Mapping) for row in self.rows):
            raise BlockTradePilotError("block_trade provider rows must be mappings")
        if self.completeness is not None and not isinstance(
            self.completeness, ResponseCompletenessEvidence
        ):
            raise BlockTradePilotError(
                "block_trade completeness must be ResponseCompletenessEvidence or None"
            )
        if self.source_ids and len(self.source_ids) != len(self.rows):
            raise BlockTradePilotError("block_trade source_ids length must match rows")


@runtime_checkable
class BlockTradeProviderPort(Protocol):
    def fetch_block_trade(self, request: BlockTradeProviderRequest) -> BlockTradeNativeResponse: ...


CompletenessEvidenceFactory = Callable[
    [BlockTradeProviderRequest, int, tuple[str, ...]],
    ResponseCompletenessEvidence | None,
]


@runtime_checkable
class TushareRawProviderPort(Protocol):
    """Narrow raw-envelope port used only by the block_trade snapshot pilot."""

    def fetch_raw(
        self,
        api_name: str,
        *,
        params: Mapping[str, Any] | None = None,
        fields: str = "",
    ) -> TushareRawEnvelope: ...


@dataclass(frozen=True, slots=True)
class BlockTradeSinglePageCompletenessFactory:
    """Completeness proof for the documented non-paginated block_trade contract."""

    documented_cap: int = BLOCK_TRADE_DOCUMENTED_SINGLE_REQUEST_CAP
    documented_fields: tuple[str, ...] = BLOCK_TRADE_DOCUMENTED_FIELDS

    def __post_init__(self) -> None:
        if self.documented_cap <= 0:
            raise BlockTradePilotError("block_trade documented cap must be positive")
        if not self.documented_fields or len(self.documented_fields) != len(set(self.documented_fields)):
            raise BlockTradePilotError("block_trade documented field set must be non-empty and unique")

    def build(
        self,
        request: BlockTradeProviderRequest,
        envelope: TushareRawEnvelope,
    ) -> ResponseCompletenessEvidence:
        if envelope.api_name != BLOCK_TRADE_ENDPOINT:
            raise BlockTradeProviderRequestError(
                "block_trade_provider_invalid_response",
                "block_trade raw envelope API does not match the requested endpoint",
            )
        if envelope.requested_fields != "":
            raise BlockTradeProviderRequestError(
                "block_trade_provider_partial_field_request",
                "block_trade must request the provider default all-field response",
            )
        if envelope.fields != self.documented_fields:
            raise BlockTradeProviderRequestError(
                "block_trade_provider_unknown_schema",
                "block_trade raw response fields do not match the verified provider schema",
            )
        returned_rows = len(envelope.items)
        # The published provider contract exposes no page, offset, cursor or total
        # request/response contract.  Its fixed single-request cap is therefore the
        # only verified termination proof: cap-boundary responses are never published.
        return ResponseCompletenessEvidence(
            pagination=PaginationEvidence(page_count=1, all_pages_fetched=True),
            limit=LimitEvidence(
                returned_rows=returned_rows,
                truncation_ruled_out=returned_rows < self.documented_cap,
            ),
            field_set=FieldSetEvidence(
                response_fields=envelope.fields,
                all_returned_fields_captured=True,
                requested_all_fields=True,
            ),
        )


class TushareBlockTradeProviderClient:
    """Convert the typed raw Tushare envelope into the snapshot provider boundary."""

    def __init__(
        self,
        raw_provider: TushareRawProviderPort,
        *,
        completeness_factory: BlockTradeSinglePageCompletenessFactory | None = None,
    ) -> None:
        self._raw_provider = raw_provider
        self._completeness_factory = completeness_factory or BlockTradeSinglePageCompletenessFactory()

    def fetch_block_trade(self, request: BlockTradeProviderRequest) -> BlockTradeNativeResponse:
        try:
            envelope = self._raw_provider.fetch_raw(
                BLOCK_TRADE_ENDPOINT,
                params={"trade_date": request.trade_date},
                fields="",
            )
        except TushareRawEnvelopeError as exc:
            raise BlockTradeProviderRequestError(
                "block_trade_provider_request_failed",
                "block_trade provider request failed",
            ) from exc
        except Exception as exc:
            raise BlockTradeProviderRequestError(
                "block_trade_provider_request_failed",
                "block_trade provider request failed",
            ) from exc
        if not isinstance(envelope, TushareRawEnvelope):
            raise BlockTradeProviderRequestError(
                "block_trade_provider_invalid_response",
                "block_trade provider returned an invalid raw response type",
            )
        try:
            evidence = self._completeness_factory.build(request, envelope)
        except BlockTradeProviderRequestError:
            raise
        except Exception as exc:
            raise BlockTradeProviderRequestError(
                "block_trade_provider_invalid_response",
                "block_trade provider raw response could not be interpreted",
            ) from exc
        return BlockTradeNativeResponse(rows=envelope.items, completeness=evidence)


class DataFrameBlockTradeProviderClient:
    """Thin injected SDK adapter that captures full rows before legacy projection.

    The adapter deliberately has no default completeness policy.  Pagination/limit and
    field-set evidence must be supplied by the composition root; absence remains
    absence and will block publication.
    """

    def __init__(
        self,
        provider_api: Any,
        *,
        completeness_factory: CompletenessEvidenceFactory,
    ) -> None:
        self._provider_api = provider_api
        self._completeness_factory = completeness_factory

    def fetch_block_trade(self, request: BlockTradeProviderRequest) -> BlockTradeNativeResponse:
        try:
            frame = self._provider_api.block_trade(trade_date=request.trade_date)
        except Exception as exc:
            raise BlockTradeProviderRequestError(
                "block_trade_provider_request_failed",
                "block_trade provider request failed",
            ) from exc
        if frame is None:
            raise BlockTradeProviderRequestError(
                "block_trade_provider_empty_response",
                "block_trade provider returned no response",
            )
        validate_market_frame("block_trade", frame, provider="tushare-auxiliary")
        try:
            fields = tuple(str(column) for column in frame.columns)
            # Full mappings are captured here, before any legacy scalar/date/string
            # normalization.  CanonicalPayload is the first value transformation.
            rows = tuple(frame.to_dict(orient="records"))
        except Exception as exc:
            raise BlockTradeProviderRequestError(
                "block_trade_provider_invalid_response",
                "block_trade provider response could not be converted to mappings",
            ) from exc
        try:
            evidence = self._completeness_factory(request, len(rows), fields)
        except Exception as exc:
            raise BlockTradeProviderRequestError(
                "block_trade_completeness_evidence_failed",
                "block_trade completeness evidence could not be constructed",
            ) from exc
        return BlockTradeNativeResponse(rows=rows, completeness=evidence)


def normalize_block_trade_trade_date(value: str) -> str:
    compact = value.strip().replace("-", "")
    if not _TRADE_DATE_PATTERN.fullmatch(compact):
        raise BlockTradePilotError("block_trade trade_date must use YYYYMMDD or YYYY-MM-DD")
    return compact


def block_trade_request_partition(trade_date: str) -> CanonicalRequestPartition:
    """Hash only provider + endpoint + requested date, never response row order."""
    compact = normalize_block_trade_trade_date(trade_date)
    return CanonicalRequestPartition.from_value(
        {
            "provider": BLOCK_TRADE_PROVIDER,
            "endpoint": BLOCK_TRADE_ENDPOINT,
            "trade_date": compact,
        }
    )


class BlockTradeSnapshotPilot:
    def __init__(self, provider_client: BlockTradeProviderPort) -> None:
        self._provider_client = provider_client

    def capture(
        self,
        *,
        batch_id: str,
        trade_date: str,
        requested_as_of: datetime,
        fetched_at: datetime,
    ) -> ProviderPartitionResult:
        compact = normalize_block_trade_trade_date(trade_date)
        partition = block_trade_request_partition(compact)
        try:
            response = self._provider_client.fetch_block_trade(
                BlockTradeProviderRequest(trade_date=compact)
            )
        except BlockTradeProviderRequestError as exc:
            return ProviderPartitionResult.failed(
                batch_id=batch_id,
                dataset=BLOCK_TRADE_DATASET,
                provider=BLOCK_TRADE_PROVIDER,
                endpoint=BLOCK_TRADE_ENDPOINT,
                request_partition=partition,
                requested_as_of=requested_as_of,
                fetched_at=fetched_at,
                code=exc.code,
                message=exc.message,
            )
        except Exception:
            return ProviderPartitionResult.failed(
                batch_id=batch_id,
                dataset=BLOCK_TRADE_DATASET,
                provider=BLOCK_TRADE_PROVIDER,
                endpoint=BLOCK_TRADE_ENDPOINT,
                request_partition=partition,
                requested_as_of=requested_as_of,
                fetched_at=fetched_at,
                code="block_trade_provider_contract_error",
                message="block_trade provider returned an invalid response",
            )
        if not isinstance(response, BlockTradeNativeResponse):
            return ProviderPartitionResult.failed(
                batch_id=batch_id,
                dataset=BLOCK_TRADE_DATASET,
                provider=BLOCK_TRADE_PROVIDER,
                endpoint=BLOCK_TRADE_ENDPOINT,
                request_partition=partition,
                requested_as_of=requested_as_of,
                fetched_at=fetched_at,
                code="block_trade_provider_invalid_response",
                message="block_trade provider returned an invalid response type",
            )
        source_ids: Sequence[str | None] | None = response.source_ids or None
        try:
            return ProviderPartitionResult.from_rows(
                batch_id=batch_id,
                dataset=BLOCK_TRADE_DATASET,
                provider=BLOCK_TRADE_PROVIDER,
                endpoint=BLOCK_TRADE_ENDPOINT,
                request_partition=partition,
                requested_as_of=requested_as_of,
                fetched_at=fetched_at,
                rows=response.rows,
                completeness=response.completeness,
                source_ids=source_ids,
            )
        except Exception:
            return ProviderPartitionResult.failed(
                batch_id=batch_id,
                dataset=BLOCK_TRADE_DATASET,
                provider=BLOCK_TRADE_PROVIDER,
                endpoint=BLOCK_TRADE_ENDPOINT,
                request_partition=partition,
                requested_as_of=requested_as_of,
                fetched_at=fetched_at,
                code="block_trade_provider_invalid_response",
                message="block_trade provider response violated the snapshot contract",
            )


@dataclass(frozen=True, slots=True)
class BlockTradeSnapshotRefreshResult:
    """Persisted pilot outcome; a blocked result never changes the active pointer."""

    provider_result: ProviderPartitionResult
    persisted: SnapshotPersistResult
    published: SnapshotPublishResult | None
    publish_block_reason: str | None

    @property
    def was_published(self) -> bool:
        return self.published is not None


class BlockTradeSnapshotRefreshService:
    """Explicitly injected pilot write boundary for V024 immutable snapshots.

    The caller owns the SQLAlchemy transaction.  Every provider outcome is persisted,
    while only responses with proven completeness can update the active pointer.
    """

    def __init__(
        self,
        pilot: BlockTradeSnapshotPilot,
        repository: ImmutablePartitionSnapshotRepositoryPort,
    ) -> None:
        self._pilot = pilot
        self._repository = repository

    def refresh(
        self,
        *,
        batch_id: str,
        trade_date: str,
        requested_as_of: datetime,
        fetched_at: datetime,
    ) -> BlockTradeSnapshotRefreshResult:
        provider_result = self._pilot.capture(
            batch_id=batch_id,
            trade_date=trade_date,
            requested_as_of=requested_as_of,
            fetched_at=fetched_at,
        )
        snapshot = provider_result.to_snapshot()
        persisted = self._repository.persist(snapshot)
        try:
            provider_result.require_publishable()
        except SnapshotPublishBlockedError as exc:
            return BlockTradeSnapshotRefreshResult(
                provider_result=provider_result,
                persisted=persisted,
                published=None,
                publish_block_reason=str(exc),
            )
        published = self._repository.publish(snapshot, provider_result.completeness)
        return BlockTradeSnapshotRefreshResult(
            provider_result=provider_result,
            persisted=persisted,
            published=published,
            publish_block_reason=None,
        )


def build_block_trade_snapshot_refresh_service(
    connection: Connection,
    *,
    raw_provider: TushareRawProviderPort,
    clock: Callable[[], datetime] | None = None,
) -> BlockTradeSnapshotRefreshService:
    """Compose the V024 repository with explicitly supplied provider and transaction."""
    return BlockTradeSnapshotRefreshService(
        BlockTradeSnapshotPilot(TushareBlockTradeProviderClient(raw_provider)),
        SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=clock),
    )


class ActiveBlockTradeProjection:
    """Project active snapshot rows to the current ``market_pool.block_trades`` view."""

    def __init__(self, repository: ImmutablePartitionSnapshotRepositoryPort) -> None:
        self._repository = repository

    def load(
        self,
        *,
        codes: Sequence[str],
        trade_date: str,
    ) -> dict[str, list[dict[str, Any]]]:
        if not codes:
            return {}
        partition = block_trade_request_partition(trade_date)
        snapshot = self._repository.load_active(
            dataset=BLOCK_TRADE_DATASET,
            provider=BLOCK_TRADE_PROVIDER,
            endpoint=BLOCK_TRADE_ENDPOINT,
            request_partition=partition,
        )
        if snapshot is None:
            return {}
        return self.project(snapshot, codes=codes)

    @staticmethod
    def project(
        snapshot: ImmutablePartitionSnapshot,
        *,
        codes: Sequence[str],
    ) -> dict[str, list[dict[str, Any]]]:
        if (
            snapshot.dataset != BLOCK_TRADE_DATASET
            or snapshot.provider != BLOCK_TRADE_PROVIDER
            or snapshot.endpoint != BLOCK_TRADE_ENDPOINT
        ):
            raise BlockTradePilotError("snapshot is not a block_trade pilot batch")
        requested_codes = set(codes)
        result: dict[str, list[dict[str, Any]]] = {}
        for row in snapshot.rows:
            native = decode_canonical_payload(row.lossless_payload)
            if not isinstance(native, Mapping):
                raise BlockTradePilotError("block_trade snapshot row is not a mapping")
            code = str(native.get("ts_code", "")).split(".")[0]
            if len(code) != 6 or code not in requested_codes:
                continue
            amount = _legacy_float(native.get("amount"))
            result.setdefault(code, []).append(
                {
                    "price": _legacy_float(native.get("price")),
                    "vol": _legacy_float(native.get("vol")),
                    "amount_yi": amount / 1e4,
                    "buyer": str(native.get("buyer", "")),
                    "seller": str(native.get("seller", "")),
                }
            )
        return result


def _legacy_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        converted = float(value)
        return default if math.isnan(converted) else converted
    except (TypeError, ValueError):
        return default


__all__ = [
    "ActiveBlockTradeProjection",
    "BLOCK_TRADE_DATASET",
    "BLOCK_TRADE_DOCUMENTED_FIELDS",
    "BLOCK_TRADE_DOCUMENTED_SINGLE_REQUEST_CAP",
    "BLOCK_TRADE_ENDPOINT",
    "BLOCK_TRADE_PROVIDER",
    "BlockTradeNativeResponse",
    "BlockTradePilotError",
    "BlockTradeProviderPort",
    "BlockTradeProviderRequest",
    "BlockTradeProviderRequestError",
    "BlockTradeSinglePageCompletenessFactory",
    "BlockTradeSnapshotPilot",
    "BlockTradeSnapshotRefreshResult",
    "BlockTradeSnapshotRefreshService",
    "DataFrameBlockTradeProviderClient",
    "TushareBlockTradeProviderClient",
    "TushareRawProviderPort",
    "block_trade_request_partition",
    "build_block_trade_snapshot_refresh_service",
    "normalize_block_trade_trade_date",
]
