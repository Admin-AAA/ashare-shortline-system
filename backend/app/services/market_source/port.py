"""Market source public contract.

Only the typed request/result surface in this module is part of
:class:`MarketSourcePort`.  Provider-specific configuration, retry behavior,
and legacy row conversion remain adapter concerns.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable


class FailurePolicy(str, Enum):
    """How a typed request handles a provider or per-instrument failure."""

    RETURN_PARTIAL = "return_partial"
    RAISE = "raise"


class BatchStatus(str, Enum):
    """Observable outcome of a market-source request."""

    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL = "partial"
    FAILURE = "failure"


class MarketSourceOperation(str, Enum):
    QUOTES = "quotes"
    DAILY_BARS = "daily_bars"


class MarketSourceErrorCode(str, Enum):
    SOURCE_UNAVAILABLE = "source_unavailable"
    INVALID_REQUEST = "invalid_request"
    INSTRUMENT_UNAVAILABLE = "instrument_unavailable"


@dataclass(frozen=True)
class QuoteRef:
    """Stable market instrument identity used by every typed request/result."""

    code: str
    exchange: str | None = None

    def __post_init__(self) -> None:
        code = str(self.code).strip()
        if not code:
            raise ValueError("QuoteRef.code must not be empty")
        exchange = str(self.exchange).strip().upper() if self.exchange else None
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "exchange", exchange or None)

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> "QuoteRef":
        return cls(code=str(row.get("code") or ""), exchange=row.get("exchange") if row.get("exchange") else None)


def infer_exchange(code: str) -> str:
    """Infer the exchange for legacy code-only callers without provider helpers."""

    normalized = str(code).strip()
    if normalized.startswith(("4", "8")):
        return "BJ"
    if normalized.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


@dataclass(frozen=True)
class QuoteRequest:
    """Request for a complete market quote snapshot.

    ``max_quote_count`` is an explicit result cap.  It deliberately does not
    expose source selection or settings, both of which are adapter internals.
    """

    max_quote_count: int | None = None
    failure_policy: FailurePolicy = FailurePolicy.RETURN_PARTIAL

    def __post_init__(self) -> None:
        if self.max_quote_count is not None and self.max_quote_count < 0:
            raise ValueError("QuoteRequest.max_quote_count must be non-negative")


@dataclass(frozen=True)
class DailyBarRequest:
    """Request daily bars for explicit instruments and an optional date range."""

    instruments: tuple[QuoteRef, ...]
    start_date: str | None = None
    end_date: str | None = None
    failure_policy: FailurePolicy = FailurePolicy.RETURN_PARTIAL

    def __post_init__(self) -> None:
        if len(set(self.instruments)) != len(self.instruments):
            raise ValueError("DailyBarRequest.instruments must not contain duplicates")
        for field_name, value in (("start_date", self.start_date), ("end_date", self.end_date)):
            if value is not None and not str(value).strip():
                raise ValueError(f"DailyBarRequest.{field_name} must not be blank")


@dataclass(frozen=True)
class MarketSourceError:
    """Stable, provider-safe error returned in a batch audit."""

    operation: MarketSourceOperation
    code: MarketSourceErrorCode
    message: str
    reference: QuoteRef | None = None
    retryable: bool = True


@dataclass(frozen=True)
class ItemAudit:
    """Outcome and optional error for one requested instrument."""

    reference: QuoteRef
    status: BatchStatus
    returned_count: int
    error: MarketSourceError | None = None


@dataclass(frozen=True)
class MarketSourceAudit:
    """Request-level evidence; it is deterministic and safe to persist."""

    source: str
    operation: MarketSourceOperation
    failure_policy: FailurePolicy
    status: BatchStatus
    requested_count: int
    returned_count: int
    empty_count: int
    failed_count: int


@dataclass(frozen=True)
class Quote:
    """A quote row paired with its typed identity while retaining vendor fields."""

    reference: QuoteRef
    values: Mapping[str, object]

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> "Quote":
        return cls(reference=QuoteRef.from_row(row), values=freeze_values(row))

    def as_legacy_row(self) -> dict[str, object]:
        return dict(self.values)


@dataclass(frozen=True)
class DailyBar:
    """A daily bar paired with the requested instrument identity."""

    reference: QuoteRef
    values: Mapping[str, object]

    @classmethod
    def from_row(cls, reference: QuoteRef, row: Mapping[str, object]) -> "DailyBar":
        return cls(reference=reference, values=freeze_values(row))

    def as_legacy_row(self) -> dict[str, object]:
        return dict(self.values)


def freeze_values(values: Mapping[str, object]) -> Mapping[str, object]:
    """Copy vendor data before exposing it from an immutable result DTO."""

    return MappingProxyType(dict(values))


def classify_status(returned_count: int, failed_count: int) -> BatchStatus:
    """Classify success, empty, partial, and fatal requests consistently."""

    if failed_count:
        return BatchStatus.PARTIAL if returned_count else BatchStatus.FAILURE
    return BatchStatus.SUCCESS if returned_count else BatchStatus.EMPTY


@dataclass(frozen=True)
class QuoteBatch:
    quotes: tuple[Quote, ...]
    errors: tuple[MarketSourceError, ...]
    audit: MarketSourceAudit

    def as_legacy_rows(self) -> list[dict[str, object]]:
        return [quote.as_legacy_row() for quote in self.quotes]


@dataclass(frozen=True)
class DailyBarBatch:
    bars_by_instrument: Mapping[QuoteRef, tuple[DailyBar, ...]]
    item_audits: Mapping[QuoteRef, ItemAudit]
    errors: tuple[MarketSourceError, ...]
    audit: MarketSourceAudit

    def as_legacy_rows_by_code(self) -> dict[str, list[dict[str, object]]]:
        return {
            reference.code: [bar.as_legacy_row() for bar in bars]
            for reference, bars in self.bars_by_instrument.items()
        }


class MarketSourceRequestFailed(RuntimeError):
    """Raised only when a request explicitly selects ``FailurePolicy.RAISE``."""

    def __init__(self, error: MarketSourceError) -> None:
        self.error = error
        super().__init__(error.message)


# Deprecated compatibility request types. They are not accepted by MarketSourcePort.
@dataclass(frozen=True)
class QuoteScope:
    max_quote_count: int | None = None


@dataclass(frozen=True)
class DateRange:
    start_date: str | None = None
    end_date: str | None = None


@runtime_checkable
class MarketSourcePort(Protocol):
    """The complete public capability required from a market-source adapter."""

    def fetch_quotes(self, request: QuoteRequest) -> QuoteBatch:
        """Fetch one typed quote batch."""
        ...

    def fetch_daily_bars(self, request: DailyBarRequest) -> DailyBarBatch:
        """Fetch typed daily-bar results with per-instrument audit/error evidence."""
        ...
