"""Database-backed capability for persistence-only run recovery."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import RecommendationRun
from app.services.runs.collection_seal import verify_recommendation_collection_seal
from app.services.runs.contracts import RunErrorCode, RunPhase, RunStatus
from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository
from app.services.runs.state_machine import ResumeCheck


@dataclass(frozen=True, slots=True)
class RecoveryCapability:
    run_id: str
    can_resume: bool
    block_code: str | None
    block_reason: str | None
    resume_check: ResumeCheck | None = None

    def require_resume_check(self) -> ResumeCheck:
        if not self.can_resume or self.resume_check is None:
            raise RecoveryCapabilityError(
                self.block_code or RunErrorCode.RESUME_REQUIRES_VERIFIED_SEAL.value,
                self.block_reason or "run is not recoverable",
            )
        return self.resume_check


class RecoveryCapabilityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


def evaluate_recovery_capability(
    db: Session,
    run_id: str,
    *,
    now: datetime,
    lock: bool = False,
) -> RecoveryCapability:
    """Validate every durable recovery fact, optionally locking it for completion."""
    normalized_run_id = str(run_id).strip()
    statement = select(RecommendationRun).where(
        RecommendationRun.run_id == normalized_run_id
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        return _blocked(
            normalized_run_id,
            RunErrorCode.RUN_NOT_FOUND,
            "运行不存在",
        )

    try:
        status = RunStatus(str(row.status))
    except ValueError:
        return _blocked(
            normalized_run_id,
            RunErrorCode.RESUME_REQUIRES_INTERRUPTED_STATUS,
            "运行状态无效，不能恢复",
        )
    if status is not RunStatus.INTERRUPTED:
        message = (
            "failed 是终态，请创建新的运行"
            if status is RunStatus.FAILED
            else f"只有 interrupted 运行可恢复，当前状态为 {status.value}"
        )
        return _blocked(
            normalized_run_id,
            RunErrorCode.RESUME_REQUIRES_INTERRUPTED_STATUS,
            message,
        )

    record = SQLAlchemyRunRepository(db).get_run(normalized_run_id)
    if record is None:
        return _blocked(
            normalized_run_id,
            RunErrorCode.RESUME_REQUIRES_CANONICAL_REQUEST,
            "运行缺少可验证的 canonical request，不能恢复",
        )
    if record.has_active_lease(now):
        return _blocked(
            normalized_run_id,
            RunErrorCode.RESUME_REQUIRES_NO_ACTIVE_LEASE,
            "仍存在活跃 worker lease，不能并发恢复",
        )
    if (
        record.last_committed_phase is not RunPhase.PERSISTENCE
        or not str(record.side_effect_key or "").strip()
    ):
        return _blocked(
            normalized_run_id,
            RunErrorCode.RESUME_REQUIRES_PERSISTENCE_CHECKPOINT,
            "恢复只允许已提交 persistence checkpoint 的运行",
        )

    verification = verify_recommendation_collection_seal(
        db,
        normalized_run_id,
        expected_side_effect_key=record.side_effect_key,
        lock=lock,
    )
    if not verification.valid:
        return RecoveryCapability(
            run_id=normalized_run_id,
            can_resume=False,
            block_code=verification.block_code,
            block_reason=verification.block_reason,
        )
    resume_check = ResumeCheck(
        last_committed_phase=record.last_committed_phase,
        side_effect_key=record.side_effect_key,
        has_active_lease=False,
        collection_seal_verified=True,
    )
    return RecoveryCapability(
        run_id=normalized_run_id,
        can_resume=True,
        block_code=None,
        block_reason=None,
        resume_check=resume_check,
    )


def _blocked(
    run_id: str,
    code: RunErrorCode,
    reason: str,
) -> RecoveryCapability:
    return RecoveryCapability(
        run_id=run_id,
        can_resume=False,
        block_code=code.value,
        block_reason=reason,
    )


__all__ = [
    "RecoveryCapability",
    "RecoveryCapabilityError",
    "evaluate_recovery_capability",
]
