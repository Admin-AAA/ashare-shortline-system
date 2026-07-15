"""Pure, immutable contracts for reproducible single-strategy backtests.

This module deliberately contains no ORM, database session, service, or network
imports.  Application/adaptor layers may implement the reader protocols below,
but strategy evaluation and simulation only receive these value objects.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


class BacktestErrorCode(str, Enum):
    """Stable machine-readable errors exposed by the backtest domain boundary."""

    INVALID_REQUEST = "invalid_backtest_request"
    INVALID_DATE_SELECTOR = "invalid_date_selector"
    INVALID_EXECUTION_POLICY = "invalid_execution_policy"
    INVALID_DATA_REQUIREMENT = "invalid_data_requirement"
    INVALID_SNAPSHOT = "invalid_data_snapshot"
    INVALID_ARTIFACT = "invalid_artifact"
    FUTURE_DATA_ACCESS_DENIED = "future_data_access_denied"
    SNAPSHOT_ARTIFACT_UNAVAILABLE = "snapshot_artifact_unavailable"
    SNAPSHOT_INTEGRITY_ERROR = "snapshot_integrity_error"
    INSUFFICIENT_DATA = "insufficient_data"
    INCOMPLETE_EVALUATION = "incomplete_evaluation"
    INVALID_SIMULATION_INPUT = "invalid_simulation_input"


class BacktestDomainError(ValueError):
    """Base exception whose ``code`` is safe for API and persistence boundaries."""

    def __init__(
        self,
        code: BacktestErrorCode | str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code.value if isinstance(code, Enum) else str(code)
        self.details = _freeze_mapping(details or {})
        super().__init__(f"{self.code}: {message}")


class BacktestValidationError(BacktestDomainError):
    """A caller supplied an invalid immutable domain value."""


class FutureDataAccessDeniedError(BacktestDomainError):
    """A signal-side reader attempted to expose data after the signal date."""

    def __init__(self, signal_date: str, visible_at: str) -> None:
        super().__init__(
            BacktestErrorCode.FUTURE_DATA_ACCESS_DENIED,
            "signal input is not visible as of the requested signal date",
            details={"signal_date": signal_date, "visible_at": visible_at},
        )


class SnapshotArtifactUnavailableError(BacktestDomainError):
    """A requested immutable snapshot artifact does not exist."""

    def __init__(self, artifact_id: str) -> None:
        super().__init__(
            BacktestErrorCode.SNAPSHOT_ARTIFACT_UNAVAILABLE,
            "snapshot artifact is unavailable",
            details={"artifact_id": artifact_id},
        )


class SnapshotIntegrityError(BacktestDomainError):
    """An immutable artifact or snapshot manifest failed hash validation."""

    def __init__(self, artifact_id: str, *, expected_hash: str, actual_hash: str) -> None:
        super().__init__(
            BacktestErrorCode.SNAPSHOT_INTEGRITY_ERROR,
            "snapshot content hash does not match the recorded hash",
            details={
                "artifact_id": artifact_id,
                "expected_hash": expected_hash,
                "actual_hash": actual_hash,
            },
        )


def _freeze_value(value: Any) -> Any:
    """Recursively remove mutation paths from values retained by frozen DTOs."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze_value(item) for item in value), key=canonical_json))
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})


def _canonicalize(value: Any) -> Any:
    """Convert supported domain values into deterministic JSON-compatible primitives."""

    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _canonicalize(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON mapping keys must be strings")
            normalized[key] = _canonicalize(item)
        return normalized
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonicalize(item) for item in value), key=canonical_json)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TypeError("canonical JSON datetime values must be timezone-aware")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON does not support non-finite floats")
        return 0.0 if value == 0 else value
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return canonical UTF-8 JSON: recursive key sorting and no formatting variance."""

    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_fingerprint(value: Any) -> str:
    """SHA-256 fingerprint of :func:`canonical_json`."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


# Short alias for callers that use the domain term rather than the implementation term.
fingerprint = canonical_fingerprint


def content_hash(payload: bytes) -> str:
    """SHA-256 hash for immutable binary artifact content."""

    return hashlib.sha256(payload).hexdigest()


def _require_non_empty_string(value: object, field_name: str, code: BacktestErrorCode) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BacktestValidationError(code, f"{field_name} must be a non-empty string")
    return value.strip()


def _require_iso_date(value: object, field_name: str, code: BacktestErrorCode) -> str:
    normalized = _require_non_empty_string(value, field_name, code)
    try:
        parsed = date.fromisoformat(normalized)
    except ValueError as exc:
        raise BacktestValidationError(code, f"{field_name} must be an ISO date (YYYY-MM-DD)") from exc
    if parsed.isoformat() != normalized:
        raise BacktestValidationError(code, f"{field_name} must be an ISO date (YYYY-MM-DD)")
    return normalized


def _require_hash(value: object, field_name: str, code: BacktestErrorCode) -> str:
    normalized = _require_non_empty_string(value, field_name, code).lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise BacktestValidationError(code, f"{field_name} must be a lowercase SHA-256 hex digest")
    return normalized


def _require_non_negative_int(value: object, field_name: str, code: BacktestErrorCode) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BacktestValidationError(code, f"{field_name} must be a non-negative integer")
    return value


def _require_positive_int(value: object, field_name: str, code: BacktestErrorCode) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BacktestValidationError(code, f"{field_name} must be a positive integer")
    return value


def _require_finite_number(value: object, field_name: str, code: BacktestErrorCode) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise BacktestValidationError(code, f"{field_name} must be a finite number")
    return float(value)


class CanonicalValue:
    """Mixin for immutable domain values that can be persisted/fingerprinted safely."""

    def canonical_payload(self) -> dict[str, Any]:
        payload = _canonicalize(self)
        if not isinstance(payload, dict):  # Defensive: all users are dataclasses.
            raise TypeError("canonical domain payload must be an object")
        return payload

    def to_canonical_json(self) -> str:
        return canonical_json(self.canonical_payload())

    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


@dataclass(frozen=True)
class SingleDate(CanonicalValue):
    """A request selector that names exactly one signal date."""

    signal_date: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "signal_date",
            _require_iso_date(self.signal_date, "signal_date", BacktestErrorCode.INVALID_DATE_SELECTOR),
        )

    @property
    def start_date(self) -> str:
        return self.signal_date

    @property
    def end_date(self) -> str:
        return self.signal_date


@dataclass(frozen=True)
class DateRange(CanonicalValue):
    """An inclusive date range; the application resolves it through a trading calendar."""

    start_date: str
    end_date: str

    def __post_init__(self) -> None:
        start = _require_iso_date(self.start_date, "start_date", BacktestErrorCode.INVALID_DATE_SELECTOR)
        end = _require_iso_date(self.end_date, "end_date", BacktestErrorCode.INVALID_DATE_SELECTOR)
        if start > end:
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_DATE_SELECTOR,
                "start_date must not be later than end_date",
            )
        object.__setattr__(self, "start_date", start)
        object.__setattr__(self, "end_date", end)


DateSelector = SingleDate | DateRange


class IntradayExitPriority(str, Enum):
    """Deterministic resolution when stop loss and take profit hit in one bar."""

    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


@dataclass(frozen=True)
class ExecutionPolicy(CanonicalValue):
    """All execution economics and exit rules for a backtest.

    Percentage fields (`stop_loss_pct`, `take_profit_pct`, `max_entry_gap_pct`) use
    percentage points (``5.0`` means five percent).  Fee rates use fractional
    rates (``0.0001`` means one basis point).  No module-level mutable policy is
    consulted by simulation.
    """

    schema_version: int = 1
    entry_lookahead_days: int = 1
    horizon_days: int = 5
    commission_rate: float = 0.0001
    stamp_tax_rate: float = 0.0005
    entry_slippage_bps: float = 10.0
    exit_slippage_bps: float = 10.0
    position_size: float = 1.0
    stop_loss_pct: float | None = 5.0
    take_profit_pct: float | None = 10.0
    max_entry_gap_pct: float | None = None
    skip_untradable_entry_bars: bool = True
    intraday_exit_priority: IntradayExitPriority = IntradayExitPriority.STOP_LOSS

    def __post_init__(self) -> None:
        _require_positive_int(self.schema_version, "schema_version", BacktestErrorCode.INVALID_EXECUTION_POLICY)
        _require_positive_int(
            self.entry_lookahead_days,
            "entry_lookahead_days",
            BacktestErrorCode.INVALID_EXECUTION_POLICY,
        )
        _require_positive_int(self.horizon_days, "horizon_days", BacktestErrorCode.INVALID_EXECUTION_POLICY)
        for name in ("commission_rate", "stamp_tax_rate", "entry_slippage_bps", "exit_slippage_bps"):
            if _require_finite_number(getattr(self, name), name, BacktestErrorCode.INVALID_EXECUTION_POLICY) < 0:
                raise BacktestValidationError(BacktestErrorCode.INVALID_EXECUTION_POLICY, f"{name} must be non-negative")
        position_size = _require_finite_number(
            self.position_size,
            "position_size",
            BacktestErrorCode.INVALID_EXECUTION_POLICY,
        )
        if not 0 < position_size <= 1:
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_EXECUTION_POLICY,
                "position_size must be in (0, 1]",
            )
        object.__setattr__(self, "position_size", position_size)
        for name, allow_zero in (("stop_loss_pct", False), ("take_profit_pct", False), ("max_entry_gap_pct", True)):
            value = getattr(self, name)
            if value is None:
                continue
            normalized = _require_finite_number(value, name, BacktestErrorCode.INVALID_EXECUTION_POLICY)
            if normalized < 0 or (not allow_zero and normalized == 0) or normalized >= 100:
                raise BacktestValidationError(
                    BacktestErrorCode.INVALID_EXECUTION_POLICY,
                    f"{name} must be {'non-negative' if allow_zero else 'positive'} and less than 100",
                )
            object.__setattr__(self, name, normalized)
        priority = self.intraday_exit_priority
        try:
            priority = priority if isinstance(priority, IntradayExitPriority) else IntradayExitPriority(str(priority))
        except ValueError as exc:
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_EXECUTION_POLICY,
                "intraday_exit_priority is invalid",
            ) from exc
        object.__setattr__(self, "intraday_exit_priority", priority)

    @property
    def fee_pct(self) -> float:
        """Round-trip fees expressed in percentage points, excluding slippage."""

        return (self.commission_rate * 2 + self.stamp_tax_rate) * 100


@dataclass(frozen=True)
class BacktestDataPolicy(CanonicalValue):
    """Safety boundary for research runs; default is local read-only data."""

    local_only: bool = True
    allow_market_data_write: bool = False
    allow_auxiliary_data_write: bool = False

    def __post_init__(self) -> None:
        if not self.local_only:
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_REQUEST,
                "backtests must use local-only input data",
            )
        if self.allow_market_data_write or self.allow_auxiliary_data_write:
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_REQUEST,
                "backtests may not write market or auxiliary data",
            )


def _normalize_codes(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise BacktestValidationError(BacktestErrorCode.INVALID_REQUEST, "target_codes must be a sequence of codes")
    normalized = tuple(sorted(_require_non_empty_string(code, "target_codes[]", BacktestErrorCode.INVALID_REQUEST) for code in value))
    if len(set(normalized)) != len(normalized):
        raise BacktestValidationError(BacktestErrorCode.INVALID_REQUEST, "target_codes must not contain duplicates")
    return normalized


@dataclass(frozen=True)
class BacktestRequest(CanonicalValue):
    """Immutable request for one and only one strategy.

    `target_codes` intentionally marks a diagnostic universe.  Consumers must
    inspect :attr:`diagnostic_scope` and must not promote its metrics as
    full-market strategy evidence.
    """

    strategy_id: str
    date_selector: DateSelector
    execution_policy: ExecutionPolicy
    schema_version: int = 1
    strategy_version: str | None = None
    target_codes: tuple[str, ...] = ()
    data_policy: BacktestDataPolicy = field(default_factory=BacktestDataPolicy)
    parameter_set: Mapping[str, Any] | None = None
    experiment_id: str | None = None
    baseline_backtest_id: str | None = None
    force_rerun: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "strategy_id",
            _require_non_empty_string(self.strategy_id, "strategy_id", BacktestErrorCode.INVALID_REQUEST),
        )
        _require_positive_int(self.schema_version, "schema_version", BacktestErrorCode.INVALID_REQUEST)
        if not isinstance(self.date_selector, (SingleDate, DateRange)):
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_DATE_SELECTOR,
                "date_selector must be exactly SingleDate or DateRange",
            )
        if not isinstance(self.execution_policy, ExecutionPolicy):
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_EXECUTION_POLICY,
                "execution_policy must be an ExecutionPolicy",
            )
        if not isinstance(self.data_policy, BacktestDataPolicy):
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_REQUEST,
                "data_policy must be a BacktestDataPolicy",
            )
        object.__setattr__(self, "target_codes", _normalize_codes(self.target_codes))
        if self.strategy_version is not None:
            object.__setattr__(
                self,
                "strategy_version",
                _require_non_empty_string(
                    self.strategy_version,
                    "strategy_version",
                    BacktestErrorCode.INVALID_REQUEST,
                ),
            )
        for name in ("experiment_id", "baseline_backtest_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    _require_non_empty_string(value, name, BacktestErrorCode.INVALID_REQUEST),
                )
        if not isinstance(self.force_rerun, bool):
            raise BacktestValidationError(BacktestErrorCode.INVALID_REQUEST, "force_rerun must be a boolean")
        if self.parameter_set is not None:
            if not isinstance(self.parameter_set, Mapping):
                raise BacktestValidationError(
                    BacktestErrorCode.INVALID_REQUEST,
                    "parameter_set must be a mapping declared by the strategy manifest",
                )
            object.__setattr__(self, "parameter_set", _freeze_mapping(self.parameter_set))

    @property
    def diagnostic_scope(self) -> bool:
        return bool(self.target_codes)

    def semantic_payload(self) -> dict[str, Any]:
        """Request semantics excluding the control flag that only forces a new attempt."""

        payload = self.canonical_payload()
        payload.pop("force_rerun", None)
        return payload

    def fingerprint(self) -> str:
        return canonical_fingerprint(self.semantic_payload())

    def request_fingerprint(
        self,
        *,
        snapshot_id: str,
        effective_strategy_version: str | None = None,
        execution_version: str | None = None,
    ) -> str:
        """Fingerprint an executable attempt without treating ``force_rerun`` as input data."""

        resolved_version = effective_strategy_version or self.strategy_version
        if not resolved_version:
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_REQUEST,
                "effective_strategy_version is required before an attempt can be claimed",
            )
        return canonical_fingerprint(
            {
                "request": self.semantic_payload(),
                "snapshot_id": _require_non_empty_string(
                    snapshot_id,
                    "snapshot_id",
                    BacktestErrorCode.INVALID_REQUEST,
                ),
                "effective_strategy_version": _require_non_empty_string(
                    resolved_version,
                    "effective_strategy_version",
                    BacktestErrorCode.INVALID_REQUEST,
                ),
                "execution_version": execution_version or "",
                "execution_policy_fingerprint": self.execution_policy.fingerprint(),
            }
        )


@dataclass(frozen=True)
class AuxiliaryDataRequirement(CanonicalValue):
    key: str
    minimum_coverage: float = 1.0
    release_lag_days: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "key",
            _require_non_empty_string(self.key, "key", BacktestErrorCode.INVALID_DATA_REQUIREMENT),
        )
        coverage = _require_finite_number(
            self.minimum_coverage,
            "minimum_coverage",
            BacktestErrorCode.INVALID_DATA_REQUIREMENT,
        )
        if not 0 <= coverage <= 1:
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_DATA_REQUIREMENT,
                "minimum_coverage must be in [0, 1]",
            )
        _require_non_negative_int(
            self.release_lag_days,
            "release_lag_days",
            BacktestErrorCode.INVALID_DATA_REQUIREMENT,
        )
        object.__setattr__(self, "minimum_coverage", coverage)


@dataclass(frozen=True)
class DataRequirement(CanonicalValue):
    """Data declared by one backtest strategy before per-date preflight."""

    lookback_bars: int
    future_label_horizon_days: int
    requires_daily_bars: bool = True
    requires_minute_bars: bool = False
    benchmark_code: str | None = None
    auxiliary_datasets: tuple[AuxiliaryDataRequirement, ...] = ()
    minimum_coverage: float = 1.0

    def __post_init__(self) -> None:
        _require_non_negative_int(self.lookback_bars, "lookback_bars", BacktestErrorCode.INVALID_DATA_REQUIREMENT)
        _require_non_negative_int(
            self.future_label_horizon_days,
            "future_label_horizon_days",
            BacktestErrorCode.INVALID_DATA_REQUIREMENT,
        )
        if not self.requires_daily_bars and not self.requires_minute_bars:
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_DATA_REQUIREMENT,
                "at least one market-data frequency is required",
            )
        coverage = _require_finite_number(
            self.minimum_coverage,
            "minimum_coverage",
            BacktestErrorCode.INVALID_DATA_REQUIREMENT,
        )
        if not 0 <= coverage <= 1:
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_DATA_REQUIREMENT,
                "minimum_coverage must be in [0, 1]",
            )
        if self.benchmark_code is not None:
            object.__setattr__(
                self,
                "benchmark_code",
                _require_non_empty_string(
                    self.benchmark_code,
                    "benchmark_code",
                    BacktestErrorCode.INVALID_DATA_REQUIREMENT,
                ),
            )
        normalized = tuple(self.auxiliary_datasets)
        if not all(isinstance(item, AuxiliaryDataRequirement) for item in normalized):
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_DATA_REQUIREMENT,
                "auxiliary_datasets must contain AuxiliaryDataRequirement values",
            )
        keys = tuple(item.key for item in normalized)
        if len(set(keys)) != len(keys):
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_DATA_REQUIREMENT,
                "auxiliary_datasets must not repeat a dataset key",
            )
        object.__setattr__(self, "auxiliary_datasets", normalized)
        object.__setattr__(self, "minimum_coverage", coverage)


@dataclass(frozen=True)
class BarInput(CanonicalValue):
    """Normalized OHLC bar supplied to the pure simulation boundary."""

    code: str
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    tradable: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _require_non_empty_string(self.code, "code", BacktestErrorCode.INVALID_SIMULATION_INPUT))
        object.__setattr__(self, "trade_date", _require_iso_date(self.trade_date, "trade_date", BacktestErrorCode.INVALID_SIMULATION_INPUT))
        prices = {
            name: _require_finite_number(getattr(self, name), name, BacktestErrorCode.INVALID_SIMULATION_INPUT)
            for name in ("open", "high", "low", "close")
        }
        if any(price <= 0 for price in prices.values()):
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "OHLC prices must be positive")
        if prices["low"] > min(prices["open"], prices["high"], prices["close"]) or prices["high"] < max(
            prices["open"], prices["low"], prices["close"]
        ):
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "OHLC prices are inconsistent")
        for name, value in prices.items():
            object.__setattr__(self, name, value)
        if self.volume is not None:
            volume = _require_finite_number(self.volume, "volume", BacktestErrorCode.INVALID_SIMULATION_INPUT)
            if volume < 0:
                raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "volume must be non-negative")
            object.__setattr__(self, "volume", volume)
        if not isinstance(self.tradable, bool):
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "tradable must be a boolean")


@dataclass(frozen=True)
class RecommendationInput(CanonicalValue):
    """The minimum pure input needed to simulate a signal's execution."""

    code: str
    signal_date: str
    signal_price: float
    recommendation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _require_non_empty_string(self.code, "code", BacktestErrorCode.INVALID_SIMULATION_INPUT))
        object.__setattr__(self, "signal_date", _require_iso_date(self.signal_date, "signal_date", BacktestErrorCode.INVALID_SIMULATION_INPUT))
        price = _require_finite_number(self.signal_price, "signal_price", BacktestErrorCode.INVALID_SIMULATION_INPUT)
        if price <= 0:
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "signal_price must be positive")
        object.__setattr__(self, "signal_price", price)
        if self.recommendation_id is not None:
            object.__setattr__(
                self,
                "recommendation_id",
                _require_non_empty_string(
                    self.recommendation_id,
                    "recommendation_id",
                    BacktestErrorCode.INVALID_SIMULATION_INPUT,
                ),
            )


@dataclass(frozen=True)
class SignalInputRequirement(CanonicalValue):
    """The restricted input shape a strategy may ask a signal-side reader for."""

    lookback_bars: int = 0
    required_datasets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_negative_int(self.lookback_bars, "lookback_bars", BacktestErrorCode.INVALID_DATA_REQUIREMENT)
        if isinstance(self.required_datasets, (str, bytes)):
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_DATA_REQUIREMENT,
                "required_datasets must be a sequence of dataset keys",
            )
        datasets = tuple(
            _require_non_empty_string(key, "required_datasets[]", BacktestErrorCode.INVALID_DATA_REQUIREMENT)
            for key in self.required_datasets
        )
        if len(set(datasets)) != len(datasets):
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_DATA_REQUIREMENT,
                "required_datasets must not contain duplicates",
            )
        object.__setattr__(self, "required_datasets", datasets)


@dataclass(frozen=True)
class SignalInput(CanonicalValue):
    """As-of input passed to strategy/factor evaluation, never future outcome data."""

    signal_date: str
    visible_at: str
    payload: Mapping[str, Any]
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        signal_date = _require_iso_date(self.signal_date, "signal_date", BacktestErrorCode.INVALID_DATA_REQUIREMENT)
        visible_at = _require_iso_date(self.visible_at, "visible_at", BacktestErrorCode.INVALID_DATA_REQUIREMENT)
        assert_signal_visible_at(signal_date, visible_at)
        if not isinstance(self.payload, Mapping):
            raise BacktestValidationError(BacktestErrorCode.INVALID_DATA_REQUIREMENT, "payload must be a mapping")
        object.__setattr__(self, "signal_date", signal_date)
        object.__setattr__(self, "visible_at", visible_at)
        object.__setattr__(self, "payload", _freeze_mapping(self.payload))
        refs = tuple(_require_non_empty_string(ref, "artifact_refs[]", BacktestErrorCode.INVALID_DATA_REQUIREMENT) for ref in self.artifact_refs)
        object.__setattr__(self, "artifact_refs", refs)


@dataclass(frozen=True)
class OutcomeInput(CanonicalValue):
    """Future bars available only to the outcome/simulation side of the boundary."""

    signal_date: str
    bars: tuple[BarInput, ...]
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_date", _require_iso_date(self.signal_date, "signal_date", BacktestErrorCode.INVALID_DATA_REQUIREMENT))
        bars = tuple(self.bars)
        if not all(isinstance(bar, BarInput) for bar in bars):
            raise BacktestValidationError(BacktestErrorCode.INVALID_DATA_REQUIREMENT, "bars must contain BarInput values")
        object.__setattr__(self, "bars", bars)
        object.__setattr__(
            self,
            "artifact_refs",
            tuple(_require_non_empty_string(ref, "artifact_refs[]", BacktestErrorCode.INVALID_DATA_REQUIREMENT) for ref in self.artifact_refs),
        )


def assert_signal_visible_at(signal_date: str, visible_at: str) -> None:
    """Common reader guard; callers must never hand a future publication to a strategy."""

    normalized_signal = _require_iso_date(signal_date, "signal_date", BacktestErrorCode.INVALID_DATA_REQUIREMENT)
    normalized_visible = _require_iso_date(visible_at, "visible_at", BacktestErrorCode.INVALID_DATA_REQUIREMENT)
    if normalized_visible > normalized_signal:
        raise FutureDataAccessDeniedError(normalized_signal, normalized_visible)


@runtime_checkable
class SignalDateDataReader(Protocol):
    """As-of data capability granted to strategy, factor, and feature code only."""

    def read(self, signal_date: str, requirement: SignalInputRequirement) -> SignalInput:
        ...


@runtime_checkable
class OutcomeDataReader(Protocol):
    """Future execution/outcome capability granted to simulation code only."""

    def read(self, signal_date: str, policy: ExecutionPolicy) -> OutcomeInput:
        ...


@dataclass(frozen=True)
class PartitionManifest(CanonicalValue):
    """The exact query partition that contributed to an immutable snapshot."""

    dataset: str
    query_range: DateRange
    source_version: str
    row_count: int
    content_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset", _require_non_empty_string(self.dataset, "dataset", BacktestErrorCode.INVALID_SNAPSHOT))
        if not isinstance(self.query_range, DateRange):
            raise BacktestValidationError(BacktestErrorCode.INVALID_SNAPSHOT, "query_range must be a DateRange")
        object.__setattr__(
            self,
            "source_version",
            _require_non_empty_string(self.source_version, "source_version", BacktestErrorCode.INVALID_SNAPSHOT),
        )
        _require_non_negative_int(self.row_count, "row_count", BacktestErrorCode.INVALID_SNAPSHOT)
        object.__setattr__(self, "content_hash", _require_hash(self.content_hash, "content_hash", BacktestErrorCode.INVALID_SNAPSHOT))


class ArtifactScope(str, Enum):
    SIGNAL_INPUT = "signal_input"
    OUTCOME_INPUT = "outcome_input"


@dataclass(frozen=True)
class ArtifactRef(CanonicalValue):
    """Metadata for content-addressed input retained independently of mutable source tables."""

    artifact_id: str
    signal_date: str
    artifact_scope: ArtifactScope
    artifact_type: str
    schema_version: int
    row_count: int
    content_hash: str
    content_encoding: str = "json"

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_id", _require_non_empty_string(self.artifact_id, "artifact_id", BacktestErrorCode.INVALID_ARTIFACT))
        object.__setattr__(self, "signal_date", _require_iso_date(self.signal_date, "signal_date", BacktestErrorCode.INVALID_ARTIFACT))
        try:
            scope = self.artifact_scope if isinstance(self.artifact_scope, ArtifactScope) else ArtifactScope(str(self.artifact_scope))
        except ValueError as exc:
            raise BacktestValidationError(BacktestErrorCode.INVALID_ARTIFACT, "artifact_scope is invalid") from exc
        object.__setattr__(self, "artifact_scope", scope)
        object.__setattr__(self, "artifact_type", _require_non_empty_string(self.artifact_type, "artifact_type", BacktestErrorCode.INVALID_ARTIFACT))
        _require_positive_int(self.schema_version, "schema_version", BacktestErrorCode.INVALID_ARTIFACT)
        _require_non_negative_int(self.row_count, "row_count", BacktestErrorCode.INVALID_ARTIFACT)
        object.__setattr__(self, "content_hash", _require_hash(self.content_hash, "content_hash", BacktestErrorCode.INVALID_ARTIFACT))
        object.__setattr__(self, "content_encoding", _require_non_empty_string(self.content_encoding, "content_encoding", BacktestErrorCode.INVALID_ARTIFACT))

    @classmethod
    def from_content(
        cls,
        *,
        artifact_id: str,
        signal_date: str,
        artifact_scope: ArtifactScope,
        artifact_type: str,
        schema_version: int,
        row_count: int,
        payload: bytes,
        content_encoding: str = "json",
    ) -> "ArtifactRef":
        return cls(
            artifact_id=artifact_id,
            signal_date=signal_date,
            artifact_scope=artifact_scope,
            artifact_type=artifact_type,
            schema_version=schema_version,
            row_count=row_count,
            content_hash=content_hash(payload),
            content_encoding=content_encoding,
        )

    def validate_content(self, payload: bytes) -> None:
        actual_hash = content_hash(payload)
        if actual_hash != self.content_hash:
            raise SnapshotIntegrityError(
                self.artifact_id,
                expected_hash=self.content_hash,
                actual_hash=actual_hash,
            )


@dataclass(frozen=True)
class DataGap(CanonicalValue):
    signal_date: str
    requirement: str
    error_code: BacktestErrorCode
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_date", _require_iso_date(self.signal_date, "signal_date", BacktestErrorCode.INVALID_SNAPSHOT))
        object.__setattr__(self, "requirement", _require_non_empty_string(self.requirement, "requirement", BacktestErrorCode.INVALID_SNAPSHOT))
        if self.error_code not in {BacktestErrorCode.INSUFFICIENT_DATA, BacktestErrorCode.INCOMPLETE_EVALUATION}:
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_SNAPSHOT,
                "data gap error_code must be insufficient_data or incomplete_evaluation",
            )


class CoverageStatus(str, Enum):
    READY = "ready"
    INSUFFICIENT_DATA = BacktestErrorCode.INSUFFICIENT_DATA.value
    INCOMPLETE_EVALUATION = BacktestErrorCode.INCOMPLETE_EVALUATION.value


@dataclass(frozen=True)
class CoverageResult(CanonicalValue):
    signal_date: str
    status: CoverageStatus
    coverage_ratio: float
    gaps: tuple[DataGap, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_date", _require_iso_date(self.signal_date, "signal_date", BacktestErrorCode.INVALID_SNAPSHOT))
        try:
            status = self.status if isinstance(self.status, CoverageStatus) else CoverageStatus(str(self.status))
        except ValueError as exc:
            raise BacktestValidationError(BacktestErrorCode.INVALID_SNAPSHOT, "coverage status is invalid") from exc
        ratio = _require_finite_number(self.coverage_ratio, "coverage_ratio", BacktestErrorCode.INVALID_SNAPSHOT)
        if not 0 <= ratio <= 1:
            raise BacktestValidationError(BacktestErrorCode.INVALID_SNAPSHOT, "coverage_ratio must be in [0, 1]")
        gaps = tuple(self.gaps)
        if not all(isinstance(gap, DataGap) for gap in gaps):
            raise BacktestValidationError(BacktestErrorCode.INVALID_SNAPSHOT, "gaps must contain DataGap values")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "coverage_ratio", ratio)
        object.__setattr__(self, "gaps", gaps)

    @property
    def is_ready(self) -> bool:
        return self.status is CoverageStatus.READY


@dataclass(frozen=True)
class DataSnapshot(CanonicalValue):
    """Immutable replay boundary; source tables are never consulted during replay."""

    snapshot_id: str
    snapshot_version: int
    partitions: tuple[PartitionManifest, ...]
    artifacts_by_signal_date: Mapping[str, tuple[ArtifactRef, ...]] = field(default_factory=dict)
    coverage_by_signal_date: Mapping[str, CoverageResult] = field(default_factory=dict)
    missing_requirements: tuple[DataGap, ...] = ()
    input_manifest_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _require_non_empty_string(self.snapshot_id, "snapshot_id", BacktestErrorCode.INVALID_SNAPSHOT))
        _require_positive_int(self.snapshot_version, "snapshot_version", BacktestErrorCode.INVALID_SNAPSHOT)
        partitions = tuple(self.partitions)
        if not all(isinstance(partition, PartitionManifest) for partition in partitions):
            raise BacktestValidationError(BacktestErrorCode.INVALID_SNAPSHOT, "partitions must contain PartitionManifest values")
        partition_identity = tuple((item.dataset, item.query_range.start_date, item.query_range.end_date, item.source_version) for item in partitions)
        if len(set(partition_identity)) != len(partition_identity):
            raise BacktestValidationError(BacktestErrorCode.INVALID_SNAPSHOT, "partitions must not repeat the same source partition")
        object.__setattr__(self, "partitions", partitions)

        artifacts: dict[str, tuple[ArtifactRef, ...]] = {}
        if not isinstance(self.artifacts_by_signal_date, Mapping):
            raise BacktestValidationError(BacktestErrorCode.INVALID_SNAPSHOT, "artifacts_by_signal_date must be a mapping")
        for signal_date, refs in self.artifacts_by_signal_date.items():
            normalized_date = _require_iso_date(signal_date, "artifact signal_date", BacktestErrorCode.INVALID_SNAPSHOT)
            normalized_refs = tuple(refs)
            if not all(isinstance(ref, ArtifactRef) for ref in normalized_refs):
                raise BacktestValidationError(BacktestErrorCode.INVALID_SNAPSHOT, "artifact mapping must contain ArtifactRef values")
            if any(ref.signal_date != normalized_date for ref in normalized_refs):
                raise BacktestValidationError(BacktestErrorCode.INVALID_SNAPSHOT, "artifact signal date does not match its mapping key")
            ids = tuple(ref.artifact_id for ref in normalized_refs)
            if len(set(ids)) != len(ids):
                raise BacktestValidationError(BacktestErrorCode.INVALID_SNAPSHOT, "artifacts may not repeat an artifact_id per signal date")
            artifacts[normalized_date] = normalized_refs
        object.__setattr__(self, "artifacts_by_signal_date", MappingProxyType(dict(sorted(artifacts.items()))))

        coverage: dict[str, CoverageResult] = {}
        if not isinstance(self.coverage_by_signal_date, Mapping):
            raise BacktestValidationError(BacktestErrorCode.INVALID_SNAPSHOT, "coverage_by_signal_date must be a mapping")
        for signal_date, result in self.coverage_by_signal_date.items():
            normalized_date = _require_iso_date(signal_date, "coverage signal_date", BacktestErrorCode.INVALID_SNAPSHOT)
            if not isinstance(result, CoverageResult) or result.signal_date != normalized_date:
                raise BacktestValidationError(BacktestErrorCode.INVALID_SNAPSHOT, "coverage mapping must match its signal dates")
            coverage[normalized_date] = result
        object.__setattr__(self, "coverage_by_signal_date", MappingProxyType(dict(sorted(coverage.items()))))

        gaps = tuple(self.missing_requirements)
        if not all(isinstance(gap, DataGap) for gap in gaps):
            raise BacktestValidationError(BacktestErrorCode.INVALID_SNAPSHOT, "missing_requirements must contain DataGap values")
        object.__setattr__(self, "missing_requirements", gaps)

        computed = self.calculated_manifest_hash()
        if self.input_manifest_hash is None:
            object.__setattr__(self, "input_manifest_hash", computed)
        else:
            supplied = _require_hash(self.input_manifest_hash, "input_manifest_hash", BacktestErrorCode.INVALID_SNAPSHOT)
            if supplied != computed:
                raise SnapshotIntegrityError(
                    self.snapshot_id,
                    expected_hash=computed,
                    actual_hash=supplied,
                )
            object.__setattr__(self, "input_manifest_hash", supplied)

    def manifest_payload(self) -> dict[str, Any]:
        """The content-addressed identity excludes a caller-chosen snapshot id."""

        return {
            "snapshot_version": self.snapshot_version,
            "partitions": self.partitions,
            "artifacts_by_signal_date": self.artifacts_by_signal_date,
            "coverage_by_signal_date": self.coverage_by_signal_date,
            "missing_requirements": self.missing_requirements,
        }

    def calculated_manifest_hash(self) -> str:
        return canonical_fingerprint(self.manifest_payload())

    def artifacts_for(self, signal_date: str, scope: ArtifactScope | None = None) -> tuple[ArtifactRef, ...]:
        normalized_date = _require_iso_date(signal_date, "signal_date", BacktestErrorCode.INVALID_SNAPSHOT)
        refs = self.artifacts_by_signal_date.get(normalized_date, ())
        if scope is None:
            return refs
        return tuple(ref for ref in refs if ref.artifact_scope is scope)


class TradeOutcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    NOT_EXECUTED = "not_executed"


@dataclass(frozen=True)
class Trade(CanonicalValue):
    """One isolated simulated trade; returns are percentage points before/after fees."""

    code: str
    signal_date: str
    entry_date: str | None
    exit_date: str | None
    entry_price: float | None
    exit_price: float | None
    gross_return_pct: float
    net_return_pct: float
    position_size: float
    holding_days: int
    outcome_label: TradeOutcome
    exit_reason: str | None = None
    trade_id: str | None = None

    def __post_init__(self) -> None:
        code = _require_non_empty_string(self.code, "code", BacktestErrorCode.INVALID_SIMULATION_INPUT)
        signal_date = _require_iso_date(self.signal_date, "signal_date", BacktestErrorCode.INVALID_SIMULATION_INPUT)
        try:
            outcome = self.outcome_label if isinstance(self.outcome_label, TradeOutcome) else TradeOutcome(str(self.outcome_label))
        except ValueError as exc:
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "outcome_label is invalid") from exc
        gross = _require_finite_number(self.gross_return_pct, "gross_return_pct", BacktestErrorCode.INVALID_SIMULATION_INPUT)
        net = _require_finite_number(self.net_return_pct, "net_return_pct", BacktestErrorCode.INVALID_SIMULATION_INPUT)
        position_size = _require_finite_number(self.position_size, "position_size", BacktestErrorCode.INVALID_SIMULATION_INPUT)
        if not 0 <= position_size <= 1:
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "position_size must be in [0, 1]")
        _require_non_negative_int(self.holding_days, "holding_days", BacktestErrorCode.INVALID_SIMULATION_INPUT)

        executed_fields = (self.entry_date, self.exit_date, self.entry_price, self.exit_price)
        is_executed = all(value is not None for value in executed_fields)
        if is_executed:
            entry_date = _require_iso_date(self.entry_date, "entry_date", BacktestErrorCode.INVALID_SIMULATION_INPUT)
            exit_date = _require_iso_date(self.exit_date, "exit_date", BacktestErrorCode.INVALID_SIMULATION_INPUT)
            if entry_date < signal_date or exit_date < entry_date:
                raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "trade dates are not chronological")
            entry_price = _require_finite_number(self.entry_price, "entry_price", BacktestErrorCode.INVALID_SIMULATION_INPUT)
            exit_price = _require_finite_number(self.exit_price, "exit_price", BacktestErrorCode.INVALID_SIMULATION_INPUT)
            if entry_price <= 0 or exit_price <= 0 or self.holding_days <= 0 or position_size <= 0:
                raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "executed trade fields must be positive")
            if outcome is TradeOutcome.NOT_EXECUTED:
                raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "executed trade cannot use not_executed outcome")
            object.__setattr__(self, "entry_date", entry_date)
            object.__setattr__(self, "exit_date", exit_date)
            object.__setattr__(self, "entry_price", entry_price)
            object.__setattr__(self, "exit_price", exit_price)
        else:
            if any(value is not None for value in executed_fields):
                raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "unexecuted trade must omit all entry and exit fields")
            if outcome is not TradeOutcome.NOT_EXECUTED or self.holding_days != 0 or gross != 0 or net != 0:
                raise BacktestValidationError(
                    BacktestErrorCode.INVALID_SIMULATION_INPUT,
                    "unexecuted trade must have not_executed outcome and zero returns",
                )
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "signal_date", signal_date)
        object.__setattr__(self, "gross_return_pct", gross)
        object.__setattr__(self, "net_return_pct", net)
        object.__setattr__(self, "position_size", position_size)
        object.__setattr__(self, "outcome_label", outcome)
        if self.exit_reason is not None:
            object.__setattr__(self, "exit_reason", _require_non_empty_string(self.exit_reason, "exit_reason", BacktestErrorCode.INVALID_SIMULATION_INPUT))
        deterministic_id = canonical_fingerprint(
            {
                "code": code,
                "signal_date": signal_date,
                "entry_date": self.entry_date,
                "exit_date": self.exit_date,
                "entry_price": self.entry_price,
                "exit_price": self.exit_price,
                "exit_reason": self.exit_reason,
            }
        )[:24]
        object.__setattr__(
            self,
            "trade_id",
            _require_non_empty_string(self.trade_id, "trade_id", BacktestErrorCode.INVALID_SIMULATION_INPUT)
            if self.trade_id is not None
            else f"trade_{deterministic_id}",
        )

    @property
    def executed(self) -> bool:
        return self.outcome_label is not TradeOutcome.NOT_EXECUTED

    @property
    def portfolio_return_pct(self) -> float:
        return self.net_return_pct * self.position_size if self.executed else 0.0


@dataclass(frozen=True)
class EquityPoint(CanonicalValue):
    trade_date: str
    equity: float
    daily_return_pct: float
    drawdown_pct: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "trade_date", _require_iso_date(self.trade_date, "trade_date", BacktestErrorCode.INVALID_SIMULATION_INPUT))
        equity = _require_finite_number(self.equity, "equity", BacktestErrorCode.INVALID_SIMULATION_INPUT)
        daily_return = _require_finite_number(self.daily_return_pct, "daily_return_pct", BacktestErrorCode.INVALID_SIMULATION_INPUT)
        drawdown = _require_finite_number(self.drawdown_pct, "drawdown_pct", BacktestErrorCode.INVALID_SIMULATION_INPUT)
        if equity < 0 or drawdown < 0:
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "equity and drawdown_pct must be non-negative")
        object.__setattr__(self, "equity", equity)
        object.__setattr__(self, "daily_return_pct", daily_return)
        object.__setattr__(self, "drawdown_pct", drawdown)


class SampleConfidence(str, Enum):
    INSUFFICIENT = "insufficient"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class BacktestMetrics(CanonicalValue):
    sample_count: int
    executed_trade_count: int
    unexecuted_count: int
    win_count: int
    loss_count: int
    win_rate_pct: float
    average_net_return_pct: float
    profit_loss_ratio: float | None
    total_net_return_pct: float
    max_drawdown_pct: float
    confidence: SampleConfidence

    def __post_init__(self) -> None:
        for name in ("sample_count", "executed_trade_count", "unexecuted_count", "win_count", "loss_count"):
            _require_non_negative_int(getattr(self, name), name, BacktestErrorCode.INVALID_SIMULATION_INPUT)
        if self.executed_trade_count + self.unexecuted_count != self.sample_count:
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "sample counts are inconsistent")
        if self.win_count + self.loss_count > self.executed_trade_count:
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "win/loss counts exceed executed trades")
        for name in ("win_rate_pct", "average_net_return_pct", "total_net_return_pct", "max_drawdown_pct"):
            value = _require_finite_number(getattr(self, name), name, BacktestErrorCode.INVALID_SIMULATION_INPUT)
            if name in {"win_rate_pct", "max_drawdown_pct"} and value < 0:
                raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, f"{name} must be non-negative")
            object.__setattr__(self, name, value)
        if self.profit_loss_ratio is not None:
            ratio = _require_finite_number(self.profit_loss_ratio, "profit_loss_ratio", BacktestErrorCode.INVALID_SIMULATION_INPUT)
            if ratio < 0:
                raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "profit_loss_ratio must be non-negative")
            object.__setattr__(self, "profit_loss_ratio", ratio)
        try:
            confidence = self.confidence if isinstance(self.confidence, SampleConfidence) else SampleConfidence(str(self.confidence))
        except ValueError as exc:
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "confidence is invalid") from exc
        object.__setattr__(self, "confidence", confidence)

    @property
    def promotable(self) -> bool:
        return self.confidence is not SampleConfidence.INSUFFICIENT


class FailureReasonCode(str, Enum):
    CHASE_HIGH = "chase_high"
    STOP_LOSS = "stop_loss"
    SECTOR_DECAY = "sector_decay"
    BLACK_SWAN = "black_swan"
    EXECUTION_FAILURE = "execution_failure"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EvidenceRef(CanonicalValue):
    reference: str
    kind: str = "artifact"

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference", _require_non_empty_string(self.reference, "reference", BacktestErrorCode.INVALID_SIMULATION_INPUT))
        object.__setattr__(self, "kind", _require_non_empty_string(self.kind, "kind", BacktestErrorCode.INVALID_SIMULATION_INPUT))


@dataclass(frozen=True)
class FailureAttribution(CanonicalValue):
    trade_id: str
    reason_code: FailureReasonCode
    evidence_references: tuple[EvidenceRef, ...] = ()
    rule_or_factor_reference: str | None = None
    confidence: float = 0.0
    unknown: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "trade_id", _require_non_empty_string(self.trade_id, "trade_id", BacktestErrorCode.INVALID_SIMULATION_INPUT))
        try:
            reason = self.reason_code if isinstance(self.reason_code, FailureReasonCode) else FailureReasonCode(str(self.reason_code))
        except ValueError as exc:
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "reason_code is invalid") from exc
        evidence = tuple(self.evidence_references)
        if not all(isinstance(item, EvidenceRef) for item in evidence):
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "evidence_references must contain EvidenceRef values")
        confidence = _require_finite_number(self.confidence, "confidence", BacktestErrorCode.INVALID_SIMULATION_INPUT)
        if not 0 <= confidence <= 1:
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "confidence must be in [0, 1]")
        if self.unknown != (reason is FailureReasonCode.UNKNOWN):
            raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, "unknown must agree with reason_code")
        object.__setattr__(self, "reason_code", reason)
        object.__setattr__(self, "evidence_references", evidence)
        object.__setattr__(self, "confidence", confidence)
        if self.rule_or_factor_reference is not None:
            object.__setattr__(
                self,
                "rule_or_factor_reference",
                _require_non_empty_string(
                    self.rule_or_factor_reference,
                    "rule_or_factor_reference",
                    BacktestErrorCode.INVALID_SIMULATION_INPUT,
                ),
            )


class BacktestStatus(str, Enum):
    COMPLETED = "completed"
    INSUFFICIENT_DATA = BacktestErrorCode.INSUFFICIENT_DATA.value
    INCOMPLETE_EVALUATION = BacktestErrorCode.INCOMPLETE_EVALUATION.value
    FAILED = "failed"


@dataclass(frozen=True)
class BacktestResult(CanonicalValue):
    """Pure projection of a completed or observable non-successful backtest attempt."""

    request_fingerprint: str
    snapshot_id: str
    status: BacktestStatus
    trades: tuple[Trade, ...] = ()
    equity_curve: tuple[EquityPoint, ...] = ()
    metrics: BacktestMetrics | None = None
    failure_attributions: tuple[FailureAttribution, ...] = ()
    error_code: BacktestErrorCode | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_fingerprint",
            _require_hash(self.request_fingerprint, "request_fingerprint", BacktestErrorCode.INVALID_REQUEST),
        )
        object.__setattr__(self, "snapshot_id", _require_non_empty_string(self.snapshot_id, "snapshot_id", BacktestErrorCode.INVALID_REQUEST))
        try:
            status = self.status if isinstance(self.status, BacktestStatus) else BacktestStatus(str(self.status))
        except ValueError as exc:
            raise BacktestValidationError(BacktestErrorCode.INVALID_REQUEST, "backtest result status is invalid") from exc
        trades = tuple(self.trades)
        points = tuple(self.equity_curve)
        attributions = tuple(self.failure_attributions)
        if not all(isinstance(trade, Trade) for trade in trades):
            raise BacktestValidationError(BacktestErrorCode.INVALID_REQUEST, "trades must contain Trade values")
        if not all(isinstance(point, EquityPoint) for point in points):
            raise BacktestValidationError(BacktestErrorCode.INVALID_REQUEST, "equity_curve must contain EquityPoint values")
        if not all(isinstance(item, FailureAttribution) for item in attributions):
            raise BacktestValidationError(BacktestErrorCode.INVALID_REQUEST, "failure_attributions must contain FailureAttribution values")
        if self.metrics is not None and not isinstance(self.metrics, BacktestMetrics):
            raise BacktestValidationError(BacktestErrorCode.INVALID_REQUEST, "metrics must be a BacktestMetrics")
        if status is BacktestStatus.COMPLETED and self.metrics is None:
            raise BacktestValidationError(BacktestErrorCode.INVALID_REQUEST, "completed result must include metrics")
        if status is not BacktestStatus.COMPLETED and self.metrics is not None and self.metrics.promotable:
            raise BacktestValidationError(BacktestErrorCode.INVALID_REQUEST, "non-completed result cannot contain promotable metrics")
        if self.error_code is not None:
            code = self.error_code if isinstance(self.error_code, BacktestErrorCode) else BacktestErrorCode(str(self.error_code))
            object.__setattr__(self, "error_code", code)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "trades", trades)
        object.__setattr__(self, "equity_curve", points)
        object.__setattr__(self, "failure_attributions", attributions)
