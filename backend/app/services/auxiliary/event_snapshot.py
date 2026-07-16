"""Reusable immutable snapshot path for single-page Tushare event feeds.

This module is deliberately provider-, transaction-, and ORM-global free.  A caller
injects a raw provider and a transaction-bound snapshot repository.  Each response is
captured as lossless mappings before any legacy projection.  Responses without a
provable completeness contract are persisted for audit but cannot move an active
pointer.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from app.services.auxiliary.contracts import (
    AuxiliaryAuditEvent,
    AuxiliaryAuditOutcome,
    AuxiliaryDatasetRequest,
    AuxiliaryDatasetResult,
    AuxiliaryError,
    AuxiliaryErrorCode,
    AuxiliaryPersistResult,
    AuxiliaryStage,
    CoveragePolicy,
    RefreshDecision,
    RefreshReason,
)
from app.services.auxiliary.provider_results import (
    FieldSetEvidence,
    LimitEvidence,
    PaginationEvidence,
    ProviderPartitionResult,
    ResponseCompletenessEvidence,
    SnapshotPublishBlockedError,
)
from app.services.auxiliary.snapshot_contracts import (
    CanonicalRequestPartition,
    ImmutablePartitionSnapshot,
    PartitionSnapshotStatus,
)
from app.services.auxiliary.snapshot_repository import (
    ImmutablePartitionSnapshotRepositoryPort,
    SnapshotAuditOnlyAccessError,
    SnapshotPersistResult,
    SnapshotPublishResult,
)
from app.services.tushare_client import TushareRawEnvelope, TushareRawEnvelopeError


ViewT = TypeVar("ViewT")
tushare_provider = "tushare_pro"


class EventSnapshotError(ValueError):
    """A typed event-snapshot request or provider result is invalid."""


class EventSnapshotProviderError(RuntimeError):
    """A safe, stable provider failure persisted in snapshot metadata."""

    def __init__(self, code: str, message: str) -> None:
        if not code.strip() or not message.strip():
            raise EventSnapshotError("provider error code and message must not be blank")
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class EventSnapshotRequest:
    """A canonical, provider-visible partition request with no credentials."""

    parameters: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.parameters:
            raise EventSnapshotError("event snapshot request must include a partition parameter")
        keys = tuple(key for key, _value in self.parameters)
        if len(keys) != len(set(keys)) or any(not key.strip() for key in keys):
            raise EventSnapshotError("event snapshot request parameter names must be unique and non-blank")
        if any(not value.strip() for _key, value in self.parameters):
            raise EventSnapshotError("event snapshot request parameter values must be non-blank")
        if self.parameters != tuple(sorted(self.parameters)):
            raise EventSnapshotError("event snapshot request parameters must use canonical key order")

    @classmethod
    def from_mapping(cls, parameters: Mapping[str, object]) -> "EventSnapshotRequest":
        return cls(tuple(sorted((str(key), str(value)) for key, value in parameters.items())))

    @property
    def parameter_map(self) -> Mapping[str, str]:
        return dict(self.parameters)


@dataclass(frozen=True, slots=True)
class TushareSinglePageEventContract:
    """Documented raw schema and single-response completeness gate for one endpoint.

    ``documented_single_request_cap=None`` is intentional for a feed whose official
    contract has not yet proved an upper bound or termination mechanism.  It remains
    auditable and raw-preserving, but its evidence always blocks publication.
    """

    dataset: str
    endpoint: str
    documented_fields: tuple[str, ...]
    documented_single_request_cap: int | None
    documentation_url: str | None
    provider: str = tushare_provider

    def __post_init__(self) -> None:
        if not self.dataset.strip() or not self.endpoint.strip() or not self.provider.strip():
            raise EventSnapshotError("event contract dataset, endpoint and provider must be non-blank")
        if not self.documented_fields or len(self.documented_fields) != len(set(self.documented_fields)):
            raise EventSnapshotError("event contract must declare a non-empty unique raw field schema")
        if any(not field.strip() for field in self.documented_fields):
            raise EventSnapshotError("event contract fields must be non-blank")
        if self.documented_single_request_cap is not None and self.documented_single_request_cap <= 0:
            raise EventSnapshotError("documented single-request cap must be positive when supplied")
        if self.documentation_url is not None and not self.documentation_url.startswith("https://"):
            raise EventSnapshotError("event contract documentation URL must use HTTPS when supplied")
        if self.documentation_url is None and self.documented_single_request_cap is not None:
            raise EventSnapshotError(
                "a publishable single-page cap requires an official documentation URL"
            )

    @property
    def completeness_is_provable(self) -> bool:
        return self.documented_single_request_cap is not None and self.documentation_url is not None

    def request_partition(self, request: EventSnapshotRequest) -> CanonicalRequestPartition:
        return CanonicalRequestPartition.from_value(
            {
                "provider": self.provider,
                "endpoint": self.endpoint,
                "params": dict(request.parameters),
            }
        )

    def completeness_for(self, envelope: TushareRawEnvelope) -> ResponseCompletenessEvidence | None:
        if envelope.api_name != self.endpoint:
            raise EventSnapshotProviderError(
                "event_provider_invalid_response",
                "raw envelope endpoint does not match its event contract",
            )
        if envelope.requested_fields != "":
            raise EventSnapshotProviderError(
                "event_provider_partial_field_request",
                "event snapshot fetches must request the provider default all-field response",
            )
        returned_rows = len(envelope.items)
        # Tushare may omit data.fields for a valid empty envelope. For an empty
        # response, the declared schema is audit evidence; non-empty rows must still
        # prove an exact all-field raw schema before their payloads are persisted.
        if returned_rows:
            if envelope.fields != self.documented_fields:
                raise EventSnapshotProviderError(
                    "event_provider_unknown_schema",
                    "raw response fields do not match the verified event schema",
                )
        elif envelope.fields not in ((), self.documented_fields):
            raise EventSnapshotProviderError(
                "event_provider_unknown_schema",
                "empty raw response returned an unverified field schema",
            )

        # An audit-only source deliberately has no inferred cap, pagination, total,
        # count, cursor, or has-more semantics. It retains the validated all-field
        # envelope as immutable evidence but supplies no publication evidence.
        if not self.completeness_is_provable:
            return None

        cap = self.documented_single_request_cap
        if cap is None:  # pragma: no cover - implied by completeness_is_provable
            raise EventSnapshotError("publishable event contract requires a request cap")
        return ResponseCompletenessEvidence(
            pagination=PaginationEvidence(page_count=1, all_pages_fetched=True),
            limit=LimitEvidence(
                returned_rows=returned_rows,
                truncation_ruled_out=returned_rows < cap,
            ),
            field_set=FieldSetEvidence(
                response_fields=self.documented_fields,
                all_returned_fields_captured=True,
                requested_all_fields=True,
            ),
        )


@dataclass(frozen=True, slots=True)
class EventSnapshotNativeResponse:
    """Full provider mappings and explicit completeness evidence before projection."""

    rows: tuple[Mapping[str, Any], ...]
    completeness: ResponseCompletenessEvidence | None

    def __post_init__(self) -> None:
        if any(not isinstance(row, Mapping) for row in self.rows):
            raise EventSnapshotError("event provider rows must be mappings")


@runtime_checkable
class EventSnapshotProviderPort(Protocol):
    def fetch_event(self, request: EventSnapshotRequest) -> EventSnapshotNativeResponse: ...


@runtime_checkable
class TushareRawProviderPort(Protocol):
    def fetch_raw(
        self,
        api_name: str,
        *,
        params: Mapping[str, Any] | None = None,
        fields: str = "",
    ) -> TushareRawEnvelope: ...


class TushareSinglePageEventProviderClient:
    """Raw adapter shared by documented non-paginated Tushare event feeds."""

    def __init__(
        self,
        raw_provider: TushareRawProviderPort,
        contract: TushareSinglePageEventContract,
    ) -> None:
        self._raw_provider = raw_provider
        self._contract = contract

    def fetch_event(self, request: EventSnapshotRequest) -> EventSnapshotNativeResponse:
        try:
            envelope = self._raw_provider.fetch_raw(
                self._contract.endpoint,
                params=request.parameter_map,
                fields="",
            )
        except TushareRawEnvelopeError as exc:
            raise EventSnapshotProviderError(
                "event_provider_request_failed", "event provider request failed"
            ) from exc
        except Exception as exc:
            raise EventSnapshotProviderError(
                "event_provider_request_failed", "event provider request failed"
            ) from exc
        if not isinstance(envelope, TushareRawEnvelope):
            raise EventSnapshotProviderError(
                "event_provider_invalid_response", "event provider returned an invalid raw response type"
            )
        try:
            completeness = self._contract.completeness_for(envelope)
        except EventSnapshotProviderError:
            raise
        except Exception as exc:
            raise EventSnapshotProviderError(
                "event_provider_invalid_response", "event provider raw response could not be interpreted"
            ) from exc
        return EventSnapshotNativeResponse(rows=envelope.items, completeness=completeness)


class EventSnapshotPilot:
    """Capture a typed provider result without projecting, deduplicating, or mutating rows."""

    def __init__(
        self,
        contract: TushareSinglePageEventContract,
        provider_client: EventSnapshotProviderPort,
    ) -> None:
        self._contract = contract
        self._provider_client = provider_client

    @property
    def contract(self) -> TushareSinglePageEventContract:
        return self._contract

    def capture(
        self,
        *,
        batch_id: str,
        request: EventSnapshotRequest,
        requested_as_of: datetime,
        fetched_at: datetime,
    ) -> ProviderPartitionResult:
        partition = self._contract.request_partition(request)
        try:
            response = self._provider_client.fetch_event(request)
        except EventSnapshotProviderError as exc:
            return self._failure(batch_id, partition, requested_as_of, fetched_at, exc.code, exc.message)
        except Exception:
            return self._failure(
                batch_id,
                partition,
                requested_as_of,
                fetched_at,
                "event_provider_contract_error",
                "event provider returned an invalid response",
            )
        if not isinstance(response, EventSnapshotNativeResponse):
            return self._failure(
                batch_id,
                partition,
                requested_as_of,
                fetched_at,
                "event_provider_invalid_response",
                "event provider returned an invalid response type",
            )
        try:
            return ProviderPartitionResult.from_rows(
                batch_id=batch_id,
                dataset=self._contract.dataset,
                provider=self._contract.provider,
                endpoint=self._contract.endpoint,
                request_partition=partition,
                requested_as_of=requested_as_of,
                fetched_at=fetched_at,
                rows=response.rows,
                completeness=response.completeness,
                allow_empty_without_completeness=not self._contract.completeness_is_provable,
            )
        except Exception:
            return self._failure(
                batch_id,
                partition,
                requested_as_of,
                fetched_at,
                "event_provider_invalid_response",
                "event provider response violated the snapshot contract",
            )

    def _failure(
        self,
        batch_id: str,
        partition: CanonicalRequestPartition,
        requested_as_of: datetime,
        fetched_at: datetime,
        code: str,
        message: str,
    ) -> ProviderPartitionResult:
        return ProviderPartitionResult.failed(
            batch_id=batch_id,
            dataset=self._contract.dataset,
            provider=self._contract.provider,
            endpoint=self._contract.endpoint,
            request_partition=partition,
            requested_as_of=requested_as_of,
            fetched_at=fetched_at,
            code=code,
            message=message,
        )


@dataclass(frozen=True, slots=True)
class EventSnapshotAuditCaptureResult:
    """Immutable audit evidence deliberately detached from active-pointer publication."""

    provider_result: ProviderPartitionResult
    persisted: SnapshotPersistResult

    @property
    def active_pointer_published(self) -> bool:
        """Audit capture has no publication operation and therefore always returns false."""
        return False


class EventSnapshotAuditCaptureService:
    """Capture and persist raw event evidence without reading or publishing pointers.

    The service uses the same strict raw-envelope client as publishable feeds. Its
    intentionally narrow contract has no active reader and never invokes repository
    ``publish``; transaction commit or rollback remains entirely caller-owned.
    """

    def __init__(
        self,
        pilot: EventSnapshotPilot,
        repository: ImmutablePartitionSnapshotRepositoryPort,
    ) -> None:
        self._pilot = pilot
        self._repository = repository

    def capture(
        self,
        *,
        batch_id: str,
        request: EventSnapshotRequest,
        requested_as_of: datetime,
        fetched_at: datetime,
    ) -> EventSnapshotAuditCaptureResult:
        provider_result = self._pilot.capture(
            batch_id=batch_id,
            request=request,
            requested_as_of=requested_as_of,
            fetched_at=fetched_at,
        )
        return EventSnapshotAuditCaptureResult(
            provider_result=provider_result,
            persisted=self._repository.persist(provider_result.to_snapshot()),
        )


@dataclass(frozen=True, slots=True)
class EventSnapshotRefreshResult:
    """Persisted event outcome; nonpublishable snapshots never change the pointer."""

    provider_result: ProviderPartitionResult
    persisted: SnapshotPersistResult
    published: SnapshotPublishResult | None
    publish_block_reason: str | None

    @property
    def was_published(self) -> bool:
        return self.published is not None


class EventSnapshotRefreshService:
    """Persist every outcome in a caller-owned transaction and publish only complete ones."""

    def __init__(
        self,
        pilot: EventSnapshotPilot,
        repository: ImmutablePartitionSnapshotRepositoryPort,
    ) -> None:
        self._pilot = pilot
        self._repository = repository

    def refresh(
        self,
        *,
        batch_id: str,
        request: EventSnapshotRequest,
        requested_as_of: datetime,
        fetched_at: datetime,
    ) -> EventSnapshotRefreshResult:
        provider_result = self._pilot.capture(
            batch_id=batch_id,
            request=request,
            requested_as_of=requested_as_of,
            fetched_at=fetched_at,
        )
        snapshot = provider_result.to_snapshot()
        persisted = self._repository.persist(snapshot)
        try:
            provider_result.require_publishable()
        except SnapshotPublishBlockedError as exc:
            return EventSnapshotRefreshResult(
                provider_result=provider_result,
                persisted=persisted,
                published=None,
                publish_block_reason=str(exc),
            )
        published = self._repository.publish(snapshot, provider_result.completeness)
        return EventSnapshotRefreshResult(
            provider_result=provider_result,
            persisted=persisted,
            published=published,
            publish_block_reason=None,
        )


class ActiveEventSnapshotReader:
    """Pointer-only reader; immutable historical batches are never implicit fallbacks."""

    def __init__(
        self,
        contract: TushareSinglePageEventContract,
        repository: ImmutablePartitionSnapshotRepositoryPort,
    ) -> None:
        if contract.dataset == "institutional_survey":
            raise SnapshotAuditOnlyAccessError(
                "audit-only dataset 'institutional_survey' cannot construct an active-pointer reader"
            )
        self._contract = contract
        self._repository = repository

    def load(self, request: EventSnapshotRequest) -> ImmutablePartitionSnapshot | None:
        return self._repository.load_active(
            dataset=self._contract.dataset,
            provider=self._contract.provider,
            endpoint=self._contract.endpoint,
            request_partition=self._contract.request_partition(request),
        )


PartitionRequests = Callable[[AuxiliaryDatasetRequest], tuple[EventSnapshotRequest, ...]]
BuildView = Callable[[tuple[ImmutablePartitionSnapshot, ...], AuxiliaryDatasetRequest], ViewT]
BatchIdFactory = Callable[[str, EventSnapshotRequest], str]
Clock = Callable[[], datetime]


class EventSnapshotAuxiliaryHandler(Generic[ViewT]):
    """Typed custom Registry handler for multi-partition immutable event snapshots."""

    def __init__(
        self,
        *,
        contract: TushareSinglePageEventContract,
        refresh_service: EventSnapshotRefreshService,
        reader: ActiveEventSnapshotReader,
        partition_requests: PartitionRequests,
        build_view: BuildView[ViewT],
        batch_id_factory: BatchIdFactory,
        clock: Clock,
        view_key: str,
    ) -> None:
        self._contract = contract
        self._refresh_service = refresh_service
        self._reader = reader
        self._partition_requests = partition_requests
        self._build_view = build_view
        self._batch_id_factory = batch_id_factory
        self._clock = clock
        self._view_key = view_key

    def prepare(self, request: AuxiliaryDatasetRequest) -> AuxiliaryDatasetResult[ViewT]:
        partitions = self._partition_requests(request)
        if not partitions:
            return self._result(
                request,
                snapshots=(),
                audit=(self._audit(AuxiliaryStage.FETCH, AuxiliaryAuditOutcome.SKIPPED, {"partitions": 0}),),
                errors=(),
                persistence=None,
                refreshed=False,
                covered=False,
            )

        audit: list[AuxiliaryAuditEvent] = []
        errors: list[AuxiliaryError] = []
        persisted_rows = 0
        persisted_batches = 0
        if request.refresh_enabled:
            for partition in partitions:
                now = self._clock()
                outcome = self._refresh_service.refresh(
                    batch_id=self._batch_id_factory(self._contract.dataset, partition),
                    request=partition,
                    requested_as_of=request.requested_at,
                    fetched_at=now,
                )
                persisted_rows += outcome.persisted.row_count
                persisted_batches += 1
                audit.append(
                    self._audit(
                        AuxiliaryStage.PERSIST,
                        AuxiliaryAuditOutcome.SUCCEEDED,
                        {"batch_id": outcome.persisted.batch_id, "rows": outcome.persisted.row_count},
                    )
                )
                if not outcome.was_published:
                    code = (
                        outcome.provider_result.failure.code
                        if outcome.provider_result.status is PartitionSnapshotStatus.FAILURE
                        and outcome.provider_result.failure is not None
                        else AuxiliaryErrorCode.SNAPSHOT_PUBLISH_BLOCKED.value
                    )
                    errors.append(
                        AuxiliaryError(
                            AuxiliaryErrorCode.SNAPSHOT_PUBLISH_BLOCKED,
                            AuxiliaryStage.COVERAGE,
                            f"event snapshot pointer was not published ({code})",
                            retriable=outcome.provider_result.status is PartitionSnapshotStatus.FAILURE,
                        )
                    )
                    audit.append(
                        self._audit(
                            AuxiliaryStage.COVERAGE,
                            AuxiliaryAuditOutcome.FAILED,
                            {"reason": "pointer_not_published"},
                        )
                    )
                else:
                    audit.append(
                        self._audit(AuxiliaryStage.COVERAGE, AuxiliaryAuditOutcome.SUCCEEDED, {"published": True})
                    )
        else:
            audit.append(self._audit(AuxiliaryStage.REFRESH, AuxiliaryAuditOutcome.SKIPPED, {"reason": "refresh_disabled"}))

        snapshots: list[ImmutablePartitionSnapshot] = []
        for partition in partitions:
            active = self._reader.load(partition)
            if active is None:
                errors.append(
                    AuxiliaryError(
                        AuxiliaryErrorCode.SNAPSHOT_POINTER_MISSING,
                        AuxiliaryStage.LOAD,
                        "active event snapshot pointer is absent; legacy fallback is disabled",
                    )
                )
                continue
            snapshots.append(active)
        audit.append(
            self._audit(
                AuxiliaryStage.LOAD,
                AuxiliaryAuditOutcome.SUCCEEDED if len(snapshots) == len(partitions) else AuxiliaryAuditOutcome.FAILED,
                {"active_partitions": len(snapshots), "requested_partitions": len(partitions)},
            )
        )
        persistence = (
            AuxiliaryPersistResult(
                attempted_rows=persisted_rows,
                affected_rows=None,
                failed_rows=0,
                execute_count=persisted_batches,
                duration_ms=0.0,
            )
            if request.refresh_enabled
            else None
        )
        return self._result(
            request,
            snapshots=tuple(snapshots),
            audit=tuple(audit),
            errors=tuple(errors),
            persistence=persistence,
            refreshed=request.refresh_enabled,
            covered=len(snapshots) == len(partitions),
        )

    def _result(
        self,
        request: AuxiliaryDatasetRequest,
        *,
        snapshots: tuple[ImmutablePartitionSnapshot, ...],
        audit: tuple[AuxiliaryAuditEvent, ...],
        errors: tuple[AuxiliaryError, ...],
        persistence: AuxiliaryPersistResult | None,
        refreshed: bool,
        covered: bool,
    ) -> AuxiliaryDatasetResult[ViewT]:
        coverage = CoveragePolicy[Mapping[str, Any]]().evaluate_snapshot(
            record_count=sum(snapshot.row_count for snapshot in snapshots),
            covered_instruments=(request.requested_codes if covered else ()),
            requested_instruments=request.instruments,
            source="active_event_snapshot",
        )
        return AuxiliaryDatasetResult(
            dataset_key=request.dataset_key,
            view_key=self._view_key,
            view=self._build_view(snapshots, request),
            coverage=coverage,
            refresh=RefreshDecision(
                refreshed,
                RefreshReason.POLICY_ALWAYS if refreshed else RefreshReason.REFRESH_DISABLED,
                None,
            ),
            audit=audit,
            errors=errors,
            persistence=persistence,
        )

    def _audit(
        self,
        stage: AuxiliaryStage,
        outcome: AuxiliaryAuditOutcome,
        details: Mapping[str, object],
    ) -> AuxiliaryAuditEvent:
        return AuxiliaryAuditEvent.create(stage, outcome, self._clock(), details)


__all__ = [
    "ActiveEventSnapshotReader",
    "BatchIdFactory",
    "BuildView",
    "EventSnapshotAuditCaptureResult",
    "EventSnapshotAuditCaptureService",
    "EventSnapshotAuxiliaryHandler",
    "EventSnapshotError",
    "EventSnapshotNativeResponse",
    "EventSnapshotPilot",
    "EventSnapshotProviderError",
    "EventSnapshotProviderPort",
    "EventSnapshotRefreshResult",
    "EventSnapshotRefreshService",
    "EventSnapshotRequest",
    "PartitionRequests",
    "TushareRawProviderPort",
    "TushareSinglePageEventContract",
    "TushareSinglePageEventProviderClient",
    "tushare_provider",
]
