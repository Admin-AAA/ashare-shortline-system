"""Offline, content-addressed golden harness for cross-module parity tests.

The harness is deliberately adapter-neutral: historical reconstruction,
research backtest, and formal outcome adapters receive the exact same immutable
``GoldenDataInput``.  Each adapter must project its result into the stable
outcome/position/risk/evidence records defined here before comparison.

This module belongs to the test kit rather than a production execution path. It
reuses the architecture-hardening ``DataSnapshot``/``ArtifactRef`` contracts and
never captures mutable database or provider data while a golden case is run.

The cross-module canonical projection adapters (evidence eligibility, T+1
execution, outcome identity, and min-cap risk decision) emit Layer 1 evidence
records only: every projection carries ``layer="layer_1"`` and no harness output
marks an environment ``ACCEPTED``.
"""
from __future__ import annotations

import json
import socket
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Mapping, Protocol, Sequence, runtime_checkable
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.engine.backtest import (
    ArtifactRef,
    ArtifactScope,
    BarInput,
    CanonicalValue,
    CoverageResult,
    CoverageStatus,
    DataSnapshot,
    DateRange,
    ExecutionPolicy,
    PartitionManifest,
    RecommendationInput,
    SnapshotArtifactUnavailableError,
    canonical_fingerprint,
    canonical_json,
    content_hash,
)
from app.engine.simulation import simulate
from app.services.evidence.identity import (
    EvidenceIdentityBuilder,
    EvidenceIdentityError,
    EvidenceSnapshotIdentity,
    RunSource,
)


PROJECTION_SCHEMA_VERSION = 1
FIXTURE_SCHEMA_VERSION = 1
LAYER_1_EVIDENCE_LAYER = "layer_1"
CROSS_MODULE_PROJECTION_SECTION = "cross_module_projection_golden"
_DECIMAL_QUANTUM = Decimal("0.00000001")
_REQUIRED_PROJECTIONS = ("outcome", "position", "risk", "evidence")
_BEIJING_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
_UNVERIFIED_PROVENANCE_IDENTIFIERS = frozenset({"n/a", "none", "unknown", "unavailable"})


class GoldenHarnessError(RuntimeError):
    """Base error for fixture, adapter, and comparison contract failures."""


class GoldenFixtureError(GoldenHarnessError):
    """A checked-in snapshot/artifact fixture is malformed or inconsistent."""


class GoldenAdapterContractError(GoldenHarnessError):
    """An adapter did not implement the deterministic harness contract."""


class ExternalNetworkAccessError(GoldenHarnessError):
    """A golden adapter attempted socket or DNS access."""


class GoldenMismatchError(GoldenHarnessError):
    """Raised when a caller requires parity but projections differ."""


class AdapterRole(StrEnum):
    HISTORICAL_RECONSTRUCTION = "historical_reconstruction"
    BACKTEST = "backtest"
    FORMAL = "formal"


class GoldenEvidenceClass(StrEnum):
    LIVE_FORWARD = "live_forward"
    HISTORICAL_RECONSTRUCTION = "historical_reconstruction"
    RESEARCH_BACKTEST = "research_backtest"
    EXPERIMENT = "experiment"
    UNVERIFIED = "unverified"


class GoldenRiskDecision(StrEnum):
    ALLOW = "allow"
    REDUCE = "reduce"
    REJECT = "reject"
    HALT = "halt"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GoldenAdapterContractError(f"{field_name} must be a non-empty string")
    return value.strip()


def _iso_date(value: object, field_name: str) -> str:
    text = _required_text(value, field_name)
    try:
        normalized = date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise GoldenAdapterContractError(f"{field_name} must be an ISO date") from exc
    if normalized != text:
        raise GoldenAdapterContractError(f"{field_name} must be a canonical ISO date")
    return normalized


def _utc_datetime(value: object, field_name: str) -> str:
    text = _required_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise GoldenAdapterContractError(f"{field_name} must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        raise GoldenAdapterContractError(f"{field_name} must include a timezone")
    normalized = parsed.astimezone(timezone.utc)
    return normalized.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def decimal_text(value: object, field_name: str = "value") -> str:
    """Return one deterministic, finite eight-decimal comparison representation."""

    if isinstance(value, bool):
        raise GoldenAdapterContractError(f"{field_name} must be numeric")
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation
        number = number.quantize(_DECIMAL_QUANTUM, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise GoldenAdapterContractError(f"{field_name} must be a finite decimal") from exc
    if number == 0:
        number = Decimal(0).quantize(_DECIMAL_QUANTUM)
    return format(number, ".8f")


def _decimal_value(value: str) -> Decimal:
    return Decimal(value)


@dataclass(frozen=True)
class OutcomeProjection(CanonicalValue):
    recommendation_id: str
    code: str
    signal_date: str
    entry_triggered: bool
    entry_date: str | None
    entry_price: str | int | float | Decimal | None
    exit_date: str | None
    exit_price: str | int | float | Decimal | None
    outcome_label: str
    exit_reason: str
    gross_return_pct: str | int | float | Decimal
    net_return_pct: str | int | float | Decimal
    cost_pct: str | int | float | Decimal
    schema_version: int = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "recommendation_id", _required_text(self.recommendation_id, "recommendation_id"))
        object.__setattr__(self, "code", _required_text(self.code, "code"))
        object.__setattr__(self, "signal_date", _iso_date(self.signal_date, "signal_date"))
        if not isinstance(self.entry_triggered, bool):
            raise GoldenAdapterContractError("entry_triggered must be a boolean")
        paired = (self.entry_date, self.entry_price, self.exit_date, self.exit_price)
        if self.entry_triggered:
            if any(value is None for value in paired):
                raise GoldenAdapterContractError("executed outcomes require entry and exit fields")
            entry_date = _iso_date(self.entry_date, "entry_date")
            exit_date = _iso_date(self.exit_date, "exit_date")
            if entry_date < self.signal_date or exit_date < entry_date:
                raise GoldenAdapterContractError("outcome dates must be chronological")
            entry_price = decimal_text(self.entry_price, "entry_price")
            exit_price = decimal_text(self.exit_price, "exit_price")
            if _decimal_value(entry_price) <= 0 or _decimal_value(exit_price) <= 0:
                raise GoldenAdapterContractError("executed prices must be positive")
            object.__setattr__(self, "entry_date", entry_date)
            object.__setattr__(self, "exit_date", exit_date)
            object.__setattr__(self, "entry_price", entry_price)
            object.__setattr__(self, "exit_price", exit_price)
        else:
            if any(value is not None for value in paired):
                raise GoldenAdapterContractError("unexecuted outcomes must omit entry and exit fields")
        object.__setattr__(self, "outcome_label", _required_text(self.outcome_label, "outcome_label"))
        object.__setattr__(self, "exit_reason", _required_text(self.exit_reason, "exit_reason"))
        object.__setattr__(self, "gross_return_pct", decimal_text(self.gross_return_pct, "gross_return_pct"))
        object.__setattr__(self, "net_return_pct", decimal_text(self.net_return_pct, "net_return_pct"))
        object.__setattr__(self, "cost_pct", decimal_text(self.cost_pct, "cost_pct"))
        if _decimal_value(str(self.cost_pct)) < 0:
            raise GoldenAdapterContractError("cost_pct must be non-negative")
        if not self.entry_triggered and any(
            _decimal_value(str(value)) != 0
            for value in (self.gross_return_pct, self.net_return_pct)
        ):
            raise GoldenAdapterContractError("unexecuted outcomes must have zero returns")
        if self.schema_version != PROJECTION_SCHEMA_VERSION:
            raise GoldenAdapterContractError("unsupported outcome projection schema")


@dataclass(frozen=True)
class PositionCapProjection(CanonicalValue):
    name: str
    value: str | int | float | Decimal
    schema_version: int = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, "cap.name"))
        normalized = decimal_text(self.value, f"cap.{self.name}")
        if not 0 <= _decimal_value(normalized) <= 1:
            raise GoldenAdapterContractError("position caps must be in [0, 1]")
        object.__setattr__(self, "value", normalized)
        if self.schema_version != PROJECTION_SCHEMA_VERSION:
            raise GoldenAdapterContractError("unsupported cap projection schema")


@dataclass(frozen=True)
class PositionProjection(CanonicalValue):
    recommendation_id: str
    requested_size: str | int | float | Decimal
    approved_size: str | int | float | Decimal
    final_size: str | int | float | Decimal
    binding_cap: str
    caps: tuple[PositionCapProjection, ...]
    schema_version: int = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "recommendation_id", _required_text(self.recommendation_id, "recommendation_id"))
        requested = decimal_text(self.requested_size, "requested_size")
        approved = decimal_text(self.approved_size, "approved_size")
        final = decimal_text(self.final_size, "final_size")
        if not 0 <= _decimal_value(requested) <= 1:
            raise GoldenAdapterContractError("requested_size must be in [0, 1]")
        if not 0 <= _decimal_value(approved) <= _decimal_value(requested):
            raise GoldenAdapterContractError("approved_size must be in [0, requested_size]")
        if not 0 <= _decimal_value(final) <= _decimal_value(approved):
            raise GoldenAdapterContractError("final_size must be in [0, approved_size]")
        caps = tuple(sorted(tuple(self.caps), key=lambda item: item.name))
        if not all(isinstance(item, PositionCapProjection) for item in caps):
            raise GoldenAdapterContractError("caps must contain PositionCapProjection values")
        names = tuple(item.name for item in caps)
        if len(names) != len(set(names)):
            raise GoldenAdapterContractError("position cap names must be unique")
        binding_cap = _required_text(self.binding_cap, "binding_cap")
        if binding_cap not in names:
            raise GoldenAdapterContractError("binding_cap must name one of the projected caps")
        if caps:
            minimum = min(_decimal_value(str(item.value)) for item in caps)
            if _decimal_value(final) > minimum:
                raise GoldenAdapterContractError("final_size may not exceed any applicable cap")
        object.__setattr__(self, "requested_size", requested)
        object.__setattr__(self, "approved_size", approved)
        object.__setattr__(self, "final_size", final)
        object.__setattr__(self, "binding_cap", binding_cap)
        object.__setattr__(self, "caps", caps)
        if self.schema_version != PROJECTION_SCHEMA_VERSION:
            raise GoldenAdapterContractError("unsupported position projection schema")


@dataclass(frozen=True)
class RiskProjection(CanonicalValue):
    recommendation_id: str
    decision: GoldenRiskDecision | str
    requested_size: str | int | float | Decimal
    approved_size: str | int | float | Decimal
    reason_codes: tuple[str, ...]
    policy_fingerprint: str
    state_fingerprint: str
    schema_version: int = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "recommendation_id", _required_text(self.recommendation_id, "recommendation_id"))
        try:
            decision = self.decision if isinstance(self.decision, GoldenRiskDecision) else GoldenRiskDecision(str(self.decision))
        except ValueError as exc:
            raise GoldenAdapterContractError("risk decision is invalid") from exc
        requested = decimal_text(self.requested_size, "requested_size")
        approved = decimal_text(self.approved_size, "approved_size")
        if not 0 <= _decimal_value(approved) <= _decimal_value(requested) <= 1:
            raise GoldenAdapterContractError("risk sizes must satisfy 0 <= approved <= requested <= 1")
        reasons = tuple(sorted({_required_text(value, "reason_codes[]") for value in self.reason_codes}))
        if decision in {GoldenRiskDecision.REJECT, GoldenRiskDecision.HALT} and _decimal_value(approved) != 0:
            raise GoldenAdapterContractError("reject/halt risk decisions must approve zero size")
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "requested_size", requested)
        object.__setattr__(self, "approved_size", approved)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "policy_fingerprint", _required_hash(self.policy_fingerprint, "policy_fingerprint"))
        object.__setattr__(self, "state_fingerprint", _required_hash(self.state_fingerprint, "state_fingerprint"))
        if self.schema_version != PROJECTION_SCHEMA_VERSION:
            raise GoldenAdapterContractError("unsupported risk projection schema")


@dataclass(frozen=True)
class EvidenceProjection(CanonicalValue):
    recommendation_id: str
    evidence_class: GoldenEvidenceClass | str
    signal_date: str
    actual_generated_at: str
    data_as_of: str
    source_snapshot_id: str
    strategy_policy_fingerprint: str
    execution_policy_fingerprint: str
    code_revision: str
    reconstruction_batch_id: str | None
    is_true_forward: bool
    schema_version: int = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "recommendation_id", _required_text(self.recommendation_id, "recommendation_id"))
        try:
            evidence_class = self.evidence_class if isinstance(self.evidence_class, GoldenEvidenceClass) else GoldenEvidenceClass(str(self.evidence_class))
        except ValueError as exc:
            raise GoldenAdapterContractError("evidence_class is invalid") from exc
        signal_date = _iso_date(self.signal_date, "signal_date")
        data_as_of = _iso_date(self.data_as_of, "data_as_of")
        if data_as_of > signal_date:
            raise GoldenAdapterContractError("data_as_of may not be after signal_date")
        if not isinstance(self.is_true_forward, bool):
            raise GoldenAdapterContractError("is_true_forward must be a boolean")
        if self.is_true_forward != (evidence_class is GoldenEvidenceClass.LIVE_FORWARD):
            raise GoldenAdapterContractError("is_true_forward must agree with evidence_class")
        batch_id = self.reconstruction_batch_id
        if evidence_class is GoldenEvidenceClass.HISTORICAL_RECONSTRUCTION:
            batch_id = _required_text(batch_id, "reconstruction_batch_id")
        elif batch_id is not None:
            batch_id = _required_text(batch_id, "reconstruction_batch_id")
        object.__setattr__(self, "evidence_class", evidence_class)
        object.__setattr__(self, "signal_date", signal_date)
        object.__setattr__(self, "actual_generated_at", _utc_datetime(self.actual_generated_at, "actual_generated_at"))
        object.__setattr__(self, "data_as_of", data_as_of)
        object.__setattr__(self, "source_snapshot_id", _required_text(self.source_snapshot_id, "source_snapshot_id"))
        object.__setattr__(
            self,
            "strategy_policy_fingerprint",
            _required_hash(self.strategy_policy_fingerprint, "strategy_policy_fingerprint"),
        )
        object.__setattr__(
            self,
            "execution_policy_fingerprint",
            _required_hash(self.execution_policy_fingerprint, "execution_policy_fingerprint"),
        )
        object.__setattr__(self, "code_revision", _required_text(self.code_revision, "code_revision"))
        object.__setattr__(self, "reconstruction_batch_id", batch_id)
        if self.schema_version != PROJECTION_SCHEMA_VERSION:
            raise GoldenAdapterContractError("unsupported evidence projection schema")


def _required_hash(value: object, field_name: str) -> str:
    text = _required_text(value, field_name).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise GoldenAdapterContractError(f"{field_name} must be a 64-character SHA-256 hex digest")
    return text


@dataclass(frozen=True)
class GoldenProjectionSet(CanonicalValue):
    outcomes: tuple[OutcomeProjection, ...]
    positions: tuple[PositionProjection, ...]
    risks: tuple[RiskProjection, ...]
    evidence: tuple[EvidenceProjection, ...]
    schema_version: int = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        collections: tuple[tuple[str, tuple[Any, ...], type[Any]], ...] = (
            ("outcomes", tuple(self.outcomes), OutcomeProjection),
            ("positions", tuple(self.positions), PositionProjection),
            ("risks", tuple(self.risks), RiskProjection),
            ("evidence", tuple(self.evidence), EvidenceProjection),
        )
        expected_ids: tuple[str, ...] | None = None
        for field_name, values, expected_type in collections:
            if not all(isinstance(value, expected_type) for value in values):
                raise GoldenAdapterContractError(f"{field_name} contains an invalid projection type")
            ordered = tuple(sorted(values, key=lambda value: value.recommendation_id))
            ids = tuple(value.recommendation_id for value in ordered)
            if len(ids) != len(set(ids)):
                raise GoldenAdapterContractError(f"{field_name} contains duplicate recommendation ids")
            if expected_ids is None:
                expected_ids = ids
            elif ids != expected_ids:
                raise GoldenAdapterContractError("all projection collections must cover the same recommendations")
            object.__setattr__(self, field_name, ordered)
        if self.schema_version != PROJECTION_SCHEMA_VERSION:
            raise GoldenAdapterContractError("unsupported projection-set schema")

    def selected_payload(self, projections: Iterable[str] = _REQUIRED_PROJECTIONS) -> dict[str, Any]:
        selected = _normalize_projection_names(projections)
        payload = self.canonical_payload()
        field_by_projection = {
            "outcome": "outcomes",
            "position": "positions",
            "risk": "risks",
            "evidence": "evidence",
        }
        return {
            "schema_version": self.schema_version,
            **{field_by_projection[name]: payload[field_by_projection[name]] for name in selected},
        }


@dataclass(frozen=True)
class GoldenDataInput:
    fixture_id: str
    snapshot: DataSnapshot
    execution_policy: ExecutionPolicy
    payloads_by_artifact_id: Mapping[str, bytes]
    fixture_schema_version: int = FIXTURE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "fixture_id", _required_text(self.fixture_id, "fixture_id"))
        if not isinstance(self.snapshot, DataSnapshot):
            raise GoldenFixtureError("snapshot must be a DataSnapshot")
        if not isinstance(self.execution_policy, ExecutionPolicy):
            raise GoldenFixtureError("execution_policy must be an ExecutionPolicy")
        if self.fixture_schema_version != FIXTURE_SCHEMA_VERSION:
            raise GoldenFixtureError("unsupported golden fixture schema")
        payloads = dict(self.payloads_by_artifact_id)
        referenced = {
            ref.artifact_id: ref
            for refs in self.snapshot.artifacts_by_signal_date.values()
            for ref in refs
        }
        if set(payloads) != set(referenced):
            raise GoldenFixtureError("artifact payload ids must exactly match the snapshot manifest")
        normalized: dict[str, bytes] = {}
        for artifact_id, ref in referenced.items():
            payload = payloads[artifact_id]
            if not isinstance(payload, bytes):
                raise GoldenFixtureError("artifact payloads must be bytes")
            ref.validate_content(payload)
            normalized[artifact_id] = payload
        object.__setattr__(self, "payloads_by_artifact_id", MappingProxyType(dict(sorted(normalized.items()))))

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(
            {
                "fixture_schema_version": self.fixture_schema_version,
                "fixture_id": self.fixture_id,
                "snapshot_manifest_hash": self.snapshot.input_manifest_hash,
                "execution_policy_fingerprint": self.execution_policy.fingerprint(),
                "artifact_hashes": {
                    artifact_id: content_hash(payload)
                    for artifact_id, payload in self.payloads_by_artifact_id.items()
                },
            }
        )

    @property
    def signal_dates(self) -> tuple[str, ...]:
        return tuple(sorted(self.snapshot.artifacts_by_signal_date))

    def artifact_payload(self, signal_date: str, scope: ArtifactScope) -> Mapping[str, Any]:
        refs = self.snapshot.artifacts_for(signal_date, scope)
        if len(refs) != 1:
            raise SnapshotArtifactUnavailableError(f"{signal_date}:{scope.value}")
        ref = refs[0]
        payload_bytes = self.payloads_by_artifact_id[ref.artifact_id]
        ref.validate_content(payload_bytes)
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GoldenFixtureError(f"artifact {ref.artifact_id} is not canonical JSON") from exc
        if not isinstance(payload, Mapping):
            raise GoldenFixtureError(f"artifact {ref.artifact_id} payload must be an object")
        return MappingProxyType(dict(payload))

    def recommendation_contexts(self) -> tuple[Mapping[str, Any], ...]:
        contexts: list[Mapping[str, Any]] = []
        for signal_date in self.signal_dates:
            payload = self.artifact_payload(signal_date, ArtifactScope.SIGNAL_INPUT)
            stocks = payload.get("stocks")
            if not isinstance(stocks, list):
                raise GoldenFixtureError("signal artifact stocks must be a list")
            for raw in stocks:
                if not isinstance(raw, Mapping):
                    raise GoldenFixtureError("signal artifact stock rows must be objects")
                context = dict(raw)
                context.setdefault("signal_date", signal_date)
                contexts.append(MappingProxyType(context))
        return tuple(sorted(contexts, key=lambda value: str(value.get("recommendation_id") or "")))

    def recommendations(self) -> tuple[RecommendationInput, ...]:
        recommendations: list[RecommendationInput] = []
        for context in self.recommendation_contexts():
            recommendations.append(
                RecommendationInput(
                    code=str(context.get("code") or ""),
                    signal_date=str(context.get("signal_date") or ""),
                    signal_price=context.get("price") or context.get("close"),
                    recommendation_id=str(context.get("recommendation_id") or ""),
                )
            )
        return tuple(recommendations)

    def context_for(self, recommendation_id: str) -> Mapping[str, Any]:
        matches = tuple(
            context
            for context in self.recommendation_contexts()
            if context.get("recommendation_id") == recommendation_id
        )
        if len(matches) != 1:
            raise GoldenFixtureError(f"expected one context for recommendation {recommendation_id}")
        return matches[0]

    def bars_for(self, recommendation: RecommendationInput) -> tuple[BarInput, ...]:
        payload = self.artifact_payload(recommendation.signal_date, ArtifactScope.OUTCOME_INPUT)
        bars_by_code = payload.get("bars_by_code")
        if not isinstance(bars_by_code, Mapping):
            raise GoldenFixtureError("outcome artifact bars_by_code must be an object")
        rows = bars_by_code.get(recommendation.code)
        if not isinstance(rows, list):
            raise GoldenFixtureError(f"outcome artifact has no bars for {recommendation.code}")
        bars: list[BarInput] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise GoldenFixtureError("outcome bar rows must be objects")
            bars.append(
                BarInput(
                    code=str(row.get("code") or recommendation.code),
                    trade_date=str(row.get("trade_date") or ""),
                    open=row.get("open"),
                    high=row.get("high"),
                    low=row.get("low"),
                    close=row.get("close"),
                    volume=row.get("volume"),
                    tradable=row.get("tradable", True),
                )
            )
        return tuple(sorted(bars, key=lambda bar: bar.trade_date))


@runtime_checkable
class GoldenAdapter(Protocol):
    role: AdapterRole

    def run(self, data: GoldenDataInput) -> GoldenProjectionSet:
        """Execute one adapter against the immutable fixture without I/O."""


@dataclass(frozen=True)
class ProjectionDifference(CanonicalValue):
    path: str
    expected: Any
    actual: Any


@dataclass(frozen=True)
class GoldenComparison(CanonicalValue):
    expected_role: AdapterRole
    actual_role: AdapterRole
    projections: tuple[str, ...]
    differences: tuple[ProjectionDifference, ...]

    @property
    def matches(self) -> bool:
        return not self.differences

    def require_match(self) -> None:
        if self.matches:
            return
        raise GoldenMismatchError(
            f"golden parity failed: {self.expected_role.value} -> {self.actual_role.value}: "
            f"{canonical_json([difference.canonical_payload() for difference in self.differences])}"
        )


@dataclass(frozen=True)
class GoldenHarnessRun:
    input_fingerprint: str
    results: Mapping[AdapterRole, GoldenProjectionSet]

    def __post_init__(self) -> None:
        _required_hash(self.input_fingerprint, "input_fingerprint")
        normalized = {AdapterRole(role): result for role, result in self.results.items()}
        if set(normalized) != set(AdapterRole):
            raise GoldenAdapterContractError("a harness run requires all three adapter roles")
        if not all(isinstance(result, GoldenProjectionSet) for result in normalized.values()):
            raise GoldenAdapterContractError("harness results must be GoldenProjectionSet values")
        object.__setattr__(
            self,
            "results",
            MappingProxyType({role: normalized[role] for role in AdapterRole}),
        )

    def compare(
        self,
        expected_role: AdapterRole,
        actual_role: AdapterRole,
        *,
        projections: Iterable[str] = _REQUIRED_PROJECTIONS,
    ) -> GoldenComparison:
        selected = _normalize_projection_names(projections)
        expected = self.results[AdapterRole(expected_role)].selected_payload(selected)
        actual = self.results[AdapterRole(actual_role)].selected_payload(selected)
        return GoldenComparison(
            expected_role=AdapterRole(expected_role),
            actual_role=AdapterRole(actual_role),
            projections=selected,
            differences=tuple(_diff_values(expected, actual)),
        )


class GoldenHarness:
    """Run all required adapters under an offline guard using one input object."""

    def __init__(self, data: GoldenDataInput) -> None:
        if not isinstance(data, GoldenDataInput):
            raise TypeError("data must be a GoldenDataInput")
        self.data = data

    def run(self, adapters: Iterable[GoldenAdapter]) -> GoldenHarnessRun:
        by_role: dict[AdapterRole, GoldenAdapter] = {}
        for adapter in adapters:
            try:
                role = adapter.role if isinstance(adapter.role, AdapterRole) else AdapterRole(str(adapter.role))
            except (AttributeError, ValueError) as exc:
                raise GoldenAdapterContractError("adapter role is invalid") from exc
            if role in by_role:
                raise GoldenAdapterContractError(f"duplicate golden adapter role: {role.value}")
            by_role[role] = adapter
        missing = set(AdapterRole) - set(by_role)
        extra = set(by_role) - set(AdapterRole)
        if missing or extra:
            raise GoldenAdapterContractError(
                "golden harness requires historical_reconstruction, backtest, and formal adapters"
            )

        original_fingerprint = self.data.fingerprint
        results: dict[AdapterRole, GoldenProjectionSet] = {}
        with external_network_disabled():
            for role in AdapterRole:
                result = by_role[role].run(self.data)
                if not isinstance(result, GoldenProjectionSet):
                    raise GoldenAdapterContractError(
                        f"{role.value} adapter must return GoldenProjectionSet"
                    )
                if self.data.fingerprint != original_fingerprint:
                    raise GoldenAdapterContractError(
                        f"{role.value} adapter mutated the fixed golden input"
                    )
                results[role] = result
        return GoldenHarnessRun(input_fingerprint=original_fingerprint, results=results)


@contextmanager
def external_network_disabled() -> Iterator[None]:
    """Fail closed on DNS or socket creation while golden adapters execute."""

    def denied(*_args: Any, **_kwargs: Any) -> Any:
        raise ExternalNetworkAccessError(
            "golden adapter attempted network access; use only fixed snapshot artifacts"
        )

    with ExitStack() as stack:
        stack.enter_context(patch.object(socket, "getaddrinfo", denied))
        stack.enter_context(patch.object(socket, "create_connection", denied))
        stack.enter_context(patch.object(socket, "socket", denied))
        yield


def load_golden_fixture(path: Path | str) -> GoldenDataInput:
    """Load and hash-verify one checked-in DataSnapshot/Artifact fixture."""

    fixture_path = Path(path)
    try:
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoldenFixtureError(f"cannot load golden fixture: {fixture_path}") from exc
    if not isinstance(raw, Mapping):
        raise GoldenFixtureError("golden fixture root must be an object")
    if raw.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise GoldenFixtureError("unsupported golden fixture schema")

    fixture_id = raw.get("fixture_id")
    snapshot_id = raw.get("snapshot_id")
    policy_payload = raw.get("execution_policy")
    partition_payload = raw.get("partition")
    artifact_payloads = raw.get("artifacts")
    if not isinstance(policy_payload, Mapping):
        raise GoldenFixtureError("execution_policy must be an object")
    if not isinstance(partition_payload, Mapping):
        raise GoldenFixtureError("partition must be an object")
    if not isinstance(artifact_payloads, list) or not artifact_payloads:
        raise GoldenFixtureError("artifacts must be a non-empty list")

    try:
        policy = ExecutionPolicy(**dict(policy_payload))
        payloads_by_id: dict[str, bytes] = {}
        artifacts_by_date: dict[str, list[ArtifactRef]] = {}
        for artifact in artifact_payloads:
            if not isinstance(artifact, Mapping) or not isinstance(artifact.get("payload"), Mapping):
                raise GoldenFixtureError("each artifact and payload must be an object")
            payload_bytes = canonical_json(artifact["payload"]).encode("utf-8")
            scope = ArtifactScope(str(artifact.get("artifact_scope") or ""))
            ref = ArtifactRef.from_content(
                artifact_id=str(artifact.get("artifact_id") or ""),
                signal_date=str(artifact.get("signal_date") or ""),
                artifact_scope=scope,
                artifact_type=str(artifact.get("artifact_type") or ""),
                schema_version=int(artifact.get("schema_version") or 0),
                row_count=int(artifact.get("row_count") or 0),
                payload=payload_bytes,
            )
            expected_hash = artifact.get("content_hash")
            if expected_hash is not None and str(expected_hash).lower() != ref.content_hash:
                raise GoldenFixtureError(f"artifact content hash mismatch: {ref.artifact_id}")
            if ref.artifact_id in payloads_by_id:
                raise GoldenFixtureError(f"duplicate artifact id: {ref.artifact_id}")
            payloads_by_id[ref.artifact_id] = payload_bytes
            artifacts_by_date.setdefault(ref.signal_date, []).append(ref)

        for signal_date, refs in artifacts_by_date.items():
            scopes = [ref.artifact_scope for ref in refs]
            if scopes.count(ArtifactScope.SIGNAL_INPUT) != 1 or scopes.count(ArtifactScope.OUTCOME_INPUT) != 1:
                raise GoldenFixtureError(
                    f"{signal_date} must have exactly one signal and one outcome artifact"
                )

        partition_hash = canonical_fingerprint(
            {
                "fixture_id": fixture_id,
                "artifact_hashes": {
                    artifact_id: ref.content_hash
                    for refs in artifacts_by_date.values()
                    for ref in refs
                    for artifact_id in (ref.artifact_id,)
                },
            }
        )
        expected_partition_hash = partition_payload.get("content_hash")
        if expected_partition_hash is not None and str(expected_partition_hash).lower() != partition_hash:
            raise GoldenFixtureError("partition content hash mismatch")
        partition = PartitionManifest(
            dataset=str(partition_payload.get("dataset") or ""),
            query_range=DateRange(
                str(partition_payload.get("start_date") or ""),
                str(partition_payload.get("end_date") or ""),
            ),
            source_version=str(partition_payload.get("source_version") or ""),
            row_count=int(partition_payload.get("row_count") or 0),
            content_hash=partition_hash,
        )
        coverage = {
            signal_date: CoverageResult(signal_date, CoverageStatus.READY, 1.0)
            for signal_date in artifacts_by_date
        }
        provisional = DataSnapshot(
            snapshot_id="pending",
            snapshot_version=1,
            partitions=(partition,),
            artifacts_by_signal_date={
                signal_date: tuple(sorted(refs, key=lambda ref: ref.artifact_scope.value))
                for signal_date, refs in artifacts_by_date.items()
            },
            coverage_by_signal_date=coverage,
        )
        expected_manifest_hash = raw.get("input_manifest_hash")
        if expected_manifest_hash is not None and str(expected_manifest_hash).lower() != provisional.input_manifest_hash:
            raise GoldenFixtureError("snapshot input manifest hash mismatch")
        snapshot = DataSnapshot(
            snapshot_id=str(snapshot_id or ""),
            snapshot_version=1,
            partitions=provisional.partitions,
            artifacts_by_signal_date=provisional.artifacts_by_signal_date,
            coverage_by_signal_date=provisional.coverage_by_signal_date,
            input_manifest_hash=provisional.input_manifest_hash,
        )
    except GoldenFixtureError:
        raise
    except Exception as exc:
        raise GoldenFixtureError(f"invalid golden fixture: {fixture_path}") from exc

    return GoldenDataInput(
        fixture_id=str(fixture_id or ""),
        snapshot=snapshot,
        execution_policy=policy,
        payloads_by_artifact_id=payloads_by_id,
    )


# ---------------------------------------------------------------------------
# Cross-module canonical projection adapters (Layer 1 evidence only).
#
# The four adapters below project fixed, fixture-injected inputs into stable
# per-field records for evidence eligibility, T+1 execution, outcome identity,
# and min-cap risk decisions.  Every projection validates ``layer == "layer_1"``
# so harness output can never carry environment ``ACCEPTED`` semantics.
# ---------------------------------------------------------------------------


def _layer_1_marker(value: object, field_name: str = "layer") -> str:
    text = _required_text(value, field_name)
    if text != LAYER_1_EVIDENCE_LAYER:
        raise GoldenAdapterContractError(
            "golden projections are layer_1 evidence only; they never assert environment acceptance"
        )
    return text


def _boolean_field(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise GoldenAdapterContractError(f"{field_name} must be a boolean")
    return value


def _positive_int_field(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GoldenAdapterContractError(f"{field_name} must be a positive integer")
    return value


def _aware_datetime(value: object, field_name: str) -> datetime:
    text = _required_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise GoldenAdapterContractError(f"{field_name} must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GoldenAdapterContractError(f"{field_name} must include a timezone")
    return parsed


def _ranged_decimal(
    value: object,
    field_name: str,
    *,
    minimum: str,
    maximum: str,
    minimum_inclusive: bool = True,
) -> Decimal:
    if isinstance(value, bool):
        raise GoldenAdapterContractError(f"{field_name} must be numeric")
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise GoldenAdapterContractError(f"{field_name} must be a finite decimal") from exc
    lower = Decimal(minimum)
    upper = Decimal(maximum)
    below = number < lower if minimum_inclusive else number <= lower
    if below or number > upper:
        operator = ">=" if minimum_inclusive else ">"
        raise GoldenAdapterContractError(f"{field_name} must be {operator} {minimum} and <= {maximum}")
    return number


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def load_projection_golden_cases(path: Path | str) -> Mapping[str, Any]:
    """Load the additive cross-module projection golden section of one fixture.

    The section is append-only relative to the existing fixture keys; loading it
    never re-reads or mutates the snapshot/artifact entries.  The returned mapping
    is deep-frozen so adapters cannot mutate the fixed golden expectations.
    """

    fixture_path = Path(path)
    try:
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoldenFixtureError(f"cannot load golden fixture: {fixture_path}") from exc
    if not isinstance(raw, Mapping):
        raise GoldenFixtureError("golden fixture root must be an object")
    section = raw.get(CROSS_MODULE_PROJECTION_SECTION)
    if not isinstance(section, Mapping):
        raise GoldenFixtureError("golden fixture is missing the cross-module projection section")
    if section.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise GoldenFixtureError("unsupported cross-module projection section schema")
    for group in ("evidence_eligibility", "execution", "outcome_identity", "risk_decision"):
        if not isinstance(section.get(group), Mapping):
            raise GoldenFixtureError(f"cross-module projection section is missing {group}")
    return _deep_freeze(section)


@dataclass(frozen=True)
class EvidenceEligibilityProjection(CanonicalValue):
    """Layer 1 per-field evidence-class and LIVE-eligibility projection.

    The projection records classification facts derived from fixed run provenance
    inputs.  It is Layer 1 evidence only: it carries no environment acceptance
    semantics and can never mark an environment ``ACCEPTED``.
    """

    case_id: str
    run_source: str
    signal_date: str
    evidence_class: GoldenEvidenceClass | str
    is_live_eligible: bool
    generated_on_signal_business_date: bool
    data_as_of_on_or_before_signal: bool
    source_snapshot_id_verified: bool
    code_revision_verified: bool
    identity_hash: str
    layer: str = LAYER_1_EVIDENCE_LAYER
    schema_version: int = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _required_text(self.case_id, "case_id"))
        try:
            run_source = RunSource(str(self.run_source))
        except ValueError as exc:
            raise GoldenAdapterContractError("run_source is invalid") from exc
        object.__setattr__(self, "run_source", run_source.value)
        object.__setattr__(self, "signal_date", _iso_date(self.signal_date, "signal_date"))
        try:
            evidence_class = (
                self.evidence_class
                if isinstance(self.evidence_class, GoldenEvidenceClass)
                else GoldenEvidenceClass(str(self.evidence_class))
            )
        except ValueError as exc:
            raise GoldenAdapterContractError("evidence_class is invalid") from exc
        checks = (
            self.generated_on_signal_business_date,
            self.data_as_of_on_or_before_signal,
            self.source_snapshot_id_verified,
            self.code_revision_verified,
        )
        if not all(isinstance(value, bool) for value in checks):
            raise GoldenAdapterContractError("evidence eligibility check fields must be booleans")
        _boolean_field(self.is_live_eligible, "is_live_eligible")
        if self.is_live_eligible != (evidence_class is GoldenEvidenceClass.LIVE_FORWARD):
            raise GoldenAdapterContractError("is_live_eligible must agree with evidence_class")
        if run_source is RunSource.FORMAL and self.is_live_eligible != all(checks):
            raise GoldenAdapterContractError(
                "formal LIVE eligibility must equal the conjunction of its field checks"
            )
        object.__setattr__(self, "evidence_class", evidence_class)
        object.__setattr__(self, "identity_hash", _required_hash(self.identity_hash, "identity_hash"))
        object.__setattr__(self, "layer", _layer_1_marker(self.layer))
        if self.schema_version != PROJECTION_SCHEMA_VERSION:
            raise GoldenAdapterContractError("unsupported evidence eligibility projection schema")


def _derived_evidence_class(run_source: RunSource, live_eligible: bool) -> GoldenEvidenceClass:
    if run_source is RunSource.HISTORICAL_REPLAY:
        return GoldenEvidenceClass.HISTORICAL_RECONSTRUCTION
    if run_source is RunSource.BACKTEST:
        return GoldenEvidenceClass.RESEARCH_BACKTEST
    if run_source is RunSource.EXPERIMENT:
        return GoldenEvidenceClass.EXPERIMENT
    if run_source is RunSource.FORMAL and live_eligible:
        return GoldenEvidenceClass.LIVE_FORWARD
    return GoldenEvidenceClass.UNVERIFIED


@dataclass(frozen=True)
class EvidenceEligibilityAdapter:
    """Project fixed run provenance facts into per-field Layer 1 eligibility.

    The production ``EvidenceIdentityBuilder`` stays the authoritative classifier.
    The adapter re-derives each LIVE eligibility field from the same injected
    facts and fails closed when the field-level derivation disagrees with the
    builder.  Every timestamp is injected by the fixture; the adapter never reads
    a wall clock, a database, or the network, and its output never marks an
    environment ``ACCEPTED``.
    """

    def run(self, facts: Mapping[str, Any]) -> EvidenceEligibilityProjection:
        with external_network_disabled():
            return self._project(facts)

    def _project(self, facts: Mapping[str, Any]) -> EvidenceEligibilityProjection:
        if not isinstance(facts, Mapping):
            raise GoldenAdapterContractError("evidence facts must be a mapping")
        case_id = _required_text(facts.get("case_id"), "facts.case_id")
        try:
            run_source = RunSource(str(facts.get("run_source")))
        except ValueError as exc:
            raise GoldenAdapterContractError("facts.run_source is invalid") from exc
        signal_date = _iso_date(facts.get("signal_date"), "facts.signal_date")
        generated_at = _aware_datetime(facts.get("actual_generated_at"), "facts.actual_generated_at")
        batch_id = facts.get("reconstruction_batch_id")
        try:
            identity = EvidenceIdentityBuilder().build(
                run_source=run_source,
                actual_generated_at=generated_at,
                signal_date=signal_date,
                snapshot=EvidenceSnapshotIdentity(
                    source_snapshot_id=str(facts.get("source_snapshot_id")),
                    data_as_of=str(facts.get("data_as_of")),
                ),
                strategy_policy=str(facts.get("strategy_policy_fingerprint")),
                execution_policy=str(facts.get("execution_policy_fingerprint")),
                code_revision=str(facts.get("code_revision")),
                reconstruction_batch_id=None if batch_id is None else str(batch_id),
            )
        except EvidenceIdentityError as exc:
            raise GoldenAdapterContractError(f"evidence facts rejected: {exc.code}") from exc
        generated_business_date = (
            identity.actual_generated_at.astimezone(_BEIJING_BUSINESS_TIMEZONE).date().isoformat()
        )
        generated_on_signal = generated_business_date == identity.signal_date
        data_as_of_on_or_before = identity.data_as_of <= identity.signal_date
        snapshot_verified = identity.source_snapshot_id.lower() not in _UNVERIFIED_PROVENANCE_IDENTIFIERS
        revision_verified = identity.code_revision.lower() not in _UNVERIFIED_PROVENANCE_IDENTIFIERS
        live_eligible = (
            run_source is RunSource.FORMAL
            and generated_on_signal
            and data_as_of_on_or_before
            and snapshot_verified
            and revision_verified
        )
        derived_class = _derived_evidence_class(run_source, live_eligible)
        if derived_class.value != identity.evidence_class.value:
            raise GoldenAdapterContractError(
                "field-level eligibility derivation disagrees with EvidenceIdentityBuilder"
            )
        if identity.is_true_forward != live_eligible:
            raise GoldenAdapterContractError("builder is_true_forward disagrees with eligibility fields")
        return EvidenceEligibilityProjection(
            case_id=case_id,
            run_source=run_source.value,
            signal_date=identity.signal_date,
            evidence_class=identity.evidence_class.value,
            is_live_eligible=live_eligible,
            generated_on_signal_business_date=generated_on_signal,
            data_as_of_on_or_before_signal=data_as_of_on_or_before,
            source_snapshot_id_verified=snapshot_verified,
            code_revision_verified=revision_verified,
            identity_hash=identity.identity_hash,
        )


@dataclass(frozen=True)
class ExecutionProjection(CanonicalValue):
    """Layer 1 per-field execution projection under the T+1 same-bar-exit ban.

    When ``t_plus_one_entry_bar_excluded`` is set, an executed projection always
    satisfies ``exit_date > entry_date``.  The record is Layer 1 evidence only and
    never marks an environment ``ACCEPTED``.
    """

    recommendation_id: str
    entry_triggered: bool
    entry_date: str | None
    entry_price: str | int | float | Decimal | None
    exit_date: str | None
    exit_price: str | int | float | Decimal | None
    exit_reason: str
    gross_return_pct: str | int | float | Decimal
    cost_pct: str | int | float | Decimal
    position_size: str | int | float | Decimal
    net_return_pct: str | int | float | Decimal
    t_plus_one_entry_bar_excluded: bool
    layer: str = LAYER_1_EVIDENCE_LAYER
    schema_version: int = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "recommendation_id", _required_text(self.recommendation_id, "recommendation_id"))
        _boolean_field(self.entry_triggered, "entry_triggered")
        _boolean_field(self.t_plus_one_entry_bar_excluded, "t_plus_one_entry_bar_excluded")
        paired = (self.entry_date, self.entry_price, self.exit_date, self.exit_price)
        if self.entry_triggered:
            if any(value is None for value in paired):
                raise GoldenAdapterContractError("executed projections require entry and exit fields")
            entry_date = _iso_date(self.entry_date, "entry_date")
            exit_date = _iso_date(self.exit_date, "exit_date")
            if exit_date < entry_date:
                raise GoldenAdapterContractError("execution dates must be chronological")
            if self.t_plus_one_entry_bar_excluded and exit_date == entry_date:
                raise GoldenAdapterContractError("T+1 execution may not exit on the entry date")
            entry_price = decimal_text(self.entry_price, "entry_price")
            exit_price = decimal_text(self.exit_price, "exit_price")
            if _decimal_value(entry_price) <= 0 or _decimal_value(exit_price) <= 0:
                raise GoldenAdapterContractError("executed prices must be positive")
            object.__setattr__(self, "entry_date", entry_date)
            object.__setattr__(self, "exit_date", exit_date)
            object.__setattr__(self, "entry_price", entry_price)
            object.__setattr__(self, "exit_price", exit_price)
        else:
            if any(value is not None for value in paired):
                raise GoldenAdapterContractError("unexecuted projections must omit entry and exit fields")
        object.__setattr__(self, "exit_reason", _required_text(self.exit_reason, "exit_reason"))
        object.__setattr__(self, "gross_return_pct", decimal_text(self.gross_return_pct, "gross_return_pct"))
        object.__setattr__(self, "cost_pct", decimal_text(self.cost_pct, "cost_pct"))
        object.__setattr__(self, "position_size", decimal_text(self.position_size, "position_size"))
        object.__setattr__(self, "net_return_pct", decimal_text(self.net_return_pct, "net_return_pct"))
        if _decimal_value(str(self.cost_pct)) < 0:
            raise GoldenAdapterContractError("cost_pct must be non-negative")
        if not 0 <= _decimal_value(str(self.position_size)) <= 1:
            raise GoldenAdapterContractError("position_size must be in [0, 1]")
        if not self.entry_triggered and any(
            _decimal_value(str(value)) != 0
            for value in (self.gross_return_pct, self.cost_pct, self.position_size, self.net_return_pct)
        ):
            raise GoldenAdapterContractError("unexecuted projections must have zero returns, cost, and size")
        object.__setattr__(self, "layer", _layer_1_marker(self.layer))
        if self.schema_version != PROJECTION_SCHEMA_VERSION:
            raise GoldenAdapterContractError("unsupported execution projection schema")


@dataclass(frozen=True)
class TPlusOneExecutionAdapter:
    """Project the canonical execution core into stable Layer 1 fields.

    This adapter owns no entry, exit, cost, position, or T+1 rules.  It is only
    a DTO projection over :func:`app.engine.simulation.simulate`, which itself
    delegates to ``app.engine.execution_core``.
    """

    def run(
        self,
        recommendation: RecommendationInput,
        bars: Iterable[BarInput],
        policy: ExecutionPolicy,
    ) -> ExecutionProjection:
        with external_network_disabled():
            if not isinstance(recommendation, RecommendationInput):
                raise GoldenAdapterContractError(
                    "recommendation must be a RecommendationInput"
                )
            if not isinstance(policy, ExecutionPolicy):
                raise GoldenAdapterContractError(
                    "policy must be an ExecutionPolicy"
                )
            result = simulate(recommendation, tuple(bars), policy)
            trade = result.trade
            if not trade.executed:
                return ExecutionProjection(
                    recommendation_id=str(
                        recommendation.recommendation_id or recommendation.code
                    ),
                    entry_triggered=False,
                    entry_date=None,
                    entry_price=None,
                    exit_date=None,
                    exit_price=None,
                    exit_reason=trade.exit_reason or result.status,
                    gross_return_pct=0,
                    cost_pct=0,
                    position_size=0,
                    net_return_pct=0,
                    t_plus_one_entry_bar_excluded=bool(policy.t_plus_one),
                )
            return ExecutionProjection(
                recommendation_id=str(
                    recommendation.recommendation_id or recommendation.code
                ),
                entry_triggered=True,
                entry_date=trade.entry_date,
                entry_price=trade.entry_price,
                exit_date=trade.exit_date,
                exit_price=trade.exit_price,
                exit_reason=trade.exit_reason or "unspecified",
                gross_return_pct=trade.gross_return_pct,
                cost_pct=policy.fee_pct,
                position_size=trade.position_size,
                net_return_pct=trade.net_return_pct,
                t_plus_one_entry_bar_excluded=bool(policy.t_plus_one),
            )


@dataclass(frozen=True)
class OutcomeVersionProjection(CanonicalValue):
    """Layer 1 projection of one append-only outcome version record."""

    version: int
    outcome_label: str
    calculation_fingerprint: str
    result_fingerprint: str
    recorded_at: str
    version_fingerprint: str
    is_current: bool
    schema_version: int = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _positive_int_field(self.version, "outcome.version")
        object.__setattr__(self, "outcome_label", _required_text(self.outcome_label, "outcome.outcome_label"))
        object.__setattr__(
            self,
            "calculation_fingerprint",
            _required_hash(self.calculation_fingerprint, "outcome.calculation_fingerprint"),
        )
        object.__setattr__(
            self,
            "result_fingerprint",
            _required_hash(self.result_fingerprint, "outcome.result_fingerprint"),
        )
        object.__setattr__(self, "recorded_at", _utc_datetime(self.recorded_at, "outcome.recorded_at"))
        object.__setattr__(
            self,
            "version_fingerprint",
            _required_hash(self.version_fingerprint, "outcome.version_fingerprint"),
        )
        _boolean_field(self.is_current, "outcome.is_current")
        if self.schema_version != PROJECTION_SCHEMA_VERSION:
            raise GoldenAdapterContractError("unsupported outcome version projection schema")


@dataclass(frozen=True)
class OutcomeIdentityProjection(CanonicalValue):
    """Layer 1 outcome identity, append-only version chain, and current pointer.

    The current pointer always references the highest appended version; prior
    versions remain intact.  The record is Layer 1 evidence only and never marks
    an environment ``ACCEPTED``.
    """

    case_id: str
    recommendation_id: str
    outcome_identity_hash: str
    version_count: int
    current_version: int
    current_version_fingerprint: str
    versions: tuple[OutcomeVersionProjection, ...]
    layer: str = LAYER_1_EVIDENCE_LAYER
    schema_version: int = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _required_text(self.case_id, "case_id"))
        object.__setattr__(self, "recommendation_id", _required_text(self.recommendation_id, "recommendation_id"))
        object.__setattr__(
            self,
            "outcome_identity_hash",
            _required_hash(self.outcome_identity_hash, "outcome_identity_hash"),
        )
        versions = tuple(self.versions)
        if not versions or not all(isinstance(item, OutcomeVersionProjection) for item in versions):
            raise GoldenAdapterContractError("outcome versions must contain OutcomeVersionProjection values")
        ordered = tuple(sorted(versions, key=lambda item: item.version))
        numbers = tuple(item.version for item in ordered)
        if numbers != tuple(range(1, len(numbers) + 1)):
            raise GoldenAdapterContractError("outcome versions must append contiguously from 1")
        if any(item.is_current for item in ordered[:-1]) or not ordered[-1].is_current:
            raise GoldenAdapterContractError("exactly the latest outcome version may be current")
        if self.version_count != len(ordered):
            raise GoldenAdapterContractError("version_count must equal the number of outcome versions")
        if self.current_version != ordered[-1].version:
            raise GoldenAdapterContractError("current_version must reference the latest outcome version")
        object.__setattr__(
            self,
            "current_version_fingerprint",
            _required_hash(self.current_version_fingerprint, "current_version_fingerprint"),
        )
        if self.current_version_fingerprint != ordered[-1].version_fingerprint:
            raise GoldenAdapterContractError("current pointer must reference the latest version fingerprint")
        object.__setattr__(self, "versions", ordered)
        object.__setattr__(self, "layer", _layer_1_marker(self.layer))
        if self.schema_version != PROJECTION_SCHEMA_VERSION:
            raise GoldenAdapterContractError("unsupported outcome identity projection schema")


@dataclass(frozen=True)
class OutcomeIdentityAdapter:
    """Project fixed outcome append records into a Layer 1 identity projection.

    Idempotent re-appends (the same version with byte-identical content) collapse
    onto the existing version; conflicting content for an already-appended
    version fails closed.  The current pointer always references the highest
    appended version.  No database, network, or wall clock is touched, and the
    output never marks an environment ``ACCEPTED``.
    """

    def run(self, payload: Mapping[str, Any]) -> OutcomeIdentityProjection:
        with external_network_disabled():
            return self._project(payload)

    def _project(self, payload: Mapping[str, Any]) -> OutcomeIdentityProjection:
        if not isinstance(payload, Mapping):
            raise GoldenAdapterContractError("outcome identity payload must be a mapping")
        case_id = _required_text(payload.get("case_id"), "outcome.case_id")
        recommendation_id = _required_text(payload.get("recommendation_id"), "outcome.recommendation_id")
        components = payload.get("identity_components")
        if not isinstance(components, Mapping):
            raise GoldenAdapterContractError("outcome.identity_components must be a mapping")
        identity_hash = canonical_fingerprint(
            {
                "code_revision": _required_text(components.get("code_revision"), "outcome.code_revision"),
                "execution_policy_fingerprint": _required_hash(
                    components.get("execution_policy_fingerprint"),
                    "outcome.execution_policy_fingerprint",
                ),
                "recommendation_id": recommendation_id,
                "source_snapshot_id": _required_text(
                    components.get("source_snapshot_id"),
                    "outcome.source_snapshot_id",
                ),
                "strategy_policy_fingerprint": _required_hash(
                    components.get("strategy_policy_fingerprint"),
                    "outcome.strategy_policy_fingerprint",
                ),
            }
        )
        raw_versions = payload.get("versions")
        if not isinstance(raw_versions, (list, tuple)) or not raw_versions:
            raise GoldenAdapterContractError("outcome.versions must be a non-empty list")
        by_version: dict[int, tuple[str, str, str, str]] = {}
        for entry in raw_versions:
            if not isinstance(entry, Mapping):
                raise GoldenAdapterContractError("outcome.versions[] must be objects")
            version = _positive_int_field(entry.get("version"), "outcome.versions[].version")
            content = (
                _required_text(entry.get("outcome_label"), "outcome.versions[].outcome_label"),
                _required_hash(entry.get("calculation_fingerprint"), "outcome.versions[].calculation_fingerprint"),
                _required_hash(entry.get("result_fingerprint"), "outcome.versions[].result_fingerprint"),
                _utc_datetime(entry.get("recorded_at"), "outcome.versions[].recorded_at"),
            )
            existing = by_version.get(version)
            if existing is not None and existing != content:
                raise GoldenAdapterContractError(
                    f"outcome version {version} conflicts with the appended immutable version"
                )
            by_version[version] = content
        ordered_numbers = tuple(sorted(by_version))
        if ordered_numbers != tuple(range(1, len(ordered_numbers) + 1)):
            raise GoldenAdapterContractError("outcome versions must append contiguously from 1")
        current_version = ordered_numbers[-1]
        projections = tuple(
            OutcomeVersionProjection(
                version=version,
                outcome_label=content[0],
                calculation_fingerprint=content[1],
                result_fingerprint=content[2],
                recorded_at=content[3],
                version_fingerprint=canonical_fingerprint(
                    {
                        "calculation_fingerprint": content[1],
                        "outcome_identity_hash": identity_hash,
                        "outcome_label": content[0],
                        "recorded_at": content[3],
                        "result_fingerprint": content[2],
                        "version": version,
                    }
                ),
                is_current=version == current_version,
            )
            for version, content in ((number, by_version[number]) for number in ordered_numbers)
        )
        return OutcomeIdentityProjection(
            case_id=case_id,
            recommendation_id=recommendation_id,
            outcome_identity_hash=identity_hash,
            version_count=len(projections),
            current_version=current_version,
            current_version_fingerprint=projections[-1].version_fingerprint,
            versions=projections,
        )


@dataclass(frozen=True)
class RiskDecisionProjection(CanonicalValue):
    """Layer 1 min-cap risk decision projection from pure policy/state/request mappings.

    The record carries the decision, sizes, reason codes, and policy/state hashes
    only; it is Layer 1 evidence and never marks an environment ``ACCEPTED``.
    """

    case_id: str
    recommendation_id: str
    decision: GoldenRiskDecision | str
    requested_size: str | int | float | Decimal
    approved_size: str | int | float | Decimal
    binding_cap: str
    reason_codes: tuple[str, ...]
    policy_fingerprint: str
    state_fingerprint: str
    layer: str = LAYER_1_EVIDENCE_LAYER
    schema_version: int = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _required_text(self.case_id, "case_id"))
        object.__setattr__(self, "recommendation_id", _required_text(self.recommendation_id, "recommendation_id"))
        try:
            decision = self.decision if isinstance(self.decision, GoldenRiskDecision) else GoldenRiskDecision(str(self.decision))
        except ValueError as exc:
            raise GoldenAdapterContractError("risk decision is invalid") from exc
        requested = decimal_text(self.requested_size, "requested_size")
        approved = decimal_text(self.approved_size, "approved_size")
        if not 0 < _decimal_value(requested) <= 1:
            raise GoldenAdapterContractError("requested_size must be in (0, 1]")
        if not 0 <= _decimal_value(approved) <= _decimal_value(requested):
            raise GoldenAdapterContractError("approved_size must be in [0, requested_size]")
        reasons = tuple(sorted({_required_text(value, "reason_codes[]") for value in self.reason_codes}))
        if decision in {GoldenRiskDecision.REJECT, GoldenRiskDecision.HALT} and _decimal_value(approved) != 0:
            raise GoldenAdapterContractError("reject/halt risk decisions must approve zero size")
        if decision is GoldenRiskDecision.ALLOW and (_decimal_value(approved) != _decimal_value(requested) or reasons):
            raise GoldenAdapterContractError("allow risk decisions must approve the request in full without reasons")
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "requested_size", requested)
        object.__setattr__(self, "approved_size", approved)
        object.__setattr__(self, "binding_cap", _required_text(self.binding_cap, "binding_cap"))
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "policy_fingerprint", _required_hash(self.policy_fingerprint, "policy_fingerprint"))
        object.__setattr__(self, "state_fingerprint", _required_hash(self.state_fingerprint, "state_fingerprint"))
        object.__setattr__(self, "layer", _layer_1_marker(self.layer))
        if self.schema_version != PROJECTION_SCHEMA_VERSION:
            raise GoldenAdapterContractError("unsupported risk decision projection schema")


@dataclass(frozen=True)
class MinCapRiskDecisionAdapter:
    """Compute a deterministic Layer 1 min-cap risk decision from plain mappings.

    This is a harness projection adapter only — it creates no production risk
    module.  Policy rules (every value fixture-injected): the approved size is the
    minimum of the request and all applicable caps (the single-name fraction and
    the gross-exposure headroom); a halted state or a daily-loss/drawdown breach
    yields ``halt``; a non-positive binding cap yields ``reject``; a trimming cap
    yields ``reduce``; otherwise ``allow``.  The output records the decision and
    policy/state hashes only and never marks an environment ``ACCEPTED``.
    """

    def run(self, payload: Mapping[str, Any]) -> RiskDecisionProjection:
        with external_network_disabled():
            return self._project(payload)

    def _project(self, payload: Mapping[str, Any]) -> RiskDecisionProjection:
        if not isinstance(payload, Mapping):
            raise GoldenAdapterContractError("risk payload must be a mapping")
        case_id = _required_text(payload.get("case_id"), "risk.case_id")
        policy = payload.get("policy")
        state = payload.get("state")
        request = payload.get("request")
        for field_name, mapping in (("policy", policy), ("state", state), ("request", request)):
            if not isinstance(mapping, Mapping):
                raise GoldenAdapterContractError(f"risk.{field_name} must be a mapping")
        max_single = _ranged_decimal(
            policy.get("max_single_name_fraction"), "risk.policy.max_single_name_fraction", minimum="0", maximum="1"
        )
        max_gross = _ranged_decimal(
            policy.get("max_gross_exposure_fraction"), "risk.policy.max_gross_exposure_fraction", minimum="0", maximum="1"
        )
        max_daily_loss = _ranged_decimal(
            policy.get("max_daily_loss_pct"),
            "risk.policy.max_daily_loss_pct",
            minimum="0",
            maximum="100",
            minimum_inclusive=False,
        )
        max_drawdown = _ranged_decimal(
            policy.get("max_drawdown_pct"),
            "risk.policy.max_drawdown_pct",
            minimum="0",
            maximum="100",
            minimum_inclusive=False,
        )
        current_gross = _ranged_decimal(
            state.get("current_gross_exposure_fraction"), "risk.state.current_gross_exposure_fraction", minimum="0", maximum="1"
        )
        daily_loss = _ranged_decimal(state.get("daily_loss_pct"), "risk.state.daily_loss_pct", minimum="0", maximum="100")
        drawdown = _ranged_decimal(state.get("drawdown_pct"), "risk.state.drawdown_pct", minimum="0", maximum="100")
        halted = _boolean_field(state.get("halted"), "risk.state.halted")
        recommendation_id = _required_text(request.get("recommendation_id"), "risk.request.recommendation_id")
        requested = _ranged_decimal(
            request.get("requested_size"),
            "risk.request.requested_size",
            minimum="0",
            maximum="1",
            minimum_inclusive=False,
        )
        policy_fingerprint = canonical_fingerprint(dict(policy))
        state_fingerprint = canonical_fingerprint(dict(state))

        halt_reason: str | None = None
        if halted:
            halt_reason = "state_halted"
        elif daily_loss >= max_daily_loss:
            halt_reason = "daily_loss_breach"
        elif drawdown >= max_drawdown:
            halt_reason = "drawdown_breach"

        if halt_reason is not None:
            decision = GoldenRiskDecision.HALT
            approved = Decimal(0)
            binding_cap = "risk_state"
            reason_codes = (halt_reason,)
        else:
            caps = {
                "gross_exposure_headroom": max(Decimal(0), max_gross - current_gross),
                "single_name": max_single,
            }
            binding = min(sorted(caps), key=lambda name: caps[name])
            approved = min(requested, caps[binding])
            if approved == requested:
                decision = GoldenRiskDecision.ALLOW
                binding_cap = "requested_size"
                reason_codes = ()
            elif approved <= 0:
                decision = GoldenRiskDecision.REJECT
                approved = Decimal(0)
                binding_cap = binding
                reason_codes = (f"{binding}_cap",)
            else:
                decision = GoldenRiskDecision.REDUCE
                binding_cap = binding
                reason_codes = (f"{binding}_cap",)
        return RiskDecisionProjection(
            case_id=case_id,
            recommendation_id=recommendation_id,
            decision=decision,
            requested_size=requested,
            approved_size=approved,
            binding_cap=binding_cap,
            reason_codes=reason_codes,
            policy_fingerprint=policy_fingerprint,
            state_fingerprint=state_fingerprint,
        )


def _normalize_projection_names(projections: Iterable[str]) -> tuple[str, ...]:
    if isinstance(projections, (str, bytes)):
        projections = (str(projections),)
    values = tuple(dict.fromkeys(str(value) for value in projections))
    unknown = set(values) - set(_REQUIRED_PROJECTIONS)
    if not values or unknown:
        raise GoldenAdapterContractError(
            f"projections must be selected from {_REQUIRED_PROJECTIONS}"
        )
    return tuple(name for name in _REQUIRED_PROJECTIONS if name in values)


def _diff_values(expected: Any, actual: Any, path: str = "$") -> Sequence[ProjectionDifference]:
    differences: list[ProjectionDifference] = []
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        keys = sorted(set(expected) | set(actual))
        for key in keys:
            child_path = f"{path}.{key}"
            if key not in expected:
                differences.append(ProjectionDifference(child_path, "<missing>", actual[key]))
            elif key not in actual:
                differences.append(ProjectionDifference(child_path, expected[key], "<missing>"))
            else:
                differences.extend(_diff_values(expected[key], actual[key], child_path))
        return differences
    if isinstance(expected, list) and isinstance(actual, list):
        for index in range(max(len(expected), len(actual))):
            child_path = f"{path}[{index}]"
            if index >= len(expected):
                differences.append(ProjectionDifference(child_path, "<missing>", actual[index]))
            elif index >= len(actual):
                differences.append(ProjectionDifference(child_path, expected[index], "<missing>"))
            else:
                differences.extend(_diff_values(expected[index], actual[index], child_path))
        return differences
    if expected != actual:
        differences.append(ProjectionDifference(path, expected, actual))
    return differences


__all__ = [
    "AdapterRole",
    "CROSS_MODULE_PROJECTION_SECTION",
    "EvidenceEligibilityAdapter",
    "EvidenceEligibilityProjection",
    "EvidenceProjection",
    "ExecutionProjection",
    "ExternalNetworkAccessError",
    "GoldenAdapter",
    "GoldenAdapterContractError",
    "GoldenComparison",
    "GoldenDataInput",
    "GoldenEvidenceClass",
    "GoldenFixtureError",
    "GoldenHarness",
    "GoldenHarnessError",
    "GoldenHarnessRun",
    "GoldenMismatchError",
    "GoldenProjectionSet",
    "GoldenRiskDecision",
    "LAYER_1_EVIDENCE_LAYER",
    "MinCapRiskDecisionAdapter",
    "OutcomeIdentityAdapter",
    "OutcomeIdentityProjection",
    "OutcomeProjection",
    "OutcomeVersionProjection",
    "PositionCapProjection",
    "PositionProjection",
    "ProjectionDifference",
    "RiskDecisionProjection",
    "RiskProjection",
    "TPlusOneExecutionAdapter",
    "decimal_text",
    "external_network_disabled",
    "load_golden_fixture",
    "load_projection_golden_cases",
]
