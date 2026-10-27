"""Canonical recommendation evidence identity and trusted-source classification.

This module is a pure domain boundary: it has no ORM, API, scheduler, or vendor
imports.  Entry-point adapters map their own trusted execution path to
:class:`RunSource`; caller-owned display metadata such as ``run_name`` and
``evaluation_scope`` is deliberately absent from the builder contract.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from app.services.evidence.live_window import is_before_entry_open, within_live_window


PROVENANCE_SCHEMA_VERSION = 1
_BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]*$")
_UNVERIFIED_IDENTIFIERS = frozenset({"n/a", "none", "unknown", "unavailable"})


class EvidenceIdentityError(ValueError):
    """Stable, caller-safe error for invalid evidence provenance."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class EvidenceClass(StrEnum):
    """Statistical evidence classes that must never be aggregated implicitly."""

    LIVE_FORWARD = "live_forward"
    HISTORICAL_RECONSTRUCTION = "historical_reconstruction"
    RESEARCH_BACKTEST = "research_backtest"
    EXPERIMENT = "experiment"
    UNVERIFIED = "unverified"


class RunSource(StrEnum):
    """Trusted execution paths used to derive, never declare, evidence class.

    These values describe server-owned adapters.  They are intentionally not an
    alias for ``RunRequest.run_mode`` or a caller-provided scope.  In particular,
    no source value itself is named ``LIVE``; the builder grants LIVE eligibility
    only after checking the temporal and provenance facts.
    """

    FORMAL = "formal"
    HISTORICAL_REPLAY = "historical_replay"
    BACKTEST = "backtest"
    EXPERIMENT = "experiment"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EvidenceSnapshotIdentity:
    """The immutable signal-side snapshot facts retained in evidence provenance."""

    source_snapshot_id: str
    data_as_of: str | date

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_snapshot_id",
            _required_identifier(
                self.source_snapshot_id,
                field_name="source_snapshot_id",
                max_length=100,
            ),
        )
        object.__setattr__(
            self,
            "data_as_of",
            _iso_date(self.data_as_of, field_name="data_as_of"),
        )


@dataclass(frozen=True, slots=True)
class RecommendationEvidenceIdentity:
    """Immutable canonical provenance identity for one recommendation run.

    ``actual_generated_at`` is normalized to UTC while LIVE business-date
    eligibility is evaluated in Asia/Shanghai.  ``recommended_at`` and legacy
    scope fields are intentionally not identity components.
    """

    evidence_class: EvidenceClass
    signal_date: str | date
    actual_generated_at: datetime
    data_as_of: str | date
    source_snapshot_id: str
    strategy_policy_fingerprint: str
    execution_policy_fingerprint: str
    code_revision: str
    reconstruction_batch_id: str | None = None
    provenance_schema_version: int = PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_class, EvidenceClass):
            raise EvidenceIdentityError(
                "invalid_evidence_class",
                "evidence_class must be an EvidenceClass value",
            )
        if (
            isinstance(self.provenance_schema_version, bool)
            or self.provenance_schema_version != PROVENANCE_SCHEMA_VERSION
        ):
            raise EvidenceIdentityError(
                "unsupported_provenance_schema",
                f"provenance_schema_version must be {PROVENANCE_SCHEMA_VERSION}",
            )

        signal_date = _iso_date(self.signal_date, field_name="signal_date")
        actual_generated_at = _aware_utc_datetime(self.actual_generated_at)
        data_as_of = _iso_date(self.data_as_of, field_name="data_as_of")
        source_snapshot_id = _required_identifier(
            self.source_snapshot_id,
            field_name="source_snapshot_id",
            max_length=100,
        )
        strategy_fingerprint = _sha256_fingerprint(
            self.strategy_policy_fingerprint,
            field_name="strategy_policy_fingerprint",
        )
        execution_fingerprint = _sha256_fingerprint(
            self.execution_policy_fingerprint,
            field_name="execution_policy_fingerprint",
        )
        code_revision = _required_identifier(
            self.code_revision,
            field_name="code_revision",
            max_length=64,
        )

        batch_id = self.reconstruction_batch_id
        if self.evidence_class is EvidenceClass.HISTORICAL_RECONSTRUCTION:
            batch_id = _required_identifier(
                batch_id,
                field_name="reconstruction_batch_id",
                max_length=80,
            )
        elif batch_id is not None:
            raise EvidenceIdentityError(
                "unexpected_reconstruction_batch",
                "reconstruction_batch_id is only valid for historical reconstruction",
            )

        if self.evidence_class in {
            EvidenceClass.LIVE_FORWARD,
            EvidenceClass.HISTORICAL_RECONSTRUCTION,
        } and data_as_of > signal_date:
            raise EvidenceIdentityError(
                "future_data_as_of",
                f"{self.evidence_class.value} requires data_as_of on or before signal_date",
            )

        if self.evidence_class in {
            EvidenceClass.LIVE_FORWARD,
            EvidenceClass.HISTORICAL_RECONSTRUCTION,
        } and (
            not _has_verified_identifier(source_snapshot_id)
            or not _has_verified_identifier(code_revision)
        ):
            raise EvidenceIdentityError(
                "incomplete_provenance",
                f"{self.evidence_class.value} requires a verified snapshot id and code revision",
            )

        if self.evidence_class is EvidenceClass.LIVE_FORWARD:
            # 精确判定（入场日 09:15 前）在生成时按交易日历做（见 _classify / live_window）；
            # 读回时只校验生成日落在信号日之后的实盘窗口内，挡住手写或错位的 LIVE 记录。
            if not within_live_window(date.fromisoformat(signal_date), actual_generated_at):
                raise EvidenceIdentityError(
                    "live_generation_date_mismatch",
                    "LIVE_FORWARD requires generation after the signal date and before the entry session",
                )

        object.__setattr__(self, "signal_date", signal_date)
        object.__setattr__(self, "actual_generated_at", actual_generated_at)
        object.__setattr__(self, "data_as_of", data_as_of)
        object.__setattr__(self, "source_snapshot_id", source_snapshot_id)
        object.__setattr__(self, "strategy_policy_fingerprint", strategy_fingerprint)
        object.__setattr__(self, "execution_policy_fingerprint", execution_fingerprint)
        object.__setattr__(self, "code_revision", code_revision)
        object.__setattr__(self, "reconstruction_batch_id", batch_id)

    @property
    def is_true_forward(self) -> bool:
        """Return whether this identity is eligible to be considered true-forward."""

        return self.evidence_class is EvidenceClass.LIVE_FORWARD

    def canonical_payload(self) -> dict[str, Any]:
        """Return the complete deterministic persistence/hash payload."""

        return {
            "actual_generated_at": _canonical_utc_datetime(self.actual_generated_at),
            "code_revision": self.code_revision,
            "data_as_of": self.data_as_of,
            "evidence_class": self.evidence_class.value,
            "execution_policy_fingerprint": self.execution_policy_fingerprint,
            "provenance_schema_version": self.provenance_schema_version,
            "reconstruction_batch_id": self.reconstruction_batch_id,
            "signal_date": self.signal_date,
            "source_snapshot_id": self.source_snapshot_id,
            "strategy_policy_fingerprint": self.strategy_policy_fingerprint,
        }

    def to_canonical_json(self) -> str:
        """Return canonical UTF-8 JSON with explicit null/default values."""

        return json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @property
    def identity_hash(self) -> str:
        """Return the SHA-256 identity used by durable evidence projections."""

        return hashlib.sha256(self.to_canonical_json().encode("utf-8")).hexdigest()

    def fingerprint(self) -> str:
        """Domain alias for :attr:`identity_hash`."""

        return self.identity_hash


class EvidenceIdentityBuilder:
    """Build evidence identity from trusted source and immutable runtime facts.

    The method intentionally accepts neither ``run_name`` nor caller scope.  A raw
    string is also rejected for ``run_source`` so API payloads cannot flow directly
    into evidence classification without an adapter selecting a trusted enum value.
    """

    def build(
        self,
        *,
        run_source: RunSource,
        actual_generated_at: datetime,
        signal_date: str | date,
        snapshot: EvidenceSnapshotIdentity,
        strategy_policy: object,
        execution_policy: object,
        code_revision: str,
        reconstruction_batch_id: str | None = None,
        next_open_date: date | None = None,
    ) -> RecommendationEvidenceIdentity:
        """next_open_date：信号日之后的第一个开市日（适配器查交易日历得到）；None 时按下一个工作日（偏严）。"""
        if not isinstance(run_source, RunSource):
            raise EvidenceIdentityError(
                "invalid_run_source",
                "run_source must be selected by a trusted adapter as RunSource",
            )
        if not isinstance(snapshot, EvidenceSnapshotIdentity):
            raise EvidenceIdentityError(
                "invalid_snapshot_identity",
                "snapshot must be an EvidenceSnapshotIdentity",
            )

        normalized_signal_date = _iso_date(signal_date, field_name="signal_date")
        generated_at = _aware_utc_datetime(actual_generated_at)
        strategy_fingerprint = _policy_fingerprint(
            strategy_policy,
            field_name="strategy_policy",
        )
        execution_fingerprint = _policy_fingerprint(
            execution_policy,
            field_name="execution_policy",
        )
        normalized_revision = _required_identifier(
            code_revision,
            field_name="code_revision",
            max_length=64,
        )

        evidence_class = self._classify(
            run_source=run_source,
            actual_generated_at=generated_at,
            signal_date=normalized_signal_date,
            snapshot=snapshot,
            code_revision=normalized_revision,
            next_open_date=next_open_date,
        )
        return RecommendationEvidenceIdentity(
            evidence_class=evidence_class,
            signal_date=normalized_signal_date,
            actual_generated_at=generated_at,
            data_as_of=snapshot.data_as_of,
            source_snapshot_id=snapshot.source_snapshot_id,
            strategy_policy_fingerprint=strategy_fingerprint,
            execution_policy_fingerprint=execution_fingerprint,
            code_revision=normalized_revision,
            reconstruction_batch_id=reconstruction_batch_id,
            provenance_schema_version=PROVENANCE_SCHEMA_VERSION,
        )

    @staticmethod
    def _classify(
        *,
        run_source: RunSource,
        actual_generated_at: datetime,
        signal_date: str,
        snapshot: EvidenceSnapshotIdentity,
        code_revision: str,
        next_open_date: date | None = None,
    ) -> EvidenceClass:
        if run_source is RunSource.HISTORICAL_REPLAY:
            return EvidenceClass.HISTORICAL_RECONSTRUCTION
        if run_source is RunSource.BACKTEST:
            return EvidenceClass.RESEARCH_BACKTEST
        if run_source is RunSource.EXPERIMENT:
            return EvidenceClass.EXPERIMENT
        if run_source is not RunSource.FORMAL:
            return EvidenceClass.UNVERIFIED

        # 入场日开盘（09:15）前生成才算实盘：信号日盘后、凌晨补跑、安全网补跑都一样按生成时间判（live_window）
        if not is_before_entry_open(date.fromisoformat(signal_date), actual_generated_at, next_open_date):
            return EvidenceClass.UNVERIFIED
        if snapshot.data_as_of > signal_date:
            return EvidenceClass.UNVERIFIED
        if not _has_verified_identifier(snapshot.source_snapshot_id):
            return EvidenceClass.UNVERIFIED
        if not _has_verified_identifier(code_revision):
            return EvidenceClass.UNVERIFIED
        return EvidenceClass.LIVE_FORWARD


def build_recommendation_evidence_identity(
    *,
    run_source: RunSource,
    actual_generated_at: datetime,
    signal_date: str | date,
    snapshot: EvidenceSnapshotIdentity,
    strategy_policy: object,
    execution_policy: object,
    code_revision: str,
    reconstruction_batch_id: str | None = None,
    next_open_date: date | None = None,
) -> RecommendationEvidenceIdentity:
    """Functional façade for adapters that do not need to retain a builder."""

    return EvidenceIdentityBuilder().build(
        run_source=run_source,
        actual_generated_at=actual_generated_at,
        signal_date=signal_date,
        snapshot=snapshot,
        strategy_policy=strategy_policy,
        execution_policy=execution_policy,
        code_revision=code_revision,
        reconstruction_batch_id=reconstruction_batch_id,
        next_open_date=next_open_date,
    )


def identity_from_persisted_run(row: Any) -> RecommendationEvidenceIdentity:
    """Rebuild the canonical identity solely from persisted run provenance."""

    generated_at = _value(row, "actual_generated_at")
    if isinstance(generated_at, datetime) and (
        generated_at.tzinfo is None or generated_at.utcoffset() is None
    ):
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    try:
        evidence_class = EvidenceClass(str(_value(row, "evidence_class") or ""))
    except ValueError as exc:
        raise EvidenceIdentityError(
            "invalid_evidence_class",
            "persisted evidence_class is not canonical",
        ) from exc
    return RecommendationEvidenceIdentity(
        evidence_class=evidence_class,
        signal_date=_value(row, "trade_date"),
        actual_generated_at=generated_at,
        data_as_of=_value(row, "data_as_of"),
        source_snapshot_id=_value(row, "source_snapshot_id"),
        strategy_policy_fingerprint=_value(row, "strategy_policy_fingerprint"),
        execution_policy_fingerprint=_value(row, "execution_policy_fingerprint"),
        code_revision=_value(row, "code_revision"),
        reconstruction_batch_id=_value(row, "reconstruction_batch_id"),
        provenance_schema_version=_value(row, "provenance_schema_version"),
    )


def has_canonical_persisted_identity(
    row: Any,
    *,
    expected_class: EvidenceClass | None = None,
) -> bool:
    """Return whether persisted provenance exactly matches its canonical hash."""

    try:
        identity = identity_from_persisted_run(row)
    except (EvidenceIdentityError, TypeError, ValueError):
        return False
    if expected_class is not None and identity.evidence_class is not expected_class:
        return False
    persisted_hash = _value(row, "evidence_identity_hash")
    if not isinstance(persisted_hash, str):
        return False
    normalized_hash = persisted_hash.lower()
    if not _SHA256_PATTERN.fullmatch(normalized_hash):
        return False
    return hmac.compare_digest(normalized_hash, identity.identity_hash)


def _value(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _iso_date(value: object, *, field_name: str) -> str:
    if isinstance(value, datetime):
        raise EvidenceIdentityError(
            "invalid_iso_date",
            f"{field_name} must be a date, not a datetime",
        )
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not value or value != value.strip():
        raise EvidenceIdentityError(
            "invalid_iso_date",
            f"{field_name} must be an ISO date (YYYY-MM-DD)",
        )
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceIdentityError(
            "invalid_iso_date",
            f"{field_name} must be an ISO date (YYYY-MM-DD)",
        ) from exc
    if parsed.isoformat() != value:
        raise EvidenceIdentityError(
            "invalid_iso_date",
            f"{field_name} must be an ISO date (YYYY-MM-DD)",
        )
    return value


def _aware_utc_datetime(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceIdentityError(
            "invalid_actual_generated_at",
            "actual_generated_at must be a timezone-aware datetime",
        )
    return value.astimezone(timezone.utc)


def _canonical_utc_datetime(value: datetime) -> str:
    normalized = _aware_utc_datetime(value)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _required_identifier(value: object, *, field_name: str, max_length: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise EvidenceIdentityError(
            "invalid_provenance_identifier",
            f"{field_name} must be a non-empty canonical identifier",
        )
    if len(value) > max_length or not _SAFE_IDENTIFIER_PATTERN.fullmatch(value):
        raise EvidenceIdentityError(
            "invalid_provenance_identifier",
            f"{field_name} must be a safe canonical identifier of at most {max_length} characters",
        )
    return value


def _has_verified_identifier(value: str) -> bool:
    return value.lower() not in _UNVERIFIED_IDENTIFIERS


def _sha256_fingerprint(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise EvidenceIdentityError(
            "invalid_policy_fingerprint",
            f"{field_name} must be a 64-character SHA-256 hex digest",
        )
    normalized = value.lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise EvidenceIdentityError(
            "invalid_policy_fingerprint",
            f"{field_name} must be a 64-character SHA-256 hex digest",
        )
    return normalized


def canonical_policy_fingerprint(
    policy: object,
    *,
    field_name: str = "policy",
) -> str:
    """Return the canonical SHA-256 fingerprint used by evidence identities."""

    return _policy_fingerprint(policy, field_name=field_name)


def _policy_fingerprint(policy: object, *, field_name: str) -> str:
    candidate = _policy_fingerprint_candidate(policy, field_name=field_name)
    return _sha256_fingerprint(candidate, field_name=f"{field_name}_fingerprint")


def _policy_fingerprint_candidate(policy: object, *, field_name: str) -> object:
    if isinstance(policy, str):
        return policy
    if isinstance(policy, Mapping):
        candidate = policy.get("fingerprint")
        if candidate is None:
            raise EvidenceIdentityError(
                "invalid_policy_identity",
                f"{field_name} mapping must expose a fingerprint entry",
            )
        return candidate
    if isinstance(policy, (list, tuple)):
        if not policy:
            raise EvidenceIdentityError(
                "invalid_policy_identity",
                f"{field_name} must contain at least one policy entry",
            )
        normalized = [
            _normalized_policy_entry(entry, field_name=field_name) for entry in policy
        ]
        canonical = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    try:
        candidate = getattr(policy, "fingerprint")
    except (AttributeError, TypeError) as exc:
        raise EvidenceIdentityError(
            "invalid_policy_identity",
            f"{field_name} must be a SHA-256 digest or expose fingerprint",
        ) from exc
    if callable(candidate):
        try:
            candidate = candidate()
        except Exception as exc:
            raise EvidenceIdentityError(
                "invalid_policy_identity",
                f"{field_name}.fingerprint could not be evaluated",
            ) from exc
    return candidate


def _normalized_policy_entry(entry: object, *, field_name: str) -> object:
    if isinstance(entry, str):
        return _sha256_fingerprint(entry, field_name=f"{field_name}_entry_fingerprint")
    if isinstance(entry, Mapping):
        raw_fingerprint = entry.get("fingerprint")
        if raw_fingerprint is None:
            components: dict[str, str] = {}
            strategy_id = entry.get("strategy_id")
            if not isinstance(strategy_id, str) or not strategy_id:
                raise EvidenceIdentityError(
                    "invalid_policy_identity",
                    f"{field_name} entry must include strategy_id",
                )
            components["strategy_id"] = strategy_id
            for key in (
                "effective_version",
                "implementation_fingerprint",
                "manifest_fingerprint",
            ):
                components[key] = _sha256_fingerprint(
                    entry.get(key), field_name=f"{field_name}_entry_{key}"
                )
            canonical = json.dumps(
                components,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            raw_fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        fingerprint = _sha256_fingerprint(
            raw_fingerprint,
            field_name=f"{field_name}_entry_fingerprint",
        )
        normalized: dict[str, object] = {"fingerprint": fingerprint}
        for key in (
            "strategy_id",
            "strategy_version",
            "effective_version",
            "policy_id",
            "policy_version",
        ):
            value = entry.get(key)
            if isinstance(value, str) and value:
                normalized[key] = value
        return normalized
    candidate = getattr(entry, "fingerprint", None)
    if candidate is None:
        raise EvidenceIdentityError(
            "invalid_policy_identity",
            f"{field_name} entries must be SHA-256 digests or expose fingerprint",
        )
    if callable(candidate):
        try:
            candidate = candidate()
        except Exception as exc:
            raise EvidenceIdentityError(
                "invalid_policy_identity",
                f"{field_name} entry fingerprint could not be evaluated",
            ) from exc
    return _sha256_fingerprint(candidate, field_name=f"{field_name}_entry_fingerprint")


__all__ = [
    "EvidenceClass",
    "EvidenceIdentityBuilder",
    "EvidenceIdentityError",
    "EvidenceSnapshotIdentity",
    "PROVENANCE_SCHEMA_VERSION",
    "RecommendationEvidenceIdentity",
    "RunSource",
    "build_recommendation_evidence_identity",
    "canonical_policy_fingerprint",
    "has_canonical_persisted_identity",
    "identity_from_persisted_run",
]
