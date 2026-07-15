"""Run the full recommendation pipeline through the staged RunCoordinator.

This module provides the new execution path that drives all 8 stages through
``RunCoordinator.execute_next_stage_async``, replacing the monolithic
``run_workbench`` function body.  The old function remains available as a
fallback until the coordinator path is proven in production.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from .contracts import RunPhase, RunRequest, RunStatus
from .coordinator import RunCoordinator, RunCoordinatorDependencies, RunResourceLeases, StageData
from .sqlalchemy_repository import SQLAlchemyRunRepository
from .stages.assembly import assemble_pipeline_stages
from .stages.contracts import StageOutput

logger = logging.getLogger(__name__)

RUN_HEARTBEAT_STALE_SECONDS = 1200


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
    from app.models.tables import RecommendationRun
    from app.services.runs.request_persistence import load_canonical_run_request
    from app.services.workbench_service import (
        _update_run_progress,
        _build_run_summary,
        _dumps,
        invalidate_bootstrap_cache,
    )
    from app.agents.session_registry import AgentSessionRegistry
    from app.db.session import SessionLocal
    from app.services.registry.skills import sync_skills

    lifecycle_repository = SQLAlchemyRunRepository(db)
    is_resume = bool(existing_run_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # --- Assemble coordinator with all stages ---
    stages = assemble_pipeline_stages(db, run_id=run_id)
    dependencies = RunCoordinatorDependencies(
        repository=lifecycle_repository,
        stages=stages,
    )
    coordinator = RunCoordinator.from_dependencies(dependencies)

    # --- Lifecycle setup: start dispatch + acquire lease ---
    if is_resume:
        # Resume path: the run is INTERRUPTED with a valid checkpoint.
        existing_run = db.get(RecommendationRun, run_id)
        if existing_run is None:
            raise RuntimeError(f"Run {run_id} 不存在")
        if not coordinator.resume(run_id, now=now):
            raise RuntimeError("Run interrupted resume 缺少有效 checkpoint、存在活跃 lease 或 CAS 冲突")
        lease = coordinator.acquire_worker_lease(
            run_id, now=now, ttl=timedelta(seconds=RUN_HEARTBEAT_STALE_SECONDS),
        )
        if lifecycle_repository.get_run(run_id) is not None and lease is None:
            raise RuntimeError("该 Run 已被另一个 worker 接管或缺少可用生命周期 lease")
    else:
        # Fresh run: transition from QUEUED to RUNNING and acquire lease.
        if not coordinator.start_dispatch(run_id, now=now):
            record = lifecycle_repository.get_run(run_id)
            if record is None:
                raise RuntimeError(f"Run {run_id} 在 lifecycle repository 中不存在")
            if record.status is not RunStatus.RUNNING:
                raise RuntimeError(f"Run {run_id} 无法启动，当前状态: {record.status.value}")
        lease = coordinator.acquire_worker_lease(
            run_id, now=now, ttl=timedelta(seconds=RUN_HEARTBEAT_STALE_SECONDS),
        )
        if lease is None:
            raise RuntimeError(f"Run {run_id} 无法获取 worker lease")

    # --- Execute stage loop ---
    agent_session_id = f"sess_{run_id}"
    data = StageData()

    try:
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

        while True:
            step_result = await coordinator.execute_next_stage_async(
                run_id, now=datetime.now(timezone.utc).replace(tzinfo=None), data=data,
            )
            if step_result is None:
                break
            output: StageOutput
            output, _context = step_result
            data = output.data

            # Update the legacy snapshot reference so Agent session can access it.
            if data.snapshot is not None:
                snapshot_ref.clear()
                snapshot_ref.update(data.snapshot)

            _update_run_progress(
                db,
                run_id,
                phase=output.result.phase.value,
                current=1,
                total=1,
                message=f"阶段 {output.result.phase.value} 完成",
                status="running",
            )
            logger.info(
                "run %s stage %s completed (side_effect_key=%s)",
                run_id,
                output.result.phase.value,
                output.result.side_effect_key,
            )

        # --- Complete ---
        completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        managed_record = lifecycle_repository.get_run(run_id)
        if managed_record is not None:
            if managed_record.lease is None:
                raise RuntimeError("Run lifecycle completion requires an active worker lease")
            db.flush()
            if not coordinator.complete(run_id, now=completed_at):
                raise RuntimeError("Run lifecycle completion CAS failed")
            db.expire_all()

        run = db.get(RecommendationRun, run_id)
        if run is not None:
            run.recommended_count = len(data.saved_recommendations)
            run.progress_phase = "completed"
            run.progress_current = len(data.saved_recommendations)
            run.progress_total = len(data.saved_recommendations)
            run.progress_message = "运行完成"
            snapshot_dict = dict(data.snapshot or {})
            summary = _build_run_summary(
                list(data.scored_cards), list(data.saved_recommendations),
                snapshot_dict, None,
            )
            summary["execution"] = dict(data.execution_audit or {})
            run.result_summary_json = _dumps(summary)
            db.commit()

        invalidate_bootstrap_cache()

        return {
            "run_id": run_id,
            "status": "completed",
            "recommended_count": len(data.saved_recommendations),
        }

    except Exception as exc:
        failed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        managed_record = lifecycle_repository.get_run(run_id)
        if managed_record is not None:
            if not coordinator.fail(run_id, now=failed_at, message=str(exc)):
                db.rollback()
                logger.warning("run failure CAS lost for %s", run_id)
                raise
            db.expire_all()
            run = db.get(RecommendationRun, run_id)
            if run is not None:
                run.progress_phase = "failed"
                run.progress_message = str(exc)
        else:
            run = db.get(RecommendationRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error_message = str(exc)
                run.finished_at = failed_at
                run.heartbeat_at = failed_at
                run.progress_phase = "failed"
                run.progress_message = str(exc)
        db.commit()
        invalidate_bootstrap_cache()
        raise
    finally:
        AgentSessionRegistry.unregister(agent_session_id)


__all__ = ["run_staged_pipeline"]
