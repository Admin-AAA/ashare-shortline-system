"""Run the full recommendation pipeline through the staged RunCoordinator.

This is the only production execution path. Every API, worker, scheduler and
recovery entry point reaches the same eight coordinator stages.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.evidence import EvidenceClass, apply_formal_run_provenance

from .contracts import RunPhase, RunRequest, RunStatus, truncate_run_error_message
from .coordinator import RunCoordinator, RunCoordinatorDependencies, StageData, WorkerLease
from .recovery import RecoveryCapabilityError, evaluate_recovery_capability
from .sqlalchemy_repository import SQLAlchemyRunRepository
from .stages.assembly import assemble_pipeline_stages
from .stages.contracts import StageOutput

logger = logging.getLogger(__name__)

RUN_HEARTBEAT_STALE_SECONDS = 1200


def _stage_cardinality(phase: RunPhase, data: StageData) -> tuple[int, int]:
    """Return stable logical input/output counts for one completed stage."""
    if phase is RunPhase.PREPARE:
        return 1, len(data.selected_skills)
    if phase is RunPhase.MARKET_DATA:
        return len(data.stocks), len(data.prefiltered_stocks)
    if phase is RunPhase.AUXILIARY_DATA:
        return len(data.prefiltered_stocks), len(data.prefiltered_stocks)
    if phase is RunPhase.FEATURES:
        return len(data.prefiltered_stocks), len(data.features)
    if phase is RunPhase.STRATEGY_EVALUATION:
        return len(data.research_contexts), len(data.skill_candidates)
    if phase is RunPhase.REVIEW:
        return len(data.skill_candidates), len(data.candidate_results)
    if phase is RunPhase.AGGREGATION:
        return len(data.candidate_results), len(data.cards)
    if phase is RunPhase.PERSISTENCE:
        return len(data.cards), len(data.saved_recommendations)
    return 0, 0


def _is_reconstruction_marked(run: Any) -> bool:
    return (
        str(getattr(run, "evidence_class", "") or "")
        == EvidenceClass.HISTORICAL_RECONSTRUCTION.value
        or bool(getattr(run, "reconstruction_batch_id", None))
    )


def _finalize_completed_run_provenance(db: Session, run_id: str) -> bool:
    """Seal only explicitly marked reconstruction runs in the completion transaction."""
    from app.models.tables import RecommendationRun
    from app.services.evidence.reconstruction_batch import finalize_reconstruction_run

    run = db.get(RecommendationRun, run_id)
    if run is None:
        raise RuntimeError(f"Run {run_id} 在完成 provenance 时不存在")
    apply_formal_run_provenance(run)
    if _is_reconstruction_marked(run):
        finalize_reconstruction_run(db, run_id)
        return True
    return False


def _mark_pipeline_run_failed(
    db: Session,
    *,
    run_id: str,
    coordinator: RunCoordinator,
    worker_lease: WorkerLease | None,
    message: str,
) -> None:
    """Rollback uncommitted facts and fail only if this worker still owns the run."""
    from app.models.tables import RecommendationRun
    from app.services.evidence.reconstruction_batch import mark_reconstruction_batch_failed
    from app.services.workbench_service import invalidate_bootstrap_cache

    # 异常文本可能带整条 SQL 和全部绑定参数（10-09 实测 107KB），不截断写进
    # progress_message 会触发 1406，连带回滚 fail 状态迁移，run 卡在 running 被判中断。
    message = truncate_run_error_message(message)
    db.rollback()
    failed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    managed_record = SQLAlchemyRunRepository(db).get_run(run_id)
    if managed_record is None:
        return
    if managed_record.status is RunStatus.QUEUED and worker_lease is None:
        transitioned = coordinator.spawn_failed(run_id, now=failed_at, message=message)
    elif managed_record.status is RunStatus.RUNNING and worker_lease is not None:
        transitioned = coordinator.fail(worker_lease, now=failed_at, message=message)
    else:
        logger.warning("run failure has no current worker lease; refusing mutation for %s", run_id)
        return
    if not transitioned:
        db.rollback()
        logger.warning("run failure CAS lost for %s", run_id)
        return
    db.expire_all()
    run = db.get(RecommendationRun, run_id)
    if run is not None:
        run.progress_phase = "failed"
        run.progress_message = message
        if _is_reconstruction_marked(run):
            mark_reconstruction_batch_failed(
                db,
                str(run.reconstruction_batch_id),
                message,
            )
    db.commit()
    invalidate_bootstrap_cache()


def _commit_stage_progress(
    db: Session,
    *,
    coordinator: RunCoordinator,
    worker_lease: WorkerLease,
    phase: str,
) -> WorkerLease:
    """Renew the caller's lease before committing legacy progress projections."""
    from app.models.tables import RecommendationRun
    from app.services.workbench_service import _update_run_progress

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    renewed_lease = coordinator.renew_worker_lease(
        worker_lease,
        now=now,
        ttl=timedelta(seconds=RUN_HEARTBEAT_STALE_SECONDS),
    )
    if renewed_lease is None:
        raise RuntimeError("Run lifecycle lease 已丢失或过期，worker 必须停止写入")
    progress_run = db.get(RecommendationRun, worker_lease.run_id)
    if progress_run is not None:
        progress_run.checkpoint_phase = phase
    _update_run_progress(
        db,
        worker_lease.run_id,
        phase=phase,
        current=1,
        total=1,
        message=f"阶段 {phase} 完成",
        status="running",
        renew_lease=False,
    )
    return renewed_lease


def _complete_sealed_persistence_recovery(
    db: Session,
    *,
    run_id: str,
    now: datetime,
) -> dict[str, Any]:
    """Complete one verified persistence checkpoint without executing a stage."""
    from app.models.tables import Recommendation, RecommendationRun
    from app.services.workbench_service import (
        _serialize_recommendation,
        _serialize_run_summary,
        invalidate_bootstrap_cache,
    )

    repository = SQLAlchemyRunRepository(db)
    coordinator = RunCoordinator.from_dependencies(
        RunCoordinatorDependencies(repository=repository)
    )
    try:
        capability = evaluate_recovery_capability(
            db,
            run_id,
            now=now,
            lock=True,
        )
        if not capability.can_resume:
            raise RecoveryCapabilityError(
                capability.block_code or "resume_rejected",
                capability.block_reason or "运行不满足恢复条件",
            )
        verified_resume = capability.require_resume_check()
        if not coordinator.resume(
            run_id,
            now=now,
            verified_resume=verified_resume,
        ):
            raise RuntimeError("Run completion-only resume CAS 失败")
        lease = coordinator.acquire_worker_lease(
            run_id,
            now=now,
            ttl=timedelta(seconds=RUN_HEARTBEAT_STALE_SECONDS),
        )
        if lease is None:
            raise RuntimeError("Run completion-only recovery 无法获取 worker lease")
        completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if not coordinator.complete(lease, now=completed_at):
            raise RuntimeError("Run completion-only recovery completion CAS 失败")

        db.expire_all()
        saved = list(
            db.scalars(
                select(Recommendation)
                .where(Recommendation.run_id == run_id)
                .order_by(Recommendation.final_score.desc())
            ).all()
        )
        run = db.get(RecommendationRun, run_id)
        if run is None:
            raise RuntimeError(f"Run {run_id} 在恢复完成后不存在")
        run.recommended_count = len(saved)
        run.progress_phase = RunStatus.COMPLETED.value
        run.checkpoint_phase = RunStatus.COMPLETED.value
        run.progress_current = len(saved)
        run.progress_total = len(saved)
        run.progress_message = "已核验封印结果并完成运行"
        _finalize_completed_run_provenance(db, run_id)
        db.flush()
        db.commit()
    except Exception:
        db.rollback()
        raise

    invalidate_bootstrap_cache()
    db.expire_all()
    completed_run = db.get(RecommendationRun, run_id)
    if completed_run is None:
        raise RuntimeError(f"Run {run_id} 在恢复提交后不存在")
    persisted = list(
        db.scalars(
            select(Recommendation)
            .where(Recommendation.run_id == run_id)
            .order_by(Recommendation.final_score.desc())
        ).all()
    )
    return {
        "run": _serialize_run_summary(db, completed_run),
        "recommendations": [
            _serialize_recommendation(db, recommendation)
            for recommendation in persisted
        ],
    }


async def run_staged_pipeline(
    db: Session,
    *,
    run_id: str,
    request: RunRequest,
    existing_run_id: str | None = None,
) -> dict[str, Any]:
    """Execute the full recommendation pipeline through typed RunCoordinator stages.

    Prerequisites: the run must already be persisted as QUEUED (by
    ``enqueue_workbench_run``) so that the lifecycle repository can find it.
    This function transitions it to RUNNING, acquires a worker lease, and then
    iterates ``execute_next_stage_async`` until all 8 phases are committed.
    """
    from app.models.tables import Recommendation, RecommendationRun
    from app.services.workbench_service import (
        _build_run_summary,
        _dumps,
        _loads,
        _serialize_recommendation,
        _serialize_run_summary,
        _slim_market_snapshot,
        _strip_audit_rows,
        invalidate_bootstrap_cache,
    )
    from app.agents.session_registry import AgentSessionRegistry
    from app.db.session import SessionLocal
    from app.services.registry.skills import sync_skills

    lifecycle_repository = SQLAlchemyRunRepository(db)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    persisted = lifecycle_repository.get_run(run_id)
    if persisted is None:
        raise RuntimeError(f"Run {run_id} 在 lifecycle repository 中不存在")
    if persisted.status is RunStatus.COMPLETED:
        run = db.get(RecommendationRun, run_id)
        saved = list(
            db.scalars(
                select(Recommendation)
                .where(Recommendation.run_id == run_id)
                .order_by(Recommendation.final_score.desc())
            ).all()
        )
        return {
            "run": _serialize_run_summary(db, run),
            "recommendations": [
                _serialize_recommendation(db, recommendation)
                for recommendation in saved
            ],
        }
    # Compatibility callers may still pass existing_run_id. It is never used to
    # infer recovery: durable status is the only lifecycle source of truth.
    if persisted.status is RunStatus.INTERRUPTED:
        return _complete_sealed_persistence_recovery(
            db,
            run_id=run_id,
            now=now,
        )

    # --- Assemble coordinator with all stages ---
    stages = assemble_pipeline_stages(db, run_id=run_id)
    dependencies = RunCoordinatorDependencies(
        repository=lifecycle_repository,
        stages=stages,
    )
    coordinator = RunCoordinator.from_dependencies(dependencies)
    lease: WorkerLease | None = None
    agent_session_id = f"sess_{run_id}"
    agent_session_registered = False

    try:
        # --- Lifecycle setup: start dispatch + acquire lease ---
        # Fresh queued runs transition here for direct in-process callers. A
        # Prefect dispatcher may already own the transition, but that does not
        # confer worker ownership; only this worker's successful lease does.
        record = lifecycle_repository.get_run(run_id)
        if record is None:
            raise RuntimeError(f"Run {run_id} 在 lifecycle repository 中不存在")
        if record.status is RunStatus.QUEUED:
            if not coordinator.start_dispatch(run_id, now=now):
                raise RuntimeError("Run queued dispatch CAS 冲突")
        elif record.status is not RunStatus.RUNNING:
            raise RuntimeError(f"Run {run_id} 无法启动，当前状态: {record.status.value}")
        lease = coordinator.acquire_worker_lease(
            run_id, now=now, ttl=timedelta(seconds=RUN_HEARTBEAT_STALE_SECONDS),
        )
        if lease is None:
            raise RuntimeError(f"Run {run_id} 无法获取 worker lease")

        from app.services.evidence.reconstruction_batch import (
            bind_reconstruction_worker_revision,
        )
        assert lease is not None
        db.commit()

        if bind_reconstruction_worker_revision(
            db,
            run_id,
            worker_lease=lease,
        ) is not None:
            db.commit()

        # --- Execute stage loop ---
        data = StageData()

        # Register Agent session for review stage.
        skills = sync_skills(db)
        all_skill_objs = [s for s in skills if s.enabled]
        snapshot_ref: dict[str, Any] = {}
        AgentSessionRegistry.register(
            session_id=agent_session_id,
            session_factory=lambda: SessionLocal(),
            skills=all_skill_objs,
            snapshot=snapshot_ref,
        )
        agent_session_registered = True

        stage_timings_ms: dict[str, float] = {}
        stage_audit: dict[str, dict[str, Any]] = {}
        while True:
            stage_started = time.perf_counter()
            step_result = await coordinator.execute_next_stage_async(
                lease,
                now=datetime.now(timezone.utc).replace(tzinfo=None),
                data=data,
            )
            if step_result is None:
                break
            output: StageOutput
            output, context = step_result
            lease = context.worker_lease
            data = output.data
            stage_timings_ms[output.result.phase.value] = round(
                (time.perf_counter() - stage_started) * 1000, 2,
            )
            stage_input_count, stage_output_count = _stage_cardinality(
                output.result.phase, data,
            )
            stage_audit[output.result.phase.value] = {
                "duration_ms": stage_timings_ms[output.result.phase.value],
                "input_count": stage_input_count,
                "output_count": stage_output_count,
                "side_effect_key": output.result.side_effect_key,
            }
            data = data.with_updates(
                stage_timings_ms=dict(stage_timings_ms),
                stage_audit=dict(stage_audit),
            )

            # Update the legacy snapshot reference so Agent session can access it.
            if data.snapshot is not None:
                snapshot_ref.clear()
                snapshot_ref.update(data.snapshot)

            lease = _commit_stage_progress(
                db,
                coordinator=coordinator,
                worker_lease=lease,
                phase=output.result.phase.value,
            )
            logger.info(
                "run %s stage %s completed (side_effect_key=%s)",
                run_id,
                output.result.phase.value,
                output.result.side_effect_key,
            )

        # --- Complete ---
        completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        assert lease is not None
        db.flush()
        if not coordinator.complete(lease, now=completed_at):
            raise RuntimeError("Run lifecycle completion CAS failed")
        db.expire_all()

        run = db.get(RecommendationRun, run_id)
        if run is not None:
            run.recommended_count = len(data.saved_recommendations)
            run.universe_count = len(data.research_contexts)
            run.progress_phase = "completed"
            run.checkpoint_phase = "completed"
            run.progress_current = len(data.saved_recommendations)
            run.progress_total = len(data.saved_recommendations)
            run.progress_message = "运行完成"
            snapshot_dict = dict(data.snapshot or {})
            run.data_as_of = str(snapshot_dict.get("trade_date") or "") or None
            run.source_snapshot_id = str(snapshot_dict.get("snapshot_id") or "") or None
            run.market_snapshot_json = _dumps(_slim_market_snapshot(snapshot_dict))
            run.filter_audit_json = _dumps(dict(data.filter_audit or {}))
            summary = _build_run_summary(
                list(data.scored_cards), list(data.saved_recommendations),
                snapshot_dict, _strip_audit_rows(dict(data.pipeline_audit or {})),
            )
            summary["agent_runtime"] = (
                "rule_only"
                if bool((data.params or {}).get("disable_agent_review"))
                else summary.get("agent_runtime")
            )
            # Carry forward the identity facts PREPARE persisted.  Clobbering the
            # whole sub-document would drop the stored execution_version and the
            # enqueue-side application identity recorded when a commit landed
            # between enqueue and execution (L-140), which are exactly the values a
            # later investigation needs side by side.
            execution = dict(data.execution_audit or {})
            prepared = _loads(run.result_summary_json, {}).get("execution")
            if isinstance(prepared, dict) and prepared.get("enqueue_application") is not None:
                execution["enqueue_application"] = prepared["enqueue_application"]
            if run.execution_version:
                execution["execution_version"] = run.execution_version
            summary["execution"] = execution
            summary["stage_timings_ms"] = dict(data.stage_timings_ms or {})
            summary["stages"] = dict(data.stage_audit or {})
            summary["degraded_watch_count"] = len(data.degraded_watchlist)
            summary["degraded_watchlist"] = [
                {
                    "code": card.get("code"),
                    "name": card.get("name"),
                    "industry": card.get("industry"),
                    "final_score": card.get("final_score"),
                    "review_status": "degraded",
                    "note": "Skill Agent 全部失败，仅作降级观察，不进入正式收益统计",
                }
                for card in data.degraded_watchlist
            ]
            if data.climax_watchlist:
                summary["climax_watchlist"] = [
                    {
                        "code": card.get("code"),
                        "name": card.get("name"),
                        "industry": card.get("industry"),
                        "final_score": card.get("final_score"),
                        "effective_score_line": (card.get("score_line_meta") or {}).get("effective_score_line"),
                        "watchlist_gap": card.get("watchlist_gap"),
                        "hit_strategies": card.get("watchlist_hit_strategies"),
                        "note": card.get("watchlist_note"),
                    }
                    for card in data.climax_watchlist
                ]
            status_counts = Counter(
                str(card.get("review_status") or "") for card in data.scored_cards
            )
            summary["review_status_counts"] = {
                status: int(status_counts.get(status, 0))
                for status in ("verified", "partial", "degraded", "rule_only")
            }
            run.result_summary_json = _dumps(summary)
            _finalize_completed_run_provenance(db, run_id)
            db.commit()

        invalidate_bootstrap_cache()

        run = db.get(RecommendationRun, run_id)
        return {
            "run": _serialize_run_summary(db, run),
            "recommendations": [
                _serialize_recommendation(db, recommendation)
                for recommendation in data.saved_recommendations
            ],
        }

    except Exception as exc:
        _mark_pipeline_run_failed(
            db,
            run_id=run_id,
            coordinator=coordinator,
            worker_lease=lease,
            message=str(exc),
        )
        raise
    finally:
        if agent_session_registered:
            AgentSessionRegistry.unregister(agent_session_id)


__all__ = ["run_staged_pipeline"]
