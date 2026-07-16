"""Typed contracts for the Auxiliary Dataset lifecycle.

This module deliberately contains no ORM models, database sessions, vendor clients, or
production dataset declarations.  Dataset-specific adapters are expected to implement
the protocols below and to be assembled into a frozen registry at application bootstrap.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable


RawT = TypeVar("RawT")
NormalizedT = TypeVar("NormalizedT")
ViewT = TypeVar("ViewT")


class AuxiliaryContractError(ValueError):
    """Raised when a descriptor or lifecycle value violates the public contract."""


class AuxiliaryErrorCode(str, Enum):
    """Stable error codes shared by standard and custom dataset handlers."""

    DATASET_NOT_REGISTERED = "dataset_not_registered"
    REGISTRY_NOT_FROZEN = "registry_not_frozen"
    INVALID_DESCRIPTOR = "invalid_descriptor"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    REPOSITORY_INSPECTION_FAILED = "repository_inspection_failed"
    FETCH_FAILED = "fetch_failed"
    NORMALIZATION_FAILED = "normalization_failed"
    PERSIST_FAILED = "persist_failed"
    LOAD_FAILED = "load_failed"
    COVERAGE_INSUFFICIENT = "coverage_insufficient"
    CUSTOM_HANDLER_FAILED = "custom_handler_failed"
    CUSTOM_HANDLER_RESULT_INVALID = "custom_handler_result_invalid"
    SNAPSHOT_PUBLISH_BLOCKED = "snapshot_publish_blocked"
    SNAPSHOT_POINTER_MISSING = "snapshot_pointer_missing"


class AuxiliaryStage(str, Enum):
    """Observable lifecycle boundaries; custom handlers use the same stage names."""

    REGISTRY = "registry"
    DEPENDENCY = "dependency"
    INSPECT = "inspect"
    REFRESH = "refresh"
    FETCH = "fetch"
    NORMALIZE = "normalize"
    PERSIST = "persist"
    LOAD = "load"
    COVERAGE = "coverage"
    CUSTOM = "custom"


class AuxiliaryAuditOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"


class RefreshMode(str, Enum):
    NEVER = "never"
    ALWAYS = "always"
    IF_STALE = "if_stale"


class RefreshReason(str, Enum):
    REFRESH_DISABLED = "refresh_disabled"
    POLICY_NEVER = "policy_never"
    POLICY_ALWAYS = "policy_always"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"
    FRESHNESS_UNKNOWN = "freshness_unknown"
    STALE = "stale"
    FRESH = "fresh"


@dataclass(frozen=True, slots=True)
class AuxiliaryInstrument:
    """A minimal, immutable instrument reference independent of market-pool models."""

    code: str
    exchange: str | None = None

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise AuxiliaryContractError("instrument code 不能为空")
        if self.exchange is not None and not self.exchange.strip():
            raise AuxiliaryContractError("instrument exchange 不能是空白字符串")


@dataclass(frozen=True, slots=True)
class AuxiliaryDatasetRequest:
    """The complete typed input passed to every dataset lifecycle handler."""

    dataset_key: str
    instruments: tuple[AuxiliaryInstrument, ...]
    trade_date: date
    refresh_enabled: bool
    requested_at: datetime

    def __post_init__(self) -> None:
        if not self.dataset_key.strip():
            raise AuxiliaryContractError("dataset_key 不能为空")
        if self.requested_at.tzinfo is None:
            raise AuxiliaryContractError("requested_at 必须带时区")
        codes = [instrument.code for instrument in self.instruments]
        if len(codes) != len(set(codes)):
            raise AuxiliaryContractError("instruments 不能包含重复 code")

    @property
    def requested_codes(self) -> tuple[str, ...]:
        return tuple(instrument.code for instrument in self.instruments)


@dataclass(frozen=True, slots=True)
class AuxiliaryRepositorySnapshot:
    """Repository metadata used for refresh and cached-coverage decisions.

    ``covered_instruments`` is intentionally a set of public instrument codes rather
    than ORM rows so an orchestrator never needs a model-specific branch.
    """

    record_count: int
    covered_instruments: frozenset[str] = frozenset()
    freshness: date | datetime | None = None

    def __post_init__(self) -> None:
        if self.record_count < 0:
            raise AuxiliaryContractError("record_count 不能为负数")
        if any(not code.strip() for code in self.covered_instruments):
            raise AuxiliaryContractError("covered_instruments 不能包含空 code")


@dataclass(frozen=True, slots=True)
class CoverageResult:
    """Coverage evidence for one dataset invocation, independent of its view type."""

    record_count: int
    requested_instruments: tuple[str, ...]
    covered_instruments: tuple[str, ...]
    missing_instruments: tuple[str, ...]
    instrument_coverage_ratio: float
    sufficient: bool
    source: str

    def __post_init__(self) -> None:
        if self.record_count < 0:
            raise AuxiliaryContractError("coverage record_count 不能为负数")
        if not 0.0 <= self.instrument_coverage_ratio <= 1.0:
            raise AuxiliaryContractError("instrument_coverage_ratio 必须在 [0, 1] 范围内")
        if not self.source.strip():
            raise AuxiliaryContractError("coverage source 不能为空")


InstrumentExtractor = Callable[[NormalizedT], str | None]
FreshnessExtractor = Callable[[NormalizedT], date | datetime | None]


@dataclass(frozen=True, slots=True)
class CoveragePolicy(Generic[NormalizedT]):
    """Declarative minimum coverage for both cached and freshly normalized records."""

    minimum_record_count: int = 0
    minimum_instrument_coverage: float = 0.0
    require_all_instruments: bool = False
    instrument_extractor: InstrumentExtractor[NormalizedT] | None = None

    def __post_init__(self) -> None:
        if self.minimum_record_count < 0:
            raise AuxiliaryContractError("minimum_record_count 不能为负数")
        if not 0.0 <= self.minimum_instrument_coverage <= 1.0:
            raise AuxiliaryContractError("minimum_instrument_coverage 必须在 [0, 1] 范围内")
        if (
            self.require_all_instruments or self.minimum_instrument_coverage > 0.0
        ) and self.instrument_extractor is None:
            raise AuxiliaryContractError("按股票覆盖率校验必须声明 instrument_extractor")

    def evaluate_records(
        self,
        records: Sequence[NormalizedT],
        requested_instruments: Sequence[AuxiliaryInstrument],
        *,
        source: str,
    ) -> CoverageResult:
        covered: set[str] = set()
        if self.instrument_extractor is not None:
            for record in records:
                code = self.instrument_extractor(record)
                if code is not None and code.strip():
                    covered.add(code)
        return self.evaluate_snapshot(
            record_count=len(records),
            covered_instruments=covered,
            requested_instruments=requested_instruments,
            source=source,
        )

    def evaluate_snapshot(
        self,
        *,
        record_count: int,
        covered_instruments: Sequence[str] | frozenset[str],
        requested_instruments: Sequence[AuxiliaryInstrument],
        source: str,
    ) -> CoverageResult:
        requested = tuple(instrument.code for instrument in requested_instruments)
        requested_set = set(requested)
        covered_set = {code for code in covered_instruments if code in requested_set}
        covered = tuple(code for code in requested if code in covered_set)
        missing = tuple(code for code in requested if code not in covered_set)
        ratio = 1.0 if not requested else len(covered) / len(requested)
        coverage_ok = (
            (not self.require_all_instruments or not missing)
            and ratio >= self.minimum_instrument_coverage
        )
        return CoverageResult(
            record_count=record_count,
            requested_instruments=requested,
            covered_instruments=covered,
            missing_instruments=missing,
            instrument_coverage_ratio=ratio,
            sufficient=record_count >= self.minimum_record_count and coverage_ok,
            source=source,
        )


@dataclass(frozen=True, slots=True)
class RefreshPolicy:
    """A deterministic refresh policy evaluated against the target trading date."""

    mode: RefreshMode = RefreshMode.IF_STALE
    max_staleness: timedelta = timedelta(0)
    refresh_when_coverage_insufficient: bool = True

    def __post_init__(self) -> None:
        if self.max_staleness < timedelta(0):
            raise AuxiliaryContractError("max_staleness 不能为负数")

    def decide(
        self,
        request: AuxiliaryDatasetRequest,
        snapshot: AuxiliaryRepositorySnapshot,
        coverage: CoverageResult,
    ) -> "RefreshDecision":
        if not request.refresh_enabled:
            return RefreshDecision(False, RefreshReason.REFRESH_DISABLED, snapshot.freshness)
        if self.mode is RefreshMode.NEVER:
            return RefreshDecision(False, RefreshReason.POLICY_NEVER, snapshot.freshness)
        if self.mode is RefreshMode.ALWAYS:
            return RefreshDecision(True, RefreshReason.POLICY_ALWAYS, snapshot.freshness)
        if not coverage.sufficient and self.refresh_when_coverage_insufficient:
            return RefreshDecision(True, RefreshReason.INSUFFICIENT_COVERAGE, snapshot.freshness)
        if snapshot.freshness is None:
            return RefreshDecision(True, RefreshReason.FRESHNESS_UNKNOWN, None)
        if _is_stale(snapshot.freshness, request.trade_date, self.max_staleness):
            return RefreshDecision(True, RefreshReason.STALE, snapshot.freshness)
        return RefreshDecision(False, RefreshReason.FRESH, snapshot.freshness)


@dataclass(frozen=True, slots=True)
class RefreshDecision:
    required: bool
    reason: RefreshReason
    observed_freshness: date | datetime | None


@dataclass(frozen=True, slots=True)
class AuxiliaryPersistResult:
    """Persistence metrics without falsely classifying MySQL affected-row counts."""

    attempted_rows: int
    affected_rows: int | None
    failed_rows: int
    execute_count: int
    duration_ms: float
    inserted_rows: int | None = None
    updated_rows: int | None = None
    batch_size: int | None = None
    batch_statement_bound: int | None = None
    fixed_statement_count: int = 0

    def __post_init__(self) -> None:
        if any(
            value < 0
            for value in (
                self.attempted_rows,
                self.failed_rows,
                self.execute_count,
                self.fixed_statement_count,
            )
        ):
            raise AuxiliaryContractError("persistence 计数不能为负数")
        if self.affected_rows is not None and self.affected_rows < 0:
            raise AuxiliaryContractError("affected_rows 不能为负数")
        if self.duration_ms < 0:
            raise AuxiliaryContractError("duration_ms 不能为负数")
        if self.inserted_rows is not None or self.updated_rows is not None:
            raise AuxiliaryContractError(
                "普通 MySQL upsert 不得把 rowcount 推断为精确 inserted/updated 行数"
            )
        if (self.batch_size is None) != (self.batch_statement_bound is None):
            raise AuxiliaryContractError("batch_size 与 batch_statement_bound 必须同时声明或同时省略")
        if self.batch_size is not None and self.batch_size <= 0:
            raise AuxiliaryContractError("batch_size 必须大于零")
        if self.batch_statement_bound is not None:
            if self.batch_statement_bound < 0:
                raise AuxiliaryContractError("batch_statement_bound 不能为负数")
            if self.execute_count > self.batch_statement_bound:
                raise AuxiliaryContractError("execute_count 不得超过 batch_statement_bound")


@dataclass(frozen=True, slots=True)
class AuxiliaryError:
    """Stable, persistence-safe failure projected by both standard and custom flows."""

    code: AuxiliaryErrorCode
    stage: AuxiliaryStage
    message: str
    retriable: bool = False
    exception_type: str | None = None

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise AuxiliaryContractError("auxiliary error message 不能为空")


@dataclass(frozen=True, slots=True)
class AuxiliaryAuditEvent:
    """An immutable audit event with tuple data to avoid mutable dict leakage."""

    stage: AuxiliaryStage
    outcome: AuxiliaryAuditOutcome
    at: datetime
    details: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise AuxiliaryContractError("audit 时间必须带时区")
        if any(not key.strip() for key, _value in self.details):
            raise AuxiliaryContractError("audit detail key 不能为空")

    @classmethod
    def create(
        cls,
        stage: AuxiliaryStage,
        outcome: AuxiliaryAuditOutcome,
        at: datetime,
        details: Mapping[str, object] | None = None,
    ) -> "AuxiliaryAuditEvent":
        return cls(
            stage=stage,
            outcome=outcome,
            at=at,
            details=tuple(sorted((str(key), str(value)) for key, value in (details or {}).items())),
        )

    @property
    def detail_map(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self.details))


@dataclass(frozen=True, slots=True)
class AuxiliaryLoadedView(Generic[ViewT]):
    """A loader output with a declared key checked by registry and orchestrator."""

    key: str
    value: ViewT

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise AuxiliaryContractError("loader output key 不能为空")


@dataclass(frozen=True, slots=True)
class AuxiliaryDatasetResult(Generic[ViewT]):
    """Typed result for one dataset; no dataset-specific dictionaries escape upward."""

    dataset_key: str
    view_key: str
    view: ViewT | None
    coverage: CoverageResult
    refresh: RefreshDecision
    audit: tuple[AuxiliaryAuditEvent, ...]
    errors: tuple[AuxiliaryError, ...]
    persistence: AuxiliaryPersistResult | None = None

    def __post_init__(self) -> None:
        if not self.dataset_key.strip() or not self.view_key.strip():
            raise AuxiliaryContractError("dataset_key 与 view_key 均不能为空")

    @property
    def is_usable(self) -> bool:
        return self.view is not None and self.coverage.sufficient and not self.errors


@dataclass(frozen=True, slots=True)
class AuxiliaryOrchestratorResult:
    """The Coordinator-facing immutable aggregate for all requested datasets."""

    results: tuple[AuxiliaryDatasetResult[Any], ...]
    errors: tuple[AuxiliaryError, ...] = ()

    def __post_init__(self) -> None:
        keys = tuple(result.dataset_key for result in self.results)
        if len(keys) != len(set(keys)):
            raise AuxiliaryContractError("orchestrator result 不能包含重复 dataset_key")

    @property
    def by_dataset(self) -> Mapping[str, AuxiliaryDatasetResult[Any]]:
        return MappingProxyType({result.dataset_key: result for result in self.results})

    @property
    def is_usable(self) -> bool:
        return not self.errors and all(result.is_usable for result in self.results)

    def result_for(self, dataset_key: str) -> AuxiliaryDatasetResult[Any]:
        try:
            return self.by_dataset[dataset_key]
        except KeyError:
            raise KeyError(f"orchestrator result 不含数据集 '{dataset_key}'") from None


@runtime_checkable
class AuxiliaryFetcher(Protocol[RawT]):
    def fetch(self, request: AuxiliaryDatasetRequest) -> Sequence[RawT]:
        """Fetch source records for a typed dataset request."""


@runtime_checkable
class AuxiliaryNormalizer(Protocol[RawT, NormalizedT]):
    def normalize(
        self,
        raw: RawT,
        request: AuxiliaryDatasetRequest,
    ) -> NormalizedT | None:
        """Normalize one raw record; ``None`` explicitly discards a record."""


@runtime_checkable
class AuxiliaryRepository(Protocol[NormalizedT]):
    def inspect(self, request: AuxiliaryDatasetRequest) -> AuxiliaryRepositorySnapshot:
        """Read only metadata needed for deterministic refresh and coverage decisions."""

    def upsert(
        self,
        records: Sequence[NormalizedT],
        request: AuxiliaryDatasetRequest,
    ) -> AuxiliaryPersistResult:
        """Atomically persist a normalized batch according to its declared policy."""


@runtime_checkable
class AuxiliaryLoader(Protocol[ViewT]):
    def load(self, request: AuxiliaryDatasetRequest) -> AuxiliaryLoadedView[ViewT]:
        """Load the dataset-specific view after a standard lifecycle completes."""


@runtime_checkable
class AuxiliaryDatasetHandler(Protocol[ViewT]):
    """An explicit opt-in escape hatch which still returns the shared result model."""

    def prepare(self, request: AuxiliaryDatasetRequest) -> AuxiliaryDatasetResult[ViewT]:
        """Implement special fetch/load behaviour without leaking custom audit/errors."""


@dataclass(frozen=True, slots=True)
class AuxiliaryDatasetDescriptor(Generic[RawT, NormalizedT, ViewT]):
    """Immutable declaration of a dataset's full auxiliary-data lifecycle."""

    key: str
    view_key: str
    natural_key: tuple[str, ...]
    coverage_policy: CoveragePolicy[NormalizedT]
    refresh_policy: RefreshPolicy
    fetcher: AuxiliaryFetcher[RawT] | None = None
    normalizer: AuxiliaryNormalizer[RawT, NormalizedT] | None = None
    repository: AuxiliaryRepository[NormalizedT] | None = None
    loader: AuxiliaryLoader[ViewT] | None = None
    freshness_field: str | None = None
    freshness_extractor: FreshnessExtractor[NormalizedT] | None = None
    dependencies: tuple[str, ...] = ()
    custom_handler: AuxiliaryDatasetHandler[ViewT] | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate this declaration before it is published by a registry.

        Construction performs the first check.  Registry bootstrap invokes this
        method again so only a complete descriptor can enter a frozen snapshot.
        """
        if not self.key.strip():
            raise AuxiliaryContractError("descriptor key 不能为空")
        if not self.view_key.strip():
            raise AuxiliaryContractError("descriptor view_key 不能为空")
        if not self.natural_key or any(not item.strip() for item in self.natural_key):
            raise AuxiliaryContractError("descriptor natural_key 必须包含非空字段")
        if len(self.natural_key) != len(set(self.natural_key)):
            raise AuxiliaryContractError("descriptor natural_key 不能重复")
        if self.key in self.dependencies or len(self.dependencies) != len(set(self.dependencies)):
            raise AuxiliaryContractError("descriptor dependencies 不能自引用或重复")
        if any(not dependency.strip() for dependency in self.dependencies):
            raise AuxiliaryContractError("descriptor dependency 不能为空")
        has_freshness_field = self.freshness_field is not None
        has_freshness_extractor = self.freshness_extractor is not None
        if has_freshness_field != has_freshness_extractor:
            raise AuxiliaryContractError(
                "freshness_field 与 freshness_extractor 必须同时声明或同时省略"
            )
        if self.freshness_field is not None and not self.freshness_field.strip():
            raise AuxiliaryContractError("freshness_field 不能是空白字符串")
        if self.custom_handler is not None:
            if not callable(getattr(self.custom_handler, "prepare", None)):
                raise AuxiliaryContractError("custom_handler 必须实现 prepare(request)")
            if any(item is not None for item in (self.fetcher, self.normalizer, self.repository, self.loader)):
                raise AuxiliaryContractError("custom_handler 与标准 fetch/normalize/repository/load 只能二选一")
            return
        if any(item is None for item in (self.fetcher, self.normalizer, self.repository, self.loader)):
            raise AuxiliaryContractError("标准 descriptor 必须完整声明 fetcher、normalizer、repository、loader")
        handler_checks = (
            ("fetcher", self.fetcher, "fetch"),
            ("normalizer", self.normalizer, "normalize"),
            ("repository", self.repository, "inspect"),
            ("repository", self.repository, "upsert"),
            ("loader", self.loader, "load"),
        )
        invalid_handler = next(
            (
                f"{name}.{method}"
                for name, handler, method in handler_checks
                if not callable(getattr(handler, method, None))
            ),
            None,
        )
        if invalid_handler is not None:
            raise AuxiliaryContractError(f"标准 descriptor handler 必须实现 {invalid_handler}")
        if self.refresh_policy.mode is RefreshMode.IF_STALE and not has_freshness_field:
            raise AuxiliaryContractError("IF_STALE refresh policy 必须声明 freshness_field/extractor")

    @property
    def is_custom(self) -> bool:
        return self.custom_handler is not None


def _is_stale(value: date | datetime, trade_date: date, max_staleness: timedelta) -> bool:
    """Compare freshness at the trading-day boundary, not an arbitrary clock time.

    A daily dataset stamped with the requested ``trade_date`` is fresh even when its
    database representation has midnight as its time component.  Auxiliary freshness
    is intentionally a trading-day contract; an intraday dataset must expose a
    day-normalized watermark before it participates in this lifecycle.
    """
    observed_date = value.date() if isinstance(value, datetime) else value
    observed = datetime.combine(observed_date, time.min, tzinfo=timezone.utc)
    target = datetime.combine(trade_date, time.min, tzinfo=timezone.utc)
    return target - observed > max_staleness


__all__ = [
    "AuxiliaryAuditEvent",
    "AuxiliaryAuditOutcome",
    "AuxiliaryContractError",
    "AuxiliaryDatasetDescriptor",
    "AuxiliaryDatasetHandler",
    "AuxiliaryDatasetRequest",
    "AuxiliaryDatasetResult",
    "AuxiliaryError",
    "AuxiliaryErrorCode",
    "AuxiliaryFetcher",
    "AuxiliaryInstrument",
    "AuxiliaryLoadedView",
    "AuxiliaryLoader",
    "AuxiliaryNormalizer",
    "AuxiliaryOrchestratorResult",
    "AuxiliaryPersistResult",
    "AuxiliaryRepository",
    "AuxiliaryRepositorySnapshot",
    "AuxiliaryStage",
    "CoveragePolicy",
    "CoverageResult",
    "FreshnessExtractor",
    "RefreshDecision",
    "RefreshMode",
    "RefreshPolicy",
    "RefreshReason",
]
