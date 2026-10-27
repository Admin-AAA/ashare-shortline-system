"""SQLAlchemy implementation of the durable :mod:`runs.repository` port.

Every lifecycle mutation is a single conditional UPDATE.  Reads are used only to
build the value-object result and to classify a failed CAS; they are never treated as
an ownership check by themselves.  In particular, checkpoint phase, side-effect key
and heartbeat are written by one UPDATE guarded by revision, status and lease token.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utcnow_naive
from app.models.tables import RecommendationRun
from app.services.runs.contracts import (
    RunActor,
    RunErrorCode,
    RunPhase,
    RunStatus,
    RunTrigger,
)
from app.services.runs.request_persistence import (
    assert_run_request_index_consistency,
    load_canonical_run_request,
)
from app.services.runs.repository import (
    CheckpointCommit,
    CompareAndSetOutcome,
    CompareAndSetResult,
    CreateQueuedRun,
    LeaseRenewal,
    LeaseRequest,
    RunCheckpoint,
    RunFailure,
    RunLease,
    RunRecord,
    TransitionCommit,
)
from app.services.runs.state_machine import RunStateMachine, RunStateTransitionError
from app.services.official_sample_scope import evaluation_scope_for_run, scope_on_transition


class SQLAlchemyRunRepository:
    """Forward-only repository adapter over ``RecommendationRun`` lifecycle columns."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        return self._session

    def create_queued_run(self, command: CreateQueuedRun) -> CompareAndSetResult:
        if self._session.get(RecommendationRun, command.run_id) is not None:
            return CompareAndSetResult(CompareAndSetOutcome.DUPLICATE, self.get_run(command.run_id))
        request = command.request
        row = RecommendationRun(
            run_id=command.run_id,
            preset_id=request.preset_id,
            run_name=request.run_name,
            run_mode=request.run_mode.value,
            evaluation_scope=evaluation_scope_for_run(
                run_mode=request.run_mode.value,
                trade_date_override=request.trade_date_override,
                matrix_run=request.matrix_run,
                generated_at=command.created_at,  # 排队时刻，仅暂定；开跑时重算、完成时按真实生成时刻定稿
            ),
            trade_date=request.trade_date_override or command.created_at.date().isoformat(),
            started_at=command.created_at,
            universe_count=0,
            recommended_count=0,
            market_scope=request.market_scope,
            market_scope_value=request.market_scope_value,
            risk_template=request.risk_template,
            score_line=request.score_line,
            output_count=request.output_count,
            selected_skills_json=json.dumps(
                [item.model_dump(mode="json") for item in request.skills],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            params_json=request.to_canonical_json(),
            market_snapshot_json="{}",
            result_summary_json=json.dumps({"agent_runtime": "queued"}, ensure_ascii=False),
            filter_audit_json="{}",
            status=RunStatus.QUEUED.value,
            heartbeat_at=command.created_at,
            progress_phase=RunStatus.QUEUED.value,
            progress_current=0,
            progress_total=0,
            progress_message="等待后台工作线程接管",
            request_schema_version=request.schema_version,
            request_json=command.request_json,
            execution_version=command.execution_version,
            run_revision=0,
            lifecycle_phase=None,
            checkpoint_side_effect_key=None,
            lease_owner_id=None,
            lease_token=None,
            lease_acquired_at=None,
            lease_expires_at=None,
            stable_error_code=None,
        )
        assert_run_request_index_consistency(row, request)
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError:
            self._session.expire_all()
            return CompareAndSetResult(CompareAndSetOutcome.DUPLICATE, self._get_run_for_update(command.run_id))
        return CompareAndSetResult(CompareAndSetOutcome.APPLIED, self._to_record(row))

    def get_run(self, run_id: str) -> RunRecord | None:
        row = self._session.get(RecommendationRun, run_id)
        if row is None:
            return None
        return self._to_record(row)

    def acquire_lease(self, run_id: str, request: LeaseRequest) -> CompareAndSetResult:
        record = self.get_run(run_id)
        if record is None:
            return CompareAndSetResult.not_found()
        if request.expected_revision is not None and record.revision != request.expected_revision:
            return CompareAndSetResult(CompareAndSetOutcome.REVISION_MISMATCH, record, RunErrorCode.COMPARE_AND_SET_CONFLICT.value)
        if record.status is not RunStatus.RUNNING:
            return CompareAndSetResult(CompareAndSetOutcome.STATUS_MISMATCH, record, RunErrorCode.INVALID_STATUS_TRANSITION.value)
        token = uuid.uuid4().hex
        expected_revision = record.revision
        result = self._session.execute(
            update(RecommendationRun)
            .where(
                RecommendationRun.run_id == run_id,
                RecommendationRun.run_revision == expected_revision,
                RecommendationRun.status == RunStatus.RUNNING.value,
                or_(
                    RecommendationRun.lease_expires_at.is_(None),
                    RecommendationRun.lease_expires_at <= request.now,
                ),
            )
            .values(
                lease_owner_id=request.owner_id,
                lease_token=token,
                lease_acquired_at=request.now,
                lease_expires_at=request.now + request.ttl,
                heartbeat_at=request.now,
                run_revision=expected_revision + 1,
            )
        )
        self._session.flush()
        if result.rowcount != 1:
            return self._classify_cas_failure(
                run_id,
                expected_revision=expected_revision,
                expected_status=RunStatus.RUNNING,
                now=request.now,
            )
        return CompareAndSetResult(CompareAndSetOutcome.APPLIED, self._refresh_record(run_id))

    def renew_lease(self, run_id: str, renewal: LeaseRenewal) -> CompareAndSetResult:
        record = self.get_run(run_id)
        if record is None:
            return CompareAndSetResult.not_found()
        if renewal.expected_revision is not None and record.revision != renewal.expected_revision:
            return CompareAndSetResult(CompareAndSetOutcome.REVISION_MISMATCH, record, RunErrorCode.COMPARE_AND_SET_CONFLICT.value)
        expected_revision = record.revision
        result = self._session.execute(
            update(RecommendationRun)
            .where(
                RecommendationRun.run_id == run_id,
                RecommendationRun.run_revision == expected_revision,
                RecommendationRun.status == RunStatus.RUNNING.value,
                RecommendationRun.lease_token == renewal.token,
                RecommendationRun.lease_expires_at > renewal.now,
            )
            .values(
                lease_expires_at=renewal.now + renewal.ttl,
                heartbeat_at=renewal.now,
                run_revision=expected_revision + 1,
            )
        )
        self._session.flush()
        if result.rowcount != 1:
            return self._classify_cas_failure(
                run_id,
                expected_revision=expected_revision,
                expected_status=RunStatus.RUNNING,
                lease_token=renewal.token,
                now=renewal.now,
            )
        return CompareAndSetResult(CompareAndSetOutcome.APPLIED, self._refresh_record(run_id))

    def release_lease(
        self,
        run_id: str,
        *,
        token: str,
        expected_revision: int | None = None,
    ) -> CompareAndSetResult:
        record = self.get_run(run_id)
        if record is None:
            return CompareAndSetResult.not_found()
        if expected_revision is not None and record.revision != expected_revision:
            return CompareAndSetResult(CompareAndSetOutcome.REVISION_MISMATCH, record, RunErrorCode.COMPARE_AND_SET_CONFLICT.value)
        expected = record.revision
        result = self._session.execute(
            update(RecommendationRun)
            .where(
                RecommendationRun.run_id == run_id,
                RecommendationRun.run_revision == expected,
                RecommendationRun.lease_token == token,
            )
            .values(
                lease_owner_id=None,
                lease_token=None,
                lease_acquired_at=None,
                lease_expires_at=None,
                run_revision=expected + 1,
            )
        )
        self._session.flush()
        if result.rowcount != 1:
            return self._classify_cas_failure(
                run_id,
                expected_revision=expected,
                lease_token=token,
            )
        return CompareAndSetResult(CompareAndSetOutcome.APPLIED, self._refresh_record(run_id))

    def commit_checkpoint(self, run_id: str, commit: CheckpointCommit) -> CompareAndSetResult:
        record = self.get_run(run_id)
        if record is None:
            return CompareAndSetResult.not_found()
        if record.revision != commit.expected_revision:
            return CompareAndSetResult(CompareAndSetOutcome.REVISION_MISMATCH, record, RunErrorCode.COMPARE_AND_SET_CONFLICT.value)
        if record.status is not RunStatus.RUNNING:
            return CompareAndSetResult(CompareAndSetOutcome.STATUS_MISMATCH, record, RunErrorCode.INVALID_STATUS_TRANSITION.value)
        if record.lease is None or record.lease.token != commit.lease_token:
            return CompareAndSetResult.lease_lost(record)
        if not record.lease.is_active_at(commit.checkpoint.heartbeat_at):
            return CompareAndSetResult.lease_lost(record)
        if (
            record.last_committed_phase is commit.checkpoint.phase
            and record.side_effect_key == commit.checkpoint.side_effect_key
        ):
            return CompareAndSetResult(
                CompareAndSetOutcome.DUPLICATE,
                record,
                RunErrorCode.CHECKPOINT_ALREADY_COMMITTED.value,
            )
        try:
            RunStateMachine.require_next_checkpoint(
                last_committed_phase=record.last_committed_phase,
                phase=commit.checkpoint.phase,
            )
        except RunStateTransitionError as exc:
            return CompareAndSetResult(
                CompareAndSetOutcome.STATUS_MISMATCH,
                record,
                exc.code.value,
            )

        # The three checkpoint facts and the revision are one conditional write.
        result = self._session.execute(
            update(RecommendationRun)
            .where(
                RecommendationRun.run_id == run_id,
                RecommendationRun.run_revision == commit.expected_revision,
                RecommendationRun.status == RunStatus.RUNNING.value,
                RecommendationRun.lease_token == commit.lease_token,
                RecommendationRun.lease_expires_at > commit.checkpoint.heartbeat_at,
            )
            .values(
                lifecycle_phase=commit.checkpoint.phase.value,
                checkpoint_side_effect_key=commit.checkpoint.side_effect_key,
                heartbeat_at=commit.checkpoint.heartbeat_at,
                run_revision=commit.expected_revision + 1,
            )
        )
        self._session.flush()
        if result.rowcount != 1:
            return self._classify_cas_failure(
                run_id,
                expected_revision=commit.expected_revision,
                expected_status=RunStatus.RUNNING,
                lease_token=commit.lease_token,
                now=commit.checkpoint.heartbeat_at,
            )
        return CompareAndSetResult(CompareAndSetOutcome.APPLIED, self._refresh_record(run_id))

    def compare_and_set_transition(self, run_id: str, commit: TransitionCommit) -> CompareAndSetResult:
        record = self.get_run(run_id)
        if record is None:
            return CompareAndSetResult.not_found()
        if record.revision != commit.expected_revision:
            return CompareAndSetResult(CompareAndSetOutcome.REVISION_MISMATCH, record, RunErrorCode.COMPARE_AND_SET_CONFLICT.value)
        if record.status is not commit.transition.expected_status:
            return CompareAndSetResult(CompareAndSetOutcome.STATUS_MISMATCH, record, RunErrorCode.INVALID_STATUS_TRANSITION.value)
        if (
            commit.transition.trigger is RunTrigger.HEARTBEAT_TIMEOUT
            and record.has_active_lease(commit.now)
        ):
            # A watchdog may interrupt only after the worker lease has expired;
            # otherwise it could steal a live worker between heartbeats.
            return CompareAndSetResult.lease_lost(record)

        worker_owned = commit.transition.requires_active_lease or (
            commit.transition.trigger is RunTrigger.CANCEL and commit.transition.actor is RunActor.WORKER
        )
        if worker_owned and commit.lease_token is None:
            return CompareAndSetResult.lease_lost(record)
        if commit.lease_token is not None:
            if record.lease is None or record.lease.token != commit.lease_token:
                return CompareAndSetResult.lease_lost(record)
            if not record.lease.is_active_at(commit.now):
                return CompareAndSetResult.lease_lost(record)

        values: dict[str, Any] = {
            "status": commit.transition.target_status.value,
            "heartbeat_at": commit.now,
            "stable_error_code": commit.transition.stable_error_code,
            "run_revision": commit.expected_revision + 1,
        }
        if commit.transition.target_phase is not None:
            values["lifecycle_phase"] = commit.transition.target_phase.value
        if commit.transition.error_message is not None:
            values["error_message"] = commit.transition.error_message
        elif commit.transition.target_status is RunStatus.RUNNING:
            values["error_message"] = None

        clear_lease = commit.transition.terminal or commit.transition.target_status is RunStatus.INTERRUPTED
        if clear_lease:
            values.update(
                {
                    "lease_owner_id": None,
                    "lease_token": None,
                    "lease_acquired_at": None,
                    "lease_expires_at": None,
                }
            )
        if commit.transition.target_status is RunStatus.INTERRUPTED:
            values["interrupted_at"] = commit.now
            values["finished_at"] = commit.now
        elif commit.transition.terminal:
            values["finished_at"] = commit.now
        elif commit.transition.target_status is RunStatus.RUNNING:
            values["finished_at"] = None
            values["interrupted_at"] = None
        # 带 override 的补跑：失败/中断/取消 → 定稿为不算实盘；重新开跑 → 按此刻重新暂定（完成时再按真实生成时刻定稿）
        row = self._session.get(RecommendationRun, run_id)
        if row is not None:
            new_scope = scope_on_transition(row, record.request, commit.transition.target_status, commit.now)
            if new_scope is not None:
                values["evaluation_scope"] = new_scope

        conditions = [
            RecommendationRun.run_id == run_id,
            RecommendationRun.run_revision == commit.expected_revision,
            RecommendationRun.status == commit.transition.expected_status.value,
        ]
        if commit.lease_token is not None:
            conditions.extend(
                [
                    RecommendationRun.lease_token == commit.lease_token,
                    RecommendationRun.lease_expires_at > commit.now,
                ]
            )
        result = self._session.execute(update(RecommendationRun).where(*conditions).values(**values))
        self._session.flush()
        if result.rowcount != 1:
            return self._classify_cas_failure(
                run_id,
                expected_revision=commit.expected_revision,
                expected_status=commit.transition.expected_status,
                lease_token=commit.lease_token,
                now=commit.now,
            )
        return CompareAndSetResult(CompareAndSetOutcome.APPLIED, self._refresh_record(run_id))

    def _classify_cas_failure(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_status: RunStatus | None = None,
        lease_token: str | None = None,
        now: datetime | None = None,
    ) -> CompareAndSetResult:
        record = self._refresh_record(run_id)
        if record is None:
            return CompareAndSetResult.not_found()
        if expected_revision is not None and record.revision != expected_revision:
            return CompareAndSetResult(
                CompareAndSetOutcome.REVISION_MISMATCH,
                record,
                RunErrorCode.COMPARE_AND_SET_CONFLICT.value,
            )
        if expected_status is not None and record.status is not expected_status:
            return CompareAndSetResult(
                CompareAndSetOutcome.STATUS_MISMATCH,
                record,
                RunErrorCode.INVALID_STATUS_TRANSITION.value,
            )
        if lease_token is not None:
            if record.lease is None or record.lease.token != lease_token:
                return CompareAndSetResult.lease_lost(record)
            if now is not None and not record.lease.is_active_at(now):
                return CompareAndSetResult.lease_lost(record)
        return CompareAndSetResult(
            CompareAndSetOutcome.CONFLICT,
            record,
            RunErrorCode.COMPARE_AND_SET_CONFLICT.value,
        )

    def _get_run_for_update(self, run_id: str) -> RunRecord | None:
        row = self._session.scalar(
            select(RecommendationRun)
            .where(RecommendationRun.run_id == run_id)
            .with_for_update()
        )
        return self._to_record(row) if row is not None else None

    def _refresh_record(self, run_id: str) -> RunRecord | None:
        self._session.expire_all()
        return self.get_run(run_id)

    @staticmethod
    def _to_record(row: RecommendationRun | None) -> RunRecord | None:
        if row is None:
            return None
        try:
            request = load_canonical_run_request(row)
            status = RunStatus(str(row.status))
        except (ValueError, TypeError, RuntimeError):
            return None
        phase = _parse_phase(getattr(row, "lifecycle_phase", None))
        checkpoint = None
        side_effect_key = getattr(row, "checkpoint_side_effect_key", None)
        checkpoint_time = getattr(row, "heartbeat_at", None) or getattr(row, "started_at", None) or utcnow_naive()
        if phase is not None and phase is not RunPhase.COMPLETED and side_effect_key:
            checkpoint = RunCheckpoint(
                phase=phase,
                side_effect_key=str(side_effect_key),
                heartbeat_at=checkpoint_time,
            )
        lease = None
        if status not in RunStateMachine.terminal_statuses:
            owner = getattr(row, "lease_owner_id", None)
            token = getattr(row, "lease_token", None)
            acquired = getattr(row, "lease_acquired_at", None)
            expires = getattr(row, "lease_expires_at", None)
            if owner and token and acquired and expires:
                lease = RunLease(str(owner), str(token), acquired, expires)
        failure = None
        stable_error_code = getattr(row, "stable_error_code", None)
        if row.error_message or stable_error_code:
            failure = RunFailure(
                str(stable_error_code or "legacy_error"),
                str(row.error_message or stable_error_code),
            )
        return RunRecord(
            run_id=str(row.run_id),
            request=request,
            request_json=request.to_canonical_json(),
            request_schema_version=int(row.request_schema_version),
            execution_version=getattr(row, "execution_version", None),
            status=status,
            revision=int(getattr(row, "run_revision", 0) or 0),
            current_phase=phase,
            checkpoint=checkpoint,
            heartbeat_at=row.heartbeat_at,
            lease=lease,
            failure=failure,
            created_at=row.started_at or row.heartbeat_at or utcnow_naive(),
            started_at=row.started_at if status is not RunStatus.QUEUED else None,
            finished_at=row.finished_at,
        )


def _parse_phase(value: object) -> RunPhase | None:
    if value is None:
        return None
    try:
        return RunPhase(str(value))
    except ValueError:
        return None


__all__ = ["SQLAlchemyRunRepository"]
