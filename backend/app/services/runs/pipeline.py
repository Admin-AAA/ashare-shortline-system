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

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .contracts import RunActor, RunPhase, RunRequest, RunStatus, RunTrigger
from .coordinator import RunCoordinator, RunCoordinatorDependencies, StageData
from .repository import TransitionCommit
from .sqlalchemy_repository import SQLAlchemyRunRepository
from .state_machine import RunStateMachine
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


def _commit_stage_progress(db: Session, run_id: str, phase: str) -> None:
    """Commit lifecycle and legacy checkpoint projections before the next stage.

    The old ordering committed progress, then flushed ``checkpoint_phase`` in a new
    transaction. During the following LLM stage that uncommitted UPDATE held the
    ``recommendation_runs`` row lock, so the watchdog blocked and eventually raised
    MySQL 1205. Put the legacy projection into the same progress commit instead.
    """
    from app.models.tables import RecommendationRun
    from app.services.workbench_service import _update_run_progress

    progress_run = db.get(RecommendationRun, run_id)
    if progress_run is not None:
        progress_run.checkpoint_phase = phase
    _update_run_progress(
        db,
        run_id,
        phase=phase,
        current=1,
        total=1,
        message=f"阶段 {phase} 完成",
        status="running",
    )


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
    from app.models.tables import AgentDecisionLog, Recommendation, RecommendationRun
    from app.services.workbench_service import (
        _build_run_summary,
        _dumps,
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
    persistence_already_committed = (
        persisted.status is RunStatus.INTERRUPTED
        and persisted.last_committed_phase is RunPhase.PERSISTENCE
    )
    if persisted.status is RunStatus.INTERRUPTED and not persistence_already_committed:
        replay = RunStateMachine.transition(
            current_status=RunStatus.INTERRUPTED,
            trigger=RunTrigger.REPLAY,
            actor=RunActor.WORKER,
        )
        replayed = lifecycle_repository.compare_and_set_transition(
            run_id,
            TransitionCommit(
                expected_revision=persisted.revision,
                transition=replay,
                now=now,
            ),
        )
        if not replayed.applied:
            db.rollback()
            raise RuntimeError("中断 Run 完整重放 CAS 冲突")
        # The immutable canonical request is retained. Only run-owned, pre-commit
        # recommendation effects are cleared; provider/API audit is intentionally
        # preserved so cost history remains truthful.
        db.execute(delete(AgentDecisionLog).where(AgentDecisionLog.run_id == run_id))
        db.execute(delete(Recommendation).where(Recommendation.run_id == run_id))
        replay_row = db.get(RecommendationRun, run_id)
        if replay_row is not None:
            replay_row.checkpoint_phase = None
            replay_row.recommended_count = 0
        db.commit()
        persisted = lifecycle_repository.get_run(run_id)
        if persisted is None:
            raise RuntimeError(f"Run {run_id} 重放准备后丢失")
    is_resume = persistence_already_committed

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
        # Fresh queued run: transition to RUNNING and acquire one worker lease.
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

    if persistence_already_committed:
        completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if not coordinator.complete(run_id, now=completed_at):
            raise RuntimeError("持久化后恢复的 completion CAS 失败")
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
        run.progress_phase = "completed"
        run.progress_current = len(saved)
        run.progress_total = len(saved)
        run.progress_message = "已核对持久化结果并完成恢复"
        db.commit()
        invalidate_bootstrap_cache()
        return {
            "run": _serialize_run_summary(db, run),
            "recommendations": [
                _serialize_recommendation(db, recommendation)
                for recommendation in saved
            ],
        }

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

        stage_timings_ms: dict[str, float] = {}
        stage_audit: dict[str, dict[str, Any]] = {}
        while True:
            stage_started = time.perf_counter()
            step_result = await coordinator.execute_next_stage_async(
                run_id, now=datetime.now(timezone.utc).replace(tzinfo=None), data=data,
            )
            if step_result is None:
                break
            output: StageOutput
            output, _context = step_result
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

            _commit_stage_progress(db, run_id, output.result.phase.value)
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
            run.universe_count = len(data.research_contexts)
            run.progress_phase = "completed"
            run.checkpoint_phase = "completed"
            run.progress_current = len(data.saved_recommendations)
            run.progress_total = len(data.saved_recommendations)
            run.progress_message = "运行完成"
            snapshot_dict = dict(data.snapshot or {})
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
            summary["execution"] = dict(data.execution_audit or {})
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
