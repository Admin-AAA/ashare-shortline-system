"""Persistence boundary for isolated, reproducible research backtests.

This module deliberately knows only the eight dedicated ``backtest_*`` ORM
mappings.  It never imports or writes ``Recommendation`` or
``RecommendationOutcome``.  The mappings are resolved lazily because the
forward migration that introduces them can be deployed independently from this
repository module.

The caller owns the surrounding SQLAlchemy transaction.  In particular,
``claim_request`` uses ``SELECT ... FOR UPDATE`` and must be committed (or
rolled back) by the caller.  SQLite accepts the same contract for unit tests,
but MySQL/InnoDB remains the required concurrency validation environment.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utcnow_naive

JsonValue = Mapping[str, Any] | Sequence[Any] | str | int | float | bool | None


class ResearchRepositoryError(RuntimeError):
    """Base class for stable, application-safe repository failures."""

    code = "research_repository_error"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class RepositoryConfigurationError(ResearchRepositoryError):
    code = "research_repository_configuration_error"


class ResearchRecordNotFound(ResearchRepositoryError):
    code = "research_record_not_found"


class SnapshotIntegrityError(ResearchRepositoryError):
    code = "snapshot_integrity_error"


class ArtifactIntegrityError(ResearchRepositoryError):
    code = "snapshot_artifact_integrity_error"


class ImmutableRecordConflict(ResearchRepositoryError):
    code = "research_immutable_record_conflict"


class RequestClaimConflict(ResearchRepositoryError):
    code = "backtest_request_claim_conflict"


class BacktestStateConflict(ResearchRepositoryError):
    code = "backtest_state_conflict"


class AttributionValidationError(ResearchRepositoryError):
    code = "backtest_attribution_validation_error"


class ExperimentValidationError(ResearchRepositoryError):
    code = "backtest_experiment_validation_error"


@dataclass(frozen=True)
class BacktestORMModels:
    """The dedicated ORM mappings required by :class:`ResearchBacktestRepository`.

    Tests can pass a SQLite-specific set of mappings without monkeypatching
    production modules.  Production code normally relies on ``from_tables``
    after the P0 migration has installed the mapping classes.
    """

    data_snapshot: type[Any]
    input_artifact: type[Any]
    request_claim: type[Any]
    run: type[Any]
    candidate: type[Any]
    trade: type[Any]
    failure_attribution: type[Any]
    experiment: type[Any]
    holdout_claim: type[Any] | None = None

    @classmethod
    def from_tables(cls) -> "BacktestORMModels":
        try:
            from app.models import tables
        except ImportError as exc:  # pragma: no cover - import environment issue
            raise RepositoryConfigurationError("无法导入 app.models.tables。") from exc

        expected = {
            "data_snapshot": "BacktestDataSnapshot",
            "input_artifact": "BacktestInputArtifact",
            "request_claim": "BacktestRequestClaim",
            "run": "BacktestRunV2",
            "candidate": "BacktestCandidate",
            "trade": "BacktestTrade",
            "failure_attribution": "BacktestFailureAttribution",
            "experiment": "BacktestExperiment",
            "holdout_claim": "BacktestHoldoutClaim",
        }
        missing = [
            name for key, name in expected.items()
            if key != "holdout_claim" and not hasattr(tables, name)
        ]
        if missing:
            raise RepositoryConfigurationError(
                "P0 research backtest ORM 映射尚未安装；请先应用对应的 forward migration 并添加映射。",
                details={"missing_models": missing},
            )
        return cls(
            **{
                field: getattr(tables, model_name)
                for field, model_name in expected.items()
                if hasattr(tables, model_name)
            }
        )


@dataclass(frozen=True)
class RequestClaimResult:
    """The one authoritative outcome of an atomic request-claim operation."""

    backtest_id: str
    attempt_no: int
    status: str
    reused: bool
    reuse_reason: str | None


@dataclass(frozen=True)
class ExperimentCompatibility:
    experiment_id: str
    snapshot_id: str
    execution_policy_fingerprint: str
    split_hash: str
    frozen_candidate_fingerprint: str


_ACTIVE_RUN_STATUSES = frozenset({"queued", "running"})
_RETRIABLE_RUN_STATUSES = frozenset(
    {
        "failed",
        "interrupted",
        "insufficient_data",
        "incomplete_evaluation",
        "snapshot_artifact_unavailable",
        "snapshot_integrity_error",
        "future_data_access_denied",
    }
)
_FAILURE_OUTCOME_STATUSES = _RETRIABLE_RUN_STATUSES | frozenset({"cancelled"})
_EXPERIMENT_STATUSES = frozenset(
    {
        "draft",
        "running",
        "frozen",
        "completed",
        "failed",
        "insufficient_data",
        "exploratory",
        "rejected",
        "promotion_candidate",
    }
)
_STABLE_ATTRIBUTION_REASONS = frozenset(
    {
        "chase_high",
        "stop_loss",
        "sector_decay",
        "black_swan",
        "execution_failure",
        "unknown",
    }
)
_NON_TRADE_OUTCOME_LABELS = frozenset(
    {
        "no_signal",
        "insufficient_data",
        "incomplete_evaluation",
        "unfilled",
        "not_tradable",
        "skipped",
    }
)


class ResearchBacktestRepository:
    """SQLAlchemy repository for P0 research-only backtest evidence.

    All create methods are append-only or idempotent on an identical natural
    key.  There are intentionally no delete, recommendation, or outcome APIs.
    """

    def __init__(self, session: Session, *, models: BacktestORMModels | None = None) -> None:
        self._session = session
        self._models = models or BacktestORMModels.from_tables()

    @property
    def session(self) -> Session:
        """The caller-owned transaction scope used by this repository."""
        return self._session

    def claim_holdout(
        self,
        *,
        holdout_fingerprint: str,
        experiment_id: str,
        phase: str = "untouched_holdout",
    ) -> Any:
        """Atomically claim an untouched holdout, or reject reuse by another experiment."""
        model = self._models.holdout_claim
        if model is None:
            raise RepositoryConfigurationError(
                "正式 holdout ledger 尚未安装；请先应用 backtest_holdout_claims migration。"
            )
        if len(str(holdout_fingerprint)) != 64:
            raise ExperimentValidationError("holdout_fingerprint 必须是 SHA-256 fingerprint。")
        existing = self._session.get(model, holdout_fingerprint)
        if existing is not None:
            if str(existing.experiment_id) == str(experiment_id):
                return existing
            raise ImmutableRecordConflict(
                "untouched holdout 已被其他 experiment 消费。",
                details={
                    "holdout_fingerprint": holdout_fingerprint,
                    "existing_experiment_id": str(existing.experiment_id),
                },
            )
        row = model(
            holdout_fingerprint=str(holdout_fingerprint),
            experiment_id=str(experiment_id),
            phase=str(phase),
            status="consumed",
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError as exc:
            self._session.rollback()
            raise ImmutableRecordConflict(
                "untouched holdout 并发 claim 冲突。",
                details={"holdout_fingerprint": holdout_fingerprint},
            ) from exc
        return row

    # -- Snapshot and immutable input artifacts ---------------------------------

    def persist_snapshot(
        self,
        *,
        snapshot_id: str,
        snapshot_version: int,
        partition_manifest: JsonValue,
        coverage: JsonValue,
        missing_requirements: JsonValue,
        manifest_hash: str | None = None,
        created_at: datetime | None = None,
    ) -> Any:
        """Insert or return an identical snapshot after canonical hash checks."""
        self._require_nonempty(snapshot_id, "snapshot_id")
        manifest_json = _canonical_json(partition_manifest, field="partition_manifest")
        coverage_json = _canonical_json(coverage, field="coverage")
        missing_json = _canonical_json(missing_requirements, field="missing_requirements")
        calculated_hash = _sha256_text(manifest_json)
        if manifest_hash is not None and manifest_hash != calculated_hash:
            raise SnapshotIntegrityError(
                "snapshot manifest_hash 与 canonical partition manifest 不一致。",
                details={"snapshot_id": snapshot_id, "expected_hash": calculated_hash},
            )

        existing = self._session.get(self._models.data_snapshot, snapshot_id)
        if existing is not None:
            self._assert_snapshot_integrity(existing)
            if not _same_values(
                existing,
                snapshot_version=snapshot_version,
                manifest_hash=calculated_hash,
                partition_manifest_json=manifest_json,
                coverage_json=coverage_json,
                missing_requirements_json=missing_json,
            ):
                raise ImmutableRecordConflict(
                    "snapshot_id 已存在，但不可变快照内容不同。", details={"snapshot_id": snapshot_id}
                )
            return existing

        same_manifest = self._session.execute(
            select(self._models.data_snapshot).where(
                self._models.data_snapshot.manifest_hash == calculated_hash
            )
        ).scalar_one_or_none()
        if same_manifest is not None:
            self._assert_snapshot_integrity(same_manifest)
            if not _same_values(
                same_manifest,
                snapshot_version=snapshot_version,
                partition_manifest_json=manifest_json,
                coverage_json=coverage_json,
                missing_requirements_json=missing_json,
            ):
                raise ImmutableRecordConflict(
                    "同一 manifest_hash 对应的快照内容不一致。",
                    details={"snapshot_id": snapshot_id, "manifest_hash": calculated_hash},
                )
            return same_manifest

        row = self._models.data_snapshot(
            snapshot_id=snapshot_id,
            snapshot_version=snapshot_version,
            manifest_hash=calculated_hash,
            partition_manifest_json=manifest_json,
            coverage_json=coverage_json,
            missing_requirements_json=missing_json,
            created_at=created_at or _utcnow(),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get_snapshot(self, snapshot_id: str) -> Any:
        row = self._session.get(self._models.data_snapshot, snapshot_id)
        if row is None:
            raise ResearchRecordNotFound("未找到 backtest snapshot。", details={"snapshot_id": snapshot_id})
        self._assert_snapshot_integrity(row)
        return row

    def persist_artifact(
        self,
        *,
        artifact_id: str,
        snapshot_id: str,
        signal_date: str,
        artifact_scope: str,
        artifact_type: str,
        schema_version: int,
        row_count: int,
        content_encoding: str,
        payload: bytes | bytearray | memoryview | str | Mapping[str, Any] | Sequence[Any],
        content_hash: str | None = None,
        created_at: datetime | None = None,
    ) -> Any:
        """Persist an immutable payload; its hash always covers stored bytes."""
        self.get_snapshot(snapshot_id)
        for field, value in (
            ("artifact_id", artifact_id),
            ("signal_date", signal_date),
            ("artifact_scope", artifact_scope),
            ("artifact_type", artifact_type),
            ("content_encoding", content_encoding),
        ):
            self._require_nonempty(value, field)
        if row_count < 0:
            raise ArtifactIntegrityError("artifact row_count 不能为负数。")

        payload_bytes = _to_payload_bytes(payload)
        calculated_hash = _sha256_bytes(payload_bytes)
        if content_hash is not None and content_hash != calculated_hash:
            raise ArtifactIntegrityError(
                "artifact content_hash 与 payload 不一致。",
                details={"artifact_id": artifact_id, "expected_hash": calculated_hash},
            )

        existing_id = self._session.get(self._models.input_artifact, artifact_id)
        existing_key = self._session.execute(
            select(self._models.input_artifact).where(
                self._models.input_artifact.snapshot_id == snapshot_id,
                self._models.input_artifact.signal_date == signal_date,
                self._models.input_artifact.artifact_scope == artifact_scope,
                self._models.input_artifact.artifact_type == artifact_type,
            )
        ).scalar_one_or_none()
        for existing in (existing_id, existing_key):
            if existing is None:
                continue
            self._assert_artifact_integrity(existing)
            if _same_values(
                existing,
                snapshot_id=snapshot_id,
                signal_date=signal_date,
                artifact_scope=artifact_scope,
                artifact_type=artifact_type,
                schema_version=schema_version,
                row_count=row_count,
                content_hash=calculated_hash,
                content_encoding=content_encoding,
                payload=payload_bytes,
            ):
                return existing
            raise ImmutableRecordConflict(
                "artifact 标识或自然键已存在，但不可变内容不同。",
                details={"artifact_id": artifact_id, "snapshot_id": snapshot_id},
            )

        row = self._models.input_artifact(
            artifact_id=artifact_id,
            snapshot_id=snapshot_id,
            signal_date=signal_date,
            artifact_scope=artifact_scope,
            artifact_type=artifact_type,
            schema_version=schema_version,
            row_count=row_count,
            content_hash=calculated_hash,
            content_encoding=content_encoding,
            payload=payload_bytes,
            created_at=created_at or _utcnow(),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get_artifact(self, artifact_id: str, *, expected_content_hash: str | None = None) -> Any:
        row = self._session.get(self._models.input_artifact, artifact_id)
        if row is None:
            raise ResearchRecordNotFound("未找到 backtest input artifact。", details={"artifact_id": artifact_id})
        self._assert_artifact_integrity(row, expected_content_hash=expected_content_hash)
        return row

    # -- Atomic claim / append-only run attempts ---------------------------------

    def claim_request(
        self,
        *,
        request_fingerprint: str,
        backtest_id: str,
        snapshot_id: str,
        strategy_id: str,
        strategy_version: str,
        request_schema_version: int,
        request_json: JsonValue,
        execution_version: str,
        execution_policy_fingerprint: str,
        signal_date: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        experiment_id: str | None = None,
        force_rerun: bool = False,
        created_at: datetime | None = None,
    ) -> RequestClaimResult:
        """Atomically reuse a completed/active attempt or reserve a new attempt.

        A live active attempt is always reused, including for ``force_rerun``;
        this prevents force requests from losing the only active claim pointer.
        Once no active attempt exists, ordinary calls reuse completed work while
        force calls and retriable terminal outcomes allocate a new attempt.
        """
        self._validate_hash(request_fingerprint, "request_fingerprint")
        self._validate_hash(execution_policy_fingerprint, "execution_policy_fingerprint")
        for field, value in (
            ("backtest_id", backtest_id),
            ("snapshot_id", snapshot_id),
            ("strategy_id", strategy_id),
            ("strategy_version", strategy_version),
            ("execution_version", execution_version),
        ):
            self._require_nonempty(value, field)
        self.get_snapshot(snapshot_id)
        canonical_request = _canonical_json(request_json, field="request_json")

        claim = self._locked_claim(request_fingerprint)
        now = created_at or _utcnow()
        if claim is None:
            # The unique primary key is the final arbiter when two first callers
            # race to claim the same fingerprint.  A nested savepoint keeps the
            # caller's outer transaction valid after a duplicate-key race, then a
            # current locking read observes the authoritative winner.
            try:
                with self._session.begin_nested():
                    claim = self._models.request_claim(
                        request_fingerprint=request_fingerprint,
                        active_backtest_id=None,
                        completed_backtest_id=None,
                        next_attempt_no=1,
                        updated_at=now,
                    )
                    self._session.add(claim)
                    self._session.flush()
            except IntegrityError:
                claim = self._locked_claim(request_fingerprint)
                if claim is None:
                    raise RequestClaimConflict(
                        "concurrent request claim was not visible after duplicate-key retry.",
                        details={"request_fingerprint": request_fingerprint},
                    ) from None

        active = self._run_from_claim_id(claim.active_backtest_id, request_fingerprint, "active")
        if active is not None:
            active_status = str(active.status)
            if active_status in _ACTIVE_RUN_STATUSES:
                return RequestClaimResult(
                    backtest_id=active.backtest_id,
                    attempt_no=int(active.attempt_no),
                    status=active_status,
                    reused=True,
                    reuse_reason="active",
                )
            if active_status not in _RETRIABLE_RUN_STATUSES and active_status != "completed":
                raise RequestClaimConflict(
                    "active claim 指向不能安全重试的 attempt。",
                    details={"backtest_id": active.backtest_id, "status": active_status},
                )
            claim.active_backtest_id = None

        completed = self._run_from_claim_id(claim.completed_backtest_id, request_fingerprint, "completed")
        if completed is not None:
            if completed.status != "completed":
                raise RequestClaimConflict(
                    "completed claim 指向非 completed attempt。",
                    details={"backtest_id": completed.backtest_id, "status": completed.status},
                )
            if not force_rerun:
                return RequestClaimResult(
                    backtest_id=completed.backtest_id,
                    attempt_no=int(completed.attempt_no),
                    status="completed",
                    reused=True,
                    reuse_reason="completed",
                )

        existing_id = self._session.get(self._models.run, backtest_id)
        if existing_id is not None:
            raise ImmutableRecordConflict("backtest_id 已存在。", details={"backtest_id": backtest_id})

        parent = self._latest_attempt(request_fingerprint)
        attempt_no = int(claim.next_attempt_no)
        if attempt_no < 1:
            raise RequestClaimConflict(
                "next_attempt_no 非法。", details={"request_fingerprint": request_fingerprint}
            )
        row = self._models.run(
            backtest_id=backtest_id,
            experiment_id=experiment_id,
            parent_backtest_id=parent.backtest_id if parent is not None else None,
            request_fingerprint=request_fingerprint,
            attempt_no=attempt_no,
            snapshot_id=snapshot_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            request_schema_version=request_schema_version,
            request_json=canonical_request,
            execution_version=execution_version,
            execution_policy_fingerprint=execution_policy_fingerprint,
            signal_date=signal_date,
            date_from=date_from,
            date_to=date_to,
            status="queued",
            result_json="{}",
            started_at=None,
            finished_at=None,
            created_at=now,
        )
        # Persist the append-only run before publishing its identifier through
        # the mutable claim pointer.  There is deliberately no DB foreign key
        # between these isolated tables; an explicit first flush therefore
        # prevents a racing locking reader from observing a dangling active id.
        self._session.add(row)
        self._session.flush([row])
        claim.active_backtest_id = backtest_id
        claim.next_attempt_no = attempt_no + 1
        claim.updated_at = now
        self._session.flush()
        return RequestClaimResult(backtest_id, attempt_no, "queued", False, None)

    def get_run(self, backtest_id: str) -> Any:
        row = self._session.get(self._models.run, backtest_id)
        if row is None:
            raise ResearchRecordNotFound("未找到 backtest attempt。", details={"backtest_id": backtest_id})
        return row

    def mark_running(self, backtest_id: str, *, started_at: datetime | None = None) -> Any:
        run = self._locked_run(backtest_id)
        if run.status == "running":
            return run
        if run.status != "queued":
            raise BacktestStateConflict(
                "只有 queued attempt 可以进入 running。",
                details={"backtest_id": backtest_id, "status": run.status},
            )
        claim = self._require_locked_claim(run.request_fingerprint)
        self._assert_current_active_claim(claim, run)
        run.status = "running"
        run.started_at = started_at or _utcnow()
        claim.updated_at = _utcnow()
        self._session.flush()
        return run

    def complete_run(
        self,
        backtest_id: str,
        *,
        result: JsonValue,
        finished_at: datetime | None = None,
    ) -> Any:
        """Complete exactly the current active attempt and update its claim CAS."""
        run = self._locked_run(backtest_id)
        result_json = _canonical_json(result, field="result")
        if run.status == "completed":
            if str(run.result_json) != result_json:
                raise ImmutableRecordConflict("completed attempt 的 result_json 不能修改。")
            return run
        if run.status not in _ACTIVE_RUN_STATUSES:
            raise BacktestStateConflict(
                "只有 queued/running attempt 可以完成。",
                details={"backtest_id": backtest_id, "status": run.status},
            )
        claim = self._require_locked_claim(run.request_fingerprint)
        self._assert_current_active_claim(claim, run)
        now = finished_at or _utcnow()
        run.status = "completed"
        run.result_json = result_json
        run.started_at = run.started_at or now
        run.finished_at = now
        claim.active_backtest_id = None
        claim.completed_backtest_id = run.backtest_id
        claim.updated_at = now
        self._session.flush()
        return run

    def fail_run(
        self,
        backtest_id: str,
        *,
        outcome_status: str = "failed",
        result: JsonValue | None = None,
        finished_at: datetime | None = None,
    ) -> Any:
        """Record a retriable/non-success outcome without replacing old evidence."""
        if outcome_status not in _FAILURE_OUTCOME_STATUSES:
            raise BacktestStateConflict(
                "不是允许的非成功 backtest outcome。", details={"outcome_status": outcome_status}
            )
        run = self._locked_run(backtest_id)
        rendered = _canonical_json(result if result is not None else {"status": outcome_status}, field="result")
        if run.status == outcome_status:
            if str(run.result_json) != rendered:
                raise ImmutableRecordConflict("终态 attempt 的 result_json 不能修改。")
            return run
        if run.status not in _ACTIVE_RUN_STATUSES:
            raise BacktestStateConflict(
                "只有 queued/running attempt 可以失败。",
                details={"backtest_id": backtest_id, "status": run.status},
            )
        claim = self._require_locked_claim(run.request_fingerprint)
        self._assert_current_active_claim(claim, run)
        now = finished_at or _utcnow()
        run.status = outcome_status
        run.result_json = rendered
        run.started_at = run.started_at or now
        run.finished_at = now
        claim.active_backtest_id = None
        claim.updated_at = now
        self._session.flush()
        return run

    # -- Candidates and trades: isolated, immutable natural keys ----------------

    def persist_candidates(self, backtest_id: str, candidates: Sequence[Mapping[str, Any] | Any]) -> list[Any]:
        run = self.get_run(backtest_id)
        rows: list[Any] = []
        seen: set[tuple[str, str]] = set()
        verified_artifacts: dict[str, Any] = {}
        for item in candidates:
            value = _as_mapping(item)
            signal_date = _require_value(value, "signal_date")
            code = _require_value(value, "code")
            key = (str(signal_date), str(code))
            if key in seen:
                raise ImmutableRecordConflict("同一批 candidate 含重复自然键。", details={"key": key})
            seen.add(key)
            artifact_id = str(_require_value(value, "input_artifact_id"))
            artifact = verified_artifacts.get(artifact_id)
            if artifact is None:
                artifact = self.get_artifact(artifact_id)
                verified_artifacts[artifact_id] = artifact
            if artifact.snapshot_id != run.snapshot_id:
                raise ArtifactIntegrityError(
                    "candidate input artifact 不属于该 backtest snapshot。",
                    details={"backtest_id": backtest_id, "artifact_id": artifact_id},
                )
            candidate_id = str(value.get("candidate_id") or _new_id("btc"))
            signal_json = None if value.get("signal_json") is None else _canonical_json(value["signal_json"], field="signal_json")
            audit_json = _canonical_json(value.get("audit_json", {}), field="audit_json")
            decision_status = str(_require_value(value, "decision_status"))
            existing = self._session.execute(
                select(self._models.candidate).where(
                    self._models.candidate.backtest_id == backtest_id,
                    self._models.candidate.signal_date == signal_date,
                    self._models.candidate.code == code,
                )
            ).scalar_one_or_none()
            if existing is not None:
                if _same_values(
                    existing,
                    decision_status=decision_status,
                    signal_json=signal_json,
                    audit_json=audit_json,
                    input_artifact_id=artifact_id,
                ):
                    rows.append(existing)
                    continue
                raise ImmutableRecordConflict(
                    "candidate 自然键已存在，但内容不同。", details={"backtest_id": backtest_id, "key": key}
                )
            existing_id = self._session.get(self._models.candidate, candidate_id)
            if existing_id is not None:
                raise ImmutableRecordConflict("candidate_id 已存在。", details={"candidate_id": candidate_id})
            row = self._models.candidate(
                candidate_id=candidate_id,
                backtest_id=backtest_id,
                signal_date=str(signal_date),
                code=str(code),
                decision_status=decision_status,
                signal_json=signal_json,
                audit_json=audit_json,
                input_artifact_id=artifact_id,
            )
            self._session.add(row)
            rows.append(row)
        self._session.flush()
        return rows

    def list_candidates(self, backtest_id: str) -> list[Any]:
        self.get_run(backtest_id)
        return list(
            self._session.scalars(
                select(self._models.candidate)
                .where(self._models.candidate.backtest_id == backtest_id)
                .order_by(self._models.candidate.signal_date, self._models.candidate.code)
            )
        )

    def persist_trades(self, backtest_id: str, trades: Sequence[Mapping[str, Any] | Any]) -> list[Any]:
        self.get_run(backtest_id)
        rows: list[Any] = []
        seen: set[tuple[str, str]] = set()
        for item in trades:
            value = _as_mapping(item)
            signal_date = _require_value(value, "signal_date")
            code = _require_value(value, "code")
            key = (str(signal_date), str(code))
            if key in seen:
                raise ImmutableRecordConflict("同一批 trade 含重复自然键。", details={"key": key})
            seen.add(key)
            trade_id = str(value.get("trade_id") or _new_id("btt"))
            outcome_label = str(_require_value(value, "outcome_label"))
            trade_json = _canonical_json(value.get("trade_json", {}), field="trade_json")
            existing = self._session.execute(
                select(self._models.trade).where(
                    self._models.trade.backtest_id == backtest_id,
                    self._models.trade.signal_date == signal_date,
                    self._models.trade.code == code,
                )
            ).scalar_one_or_none()
            if existing is not None:
                if _same_values(
                    existing,
                    entry_date=value.get("entry_date"),
                    exit_date=value.get("exit_date"),
                    outcome_label=outcome_label,
                    trade_json=trade_json,
                ):
                    rows.append(existing)
                    continue
                raise ImmutableRecordConflict(
                    "trade 自然键已存在，但内容不同。", details={"backtest_id": backtest_id, "key": key}
                )
            if self._session.get(self._models.trade, trade_id) is not None:
                raise ImmutableRecordConflict("trade_id 已存在。", details={"trade_id": trade_id})
            row = self._models.trade(
                trade_id=trade_id,
                backtest_id=backtest_id,
                signal_date=str(signal_date),
                code=str(code),
                entry_date=value.get("entry_date"),
                exit_date=value.get("exit_date"),
                outcome_label=outcome_label,
                trade_json=trade_json,
            )
            self._session.add(row)
            rows.append(row)
        self._session.flush()
        return rows

    def list_trades(self, backtest_id: str) -> list[Any]:
        self.get_run(backtest_id)
        return list(
            self._session.scalars(
                select(self._models.trade)
                .where(self._models.trade.backtest_id == backtest_id)
                .order_by(self._models.trade.signal_date, self._models.trade.code)
            )
        )

    # -- Structured failure attribution ------------------------------------------

    def persist_attributions(
        self, backtest_id: str, attributions: Sequence[Mapping[str, Any] | Any]
    ) -> list[Any]:
        self.get_run(backtest_id)
        rows: list[Any] = []
        seen: set[tuple[str, str]] = set()
        for item in attributions:
            value = _as_mapping(item)
            trade_id = str(_require_value(value, "trade_id"))
            reason_code = str(_require_value(value, "reason_code"))
            key = (trade_id, reason_code)
            if key in seen:
                raise AttributionValidationError("同一批 attribution 含重复 trade/reason。", details={"key": key})
            seen.add(key)
            if reason_code not in _STABLE_ATTRIBUTION_REASONS:
                raise AttributionValidationError(
                    "reason_code 不在首期稳定 failure reason code 集合中。",
                    details={"reason_code": reason_code},
                )
            is_unknown = bool(value.get("is_unknown", False))
            if (reason_code == "unknown") != is_unknown:
                raise AttributionValidationError(
                    "unknown reason_code 必须且只能搭配 is_unknown=true。",
                    details={"reason_code": reason_code, "is_unknown": is_unknown},
                )
            confidence = float(_require_value(value, "confidence"))
            if not 0.0 <= confidence <= 1.0:
                raise AttributionValidationError("attribution confidence 必须在 [0, 1] 内。")
            trade = self._session.get(self._models.trade, trade_id)
            if trade is None or trade.backtest_id != backtest_id:
                raise AttributionValidationError(
                    "attribution 只能引用当前 backtest 的已持久化 trade。",
                    details={"backtest_id": backtest_id, "trade_id": trade_id},
                )
            if not _is_attributable_trade(trade):
                raise AttributionValidationError(
                    "仅失败或净收益为负的已成交 trade 可以写失败归因。",
                    details={"trade_id": trade_id, "outcome_label": trade.outcome_label},
                )
            evidence_json = _canonical_json(value.get("evidence_references", []), field="evidence_references")
            if not isinstance(_load_json(evidence_json), list):
                raise AttributionValidationError("evidence_references 必须是 JSON array。")
            attribution_id = str(value.get("attribution_id") or _new_id("bta"))
            existing = self._session.execute(
                select(self._models.failure_attribution).where(
                    self._models.failure_attribution.trade_id == trade_id,
                    self._models.failure_attribution.reason_code == reason_code,
                )
            ).scalar_one_or_none()
            if existing is not None:
                if _same_values(
                    existing,
                    backtest_id=backtest_id,
                    rule_or_factor_reference=value.get("rule_or_factor_reference"),
                    evidence_references_json=evidence_json,
                    confidence=confidence,
                    is_unknown=is_unknown,
                ):
                    rows.append(existing)
                    continue
                raise ImmutableRecordConflict(
                    "trade/reason attribution 已存在，但内容不同。", details={"key": key}
                )
            if self._session.get(self._models.failure_attribution, attribution_id) is not None:
                raise ImmutableRecordConflict("attribution_id 已存在。", details={"attribution_id": attribution_id})
            row = self._models.failure_attribution(
                attribution_id=attribution_id,
                backtest_id=backtest_id,
                trade_id=trade_id,
                reason_code=reason_code,
                rule_or_factor_reference=value.get("rule_or_factor_reference"),
                evidence_references_json=evidence_json,
                confidence=confidence,
                is_unknown=is_unknown,
                created_at=_utcnow(),
            )
            self._session.add(row)
            rows.append(row)
        self._session.flush()
        return rows

    def list_attributions(self, backtest_id: str, *, reason_code: str | None = None) -> list[Any]:
        self.get_run(backtest_id)
        stmt = select(self._models.failure_attribution).where(
            self._models.failure_attribution.backtest_id == backtest_id
        )
        if reason_code is not None:
            stmt = stmt.where(self._models.failure_attribution.reason_code == reason_code)
        return list(
            self._session.scalars(
                stmt.order_by(
                    self._models.failure_attribution.reason_code,
                    self._models.failure_attribution.trade_id,
                )
            )
        )

    def aggregate_attributions(self, backtest_id: str) -> list[dict[str, Any]]:
        """Return portable structured aggregates without relying on JSON SQL."""
        self.get_run(backtest_id)
        trades = {trade.trade_id: trade for trade in self.list_trades(backtest_id)}
        attributable = {trade_id for trade_id, trade in trades.items() if _is_attributable_trade(trade)}
        aggregates: dict[str, dict[str, Any]] = {}
        for attribution in self.list_attributions(backtest_id):
            group = aggregates.setdefault(
                attribution.reason_code,
                {
                    "reason_code": attribution.reason_code,
                    "count": 0,
                    "ratio": 0.0,
                    "net_return": 0.0,
                    "trade_ids": [],
                    "evidence_references": [],
                },
            )
            trade = trades[attribution.trade_id]
            group["count"] += 1
            group["net_return"] += _trade_net_return(trade)
            group["trade_ids"].append(trade.trade_id)
            group["evidence_references"].extend(_load_json(attribution.evidence_references_json))
        denominator = len(attributable)
        for group in aggregates.values():
            group["ratio"] = group["count"] / denominator if denominator else 0.0
            group["net_return"] = round(float(group["net_return"]), 12)
        return [aggregates[key] for key in sorted(aggregates)]

    # -- Immutable experimental plans and fingerprint validation -----------------

    def create_experiment(
        self,
        *,
        experiment_id: str,
        hypothesis: str,
        baseline_backtest_id: str,
        snapshot_id: str,
        execution_policy_fingerprint: str,
        split_json: JsonValue,
        split_hash: str | None,
        candidate_strategy_id: str,
        candidate_strategy_version: str,
        frozen_candidate_fingerprint: str,
        acceptance: JsonValue,
        status: str = "draft",
        created_at: datetime | None = None,
    ) -> Any:
        """Persist a plan only when its baseline identity and splits are frozen."""
        for field, value in (
            ("experiment_id", experiment_id),
            ("hypothesis", hypothesis),
            ("baseline_backtest_id", baseline_backtest_id),
            ("snapshot_id", snapshot_id),
            ("candidate_strategy_id", candidate_strategy_id),
            ("candidate_strategy_version", candidate_strategy_version),
        ):
            self._require_nonempty(value, field)
        if status not in _EXPERIMENT_STATUSES:
            raise ExperimentValidationError("experiment status 非法。", details={"status": status})
        self._validate_hash(execution_policy_fingerprint, "execution_policy_fingerprint")
        self._validate_hash(frozen_candidate_fingerprint, "frozen_candidate_fingerprint")
        baseline = self.get_run(baseline_backtest_id)
        if baseline.status != "completed":
            raise ExperimentValidationError(
                "experiment baseline 必须是 completed backtest。",
                details={"baseline_backtest_id": baseline_backtest_id, "status": baseline.status},
            )
        if baseline.snapshot_id != snapshot_id:
            raise ExperimentValidationError("experiment snapshot 必须与 baseline 一致。")
        if baseline.execution_policy_fingerprint != execution_policy_fingerprint:
            raise ExperimentValidationError("experiment execution policy 必须与 baseline 一致。")
        self.get_snapshot(snapshot_id)
        canonical_split = _canonical_json(split_json, field="split_json")
        calculated_split_hash = _sha256_text(canonical_split)
        if split_hash is not None and split_hash != calculated_split_hash:
            raise ExperimentValidationError(
                "split_hash 与 canonical split_json 不一致。",
                details={"expected_hash": calculated_split_hash},
            )
        self._validate_split(_load_json(canonical_split), baseline)
        acceptance_json = _canonical_json(acceptance, field="acceptance")
        if not isinstance(_load_json(acceptance_json), dict):
            raise ExperimentValidationError("acceptance 必须是 JSON object。")

        existing = self._session.get(self._models.experiment, experiment_id)
        if existing is not None:
            if _same_values(
                existing,
                hypothesis=hypothesis,
                baseline_backtest_id=baseline_backtest_id,
                snapshot_id=snapshot_id,
                execution_policy_fingerprint=execution_policy_fingerprint,
                split_hash=calculated_split_hash,
                split_json=canonical_split,
                candidate_strategy_id=candidate_strategy_id,
                candidate_strategy_version=candidate_strategy_version,
                frozen_candidate_fingerprint=frozen_candidate_fingerprint,
                acceptance_json=acceptance_json,
            ):
                return existing
            raise ImmutableRecordConflict("experiment_id 已存在，但冻结计划内容不同。")

        row = self._models.experiment(
            experiment_id=experiment_id,
            hypothesis=hypothesis,
            baseline_backtest_id=baseline_backtest_id,
            snapshot_id=snapshot_id,
            execution_policy_fingerprint=execution_policy_fingerprint,
            split_hash=calculated_split_hash,
            split_json=canonical_split,
            candidate_strategy_id=candidate_strategy_id,
            candidate_strategy_version=candidate_strategy_version,
            frozen_candidate_fingerprint=frozen_candidate_fingerprint,
            acceptance_json=acceptance_json,
            status=status,
            conclusion_json=None,
            created_at=created_at or _utcnow(),
            frozen_at=_utcnow() if status == "frozen" else None,
            finished_at=None,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get_experiment(self, experiment_id: str) -> Any:
        row = self._session.get(self._models.experiment, experiment_id)
        if row is None:
            raise ResearchRecordNotFound("未找到 backtest experiment。", details={"experiment_id": experiment_id})
        self._validate_experiment_row(row)
        return row

    def validate_experiment_compatibility(
        self,
        experiment_id: str,
        *,
        snapshot_id: str,
        execution_policy_fingerprint: str,
        split_hash: str,
        frozen_candidate_fingerprint: str,
    ) -> ExperimentCompatibility:
        """Reject snapshot/policy/split/candidate drift before comparison writes."""
        row = self.get_experiment(experiment_id)
        expected = {
            "snapshot_id": snapshot_id,
            "execution_policy_fingerprint": execution_policy_fingerprint,
            "split_hash": split_hash,
            "frozen_candidate_fingerprint": frozen_candidate_fingerprint,
        }
        actual = {name: str(getattr(row, name)) for name in expected}
        mismatches = {name: {"expected": expected[name], "actual": actual[name]} for name in expected if expected[name] != actual[name]}
        if mismatches:
            raise ExperimentValidationError(
                "experiment identity fingerprint 不匹配。",
                details={"experiment_id": experiment_id, "mismatches": mismatches},
            )
        return ExperimentCompatibility(experiment_id, **actual)

    def freeze_experiment(self, experiment_id: str, *, frozen_candidate_fingerprint: str) -> Any:
        row = self.get_experiment(experiment_id)
        if row.frozen_candidate_fingerprint != frozen_candidate_fingerprint:
            raise ExperimentValidationError("冻结时 candidate fingerprint 不匹配。")
        if row.status == "frozen":
            return row
        if row.status not in {"draft", "running"}:
            raise BacktestStateConflict(
                "只有 draft/running experiment 可以冻结。",
                details={"experiment_id": experiment_id, "status": row.status},
            )
        row.status = "frozen"
        row.frozen_at = _utcnow()
        self._session.flush()
        return row

    def complete_experiment(
        self,
        experiment_id: str,
        *,
        status: str,
        conclusion: JsonValue,
        snapshot_id: str,
        execution_policy_fingerprint: str,
        split_hash: str,
        frozen_candidate_fingerprint: str,
        finished_at: datetime | None = None,
    ) -> Any:
        if status not in {"completed", "failed", "insufficient_data", "exploratory", "rejected", "promotion_candidate"}:
            raise ExperimentValidationError("不允许的 experiment terminal status。", details={"status": status})
        self.validate_experiment_compatibility(
            experiment_id,
            snapshot_id=snapshot_id,
            execution_policy_fingerprint=execution_policy_fingerprint,
            split_hash=split_hash,
            frozen_candidate_fingerprint=frozen_candidate_fingerprint,
        )
        row = self.get_experiment(experiment_id)
        conclusion_json = _canonical_json(conclusion, field="conclusion")
        if status == "promotion_candidate":
            _validate_promotion_candidate_acceptance(row, _load_json(conclusion_json))
        if row.status == status and row.conclusion_json == conclusion_json:
            return row
        if row.status != "frozen":
            raise BacktestStateConflict(
                "experiment 必须先冻结，再写入样本外结论。",
                details={"experiment_id": experiment_id, "status": row.status},
            )
        row.status = status
        row.conclusion_json = conclusion_json
        row.finished_at = finished_at or _utcnow()
        self._session.flush()
        return row

    # -- Internal locking / validation -------------------------------------------

    def _locked_claim(self, request_fingerprint: str) -> Any | None:
        return self._session.execute(
            select(self._models.request_claim)
            .where(self._models.request_claim.request_fingerprint == request_fingerprint)
            .with_for_update()
        ).scalar_one_or_none()

    def _require_locked_claim(self, request_fingerprint: str) -> Any:
        claim = self._locked_claim(request_fingerprint)
        if claim is None:
            raise RequestClaimConflict(
                "backtest attempt 缺少 request claim。",
                details={"request_fingerprint": request_fingerprint},
            )
        return claim

    def _locked_run(self, backtest_id: str) -> Any:
        row = self._session.execute(
            select(self._models.run)
            .where(self._models.run.backtest_id == backtest_id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise ResearchRecordNotFound("未找到 backtest attempt。", details={"backtest_id": backtest_id})
        return row

    def _latest_attempt(self, request_fingerprint: str) -> Any | None:
        return self._session.execute(
            select(self._models.run)
            .where(self._models.run.request_fingerprint == request_fingerprint)
            .order_by(self._models.run.attempt_no.desc())
            .limit(1)
        ).scalar_one_or_none()

    def _run_from_claim_id(self, backtest_id: str | None, fingerprint: str, reference: str) -> Any | None:
        if not backtest_id:
            return None
        # This method follows a locking read of the claim. On InnoDB, the claim
        # can be current while a normal Session.get still serves an older
        # consistent-read snapshot, so resolve the pointed attempt as a current
        # locking read too.
        row = self._session.execute(
            select(self._models.run)
            .where(self._models.run.backtest_id == backtest_id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise RequestClaimConflict(
                f"{reference} claim 指向不存在的 backtest attempt。",
                details={"backtest_id": backtest_id},
            )
        if row.request_fingerprint != fingerprint:
            raise RequestClaimConflict(
                f"{reference} claim 指向不同 request_fingerprint 的 attempt。",
                details={"backtest_id": backtest_id},
            )
        return row

    @staticmethod
    def _assert_current_active_claim(claim: Any, run: Any) -> None:
        if claim.active_backtest_id != run.backtest_id:
            raise BacktestStateConflict(
                "attempt 不再是 request 的 current active claim。",
                details={"backtest_id": run.backtest_id, "active_backtest_id": claim.active_backtest_id},
            )

    def _assert_snapshot_integrity(self, row: Any) -> None:
        try:
            canonical_manifest = _canonical_json(row.partition_manifest_json, field="partition_manifest_json")
            _canonical_json(row.coverage_json, field="coverage_json")
            _canonical_json(row.missing_requirements_json, field="missing_requirements_json")
        except ValueError as exc:
            raise SnapshotIntegrityError(
                "snapshot JSON 不是有效 canonical JSON。", details={"snapshot_id": row.snapshot_id}
            ) from exc
        actual_hash = _sha256_text(canonical_manifest)
        if row.manifest_hash != actual_hash:
            raise SnapshotIntegrityError(
                "读取 snapshot 时发现 manifest hash 损坏。",
                details={"snapshot_id": row.snapshot_id, "expected_hash": actual_hash, "actual_hash": row.manifest_hash},
            )

    def _assert_artifact_integrity(self, row: Any, *, expected_content_hash: str | None = None) -> None:
        actual_hash = _sha256_bytes(bytes(row.payload))
        if row.content_hash != actual_hash:
            raise ArtifactIntegrityError(
                "读取 artifact 时发现 content hash 损坏。",
                details={"artifact_id": row.artifact_id, "expected_hash": actual_hash, "actual_hash": row.content_hash},
            )
        if expected_content_hash is not None and expected_content_hash != actual_hash:
            raise ArtifactIntegrityError(
                "artifact hash 不等于调用方的 snapshot 引用。",
                details={"artifact_id": row.artifact_id, "expected_hash": expected_content_hash, "actual_hash": actual_hash},
            )

    def _validate_split(self, split: Any, baseline: Any) -> None:
        if not isinstance(split, dict):
            raise ExperimentValidationError("split_json 必须是 object。")
        required = ("train", "validation", "out_of_sample")
        if set(split) != set(required):
            raise ExperimentValidationError(
                "split_json 必须且只能包含 train、validation、out_of_sample。",
                details={"actual_keys": sorted(split)},
            )
        parts: dict[str, list[str]] = {}
        all_dates: list[str] = []
        for name in required:
            dates = split[name]
            if not isinstance(dates, list) or not dates or not all(isinstance(day, str) and _is_iso_date(day) for day in dates):
                raise ExperimentValidationError(f"split.{name} 必须是非空 YYYY-MM-DD 日期数组。")
            if dates != sorted(dates) or len(set(dates)) != len(dates):
                raise ExperimentValidationError(f"split.{name} 必须升序且无重复。")
            parts[name] = dates
            all_dates.extend(dates)
        if len(set(all_dates)) != len(all_dates):
            raise ExperimentValidationError("train/validation/out_of_sample 不能重叠。")
        if not (max(parts["train"]) < min(parts["validation"]) < min(parts["out_of_sample"])):
            raise ExperimentValidationError("split 必须满足 train < validation < out_of_sample。")
        if baseline.signal_date:
            if set(all_dates) != {baseline.signal_date}:
                raise ExperimentValidationError("单日 baseline 不能伪造多段 OOS experiment split。")
            return
        if baseline.date_from and min(all_dates) < baseline.date_from:
            raise ExperimentValidationError("split 日期早于 baseline date_from。")
        if baseline.date_to and max(all_dates) > baseline.date_to:
            raise ExperimentValidationError("split 日期晚于 baseline date_to。")
        known_dates = _baseline_signal_dates(baseline)
        if known_dates is not None and set(all_dates) != known_dates:
            raise ExperimentValidationError("split 日期全集必须与 baseline 结果中的 signal dates 一致。")

    def _validate_experiment_row(self, row: Any) -> None:
        self._validate_hash(str(row.execution_policy_fingerprint), "execution_policy_fingerprint")
        self._validate_hash(str(row.split_hash), "split_hash")
        self._validate_hash(str(row.frozen_candidate_fingerprint), "frozen_candidate_fingerprint")
        canonical_split = _canonical_json(row.split_json, field="split_json")
        if _sha256_text(canonical_split) != row.split_hash:
            raise ExperimentValidationError(
                "读取 experiment 时发现 split_hash 损坏。", details={"experiment_id": row.experiment_id}
            )
        baseline = self.get_run(row.baseline_backtest_id)
        if baseline.snapshot_id != row.snapshot_id or baseline.execution_policy_fingerprint != row.execution_policy_fingerprint:
            raise ExperimentValidationError(
                "读取 experiment 时发现 baseline snapshot/policy 漂移。",
                details={"experiment_id": row.experiment_id},
            )
        self._validate_split(_load_json(canonical_split), baseline)

    @staticmethod
    def _require_nonempty(value: str, field: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ResearchRepositoryError(f"{field} 不能为空。", details={"field": field})

    @staticmethod
    def _validate_hash(value: str, field: str) -> None:
        if not isinstance(value, str) or len(value) != 64:
            raise ResearchRepositoryError(f"{field} 必须是 64 位 SHA-256 hex。", details={"field": field})
        try:
            int(value, 16)
        except ValueError as exc:
            raise ResearchRepositoryError(f"{field} 必须是 64 位 SHA-256 hex。", details={"field": field}) from exc


# The name is retained as a semantic alias for callers that prefer "backtest"
# before "research"; both names expose exactly the same dedicated API.
BacktestResearchRepository = ResearchBacktestRepository


def _canonical_json(value: JsonValue, *, field: str) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} 必须为 JSON-compatible 值或有效 JSON 字符串。") from exc
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 无法 canonical JSON 序列化。") from exc


def _to_payload_bytes(value: bytes | bytearray | memoryview | str | Mapping[str, Any] | Sequence[Any]) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    return _canonical_json(value, field="payload").encode("utf-8")


def _load_json(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("stored JSON 无法解析。") from exc


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _same_values(row: Any, /, **expected: Any) -> bool:
    return all(getattr(row, name) == value for name, value in expected.items())


def _as_mapping(value: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError("记录必须是 Mapping 或 dataclass。")


def _require_value(value: Mapping[str, Any], field: str) -> Any:
    result = value.get(field)
    if result is None or (isinstance(result, str) and not result.strip()):
        raise ResearchRepositoryError(f"{field} 不能为空。", details={"field": field})
    return result


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _utcnow() -> datetime:
    return utcnow_naive()


def _is_iso_date(value: str) -> bool:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False


def _baseline_signal_dates(run: Any) -> set[str] | None:
    """Return explicit result dates when the runner persisted them, if any."""
    try:
        result = _load_json(run.result_json)
    except ValueError:
        return None
    if not isinstance(result, dict):
        return None
    for key in ("signal_dates", "evaluated_signal_dates"):
        dates = result.get(key)
        if isinstance(dates, list) and all(isinstance(value, str) for value in dates):
            return set(dates)
    rows = result.get("results")
    if isinstance(rows, list):
        dates = {row.get("signal_date") for row in rows if isinstance(row, dict) and isinstance(row.get("signal_date"), str)}
        if dates:
            return dates
    return None


def _validate_promotion_candidate_acceptance(row: Any, conclusion: Any) -> None:
    """Allow promotion only when frozen OOS acceptance gates are evidenced."""

    if not isinstance(conclusion, Mapping):
        raise ExperimentValidationError("promotion_candidate conclusion 必须是 object。")
    metrics = conclusion.get("out_of_sample_metrics")
    if not isinstance(metrics, Mapping):
        raise ExperimentValidationError(
            "promotion_candidate 必须提供 out_of_sample_metrics。",
            details={"required_field": "out_of_sample_metrics"},
        )
    acceptance = _load_json(row.acceptance_json)
    if not isinstance(acceptance, Mapping):
        raise ExperimentValidationError("冻结 acceptance_json 不是 object。")

    def metric(name: str, *aliases: str) -> float:
        for key in (name, *aliases):
            value = metrics.get(key)
            if isinstance(value, bool):
                continue
            try:
                normalized = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(normalized):
                return normalized
        raise ExperimentValidationError(
            f"promotion_candidate 缺少有效 OOS 指标 {name}。",
            details={"required_metric": name},
        )

    expected = {
        "min_trade_count": acceptance.get("min_trade_count"),
        "min_win_rate_pct": acceptance.get("min_win_rate_pct"),
        "min_profit_loss_ratio": acceptance.get("min_profit_loss_ratio"),
        "min_average_net_return_pct": acceptance.get("min_average_net_return_pct"),
        "max_drawdown_pct": acceptance.get("max_drawdown_pct"),
    }
    try:
        thresholds = {key: float(value) for key, value in expected.items()}
    except (TypeError, ValueError) as exc:
        raise ExperimentValidationError("冻结 acceptance 指标不完整或非法。") from exc
    if any(not math.isfinite(value) for value in thresholds.values()):
        raise ExperimentValidationError("冻结 acceptance 指标必须是有限数。")

    observed = {
        "min_trade_count": metric("executed_trade_count", "trade_count"),
        "min_win_rate_pct": metric("win_rate_pct"),
        "min_profit_loss_ratio": metric("profit_loss_ratio"),
        "min_average_net_return_pct": metric("average_net_return_pct"),
        "max_drawdown_pct": metric("max_drawdown_pct"),
    }
    failures: dict[str, dict[str, float]] = {}
    for key in (
        "min_trade_count",
        "min_win_rate_pct",
        "min_profit_loss_ratio",
        "min_average_net_return_pct",
    ):
        if observed[key] < thresholds[key]:
            failures[key] = {"actual": observed[key], "required_minimum": thresholds[key]}
    if observed["max_drawdown_pct"] > thresholds["max_drawdown_pct"]:
        failures["max_drawdown_pct"] = {
            "actual": observed["max_drawdown_pct"],
            "required_maximum": thresholds["max_drawdown_pct"],
        }
    if failures:
        raise ExperimentValidationError(
            "out-of-sample 指标未满足冻结 promotion acceptance。",
            details={"failed_gates": failures, "out_of_sample_metrics": observed},
        )


def _trade_payload(trade: Any) -> Mapping[str, Any]:
    try:
        value = _load_json(trade.trade_json)
    except ValueError:
        return {}
    return value if isinstance(value, Mapping) else {}


def _trade_net_return(trade: Any) -> float:
    payload = _trade_payload(trade)
    for field in ("net_return", "net_return_pct", "executed_return", "return_pct", "pnl"):
        value = payload.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def _is_attributable_trade(trade: Any) -> bool:
    label = str(trade.outcome_label).lower()
    if label in _NON_TRADE_OUTCOME_LABELS:
        return False
    if label in {"failed", "loss", "negative"}:
        return True
    return _trade_net_return(trade) < 0


__all__ = [
    "ArtifactIntegrityError",
    "AttributionValidationError",
    "BacktestORMModels",
    "BacktestResearchRepository",
    "BacktestStateConflict",
    "ExperimentCompatibility",
    "ExperimentValidationError",
    "ImmutableRecordConflict",
    "RepositoryConfigurationError",
    "RequestClaimConflict",
    "RequestClaimResult",
    "ResearchBacktestRepository",
    "ResearchRecordNotFound",
    "ResearchRepositoryError",
    "SnapshotIntegrityError",
]
