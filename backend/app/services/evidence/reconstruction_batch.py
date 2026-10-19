"""Version-bound manifests for historical reconstruction cohorts."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, load_only

from app.models.tables import (
    RecommendationReconstructionBatch,
    RecommendationRun,
)
from app.services.evidence.identity import (
    EvidenceClass,
    EvidenceSnapshotIdentity,
    RunSource,
    build_recommendation_evidence_identity,
    canonical_policy_fingerprint,
    has_canonical_persisted_identity,
)
from app.services.runs.coordinator import WorkerLease


RECONSTRUCTION_BATCH_SCHEMA_VERSION = 1
RECONSTRUCTION_BATCH_STATUS_BUILDING = "building"
RECONSTRUCTION_BATCH_STATUS_COMPLETED = "completed"
RECONSTRUCTION_BATCH_STATUS_FAILED = "failed"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_BATCH_PREFIX = f"recon-v{RECONSTRUCTION_BATCH_SCHEMA_VERSION}-"


class ReconstructionBatchError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReconstructionBatchIdentity:
    batch_id: str
    window_start: str
    window_end: str
    expected_trade_dates: tuple[str, ...]
    request_template_fingerprint: str
    strategy_policy_fingerprint: str
    execution_policy_fingerprint: str
    code_revision: str
    schema_version: int = RECONSTRUCTION_BATCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        dates = _canonical_trade_dates(self.expected_trade_dates)
        if self.schema_version != RECONSTRUCTION_BATCH_SCHEMA_VERSION:
            raise ReconstructionBatchError("unsupported reconstruction batch schema")
        if self.window_start != dates[0] or self.window_end != dates[-1]:
            raise ReconstructionBatchError("batch window must match the expected trade dates")
        request_fingerprint = _sha256(
            self.request_template_fingerprint,
            field_name="request_template_fingerprint",
        )
        strategy_fingerprint = _sha256(
            self.strategy_policy_fingerprint,
            field_name="strategy_policy_fingerprint",
        )
        execution_fingerprint = _sha256(
            self.execution_policy_fingerprint,
            field_name="execution_policy_fingerprint",
        )
        revision = _code_revision(self.code_revision)
        expected_batch_id = _batch_id(
            window_start=self.window_start,
            window_end=self.window_end,
            expected_trade_dates=dates,
            request_template_fingerprint=request_fingerprint,
            strategy_policy_fingerprint=strategy_fingerprint,
            execution_policy_fingerprint=execution_fingerprint,
            code_revision=revision,
            schema_version=self.schema_version,
        )
        if not hmac.compare_digest(str(self.batch_id), expected_batch_id):
            raise ReconstructionBatchError("batch_id does not match its canonical manifest")
        object.__setattr__(self, "expected_trade_dates", dates)
        object.__setattr__(self, "request_template_fingerprint", request_fingerprint)
        object.__setattr__(self, "strategy_policy_fingerprint", strategy_fingerprint)
        object.__setattr__(self, "execution_policy_fingerprint", execution_fingerprint)
        object.__setattr__(self, "code_revision", revision)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "code_revision": self.code_revision,
            "execution_policy_fingerprint": self.execution_policy_fingerprint,
            "expected_trade_dates": list(self.expected_trade_dates),
            "request_template_fingerprint": self.request_template_fingerprint,
            "schema_version": self.schema_version,
            "strategy_policy_fingerprint": self.strategy_policy_fingerprint,
            "window_end": self.window_end,
            "window_start": self.window_start,
        }


@dataclass(frozen=True, slots=True)
class ValidatedReconstructionCohort:
    batch_id: str
    window_start: str
    window_end: str
    expected_trade_dates: tuple[str, ...]
    run_ids: tuple[str, ...]
    completed_at: datetime
    cohort_identity_hash: str
    request_template_fingerprint: str
    strategy_policy_fingerprint: str
    execution_policy_fingerprint: str
    code_revision: str
    # 策略身份（不含 run_name / attempt-tag）：同一策略的不同批次按它去重。
    strategy_key: str = ""
    strategy_label: str = ""
    # 同策略更新批次已覆盖、因此从本 cohort 剔除的交易日数（窗口不同但重叠时）。
    superseded_trade_date_count: int = 0

    def as_metadata(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "batch_count": 1,
            "run_count": len(self.run_ids),
            "strategy_key": self.strategy_key,
            "strategy_label": self.strategy_label,
            "superseded_trade_date_count": self.superseded_trade_date_count,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "expected_trade_date_count": len(self.expected_trade_dates),
            "completed_trade_date_count": len(self.run_ids),
            "completed_at": self.completed_at.isoformat(),
            "cohort_identity_hash": self.cohort_identity_hash,
            "request_template_fingerprint": self.request_template_fingerprint,
            "strategy_policy_fingerprint": self.strategy_policy_fingerprint,
            "execution_policy_fingerprint": self.execution_policy_fingerprint,
            "code_revision": self.code_revision,
        }


@dataclass(frozen=True, slots=True)
class MergedReconstructionCohort:
    """Union of the latest validated cohort of each strategy (2026-10-19).

    Short-line replay is rebuilt per strategy (one batch per single-skill
    template), so Metrics / review / dashboard consumers must aggregate all of
    them.  Exposes the same read surface as :class:`ValidatedReconstructionCohort`.
    """

    cohorts: tuple[ValidatedReconstructionCohort, ...]

    @property
    def run_ids(self) -> tuple[str, ...]:
        return tuple(run_id for cohort in self.cohorts for run_id in cohort.run_ids)

    @property
    def expected_trade_dates(self) -> tuple[str, ...]:
        return tuple(sorted({d for cohort in self.cohorts for d in cohort.expected_trade_dates}))

    @property
    def batch_id(self) -> str:
        return ",".join(cohort.batch_id for cohort in self.cohorts)

    @property
    def window_start(self) -> str:
        return min(cohort.window_start for cohort in self.cohorts)

    @property
    def window_end(self) -> str:
        return max(cohort.window_end for cohort in self.cohorts)

    @property
    def completed_at(self) -> datetime:
        return max(cohort.completed_at for cohort in self.cohorts)

    @property
    def cohort_identity_hash(self) -> str:
        payload = sorted(
            [cohort.batch_id, cohort.cohort_identity_hash, list(cohort.run_ids)]
            for cohort in self.cohorts
        )
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()

    def _joined(self, attribute: str) -> str:
        values: list[str] = []
        for cohort in self.cohorts:
            value = str(getattr(cohort, attribute))
            if value not in values:
                values.append(value)
        return ",".join(values)

    @property
    def request_template_fingerprint(self) -> str:
        return self._joined("request_template_fingerprint")

    @property
    def strategy_policy_fingerprint(self) -> str:
        return self._joined("strategy_policy_fingerprint")

    @property
    def execution_policy_fingerprint(self) -> str:
        return self._joined("execution_policy_fingerprint")

    @property
    def code_revision(self) -> str:
        return self._joined("code_revision")

    def as_metadata(self) -> dict[str, Any]:
        dates = self.expected_trade_dates
        return {
            "batch_id": self.batch_id,
            "batch_count": len(self.cohorts),
            "run_count": len(self.run_ids),
            "strategy_key": self._joined("strategy_key"),
            "strategy_label": self._joined("strategy_label"),
            "superseded_trade_date_count": sum(
                cohort.superseded_trade_date_count for cohort in self.cohorts
            ),
            "window_start": self.window_start,
            "window_end": self.window_end,
            "expected_trade_date_count": len(dates),
            "completed_trade_date_count": len(dates),
            "completed_at": self.completed_at.isoformat(),
            "cohort_identity_hash": self.cohort_identity_hash,
            "request_template_fingerprint": self.request_template_fingerprint,
            "strategy_policy_fingerprint": self.strategy_policy_fingerprint,
            "execution_policy_fingerprint": self.execution_policy_fingerprint,
            "code_revision": self.code_revision,
            "batches": [cohort.as_metadata() for cohort in self.cohorts],
        }


ReconstructionCohortView = ValidatedReconstructionCohort | MergedReconstructionCohort


def merge_reconstruction_cohorts(
    cohorts: Sequence[ValidatedReconstructionCohort],
) -> ReconstructionCohortView | None:
    """None / the single cohort itself (seal unchanged) / a merged view."""

    items = tuple(cohort for cohort in cohorts if cohort.run_ids)
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    return MergedReconstructionCohort(cohorts=items)


def reconstruction_strategy_identity(
    request: Mapping[str, Any] | str | bytes | bytearray | None,
    *,
    strategy_policy_fingerprint: str = "",
) -> tuple[str, str]:
    """Strategy identity of a reconstruction batch, independent of run_name / attempt-tag.

    Taken from the manifest-verified request template (``preset_id`` or the set of
    ``skills[].skill_id``).  ``strategy_policy_fingerprint`` is *not* used as the
    primary key because it embeds each skill's implementation hash and so changes
    with code revisions of the same strategy; it is only the fallback when the
    template carries no strategy selector.
    """

    payload: Any = None
    if isinstance(request, Mapping):
        payload = dict(request)
    elif request:
        try:
            payload = json.loads(request)
        except (TypeError, ValueError):
            payload = None
    if isinstance(payload, dict):
        preset = str(payload.get("preset_id") or "").strip()
        if preset:
            return f"preset:{preset}", preset
        skills = payload.get("skills")
        skill_ids = sorted(
            {
                str(item.get("skill_id")).strip()
                for item in (skills if isinstance(skills, list) else [])
                if isinstance(item, Mapping) and str(item.get("skill_id") or "").strip()
            }
        )
        if skill_ids:
            return "skills:" + "+".join(skill_ids), "+".join(skill_ids)
    fingerprint = str(strategy_policy_fingerprint or "").lower()
    return f"policy:{fingerprint}", f"policy:{fingerprint[:12]}"


def canonical_request_template_fingerprint(
    request: Mapping[str, Any] | str | bytes | bytearray,
) -> str:
    """Hash a canonical request after removing its target trading date."""

    if isinstance(request, (str, bytes, bytearray)):
        try:
            payload = json.loads(request)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ReconstructionBatchError("request template is not valid JSON") from exc
    else:
        payload = dict(request)
    if not isinstance(payload, dict):
        raise ReconstructionBatchError("request template must be an object")
    normalized = dict(payload)
    normalized["trade_date_override"] = None
    canonical = _canonical_json(normalized)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_reconstruction_batch_identity(
    *,
    expected_trade_dates: Iterable[str],
    request_template: Mapping[str, Any] | str | bytes | bytearray,
    strategy_policy: object,
    execution_policy: object,
    code_revision: str,
) -> ReconstructionBatchIdentity:
    dates = _canonical_trade_dates(expected_trade_dates)
    request_fingerprint = canonical_request_template_fingerprint(request_template)
    strategy_fingerprint = canonical_policy_fingerprint(
        strategy_policy,
        field_name="strategy_policy",
    )
    execution_fingerprint = canonical_policy_fingerprint(
        execution_policy,
        field_name="execution_policy",
    )
    revision = _code_revision(code_revision)
    batch_id = _batch_id(
        window_start=dates[0],
        window_end=dates[-1],
        expected_trade_dates=dates,
        request_template_fingerprint=request_fingerprint,
        strategy_policy_fingerprint=strategy_fingerprint,
        execution_policy_fingerprint=execution_fingerprint,
        code_revision=revision,
        schema_version=RECONSTRUCTION_BATCH_SCHEMA_VERSION,
    )
    return ReconstructionBatchIdentity(
        batch_id=batch_id,
        window_start=dates[0],
        window_end=dates[-1],
        expected_trade_dates=dates,
        request_template_fingerprint=request_fingerprint,
        strategy_policy_fingerprint=strategy_fingerprint,
        execution_policy_fingerprint=execution_fingerprint,
        code_revision=revision,
    )


def identity_from_persisted_batch(
    batch: RecommendationReconstructionBatch,
) -> ReconstructionBatchIdentity:
    return ReconstructionBatchIdentity(
        batch_id=str(batch.batch_id),
        window_start=str(batch.window_start),
        window_end=str(batch.window_end),
        expected_trade_dates=_load_expected_dates(batch.expected_trade_dates_json),
        request_template_fingerprint=str(batch.request_template_fingerprint),
        strategy_policy_fingerprint=str(batch.strategy_policy_fingerprint),
        execution_policy_fingerprint=str(batch.execution_policy_fingerprint),
        code_revision=str(batch.code_revision),
        schema_version=int(batch.schema_version),
    )


def create_or_resume_reconstruction_batch(
    db: Session,
    identity: ReconstructionBatchIdentity,
) -> RecommendationReconstructionBatch:
    batch = db.get(RecommendationReconstructionBatch, identity.batch_id)
    if batch is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        batch = RecommendationReconstructionBatch(
            batch_id=identity.batch_id,
            schema_version=identity.schema_version,
            window_start=identity.window_start,
            window_end=identity.window_end,
            expected_trade_dates_json=_canonical_json(
                list(identity.expected_trade_dates)
            ),
            expected_trade_date_count=len(identity.expected_trade_dates),
            completed_trade_date_count=0,
            request_template_fingerprint=identity.request_template_fingerprint,
            strategy_policy_fingerprint=identity.strategy_policy_fingerprint,
            execution_policy_fingerprint=identity.execution_policy_fingerprint,
            code_revision=identity.code_revision,
            cohort_identity_hash=None,
            status=RECONSTRUCTION_BATCH_STATUS_BUILDING,
            created_at=now,
            updated_at=now,
        )
        db.add(batch)
        db.flush()
        return batch
    persisted = identity_from_persisted_batch(batch)
    if persisted != identity:
        raise ReconstructionBatchError("persisted batch manifest conflicts with this request")
    if batch.status == RECONSTRUCTION_BATCH_STATUS_FAILED:
        raise ReconstructionBatchError(
            "failed reconstruction batch requires explicit append-only recovery"
        )
    if batch.status not in {
        RECONSTRUCTION_BATCH_STATUS_BUILDING,
        RECONSTRUCTION_BATCH_STATUS_COMPLETED,
    }:
        raise ReconstructionBatchError("reconstruction batch has an invalid lifecycle status")
    return batch


def bind_reconstruction_worker_revision(
    db: Session,
    run_id: str,
    *,
    worker_lease: WorkerLease,
    code_revision: str | None = None,
) -> str | None:
    """Bind only the calling worker's reconstruction run to its code revision."""
    if worker_lease.run_id != run_id:
        raise ReconstructionBatchError("worker lease does not belong to reconstruction run")
    run = db.get(RecommendationRun, run_id)
    if run is None:
        raise ReconstructionBatchError(f"reconstruction run does not exist: {run_id}")
    reconstruction_marked = (
        str(run.evidence_class) == EvidenceClass.HISTORICAL_RECONSTRUCTION.value
        or bool(run.reconstruction_batch_id)
    )
    if not reconstruction_marked:
        return None
    if (
        str(run.evidence_class) != EvidenceClass.HISTORICAL_RECONSTRUCTION.value
        or str(run.evaluation_scope) != "historical_replay"
        or not run.reconstruction_batch_id
    ):
        raise ReconstructionBatchError(
            "reconstruction run must declare historical scope, evidence class, and batch"
        )
    lease_expires_at = run.lease_expires_at
    if isinstance(lease_expires_at, datetime) and lease_expires_at.tzinfo is not None:
        lease_expires_at = lease_expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if (
        str(run.status) != "running"
        or int(run.run_revision or 0) != worker_lease.revision
        or str(run.lease_token or "") != worker_lease.token
        or not isinstance(lease_expires_at, datetime)
        or lease_expires_at <= now
    ):
        raise ReconstructionBatchError(
            "reconstruction worker revision requires the caller-owned active worker lease"
        )

    if code_revision is None:
        from app.services.evidence.provenance_writer import resolve_code_revision

        code_revision = resolve_code_revision()
    revision = _code_revision(code_revision)
    batch = db.get(
        RecommendationReconstructionBatch,
        str(run.reconstruction_batch_id),
    )
    if batch is None:
        raise ReconstructionBatchError("reconstruction run has no persisted batch manifest")
    identity = identity_from_persisted_batch(batch)
    if revision != identity.code_revision:
        raise ReconstructionBatchError(
            "reconstruction worker revision does not match its batch manifest"
        )
    persisted = str(run.code_revision or "").lower()
    if persisted and persisted != revision:
        raise ReconstructionBatchError(
            "reconstruction run is already bound to another worker revision"
        )
    bound = db.execute(
        update(RecommendationRun)
        .where(
            RecommendationRun.run_id == run_id,
            RecommendationRun.status == "running",
            RecommendationRun.run_revision == worker_lease.revision,
            RecommendationRun.lease_token == worker_lease.token,
            RecommendationRun.lease_expires_at > now,
            or_(
                RecommendationRun.code_revision.is_(None),
                RecommendationRun.code_revision == revision,
            ),
        )
        .values(code_revision=revision)
    )
    db.flush()
    if bound.rowcount != 1:
        raise ReconstructionBatchError(
            "reconstruction worker revision lost caller-owned worker lease"
        )
    db.expire(run, ["code_revision"])
    return revision


def finalize_reconstruction_run(db: Session, run_id: str) -> None:
    """Seal completed historical reconstruction provenance in the worker transaction."""
    run = db.get(RecommendationRun, run_id)
    if run is None:
        raise ReconstructionBatchError(f"reconstruction run does not exist: {run_id}")
    reconstruction_marked = (
        str(run.evidence_class) == EvidenceClass.HISTORICAL_RECONSTRUCTION.value
        or bool(run.reconstruction_batch_id)
    )
    if not reconstruction_marked:
        return
    if (
        str(run.run_mode) != "formal"
        or str(run.evaluation_scope) != "historical_replay"
        or str(run.evidence_class) != EvidenceClass.HISTORICAL_RECONSTRUCTION.value
        or not run.reconstruction_batch_id
    ):
        raise ReconstructionBatchError(
            "reconstruction run must declare formal mode, historical scope, evidence class, and batch"
        )
    if str(run.status) != "completed":
        raise ReconstructionBatchError("only completed reconstruction runs may be sealed")
    batch_id = str(run.reconstruction_batch_id or "")
    if not batch_id:
        raise ReconstructionBatchError("reconstruction run lacks a batch manifest")
    batch = db.get(RecommendationReconstructionBatch, batch_id)
    if batch is None:
        raise ReconstructionBatchError("reconstruction batch manifest does not exist")
    identity = identity_from_persisted_batch(batch)
    if str(run.code_revision or "").lower() != identity.code_revision:
        raise ReconstructionBatchError("reconstruction run worker revision does not match batch")
    if canonical_request_template_fingerprint(str(run.request_json or "")) != (
        identity.request_template_fingerprint
    ):
        raise ReconstructionBatchError("reconstruction request does not match batch template")

    try:
        summary = json.loads(run.result_summary_json or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReconstructionBatchError("reconstruction execution audit is not valid JSON") from exc
    execution = summary.get("execution") if isinstance(summary, dict) else None
    strategy_policy = execution.get("strategy_fingerprints") if isinstance(execution, dict) else None
    execution_policy = execution.get("execution_policy") if isinstance(execution, dict) else None
    if not isinstance(strategy_policy, list) or not strategy_policy:
        raise ReconstructionBatchError("reconstruction execution audit lacks strategy fingerprints")
    if not isinstance(execution_policy, dict):
        raise ReconstructionBatchError("reconstruction execution audit lacks execution policy")
    if canonical_policy_fingerprint(strategy_policy, field_name="strategy_policy") != (
        identity.strategy_policy_fingerprint
    ) or canonical_policy_fingerprint(execution_policy, field_name="execution_policy") != (
        identity.execution_policy_fingerprint
    ):
        raise ReconstructionBatchError("reconstruction execution policy does not match batch")

    generated_at = run.actual_generated_at
    if not isinstance(generated_at, datetime):
        raise ReconstructionBatchError("reconstruction run lacks actual_generated_at")
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    else:
        generated_at = generated_at.astimezone(timezone.utc)
    signal_date = str(run.trade_date or "")
    data_as_of = str(run.data_as_of or "")
    source_snapshot_id = str(run.source_snapshot_id or "")
    if not signal_date or not data_as_of or not source_snapshot_id:
        raise ReconstructionBatchError("reconstruction run lacks signal or snapshot identity")
    if signal_date not in identity.expected_trade_dates:
        raise ReconstructionBatchError("reconstruction signal date is outside its batch manifest")
    if data_as_of > signal_date:
        raise ReconstructionBatchError("reconstruction signal reader exceeded its signal date")
    evidence = build_recommendation_evidence_identity(
        run_source=RunSource.HISTORICAL_REPLAY,
        actual_generated_at=generated_at,
        signal_date=signal_date,
        snapshot=EvidenceSnapshotIdentity(
            source_snapshot_id=source_snapshot_id,
            data_as_of=data_as_of,
        ),
        strategy_policy=strategy_policy,
        execution_policy=execution_policy,
        code_revision=identity.code_revision,
        reconstruction_batch_id=identity.batch_id,
    )
    if evidence.evidence_class is not EvidenceClass.HISTORICAL_RECONSTRUCTION:
        raise ReconstructionBatchError("reconstruction evidence builder returned a non-historical class")
    run.actual_generated_at = evidence.actual_generated_at.replace(tzinfo=None)
    run.evidence_identity_hash = evidence.identity_hash
    run.strategy_policy_fingerprint = evidence.strategy_policy_fingerprint
    run.execution_policy_fingerprint = evidence.execution_policy_fingerprint
    run.code_revision = evidence.code_revision
    run.reconstruction_batch_id = evidence.reconstruction_batch_id
    run.provenance_schema_version = evidence.provenance_schema_version
    db.flush()


def publish_reconstruction_batch(
    db: Session,
    batch_id: str,
) -> ValidatedReconstructionCohort:
    batch = db.get(RecommendationReconstructionBatch, batch_id)
    if batch is None:
        raise ReconstructionBatchError(f"reconstruction batch does not exist: {batch_id}")
    if batch.status == RECONSTRUCTION_BATCH_STATUS_FAILED:
        raise ReconstructionBatchError("failed reconstruction batch cannot be published")
    if batch.status not in {
        RECONSTRUCTION_BATCH_STATUS_BUILDING,
        RECONSTRUCTION_BATCH_STATUS_COMPLETED,
    }:
        raise ReconstructionBatchError("reconstruction batch has an invalid lifecycle status")
    identity = identity_from_persisted_batch(batch)
    runs = _load_batch_runs(db, identity.batch_id)
    validated = _validate_runs(identity, runs)
    if batch.status == RECONSTRUCTION_BATCH_STATUS_COMPLETED:
        persisted_hash = str(batch.cohort_identity_hash or "").lower()
        completed_at = batch.completed_at
        if (
            not isinstance(completed_at, datetime)
            or int(batch.expected_trade_date_count) != len(identity.expected_trade_dates)
            or int(batch.completed_trade_date_count) != len(validated.run_ids)
            or not _SHA256_RE.fullmatch(persisted_hash)
            or not hmac.compare_digest(
                persisted_hash,
                validated.cohort_identity_hash,
            )
        ):
            raise ReconstructionBatchError(
                "sealed reconstruction batch no longer matches its published cohort"
            )
        return replace(validated, completed_at=completed_at)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    batch.completed_trade_date_count = len(validated.run_ids)
    batch.cohort_identity_hash = validated.cohort_identity_hash
    batch.status = RECONSTRUCTION_BATCH_STATUS_COMPLETED
    batch.failure_message = None
    batch.completed_at = now
    batch.updated_at = now
    db.flush()
    return replace(validated, completed_at=now)


def mark_reconstruction_batch_failed(
    db: Session,
    batch_id: str,
    message: str,
) -> None:
    batch = db.get(RecommendationReconstructionBatch, batch_id)
    if batch is None:
        return
    if batch.status == RECONSTRUCTION_BATCH_STATUS_COMPLETED:
        raise ReconstructionBatchError(
            "sealed reconstruction batch cannot be marked failed"
        )
    batch.status = RECONSTRUCTION_BATCH_STATUS_FAILED
    batch.failure_message = str(message or "historical reconstruction failed")[:2000]
    batch.completed_at = None
    batch.cohort_identity_hash = None
    batch.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.flush()


def latest_validated_reconstruction_cohort(
    db: Session,
    *,
    trade_date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> ValidatedReconstructionCohort | None:
    cohorts = latest_validated_reconstruction_cohorts(
        db, trade_date=trade_date, date_from=date_from, date_to=date_to, per_template=False
    )
    return cohorts[0] if cohorts else None


def latest_validated_reconstruction_cohorts(
    db: Session,
    *,
    trade_date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    per_template: bool = True,
) -> list[ValidatedReconstructionCohort]:
    """Latest validated cohort per strategy, newest first.

    Strategy identity = ``reconstruction_strategy_identity`` (preset / skill set
    from the request template; run_name / attempt-tag ignored).  For each strategy
    the batch with the latest ``completed_at`` wins; an older batch of the same
    strategy only keeps trade dates no newer batch covers (identical windows ⇒
    dropped entirely), so no (strategy, trade_date) is ever counted twice.
    Different strategies are all returned (consumers take the union).
    ``per_template=False`` returns at most the single latest cohort (legacy).
    """
    found: list[ValidatedReconstructionCohort] = []
    covered_dates: dict[str, set[str]] = {}
    if trade_date is not None and (date_from is not None or date_to is not None):
        raise ValueError("trade_date cannot be combined with a date range")
    target = _optional_iso_date(trade_date, field_name="trade_date")
    lower = _optional_iso_date(date_from, field_name="date_from")
    upper = _optional_iso_date(date_to, field_name="date_to")
    if lower and upper and lower > upper:
        raise ValueError("date_from must not be later than date_to")

    statement = (
        select(RecommendationReconstructionBatch)
        .where(
            RecommendationReconstructionBatch.status
            == RECONSTRUCTION_BATCH_STATUS_COMPLETED,
            RecommendationReconstructionBatch.completed_at.is_not(None),
            RecommendationReconstructionBatch.cohort_identity_hash.is_not(None),
        )
        .order_by(
            RecommendationReconstructionBatch.completed_at.desc(),
            RecommendationReconstructionBatch.batch_id.desc(),
        )
    )
    if target:
        statement = statement.where(
            RecommendationReconstructionBatch.window_start <= target,
            RecommendationReconstructionBatch.window_end >= target,
        )
    else:
        if lower:
            statement = statement.where(
                RecommendationReconstructionBatch.window_end >= lower
            )
        if upper:
            statement = statement.where(
                RecommendationReconstructionBatch.window_start <= upper
            )

    for batch in db.scalars(statement).all():
        try:
            identity = identity_from_persisted_batch(batch)
            batch_runs = _load_batch_runs(db, identity.batch_id)
            validated = _validate_runs(identity, batch_runs)
            persisted_hash = str(batch.cohort_identity_hash or "").lower()
            if not _SHA256_RE.fullmatch(persisted_hash) or not hmac.compare_digest(
                persisted_hash,
                validated.cohort_identity_hash,
            ):
                continue
            if int(batch.expected_trade_date_count) != len(identity.expected_trade_dates):
                continue
            if int(batch.completed_trade_date_count) != len(validated.run_ids):
                continue
            completed_at = batch.completed_at
            if not isinstance(completed_at, datetime):
                continue
            if target and target not in identity.expected_trade_dates:
                continue
            cohort = ValidatedReconstructionCohort(
                batch_id=validated.batch_id,
                window_start=validated.window_start,
                window_end=validated.window_end,
                expected_trade_dates=validated.expected_trade_dates,
                run_ids=validated.run_ids,
                completed_at=completed_at,
                cohort_identity_hash=validated.cohort_identity_hash,
                request_template_fingerprint=validated.request_template_fingerprint,
                strategy_policy_fingerprint=validated.strategy_policy_fingerprint,
                execution_policy_fingerprint=validated.execution_policy_fingerprint,
                code_revision=validated.code_revision,
            )
            strategy_key, strategy_label = reconstruction_strategy_identity(
                str(batch_runs[0].request_json or "") if batch_runs else None,
                strategy_policy_fingerprint=validated.strategy_policy_fingerprint,
            )
        except (ReconstructionBatchError, TypeError, ValueError):
            continue
        covered = covered_dates.setdefault(strategy_key, set())
        keep = [
            (run_id, run_date)
            for run_id, run_date in zip(cohort.run_ids, cohort.expected_trade_dates)
            if run_date not in covered
        ]
        if not keep:
            continue  # 同策略更新批次已覆盖全部交易日（如换 attempt-tag 重跑）
        superseded = len(cohort.run_ids) - len(keep)
        cohort = replace(
            cohort,
            run_ids=tuple(run_id for run_id, _ in keep),
            expected_trade_dates=tuple(run_date for _, run_date in keep),
            strategy_key=strategy_key,
            strategy_label=strategy_label,
            superseded_trade_date_count=superseded,
        )
        covered.update(cohort.expected_trade_dates)
        found.append(cohort)
        if not per_template:
            break
    return found


def cohort_identity_hash(runs: Sequence[RecommendationRun]) -> str:
    payload = [
        {
            "evidence_identity_hash": str(run.evidence_identity_hash or "").lower(),
            "run_id": str(run.run_id),
            "trade_date": str(run.trade_date),
        }
        for run in sorted(runs, key=lambda item: (str(item.trade_date), str(item.run_id)))
    ]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _validate_runs(
    identity: ReconstructionBatchIdentity,
    runs: Sequence[RecommendationRun],
) -> ValidatedReconstructionCohort:
    dates = tuple(str(run.trade_date) for run in runs)
    if len(runs) != len(identity.expected_trade_dates):
        raise ReconstructionBatchError("reconstruction batch is incomplete")
    if len(set(dates)) != len(dates) or tuple(sorted(dates)) != identity.expected_trade_dates:
        raise ReconstructionBatchError("reconstruction batch trade dates do not match its manifest")
    for run in runs:
        if (
            str(run.status) != "completed"
            or str(run.run_mode) != "formal"
            or str(run.evaluation_scope) != "historical_replay"
            or canonical_request_template_fingerprint(str(run.request_json or ""))
            != identity.request_template_fingerprint
            or str(run.reconstruction_batch_id) != identity.batch_id
            or str(run.strategy_policy_fingerprint).lower()
            != identity.strategy_policy_fingerprint
            or str(run.execution_policy_fingerprint).lower()
            != identity.execution_policy_fingerprint
            or str(run.code_revision).lower() != identity.code_revision
            or not has_canonical_persisted_identity(
                run,
                expected_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
            )
        ):
            raise ReconstructionBatchError("reconstruction run does not match its batch manifest")
    ordered_runs = tuple(sorted(runs, key=lambda item: (str(item.trade_date), str(item.run_id))))
    return ValidatedReconstructionCohort(
        batch_id=identity.batch_id,
        window_start=identity.window_start,
        window_end=identity.window_end,
        expected_trade_dates=identity.expected_trade_dates,
        run_ids=tuple(str(run.run_id) for run in ordered_runs),
        completed_at=datetime.min,
        cohort_identity_hash=cohort_identity_hash(ordered_runs),
        request_template_fingerprint=identity.request_template_fingerprint,
        strategy_policy_fingerprint=identity.strategy_policy_fingerprint,
        execution_policy_fingerprint=identity.execution_policy_fingerprint,
        code_revision=identity.code_revision,
    )


def _load_batch_runs(db: Session, batch_id: str) -> tuple[RecommendationRun, ...]:
    identity_columns = (
        RecommendationRun.run_id,
        RecommendationRun.run_mode,
        RecommendationRun.evaluation_scope,
        RecommendationRun.request_json,
        RecommendationRun.status,
        RecommendationRun.evidence_class,
        RecommendationRun.actual_generated_at,
        RecommendationRun.trade_date,
        RecommendationRun.data_as_of,
        RecommendationRun.source_snapshot_id,
        RecommendationRun.evidence_identity_hash,
        RecommendationRun.strategy_policy_fingerprint,
        RecommendationRun.execution_policy_fingerprint,
        RecommendationRun.code_revision,
        RecommendationRun.reconstruction_batch_id,
        RecommendationRun.provenance_schema_version,
    )
    return tuple(
        db.scalars(
            select(RecommendationRun)
            .where(RecommendationRun.reconstruction_batch_id == batch_id)
            .options(load_only(*identity_columns))
            .order_by(RecommendationRun.trade_date, RecommendationRun.run_id)
        ).all()
    )


def _batch_id(
    *,
    window_start: str,
    window_end: str,
    expected_trade_dates: Sequence[str],
    request_template_fingerprint: str,
    strategy_policy_fingerprint: str,
    execution_policy_fingerprint: str,
    code_revision: str,
    schema_version: int,
) -> str:
    payload = {
        "code_revision": code_revision,
        "execution_policy_fingerprint": execution_policy_fingerprint,
        "expected_trade_dates": list(expected_trade_dates),
        "request_template_fingerprint": request_template_fingerprint,
        "schema_version": schema_version,
        "strategy_policy_fingerprint": strategy_policy_fingerprint,
        "window_end": window_end,
        "window_start": window_start,
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{_BATCH_PREFIX}{digest}"


def _load_expected_dates(raw: str) -> tuple[str, ...]:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReconstructionBatchError("expected trade dates are not valid JSON") from exc
    if not isinstance(payload, list):
        raise ReconstructionBatchError("expected trade dates must be a list")
    return _canonical_trade_dates(payload)


def _canonical_trade_dates(values: Iterable[str]) -> tuple[str, ...]:
    try:
        dates = tuple(_iso_date(value, field_name="expected_trade_date") for value in values)
    except TypeError as exc:
        raise ReconstructionBatchError("expected trade dates must be iterable") from exc
    if not dates:
        raise ReconstructionBatchError("reconstruction batch requires at least one trade date")
    ordered = tuple(sorted(set(dates)))
    if len(ordered) != len(dates):
        raise ReconstructionBatchError("expected trade dates must be unique")
    return ordered


def _optional_iso_date(value: str | None, *, field_name: str) -> str | None:
    return None if value is None else _iso_date(value, field_name=field_name)


def _iso_date(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ReconstructionBatchError(f"{field_name} must be an ISO date")
    try:
        normalized = date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ReconstructionBatchError(f"{field_name} must be an ISO date") from exc
    if normalized != value:
        raise ReconstructionBatchError(f"{field_name} must be an ISO date")
    return normalized


def _sha256(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ReconstructionBatchError(f"{field_name} must be a SHA-256 digest")
    normalized = value.lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ReconstructionBatchError(f"{field_name} must be a SHA-256 digest")
    return normalized


def _code_revision(value: object) -> str:
    if not isinstance(value, str) or not _REVISION_RE.fullmatch(value):
        raise ReconstructionBatchError("code_revision must be a git revision")
    return value.lower()


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReconstructionBatchError("value is not canonical JSON") from exc


__all__ = [
    "RECONSTRUCTION_BATCH_SCHEMA_VERSION",
    "RECONSTRUCTION_BATCH_STATUS_BUILDING",
    "RECONSTRUCTION_BATCH_STATUS_COMPLETED",
    "RECONSTRUCTION_BATCH_STATUS_FAILED",
    "ReconstructionBatchError",
    "ReconstructionBatchIdentity",
    "MergedReconstructionCohort",
    "ReconstructionCohortView",
    "ValidatedReconstructionCohort",
    "bind_reconstruction_worker_revision",
    "build_reconstruction_batch_identity",
    "canonical_request_template_fingerprint",
    "cohort_identity_hash",
    "finalize_reconstruction_run",
    "identity_from_persisted_batch",
    "latest_validated_reconstruction_cohort",
    "latest_validated_reconstruction_cohorts",
    "mark_reconstruction_batch_failed",
    "merge_reconstruction_cohorts",
    "publish_reconstruction_batch",
    "reconstruction_strategy_identity",
]
