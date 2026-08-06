from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
import math
import statistics
from typing import Iterable, Sequence

from app.engine.canonical import CanonicalValue, canonical_fingerprint


FACTOR_ANALYSIS_SCHEMA_VERSION = 1
_ALLOWED_UNIVERSE_SCOPES = frozenset({"selected", "signal"})
_ALLOWED_EVALUATION_SCOPES = frozenset(
    {"live_forward", "historical_replay", "experiment"}
)


class FactorAnalysisError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CrossSectionalObservation(CanonicalValue):
    trade_date: str
    security_code: str
    factor_name: str
    factor_value: float | None
    future_return_pct: float | None
    universe_scope: str
    evaluation_scope: str
    recommendation_id: str | None = None
    outcome_identity: str | None = None
    source_artifact_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _parse_date(self.trade_date, "trade_date")
        for value, name in (
            (self.security_code, "security_code"),
            (self.factor_name, "factor_name"),
        ):
            _required_text(value, name)
        if self.universe_scope not in _ALLOWED_UNIVERSE_SCOPES:
            raise FactorAnalysisError("unsupported universe_scope")
        if self.evaluation_scope not in _ALLOWED_EVALUATION_SCOPES:
            raise FactorAnalysisError("unsupported evaluation_scope")
        _finite_optional(self.factor_value, "factor_value")
        _finite_optional(self.future_return_pct, "future_return_pct")
        source_ids = tuple(sorted({_required_text(item, "source_artifact_id") for item in self.source_artifact_ids}))
        object.__setattr__(self, "source_artifact_ids", source_ids)

    @property
    def identity_key(self) -> tuple[str, str, str, str | None]:
        return (
            self.trade_date,
            self.security_code,
            self.factor_name,
            self.outcome_identity,
        )


@dataclass(frozen=True, slots=True)
class FactorAnalysisRequest(CanonicalValue):
    factor_name: str
    start_date: str
    end_date: str
    horizon_days: int
    quantiles: int
    min_cross_section_size: int
    universe_scope: str = "selected"
    evaluation_scope: str = "live_forward"
    return_field: str = "net_return_pct"
    schema_version: int = FACTOR_ANALYSIS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required_text(self.factor_name, "factor_name")
        start = _parse_date(self.start_date, "start_date")
        end = _parse_date(self.end_date, "end_date")
        if start > end:
            raise FactorAnalysisError("start_date must not be after end_date")
        if self.horizon_days < 1:
            raise FactorAnalysisError("horizon_days must be positive")
        if not 2 <= self.quantiles <= 10:
            raise FactorAnalysisError("quantiles must be between 2 and 10")
        if self.min_cross_section_size < max(2, self.quantiles):
            raise FactorAnalysisError(
                "min_cross_section_size must be at least two and quantiles"
            )
        if self.universe_scope not in _ALLOWED_UNIVERSE_SCOPES:
            raise FactorAnalysisError("unsupported universe_scope")
        if self.evaluation_scope not in _ALLOWED_EVALUATION_SCOPES:
            raise FactorAnalysisError("unsupported evaluation_scope")
        if self.return_field != "net_return_pct":
            raise FactorAnalysisError("return_field must be canonical net_return_pct")
        if self.schema_version != FACTOR_ANALYSIS_SCHEMA_VERSION:
            raise FactorAnalysisError("unsupported factor analysis schema version")


@dataclass(frozen=True, slots=True)
class CorrelationSummary(CanonicalValue):
    count: int
    mean: float | None
    std: float | None
    icir: float | None
    positive_ratio: float | None


@dataclass(frozen=True, slots=True)
class QuantileMetrics(CanonicalValue):
    quantile: int
    average_return_pct: float | None
    observation_count: int
    date_count: int


@dataclass(frozen=True, slots=True)
class DailyFactorMetrics(CanonicalValue):
    trade_date: str
    universe_count: int
    valid_count: int
    coverage: float
    pearson_ic: float | None
    rank_ic: float | None
    quantile_returns_pct: tuple[float | None, ...]
    quantile_counts: tuple[int, ...]
    long_short_return_pct: float | None
    skip_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FactorPeriodMetrics(CanonicalValue):
    period: str
    observation_count: int
    valid_observation_count: int
    date_count: int
    coverage: float | None
    pearson_ic: CorrelationSummary
    rank_ic: CorrelationSummary
    quantiles: tuple[QuantileMetrics, ...]
    long_short_return_pct: float | None
    monotonicity_spearman: float | None
    factor_autocorrelation: CorrelationSummary
    daily: tuple[DailyFactorMetrics, ...]
    skip_reason_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class FactorEqualWeightedMetrics(CanonicalValue):
    month_count: int
    pearson_ic_mean: float | None
    rank_ic_mean: float | None
    quantile_returns_pct: tuple[float | None, ...]
    long_short_return_pct: float | None
    monotonicity_spearman: float | None
    factor_autocorrelation_mean: float | None


@dataclass(frozen=True, slots=True)
class FactorEvidenceDiagnostics(CanonicalValue):
    evidence_status: str
    reasons: tuple[str, ...]
    observation_count: int
    valid_observation_count: int
    independent_days: int
    independent_months: int
    max_day_observation_share: float | None
    max_month_observation_share: float | None


@dataclass(frozen=True, slots=True)
class FactorAnalysisResult(CanonicalValue):
    schema_version: int
    request_fingerprint: str
    observation_fingerprint: str
    overall: FactorPeriodMetrics
    monthly: tuple[FactorPeriodMetrics, ...]
    equal_weighted_monthly: FactorEqualWeightedMetrics
    diagnostics: FactorEvidenceDiagnostics
    result_hash: str = ""

    def __post_init__(self) -> None:
        expected = canonical_fingerprint(self.business_payload())
        if self.result_hash and self.result_hash != expected:
            raise FactorAnalysisError("result_hash does not match factor analysis result")
        object.__setattr__(self, "result_hash", expected)

    def business_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "request_fingerprint": self.request_fingerprint,
            "observation_fingerprint": self.observation_fingerprint,
            "overall": self.overall,
            "monthly": self.monthly,
            "equal_weighted_monthly": self.equal_weighted_monthly,
            "diagnostics": self.diagnostics,
        }


@dataclass(frozen=True, slots=True)
class _DatedValues:
    trade_date: str
    factor_by_security: dict[str, float]


def evaluate_factor_cross_section(
    request: FactorAnalysisRequest,
    observations: Iterable[CrossSectionalObservation],
) -> FactorAnalysisResult:
    normalized = _normalize_observations(request, observations)
    request_fingerprint = request.fingerprint()
    observation_fingerprint = canonical_fingerprint(normalized)
    overall = _evaluate_period("overall", normalized, request)

    by_month: dict[str, list[CrossSectionalObservation]] = defaultdict(list)
    for observation in normalized:
        by_month[observation.trade_date[:7]].append(observation)
    monthly = tuple(
        _evaluate_period(month, tuple(rows), request)
        for month, rows in sorted(by_month.items())
    )
    equal_weighted = _equal_weighted_monthly(monthly, request.quantiles)
    diagnostics = _diagnostics(normalized, overall, monthly)
    return FactorAnalysisResult(
        schema_version=FACTOR_ANALYSIS_SCHEMA_VERSION,
        request_fingerprint=request_fingerprint,
        observation_fingerprint=observation_fingerprint,
        overall=overall,
        monthly=monthly,
        equal_weighted_monthly=equal_weighted,
        diagnostics=diagnostics,
    )


def _normalize_observations(
    request: FactorAnalysisRequest,
    observations: Iterable[CrossSectionalObservation],
) -> tuple[CrossSectionalObservation, ...]:
    by_identity: dict[tuple[str, str, str, str | None], CrossSectionalObservation] = {}
    for observation in observations:
        if observation.factor_name != request.factor_name:
            raise FactorAnalysisError("observation factor does not match request")
        if (
            observation.universe_scope != request.universe_scope
            or observation.evaluation_scope != request.evaluation_scope
        ):
            raise FactorAnalysisError("observation scope does not match request scope")
        if not request.start_date <= observation.trade_date <= request.end_date:
            raise FactorAnalysisError("observation date is outside request range")
        previous = by_identity.get(observation.identity_key)
        if previous is not None and previous != observation:
            raise FactorAnalysisError("conflicting observation for canonical identity")
        by_identity[observation.identity_key] = observation
    return tuple(
        sorted(
            by_identity.values(),
            key=lambda item: (
                item.trade_date,
                item.security_code,
                item.factor_name,
                item.outcome_identity or "",
                item.recommendation_id or "",
            ),
        )
    )


def _evaluate_period(
    period: str,
    observations: Sequence[CrossSectionalObservation],
    request: FactorAnalysisRequest,
) -> FactorPeriodMetrics:
    by_date: dict[str, list[CrossSectionalObservation]] = defaultdict(list)
    for observation in observations:
        by_date[observation.trade_date].append(observation)

    daily: list[DailyFactorMetrics] = []
    dated_factors: list[_DatedValues] = []
    for trade_date, rows in sorted(by_date.items()):
        valid = [
            item
            for item in rows
            if item.factor_value is not None and item.future_return_pct is not None
        ]
        coverage = len(valid) / len(rows) if rows else 0.0
        skip_reasons: list[str] = []
        pearson: float | None = None
        rank_ic: float | None = None
        quantile_returns: tuple[float | None, ...] = (None,) * request.quantiles
        quantile_counts: tuple[int, ...] = (0,) * request.quantiles
        long_short: float | None = None
        if len(valid) < request.min_cross_section_size:
            skip_reasons.append("insufficient_cross_section")
        else:
            factors = [float(item.factor_value) for item in valid]
            returns = [float(item.future_return_pct) for item in valid]
            if _has_zero_variance(factors):
                skip_reasons.append("zero_variance_factor")
            elif _has_zero_variance(returns):
                skip_reasons.append("zero_variance_return")
            else:
                pearson = _pearson(factors, returns)
                rank_ic = _spearman(factors, returns)
                quantile_returns, quantile_counts = _daily_quantiles(
                    valid, request.quantiles
                )
                if quantile_returns[0] is not None and quantile_returns[-1] is not None:
                    long_short = quantile_returns[-1] - quantile_returns[0]
        daily.append(
            DailyFactorMetrics(
                trade_date=trade_date,
                universe_count=len(rows),
                valid_count=len(valid),
                coverage=coverage,
                pearson_ic=pearson,
                rank_ic=rank_ic,
                quantile_returns_pct=quantile_returns,
                quantile_counts=quantile_counts,
                long_short_return_pct=long_short,
                skip_reasons=tuple(skip_reasons),
            )
        )
        dated_factors.append(
            _DatedValues(
                trade_date,
                {
                    item.security_code: float(item.factor_value)
                    for item in rows
                    if item.factor_value is not None
                },
            )
        )

    quantiles = tuple(
        QuantileMetrics(
            quantile=index + 1,
            average_return_pct=_mean_defined(
                item.quantile_returns_pct[index] for item in daily
            ),
            observation_count=sum(item.quantile_counts[index] for item in daily),
            date_count=sum(
                item.quantile_returns_pct[index] is not None for item in daily
            ),
        )
        for index in range(request.quantiles)
    )
    quantile_returns = [item.average_return_pct for item in quantiles]
    monotonicity = (
        _spearman(
            list(range(1, request.quantiles + 1)),
            [float(item) for item in quantile_returns],
        )
        if all(item is not None for item in quantile_returns)
        and not _has_zero_variance([float(item) for item in quantile_returns])
        else None
    )
    autocorrelations: list[float] = []
    for previous, current in zip(dated_factors, dated_factors[1:]):
        common = sorted(previous.factor_by_security.keys() & current.factor_by_security.keys())
        if len(common) < 2:
            continue
        before = [previous.factor_by_security[security] for security in common]
        after = [current.factor_by_security[security] for security in common]
        if _has_zero_variance(before) or _has_zero_variance(after):
            continue
        autocorrelations.append(_pearson(before, after))

    counts = Counter(reason for item in daily for reason in item.skip_reasons)
    total = len(observations)
    valid_total = sum(item.valid_count for item in daily)
    return FactorPeriodMetrics(
        period=period,
        observation_count=total,
        valid_observation_count=valid_total,
        date_count=len(daily),
        coverage=valid_total / total if total else None,
        pearson_ic=_correlation_summary(
            item.pearson_ic for item in daily if item.pearson_ic is not None
        ),
        rank_ic=_correlation_summary(
            item.rank_ic for item in daily if item.rank_ic is not None
        ),
        quantiles=quantiles,
        long_short_return_pct=_mean_defined(
            item.long_short_return_pct for item in daily
        ),
        monotonicity_spearman=monotonicity,
        factor_autocorrelation=_correlation_summary(autocorrelations),
        daily=tuple(daily),
        skip_reason_counts=tuple(sorted(counts.items())),
    )


def _daily_quantiles(
    observations: Sequence[CrossSectionalObservation], quantiles: int
) -> tuple[tuple[float | None, ...], tuple[int, ...]]:
    ordered = sorted(
        observations,
        key=lambda item: (float(item.factor_value), item.security_code),
    )
    buckets: list[list[float]] = [[] for _ in range(quantiles)]
    size = len(ordered)
    for index, item in enumerate(ordered):
        bucket = min(quantiles - 1, index * quantiles // size)
        buckets[bucket].append(float(item.future_return_pct))
    return (
        tuple(statistics.fmean(bucket) if bucket else None for bucket in buckets),
        tuple(len(bucket) for bucket in buckets),
    )


def _correlation_summary(values: Iterable[float]) -> CorrelationSummary:
    normalized = tuple(float(item) for item in values)
    if not normalized:
        return CorrelationSummary(0, None, None, None, None)
    mean = statistics.fmean(normalized)
    std = statistics.stdev(normalized) if len(normalized) >= 2 else None
    return CorrelationSummary(
        count=len(normalized),
        mean=mean,
        std=std,
        icir=(mean / std) if std not in (None, 0.0) else None,
        positive_ratio=sum(item > 0 for item in normalized) / len(normalized),
    )


def _equal_weighted_monthly(
    monthly: Sequence[FactorPeriodMetrics], quantiles: int
) -> FactorEqualWeightedMetrics:
    return FactorEqualWeightedMetrics(
        month_count=len(monthly),
        pearson_ic_mean=_mean_defined(item.pearson_ic.mean for item in monthly),
        rank_ic_mean=_mean_defined(item.rank_ic.mean for item in monthly),
        quantile_returns_pct=tuple(
            _mean_defined(item.quantiles[index].average_return_pct for item in monthly)
            for index in range(quantiles)
        ),
        long_short_return_pct=_mean_defined(
            item.long_short_return_pct for item in monthly
        ),
        monotonicity_spearman=_mean_defined(
            item.monotonicity_spearman for item in monthly
        ),
        factor_autocorrelation_mean=_mean_defined(
            item.factor_autocorrelation.mean for item in monthly
        ),
    )


def _diagnostics(
    observations: Sequence[CrossSectionalObservation],
    overall: FactorPeriodMetrics,
    monthly: Sequence[FactorPeriodMetrics],
) -> FactorEvidenceDiagnostics:
    by_day = Counter(item.trade_date for item in observations)
    by_month = Counter(item.trade_date[:7] for item in observations)
    total = len(observations)
    max_day_share = max(by_day.values()) / total if total else None
    max_month_share = max(by_month.values()) / total if total else None
    reasons: list[str] = []
    if overall.pearson_ic.count < 3:
        reasons.append("insufficient_independent_days")
    if max_day_share is not None and max_day_share > 0.40:
        reasons.append("date_concentration")
    if len(monthly) < 2:
        reasons.append("insufficient_months")
    monthly_directions = {
        1 if item.long_short_return_pct > 0 else -1
        for item in monthly
        if item.long_short_return_pct not in (None, 0.0)
    }
    if len(monthly_directions) > 1:
        reasons.append("monthly_direction_unstable")
    status = "insufficient" if any(
        item in reasons
        for item in (
            "insufficient_independent_days",
            "date_concentration",
            "insufficient_months",
        )
    ) else "unstable" if reasons else "sufficient"
    return FactorEvidenceDiagnostics(
        evidence_status=status,
        reasons=tuple(reasons),
        observation_count=total,
        valid_observation_count=overall.valid_observation_count,
        independent_days=overall.pearson_ic.count,
        independent_months=len(monthly),
        max_day_observation_share=max_day_share,
        max_month_observation_share=max_month_share,
    )


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise FactorAnalysisError("correlation requires equal vectors with two values")
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean) for x, y in zip(left, right)
    )
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left)
        * sum((y - right_mean) ** 2 for y in right)
    )
    if denominator == 0:
        raise FactorAnalysisError("correlation is undefined for zero variance")
    return numerator / denominator


def _spearman(left: Sequence[float], right: Sequence[float]) -> float:
    return _pearson(_average_ranks(left), _average_ranks(right))


def _average_ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average_rank = (start + 1 + end) / 2.0
        for index in range(start, end):
            ranks[ordered[index][0]] = average_rank
        start = end
    return ranks


def _has_zero_variance(values: Sequence[float]) -> bool:
    return len(set(values)) < 2


def _mean_defined(values: Iterable[float | None]) -> float | None:
    normalized = [float(item) for item in values if item is not None]
    return statistics.fmean(normalized) if normalized else None


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FactorAnalysisError(f"{name} must be non-empty")
    return value.strip()


def _parse_date(value: object, name: str) -> date:
    try:
        return date.fromisoformat(_required_text(value, name))
    except ValueError as exc:
        raise FactorAnalysisError(f"{name} must be an ISO date") from exc


def _finite_optional(value: float | None, name: str) -> None:
    if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
        raise FactorAnalysisError(f"{name} must be finite when present")


__all__ = [
    "FACTOR_ANALYSIS_SCHEMA_VERSION",
    "CorrelationSummary",
    "CrossSectionalObservation",
    "DailyFactorMetrics",
    "FactorAnalysisError",
    "FactorAnalysisRequest",
    "FactorAnalysisResult",
    "FactorEqualWeightedMetrics",
    "FactorEvidenceDiagnostics",
    "FactorPeriodMetrics",
    "QuantileMetrics",
    "evaluate_factor_cross_section",
]
