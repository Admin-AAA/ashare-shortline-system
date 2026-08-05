"""Pure release-governance domain contracts for S3 (Layer 1).

The module deliberately owns no ORM table, session, scheduler, network client, or
production projection.  It reuses the existing canonical fingerprint primitive,
``RecommendationEvidenceIdentity`` and the canonical LIVE selector.  Persistence
adapters may project these immutable values later; they must not duplicate the
eligibility rules implemented in :mod:`app.services.evidence.selectors`.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Iterable, Sequence

from app.engine.canonical import CanonicalValue, canonical_fingerprint
from app.services.evidence.identity import (
    EvidenceClass,
    RecommendationEvidenceIdentity,
    RunSource,
)
from app.services.evidence.selectors import is_live_forward_run


RELEASE_GOVERNANCE_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")
_UNKNOWN_SEGMENT = "UNKNOWN"


class ReleaseGovernanceError(ValueError):
    """Stable fail-closed domain error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ProfileStatus(StrEnum):
    READY = "ready"
    INSUFFICIENT_SAMPLE = "insufficient_sample"


class ConfidenceStatus(StrEnum):
    OBSERVED = "observed"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    UNVERIFIED = "unverified"


class LedgerRecordType(StrEnum):
    RECOMMENDATION = "recommendation"
    OUTCOME = "outcome"


class PopulationScope(StrEnum):
    FULL_POPULATION = "full_population"
    SINGLE_POSITIVE_SLICE = "single_positive_slice"


class ChampionState(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    HISTORICALLY_QUALIFIED = "HISTORICALLY_QUALIFIED"
    FORWARD_CANDIDATE = "FORWARD_CANDIDATE"
    PRODUCTION_CHAMPION = "PRODUCTION_CHAMPION"
    STALE_UNVERIFIED = "STALE_UNVERIFIED"


class ChampionReason(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED_CURRENT_POLICY = "UNVERIFIED_CURRENT_POLICY"
    STALE_IDENTITY = "STALE_IDENTITY"
    NON_FORWARD_EVIDENCE_ONLY = "NON_FORWARD_EVIDENCE_ONLY"
    SINGLE_POSITIVE_SLICE_FORBIDDEN = "SINGLE_POSITIVE_SLICE_FORBIDDEN"
    EVIDENCE_GATES_FAILED = "EVIDENCE_GATES_FAILED"


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ReleaseGovernanceError(
            "INVALID_IDENTIFIER", f"{field} must be a canonical identifier"
        )
    return value


def _fingerprint(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value.lower()):
        raise ReleaseGovernanceError(
            "INVALID_FINGERPRINT", f"{field} must be a SHA-256 digest"
        )
    return value.lower()


def _iso_date(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ReleaseGovernanceError("INVALID_DATE", f"{field} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ReleaseGovernanceError(
            "INVALID_DATE", f"{field} must be an ISO date"
        ) from exc
    if parsed.isoformat() != value:
        raise ReleaseGovernanceError("INVALID_DATE", f"{field} must be an ISO date")
    return value


def _ordered_dates(values: Iterable[str], field: str) -> tuple[str, ...]:
    normalized = tuple(sorted({_iso_date(item, field) for item in values}))
    return normalized


@dataclass(frozen=True, slots=True)
class ActiveStrategy(CanonicalValue):
    """One enabled, versioned strategy contract from the strategy registry."""

    strategy_id: str
    strategy_version: str
    strategy_policy_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_id", _required(self.strategy_id, "strategy_id"))
        object.__setattr__(
            self, "strategy_version", _required(self.strategy_version, "strategy_version")
        )
        object.__setattr__(
            self,
            "strategy_policy_fingerprint",
            _fingerprint(
                self.strategy_policy_fingerprint, "strategy_policy_fingerprint"
            ),
        )


@dataclass(frozen=True, slots=True)
class CanonicalProfileContext(CanonicalValue):
    """Shared immutable facts used to build one profile per active strategy."""

    source_snapshot_id: str
    snapshot_schema_version: str
    execution_policy_fingerprint: str
    window_start: str
    window_end: str
    actual_signal_dates: tuple[str, ...]
    code_revision: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_snapshot_id", _required(self.source_snapshot_id, "source_snapshot_id")
        )
        object.__setattr__(
            self,
            "snapshot_schema_version",
            _required(self.snapshot_schema_version, "snapshot_schema_version"),
        )
        object.__setattr__(
            self,
            "execution_policy_fingerprint",
            _fingerprint(
                self.execution_policy_fingerprint, "execution_policy_fingerprint"
            ),
        )
        start = _iso_date(self.window_start, "window_start")
        end = _iso_date(self.window_end, "window_end")
        if start > end:
            raise ReleaseGovernanceError(
                "INVALID_WINDOW", "window_start must not follow window_end"
            )
        dates = _ordered_dates(self.actual_signal_dates, "actual_signal_dates")
        if any(item < start or item > end for item in dates):
            raise ReleaseGovernanceError(
                "DATE_OUTSIDE_WINDOW", "actual signal dates must be inside the window"
            )
        object.__setattr__(self, "window_start", start)
        object.__setattr__(self, "window_end", end)
        object.__setattr__(self, "actual_signal_dates", dates)
        object.__setattr__(self, "code_revision", _required(self.code_revision, "code_revision"))


@dataclass(frozen=True, slots=True)
class CanonicalProfileIdentity(CanonicalValue):
    """Exact reproducibility identity for a current strategy profile."""

    strategy_id: str
    strategy_version: str
    strategy_policy_fingerprint: str
    source_snapshot_id: str
    snapshot_schema_version: str
    execution_policy_fingerprint: str
    window_start: str
    window_end: str
    actual_signal_dates: tuple[str, ...]
    code_revision: str
    schema_version: int = RELEASE_GOVERNANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Reuse the two validated source contracts rather than maintain a second
        # validation/canonicalization path for profile identity components.
        strategy = ActiveStrategy(
            self.strategy_id,
            self.strategy_version,
            self.strategy_policy_fingerprint,
        )
        context = CanonicalProfileContext(
            self.source_snapshot_id,
            self.snapshot_schema_version,
            self.execution_policy_fingerprint,
            self.window_start,
            self.window_end,
            self.actual_signal_dates,
            self.code_revision,
        )
        if self.schema_version != RELEASE_GOVERNANCE_SCHEMA_VERSION:
            raise ReleaseGovernanceError(
                "UNSUPPORTED_SCHEMA", "unsupported release governance schema"
            )
        for name in (
            "strategy_id",
            "strategy_version",
            "strategy_policy_fingerprint",
        ):
            object.__setattr__(self, name, getattr(strategy, name))
        for name in (
            "source_snapshot_id",
            "snapshot_schema_version",
            "execution_policy_fingerprint",
            "window_start",
            "window_end",
            "actual_signal_dates",
            "code_revision",
        ):
            object.__setattr__(self, name, getattr(context, name))

    @classmethod
    def from_strategy(
        cls, strategy: ActiveStrategy, context: CanonicalProfileContext
    ) -> "CanonicalProfileIdentity":
        if not isinstance(strategy, ActiveStrategy) or not isinstance(
            context, CanonicalProfileContext
        ):
            raise ReleaseGovernanceError(
                "INVALID_PROFILE_INPUT", "validated strategy and context are required"
            )
        return cls(
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.strategy_version,
            strategy_policy_fingerprint=strategy.strategy_policy_fingerprint,
            source_snapshot_id=context.source_snapshot_id,
            snapshot_schema_version=context.snapshot_schema_version,
            execution_policy_fingerprint=context.execution_policy_fingerprint,
            window_start=context.window_start,
            window_end=context.window_end,
            actual_signal_dates=context.actual_signal_dates,
            code_revision=context.code_revision,
        )

    @property
    def identity(self) -> str:
        return self.fingerprint()

    def current_dimensions(self) -> tuple[str, ...]:
        """Dimensions whose drift makes a champion stale (Requirement 4.5)."""

        return (
            self.strategy_id,
            self.strategy_version,
            self.strategy_policy_fingerprint,
            self.source_snapshot_id,
            self.snapshot_schema_version,
            self.execution_policy_fingerprint,
            self.code_revision,
        )


@dataclass(frozen=True, slots=True)
class CompleteProvenance(CanonicalValue):
    """S3 projection of S1 evidence identity plus Trade_Date and Run_Source."""

    evidence_identity: RecommendationEvidenceIdentity
    trade_date: str
    run_source: RunSource

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_identity, RecommendationEvidenceIdentity):
            raise ReleaseGovernanceError(
                "INCOMPLETE_PROVENANCE",
                "evidence_identity must be RecommendationEvidenceIdentity",
            )
        if not isinstance(self.run_source, RunSource):
            raise ReleaseGovernanceError(
                "INCOMPLETE_PROVENANCE", "run_source must be a trusted RunSource"
            )
        object.__setattr__(self, "trade_date", _iso_date(self.trade_date, "trade_date"))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "evidence_identity": self.evidence_identity.canonical_payload(),
            "run_source": self.run_source.value,
            "trade_date": self.trade_date,
        }

    @property
    def is_complete_live_forward(self) -> bool:
        identity = self.evidence_identity
        selector_row = {
            "evidence_class": identity.evidence_class.value,
            "actual_generated_at": identity.actual_generated_at,
            "data_as_of": identity.data_as_of,
            # The canonical run selector calls Signal_Date ``trade_date`` on the
            # existing RecommendationRun projection.  Preserve that adapter
            # mapping here and retain the separate business Trade_Date above.
            "trade_date": identity.signal_date,
            "source_snapshot_id": identity.source_snapshot_id,
            "evidence_identity_hash": identity.identity_hash,
            "strategy_policy_fingerprint": identity.strategy_policy_fingerprint,
            "execution_policy_fingerprint": identity.execution_policy_fingerprint,
            "code_revision": identity.code_revision,
            "provenance_schema_version": identity.provenance_schema_version,
        }
        return (
            self.run_source is RunSource.FORMAL
            and identity.evidence_class is EvidenceClass.LIVE_FORWARD
            and is_live_forward_run(selector_row)
        )


@dataclass(frozen=True, slots=True)
class EvidenceObservation(CanonicalValue):
    """One population member used by profiles, ledger, and evidence packages."""

    sample_id: str
    record_type: LedgerRecordType
    evidence_class: EvidenceClass
    strategy_id: str
    strategy_version: str
    strategy_policy_fingerprint: str
    source_snapshot_id: str
    snapshot_schema_version: str
    execution_policy_fingerprint: str
    code_revision: str
    signal_date: str
    trade_date: str
    board: str | None = None
    market_regime: str | None = None
    score: float | None = None
    net_return_pct: float | None = None
    provenance: CompleteProvenance | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.record_type, LedgerRecordType):
            try:
                object.__setattr__(self, "record_type", LedgerRecordType(self.record_type))
            except (TypeError, ValueError) as exc:
                raise ReleaseGovernanceError(
                    "INVALID_RECORD_TYPE", "record_type must be recommendation or outcome"
                ) from exc
        if not isinstance(self.evidence_class, EvidenceClass):
            try:
                object.__setattr__(self, "evidence_class", EvidenceClass(self.evidence_class))
            except (TypeError, ValueError) as exc:
                raise ReleaseGovernanceError(
                    "INVALID_EVIDENCE_CLASS", "unknown evidence class"
                ) from exc
        for name in (
            "sample_id",
            "strategy_id",
            "strategy_version",
            "source_snapshot_id",
            "snapshot_schema_version",
            "code_revision",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        for name in (
            "strategy_policy_fingerprint",
            "execution_policy_fingerprint",
        ):
            object.__setattr__(self, name, _fingerprint(getattr(self, name), name))
        object.__setattr__(self, "signal_date", _iso_date(self.signal_date, "signal_date"))
        object.__setattr__(self, "trade_date", _iso_date(self.trade_date, "trade_date"))
        for name in ("board", "market_regime"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ReleaseGovernanceError(
                    "INVALID_SEGMENT", f"{name} must be non-empty when supplied"
                )
        if self.score is not None:
            if (
                isinstance(self.score, bool)
                or not isinstance(self.score, (int, float))
                or not math.isfinite(float(self.score))
                or not 0 <= float(self.score) <= 100
            ):
                raise ReleaseGovernanceError(
                    "INVALID_SCORE", "score must be finite and between 0 and 100"
                )
            object.__setattr__(self, "score", float(self.score))
        if self.net_return_pct is not None:
            if (
                isinstance(self.net_return_pct, bool)
                or not isinstance(self.net_return_pct, (int, float))
                or not math.isfinite(float(self.net_return_pct))
            ):
                raise ReleaseGovernanceError(
                    "INVALID_RETURN", "net_return_pct must be finite"
                )
            object.__setattr__(self, "net_return_pct", float(self.net_return_pct))
        if self.provenance is not None:
            if not isinstance(self.provenance, CompleteProvenance):
                raise ReleaseGovernanceError(
                    "INCOMPLETE_PROVENANCE", "provenance has an invalid type"
                )
            identity = self.provenance.evidence_identity
            expected = (
                identity.evidence_class,
                identity.signal_date,
                self.provenance.trade_date,
                identity.source_snapshot_id,
                identity.strategy_policy_fingerprint,
                identity.execution_policy_fingerprint,
                identity.code_revision,
            )
            observed = (
                self.evidence_class,
                self.signal_date,
                self.trade_date,
                self.source_snapshot_id,
                self.strategy_policy_fingerprint,
                self.execution_policy_fingerprint,
                self.code_revision,
            )
            if expected != observed:
                raise ReleaseGovernanceError(
                    "PROVENANCE_IDENTITY_MISMATCH",
                    "observation fields must match complete provenance",
                )

    @property
    def is_complete_live_forward(self) -> bool:
        return bool(
            self.evidence_class is EvidenceClass.LIVE_FORWARD
            and self.provenance is not None
            and self.provenance.is_complete_live_forward
        )

    def matches_profile(self, identity: CanonicalProfileIdentity) -> bool:
        return (
            self.strategy_id == identity.strategy_id
            and self.strategy_version == identity.strategy_version
            and self.strategy_policy_fingerprint
            == identity.strategy_policy_fingerprint
            and self.source_snapshot_id == identity.source_snapshot_id
            and self.snapshot_schema_version == identity.snapshot_schema_version
            and self.execution_policy_fingerprint
            == identity.execution_policy_fingerprint
            and self.code_revision == identity.code_revision
            and self.signal_date in identity.actual_signal_dates
        )


@dataclass(frozen=True, slots=True)
class PopulationMetrics(CanonicalValue):
    n: int
    positive_count: int
    non_positive_count: int
    unresolved_count: int
    win_rate: float | None
    mean_net_return_pct: float | None


@dataclass(frozen=True, slots=True)
class SegmentMetric(CanonicalValue):
    segment: str
    n: int
    positive_count: int
    non_positive_count: int
    unresolved_count: int
    mean_net_return_pct: float | None


@dataclass(frozen=True, slots=True)
class SegmentAxis(CanonicalValue):
    axis: str
    segments: tuple[SegmentMetric, ...]

    @property
    def total(self) -> int:
        return sum(item.n for item in self.segments)


@dataclass(frozen=True, slots=True)
class CanonicalProfile(CanonicalValue):
    profile_identity: CanonicalProfileIdentity
    profile_id: str
    status: ProfileStatus
    n: int
    distinct_signal_days: int
    coverage: float
    confidence_status: ConfidenceStatus
    performance: PopulationMetrics | None
    strata: tuple[SegmentAxis, ...]

    def __post_init__(self) -> None:
        if self.profile_id != self.profile_identity.identity:
            raise ReleaseGovernanceError(
                "PROFILE_IDENTITY_MISMATCH", "profile_id must be canonical"
            )
        if self.n == 0:
            if self.status is not ProfileStatus.INSUFFICIENT_SAMPLE:
                raise ReleaseGovernanceError(
                    "ZERO_SAMPLE_MISREPRESENTED", "zero-trade profile is insufficient"
                )
            if self.performance is not None:
                raise ReleaseGovernanceError(
                    "FABRICATED_ZERO_PERFORMANCE",
                    "zero-trade profile must not expose performance",
                )


@dataclass(frozen=True, slots=True)
class TrueForwardLedger(CanonicalValue):
    """Immutable append-only ledger value.

    ``append`` returns a new ledger.  Existing entries can neither be updated nor
    deleted.  Re-appending the exact same immutable sample is idempotent; using an
    existing sample id with changed content is rejected.
    """

    entries: tuple[EvidenceObservation, ...] = ()

    def __post_init__(self) -> None:
        seen: dict[str, EvidenceObservation] = {}
        for entry in self.entries:
            self._validate_entry(entry)
            previous = seen.get(entry.sample_id)
            if previous is not None:
                raise ReleaseGovernanceError(
                    "DUPLICATE_LEDGER_ID", "ledger sample ids must be unique"
                )
            seen[entry.sample_id] = entry

    @staticmethod
    def _validate_entry(entry: EvidenceObservation) -> None:
        if not isinstance(entry, EvidenceObservation):
            raise ReleaseGovernanceError(
                "INVALID_LEDGER_ENTRY", "ledger entries must be evidence observations"
            )
        if not entry.is_complete_live_forward:
            raise ReleaseGovernanceError(
                "LIVE_FORWARD_PROVENANCE_REQUIRED",
                "ledger accepts only provenance-complete LIVE_FORWARD records",
            )

    def append(self, entry: EvidenceObservation) -> "TrueForwardLedger":
        self._validate_entry(entry)
        for existing in self.entries:
            if existing.sample_id == entry.sample_id:
                if existing == entry:
                    return self
                raise ReleaseGovernanceError(
                    "APPEND_ONLY_CONFLICT",
                    "an existing ledger entry cannot be overwritten",
                )
        return TrueForwardLedger((*self.entries, entry))

    def append_many(
        self, entries: Iterable[EvidenceObservation]
    ) -> "TrueForwardLedger":
        ledger = self
        for entry in entries:
            ledger = ledger.append(entry)
        return ledger

    @property
    def ledger_id(self) -> str:
        return canonical_fingerprint(
            {"schema_version": RELEASE_GOVERNANCE_SCHEMA_VERSION, "entries": self.entries}
        )


@dataclass(frozen=True, slots=True)
class EvidencePackage(CanonicalValue):
    package_id: str
    profile_identity: CanonicalProfileIdentity
    population_scope: PopulationScope
    population_member_fingerprints: tuple[str, ...]
    population_signal_dates: tuple[str, ...]
    expected_signal_dates: tuple[str, ...]
    n: int
    distinct_signal_days: int
    coverage: float
    confidence_status: ConfidenceStatus
    failed_gates: tuple[str, ...]
    metrics: PopulationMetrics
    strata: tuple[SegmentAxis, ...]
    evidence_class_counts: tuple[tuple[str, int], ...]
    clean_forward_n: int

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.package_id):
            raise ReleaseGovernanceError(
                "INVALID_PACKAGE_ID", "package_id must be a SHA-256 digest"
            )
        if self.n != len(self.population_member_fingerprints):
            raise ReleaseGovernanceError(
                "POPULATION_ACCOUNTING_MISMATCH", "n must equal the frozen population"
            )
        if tuple(sorted(set(self.population_signal_dates))) != self.population_signal_dates:
            raise ReleaseGovernanceError(
                "POPULATION_ACCOUNTING_MISMATCH",
                "population signal dates must be unique and ordered",
            )
        if tuple(sorted(set(self.expected_signal_dates))) != self.expected_signal_dates:
            raise ReleaseGovernanceError(
                "POPULATION_ACCOUNTING_MISMATCH",
                "expected signal dates must be unique and ordered",
            )
        if not set(self.population_signal_dates) <= set(self.expected_signal_dates):
            raise ReleaseGovernanceError(
                "POPULATION_OUTSIDE_WINDOW",
                "population signal dates must be inside the frozen actual window",
            )
        if self.distinct_signal_days != len(self.population_signal_dates):
            raise ReleaseGovernanceError(
                "POPULATION_ACCOUNTING_MISMATCH",
                "distinct signal days must match the population dates",
            )
        covered_days = len(
            set(self.population_signal_dates) & set(self.expected_signal_dates)
        )
        expected_coverage = (
            covered_days / len(self.expected_signal_dates)
            if self.expected_signal_dates
            else 0.0
        )
        if self.coverage != expected_coverage:
            raise ReleaseGovernanceError(
                "POPULATION_ACCOUNTING_MISMATCH",
                "coverage must match covered divided by expected signal days",
            )
        required_axes = {"board", "market_regime", "strategy", "score_bucket", "month"}
        actual_axes = {axis.axis for axis in self.strata}
        if actual_axes != required_axes or len(actual_axes) != len(self.strata):
            raise ReleaseGovernanceError(
                "SEGMENT_AXES_INCOMPLETE", "all five unique segment axes are required"
            )
        if any(axis.total != self.n for axis in self.strata):
            raise ReleaseGovernanceError(
                "SEGMENT_ACCOUNTING_MISMATCH",
                "each segment axis must account for the complete population",
            )
        if sum(count for _, count in self.evidence_class_counts) != self.n:
            raise ReleaseGovernanceError(
                "CLASS_ACCOUNTING_MISMATCH",
                "evidence class counts must account for the complete population",
            )
        if self.metrics.n != self.n or not 0 <= self.clean_forward_n <= self.n:
            raise ReleaseGovernanceError(
                "POPULATION_ACCOUNTING_MISMATCH", "package metrics are inconsistent"
            )
        if self.n == 0 and self.confidence_status is not ConfidenceStatus.INSUFFICIENT_SAMPLE:
            raise ReleaseGovernanceError(
                "ZERO_SAMPLE_MISREPRESENTED", "zero sample package is insufficient"
            )

    def segment_total(self, axis: str) -> int:
        return next(item.total for item in self.strata if item.axis == axis)


@dataclass(frozen=True, slots=True)
class ChampionRecord(CanonicalValue):
    identity: CanonicalProfileIdentity
    state: ChampionState
    evidence_package_id: str

    def __post_init__(self) -> None:
        _fingerprint(self.evidence_package_id, "evidence_package_id")


@dataclass(frozen=True, slots=True)
class ChampionDecision(CanonicalValue):
    state: ChampionState
    reason_code: ChampionReason
    current_identity: CanonicalProfileIdentity
    evidence_package_id: str
    clean_forward_n: int


def _metrics(population: Sequence[EvidenceObservation]) -> PopulationMetrics:
    resolved = [item.net_return_pct for item in population if item.net_return_pct is not None]
    positive = sum(value > 0 for value in resolved)
    non_positive = len(resolved) - positive
    return PopulationMetrics(
        n=len(population),
        positive_count=positive,
        non_positive_count=non_positive,
        unresolved_count=len(population) - len(resolved),
        win_rate=(positive / len(resolved) if resolved else None),
        mean_net_return_pct=(sum(resolved) / len(resolved) if resolved else None),
    )


def _score_bucket(score: float | None) -> str:
    if score is None:
        return _UNKNOWN_SEGMENT
    if score < 60:
        return "00-59"
    if score < 70:
        return "60-69"
    if score < 80:
        return "70-79"
    if score < 90:
        return "80-89"
    return "90-100"


def _segment_key(item: EvidenceObservation, axis: str) -> str:
    if axis == "board":
        return item.board or _UNKNOWN_SEGMENT
    if axis == "market_regime":
        return item.market_regime or _UNKNOWN_SEGMENT
    if axis == "strategy":
        return item.strategy_id
    if axis == "score_bucket":
        return _score_bucket(item.score)
    if axis == "month":
        return item.signal_date[:7]
    raise AssertionError(axis)


def _strata(population: Sequence[EvidenceObservation]) -> tuple[SegmentAxis, ...]:
    result = []
    for axis in ("board", "market_regime", "strategy", "score_bucket", "month"):
        grouped: dict[str, list[EvidenceObservation]] = defaultdict(list)
        for item in population:
            grouped[_segment_key(item, axis)].append(item)
        segments = []
        for key in sorted(grouped):
            summary = _metrics(grouped[key])
            segments.append(
                SegmentMetric(
                    segment=key,
                    n=summary.n,
                    positive_count=summary.positive_count,
                    non_positive_count=summary.non_positive_count,
                    unresolved_count=summary.unresolved_count,
                    mean_net_return_pct=summary.mean_net_return_pct,
                )
            )
        result.append(SegmentAxis(axis, tuple(segments)))
    return tuple(result)


def _coverage(
    population: Sequence[EvidenceObservation], actual_signal_dates: Sequence[str]
) -> tuple[int, float]:
    represented = {item.signal_date for item in population}
    denominator = set(actual_signal_dates)
    distinct = len(represented)
    if not denominator:
        return distinct, 0.0
    covered = len(represented & denominator)
    return distinct, covered / len(denominator)


def build_canonical_profiles(
    active_strategies: Sequence[ActiveStrategy],
    population: Sequence[EvidenceObservation],
    context: CanonicalProfileContext,
) -> tuple[CanonicalProfile, ...]:
    """Build exactly one independently identified truthful profile per strategy."""

    strategy_ids = [item.strategy_id for item in active_strategies]
    if len(strategy_ids) != len(set(strategy_ids)):
        raise ReleaseGovernanceError(
            "DUPLICATE_ACTIVE_STRATEGY",
            "each active strategy id must map to exactly one active version",
        )
    profiles = []
    for strategy in sorted(active_strategies, key=lambda item: item.strategy_id):
        identity = CanonicalProfileIdentity.from_strategy(strategy, context)
        members = tuple(item for item in population if item.matches_profile(identity))
        distinct, coverage = _coverage(members, identity.actual_signal_dates)
        n = len(members)
        profiles.append(
            CanonicalProfile(
                profile_identity=identity,
                profile_id=identity.identity,
                status=(
                    ProfileStatus.INSUFFICIENT_SAMPLE
                    if n == 0
                    else ProfileStatus.READY
                ),
                n=n,
                distinct_signal_days=distinct,
                coverage=coverage,
                confidence_status=(
                    ConfidenceStatus.INSUFFICIENT_SAMPLE
                    if n == 0
                    else ConfidenceStatus.OBSERVED
                ),
                performance=None if n == 0 else _metrics(members),
                strata=_strata(members),
            )
        )
    return tuple(profiles)


def build_evidence_package(
    profile_identity: CanonicalProfileIdentity,
    population: Sequence[EvidenceObservation],
    *,
    confidence_status: ConfidenceStatus = ConfidenceStatus.OBSERVED,
    failed_gates: Iterable[str] = (),
    population_scope: PopulationScope = PopulationScope.FULL_POPULATION,
    expected_signal_dates: Sequence[str] | None = None,
) -> EvidencePackage:
    """Freeze a complete, stratified, internally balanced evidence population."""

    if not isinstance(profile_identity, CanonicalProfileIdentity):
        raise ReleaseGovernanceError(
            "INVALID_PROFILE_IDENTITY", "a canonical profile identity is required"
        )
    if not isinstance(confidence_status, ConfidenceStatus):
        confidence_status = ConfidenceStatus(confidence_status)
    if not isinstance(population_scope, PopulationScope):
        population_scope = PopulationScope(population_scope)
    members = tuple(population)
    fingerprints = tuple(sorted(item.fingerprint() for item in members))
    if len(fingerprints) != len(set(fingerprints)):
        raise ReleaseGovernanceError(
            "DUPLICATE_POPULATION_MEMBER", "population members must be unique"
        )
    expected = _ordered_dates(
        expected_signal_dates
        if expected_signal_dates is not None
        else profile_identity.actual_signal_dates,
        "expected_signal_dates",
    )
    distinct, coverage = _coverage(members, expected)
    gates = tuple(sorted({_required(item, "failed_gate") for item in failed_gates}))
    if not members:
        confidence_status = ConfidenceStatus.INSUFFICIENT_SAMPLE
        gates = tuple(sorted({*gates, "INSUFFICIENT_SAMPLE"}))
    metrics = _metrics(members)
    strata = _strata(members)
    class_counts = tuple(
        sorted(Counter(item.evidence_class.value for item in members).items())
    )
    clean_forward_n = sum(item.is_complete_live_forward for item in members)
    identity_payload = {
        "schema_version": RELEASE_GOVERNANCE_SCHEMA_VERSION,
        "profile_identity": profile_identity.identity,
        "population_scope": population_scope.value,
        "population_member_fingerprints": fingerprints,
        "expected_signal_dates": expected,
        "confidence_status": confidence_status.value,
        "failed_gates": gates,
    }
    return EvidencePackage(
        package_id=canonical_fingerprint(identity_payload),
        profile_identity=profile_identity,
        population_scope=population_scope,
        population_member_fingerprints=fingerprints,
        population_signal_dates=tuple(sorted({item.signal_date for item in members})),
        expected_signal_dates=expected,
        n=len(members),
        distinct_signal_days=distinct,
        coverage=coverage,
        confidence_status=confidence_status,
        failed_gates=gates,
        metrics=metrics,
        strata=strata,
        evidence_class_counts=class_counts,
        clean_forward_n=clean_forward_n,
    )


def evaluate_champion(
    current_identity: CanonicalProfileIdentity,
    package: EvidencePackage,
    *,
    incumbent: ChampionRecord | None = None,
    requested_state: ChampionState = ChampionState.PRODUCTION_CHAMPION,
) -> ChampionDecision:
    """Evaluate champion state with stale and evidence insufficiency fail-closed."""

    if not isinstance(current_identity, CanonicalProfileIdentity) or not isinstance(
        package, EvidencePackage
    ):
        raise ReleaseGovernanceError(
            "INVALID_CHAMPION_INPUT", "canonical identity and evidence package are required"
        )
    if not isinstance(requested_state, ChampionState):
        requested_state = ChampionState(requested_state)

    package_stale = (
        package.profile_identity.current_dimensions()
        != current_identity.current_dimensions()
    )
    incumbent_stale = bool(
        incumbent is not None
        and incumbent.identity.current_dimensions()
        != current_identity.current_dimensions()
    )
    if package_stale or incumbent_stale:
        return ChampionDecision(
            ChampionState.STALE_UNVERIFIED,
            ChampionReason.STALE_IDENTITY,
            current_identity,
            package.package_id,
            package.clean_forward_n,
        )

    if package.clean_forward_n == 0:
        return ChampionDecision(
            ChampionState.UNVERIFIED,
            ChampionReason.UNVERIFIED_CURRENT_POLICY,
            current_identity,
            package.package_id,
            0,
        )

    if requested_state is ChampionState.PRODUCTION_CHAMPION:
        if package.population_scope is PopulationScope.SINGLE_POSITIVE_SLICE or (
            package.n == 1 and package.metrics.positive_count == 1
        ):
            return ChampionDecision(
                ChampionState.FORWARD_CANDIDATE,
                ChampionReason.SINGLE_POSITIVE_SLICE_FORBIDDEN,
                current_identity,
                package.package_id,
                package.clean_forward_n,
            )
        class_names = {name for name, count in package.evidence_class_counts if count}
        forbidden_only = class_names <= {
            EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
            EvidenceClass.RESEARCH_BACKTEST.value,
        }
        if forbidden_only:
            return ChampionDecision(
                ChampionState.UNVERIFIED,
                ChampionReason.NON_FORWARD_EVIDENCE_ONLY,
                current_identity,
                package.package_id,
                package.clean_forward_n,
            )
        if package.failed_gates:
            return ChampionDecision(
                ChampionState.FORWARD_CANDIDATE,
                ChampionReason.EVIDENCE_GATES_FAILED,
                current_identity,
                package.package_id,
                package.clean_forward_n,
            )

    return ChampionDecision(
        requested_state,
        ChampionReason.VERIFIED,
        current_identity,
        package.package_id,
        package.clean_forward_n,
    )


__all__ = [
    "ActiveStrategy",
    "CanonicalProfile",
    "CanonicalProfileContext",
    "CanonicalProfileIdentity",
    "ChampionDecision",
    "ChampionReason",
    "ChampionRecord",
    "ChampionState",
    "CompleteProvenance",
    "ConfidenceStatus",
    "EvidenceObservation",
    "EvidencePackage",
    "LedgerRecordType",
    "PopulationMetrics",
    "PopulationScope",
    "ProfileStatus",
    "RELEASE_GOVERNANCE_SCHEMA_VERSION",
    "ReleaseGovernanceError",
    "SegmentAxis",
    "SegmentMetric",
    "TrueForwardLedger",
    "build_canonical_profiles",
    "build_evidence_package",
    "evaluate_champion",
]
