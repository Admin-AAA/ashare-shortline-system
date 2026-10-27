"""Prefect flows are the only process boundary for background business work."""
from __future__ import annotations

import asyncio

from prefect import flow, task

from app.governance.scope_guard import assert_no_brokerage_access
from app.services.observability import correlation_scope, log_observation

import logging

logger = logging.getLogger(__name__)


def _ensure_app_logging() -> None:
    """Prefect flow 跑在 worker 子进程里，不经过 app.main 的 setup_logging：在 flow 入口补上，
    让业务 INFO 日志进 backend/logs/app.log（幂等）。失败不影响 flow。"""
    try:
        from app.core.logger import setup_logging

        setup_logging()
    except Exception:  # noqa: BLE001
        logger.exception("setup_logging in flow failed")


@task(retries=2, retry_delay_seconds=[300, 900])
def execute_scheduled_task(
    task_id: str,
    trigger_source: str,
    correlation_id: str | None = None,
    scheduled_at: str | None = None,
) -> None:
    from datetime import datetime

    from app.services.scheduler_service import _execute_task

    with correlation_scope(correlation_id):
        log_observation(logger, stage="prefect", reason_code="TASK_STARTED", identity=task_id)
        _execute_task(
            task_id,
            force=trigger_source == "manual",
            trigger_source=trigger_source,
            scheduled_at=datetime.fromisoformat(scheduled_at) if scheduled_at else None,
        )


def _scheduled_start_iso() -> str:
    """当前 flow run 的计划触发时刻（带时区 ISO）；机器休眠后补跑时它早于实际开始时刻。"""
    from prefect.runtime import flow_run as runtime_flow_run

    return runtime_flow_run.scheduled_start_time.isoformat()


@flow(name="scheduled-task", timeout_seconds=14400)  # 单策略独立运行：15 个策略分批串行
def scheduled_task_flow(
    task_id: str,
    trigger_source: str = "schedule",
    correlation_id: str | None = None,
) -> None:
    _ensure_app_logging()
    execute_scheduled_task(task_id, trigger_source, correlation_id, _scheduled_start_iso())


@task(retries=1, retry_delay_seconds=30)
def execute_recommendation_run(
    run_id: str,
    correlation_id: str | None = None,
) -> None:
    from app.db.session import SessionLocal
    from app.services.runs.pipeline import run_staged_pipeline
    from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository
    from app.services.workbench_service import _mark_run_dispatched, _mark_run_failed

    with correlation_scope(correlation_id):
        log_observation(logger, stage="prefect", reason_code="TASK_STARTED", identity=run_id)
        log_observation(logger, stage="run", reason_code="RUN_DISPATCHED", identity=run_id)
        db = SessionLocal()
        log_observation(
            logger,
            stage="database",
            reason_code="SESSION_OPENED",
            identity="recommendation_runs",
            level=logging.DEBUG,
        )
        try:
            # Orphan guard: a flow run whose run_id has no recommendation_runs row (historically
            # test fixtures leaking run_queued_* dispatches into the production Prefect, fixed in
            # 0597d25) must not retry/fail noisily or touch progress state. Skip with a warning.
            if SQLAlchemyRunRepository(db).get_run(run_id) is None:
                logger.warning("recommendation-run skipped: run %s not found in recommendation_runs (orphan dispatch)", run_id)
                db.rollback()
                return
            if not _mark_run_dispatched(db, run_id):
                db.rollback()
                return
            db.commit()
            record = SQLAlchemyRunRepository(db).get_run(run_id)
            if record is None:
                raise RuntimeError(f"Run {run_id} has no canonical request")
            from app.services.datasource.cache import formal_chain

            with formal_chain():  # 正式出票链路拒绝旧缓存数据
                asyncio.run(run_staged_pipeline(db, run_id=run_id, request=record.request))
        except Exception as exc:
            db.rollback()
            _mark_run_failed(db, run_id, exc)
            raise
        finally:
            db.close()


@flow(name="recommendation-run", timeout_seconds=5400)
def recommendation_flow(
    run_id: str,
    correlation_id: str | None = None,
) -> None:
    _ensure_app_logging()
    execute_recommendation_run(run_id, correlation_id)


@flow(name="manual-preset-matrix", timeout_seconds=10800)
def manual_preset_matrix_flow(task_run_id: str, payload: dict) -> None:
    _ensure_app_logging()
    assert_no_brokerage_access(payload, context="flow:manual-preset-matrix")  # Scope_Guard 0.10
    from app.services.scheduler_service import _execute_manual_preset_matrix

    _execute_manual_preset_matrix(task_run_id, payload)


@flow(name="manual-data-health-remediation", timeout_seconds=7200)
def manual_data_health_remediation_flow(task_run_id: str, payload: dict) -> None:
    _ensure_app_logging()
    assert_no_brokerage_access(payload, context="flow:manual-data-health-remediation")  # Scope_Guard 0.10
    from app.services.scheduler_service import _execute_manual_data_health_remediation

    _execute_manual_data_health_remediation(task_run_id, payload)


@task(retries=0)
def poll_intraday_lock(scheduled_at: str) -> dict:
    """Prefect-scheduled intraday lock polling (replaces in-process interval job)."""
    from datetime import datetime, timezone

    from app.services.intraday_lock_service import poll_once
    from app.services.schedule_gate import schedule_skip_reason
    from app.services.tushare_client import get_pro, is_open_trade_date
    from app.db.session import SessionLocal
    import logging

    logger = logging.getLogger(__name__)
    skip_reason = schedule_skip_reason(
        datetime.fromisoformat(scheduled_at), datetime.now(timezone.utc), is_open_trade_date
    )
    if skip_reason is not None:
        logger.info("盘中锁利轮询%s", skip_reason)
        return {"skipped": True, "reason": skip_reason}
    try:
        db = SessionLocal()
        try:
            stats = poll_once(db, get_pro())
            if stats.get("triggered") or stats.get("errors"):
                logger.info("盘中锁利轮询: %s", stats)
            return stats
        finally:
            db.close()
    except Exception:
        logger.exception("盘中锁利轮询失败")
        return {"error": True}


@flow(name="intraday-lock-poll", timeout_seconds=900)
def intraday_lock_poll_flow() -> None:
    _ensure_app_logging()
    poll_intraday_lock(_scheduled_start_iso())


@task(retries=0)
def poll_execution_confirmation(scheduled_at: str) -> dict:
    """交易时段服务端轮询盘中买入确认。

    此前确认只由前端持仓/复盘页按钮触发：没人打开页面的交易日一次都不确认，
    推荐到盘后统一被判过期 missed（2026-10-14 批次全军覆没）。
    """
    from datetime import datetime, timedelta, timezone

    from app.db.session import SessionLocal
    from app.services.batch_execution_confirmation import (
        MAX_BATCH_IDS,
        confirm_batch_executions,
        list_unsettled_confirmation_ids,
    )
    from app.services.schedule_gate import schedule_skip_reason
    from app.services.tushare_client import is_open_trade_date

    skip_reason = schedule_skip_reason(
        datetime.fromisoformat(scheduled_at), datetime.now(timezone.utc), is_open_trade_date
    )
    if skip_reason is not None:
        logger.info("盘中买入确认轮询%s", skip_reason)
        return {"skipped": True, "reason": skip_reason}
    try:
        db = SessionLocal()
        try:
            ids = list_unsettled_confirmation_ids(db)
            if not ids:
                # 没有未结算（待确认）的正式推荐：不进入确认批次，不拉全市场行情/逐股K线，省上游额度（2026-10-26 额度耗尽根因）
                logger.info("盘中买入确认轮询：没有未结算推荐，跳过")
                # 没有待确认推荐也要清理跨日告警（漏查日 / 逐只过期 / 第 1 日缺 K），否则旧告警永远不消
                from app.services.execution_confirmation_coverage import sweep_coverage_alerts

                try:
                    sweep_coverage_alerts(db, datetime.now(timezone.utc).astimezone(
                        timezone(timedelta(hours=8))).date().isoformat())
                except Exception:  # noqa: BLE001
                    logger.exception("买入确认告警清理失败")
                return {"skipped": True, "reason": "no_unsettled_recommendations", "pending": 0}
            summaries = []
            for start in range(0, len(ids), MAX_BATCH_IDS):
                result = confirm_batch_executions(db, ids[start:start + MAX_BATCH_IDS], refresh=True)
                summaries.append(result.get("summary") or {})
                if result.get("error"):
                    logger.warning("盘中买入确认轮询批次失败: %s", result["error"])
            # 覆盖率：入场日 09:45 后仍没被检查到的待确认推荐 → 告警（全覆盖则解除）
            from app.services.execution_confirmation_coverage import report_coverage

            coverage = report_coverage(db)
            return {"pending": len(ids), "batches": summaries,
                    "unchecked": len(coverage.get("unchecked") or [])}
        finally:
            db.close()
    except Exception:
        logger.exception("盘中买入确认轮询失败")
        return {"error": True}


@flow(name="execution-confirmation-poll", timeout_seconds=900)
def execution_confirmation_poll_flow() -> None:
    _ensure_app_logging()
    poll_execution_confirmation(_scheduled_start_iso())


@task(retries=0)
def recover_stale_runs() -> int:
    """Prefect-scheduled stale run recovery (replaces in-process interval job)."""
    from app.services.workbench_service import recover_interrupted_runs
    from app.db.session import SessionLocal
    import logging

    logger = logging.getLogger(__name__)
    try:
        db = SessionLocal()
        try:
            recovered = recover_interrupted_runs(db)
            if recovered:
                logger.warning("周期巡检回收 %d 个失联运行", recovered)
            return recovered
        finally:
            db.close()
    except Exception:
        logger.exception("周期巡检回收失联运行失败")
        return 0


@flow(name="stale-run-recovery", timeout_seconds=900)
def stale_run_recovery_flow() -> None:
    _ensure_app_logging()
    recover_stale_runs()
