"""ScheduledTask 业务执行函数；进程编排由 Prefect 独占。

本模块不创建 scheduler、worker 或后台调度线程。
支持任务类型:
  - data_sync: 同步行情数据（刷新报价池 + 按日批量日K + 健康补数 + 辅助数据）
  - recommendation_run: 执行推荐流水线
  - preset_matrix_run: 盘后批量执行全部启用预设，用于预设矩阵对比
  - midterm_block_trade_backup: 08:03 盘前大宗交易溢价备份出票（主触发为 21:00 晚间补数钩子）
"""

from __future__ import annotations

import asyncio
import json
import os
import datetime as _dt_mod
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.tables import (
    Preset,
    RecommendationRun,
    ScheduledTask,
    ScheduledTaskRun,
)
from app.services.cost_service import finish_task_run, serialize_scheduled_task_run, start_task_run
from app.engine.trading_calendar import latest_allowed_bar_date as _latest_allowed_bar_date
from app.engine.trading_calendar import previous_weekday
from app.services.registry.data_source import (
    DATA_SOURCE_LOCAL_POOL,
    DEFAULT_RECOMMENDATION_DATA_SOURCE,
)
from app.services.scheduler_pure import (  # noqa: F401,E402  re-export (split)
    BLOCK_TRADE_BACKUP_CRON,
    DATA_FRESHNESS_CRON,
    DATA_QUALITY_CHECKS,
    DATA_FRESHNESS_TASK_ID,
    STOCK_IDENTITY_CRON,
    STOCK_IDENTITY_TASK_ID,
    STRATEGY_PROFILE_CRON,
    STRATEGY_PROFILE_TASK_ID,
    TASK_TYPE_STRATEGY_PROFILE_REFRESH,
    DATA_HEALTH_SNAPSHOT_CRON,
    DATA_HEALTH_SNAPSHOT_TASK_ID,
    TASK_TYPE_DATA_HEALTH_SNAPSHOT,
    ROLLING_R12_CRON,
    ROLLING_R12_TASK_ID,
    TASK_TYPE_ROLLING_R12,
    TASK_TYPE_DATA_FRESHNESS_CHECK,
    TASK_TYPE_STOCK_IDENTITY_REFRESH,
    BLOCK_TRADE_BACKUP_TASK_ID,
    TASK_TYPE_BLOCK_TRADE_BACKUP,
    EXPOSURE_TASK_ID,
    LEGACY_POSTCLOSE_TASK_IDS,
    LOCK_CREATE_CRON,
    LOCK_CREATE_TASK_ID,
    LOCK_POLL_INTERVAL_SECONDS,
    LOCK_SETTLE_CRON,
    LOCK_SETTLE_TASK_ID,
    MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID,
    MANUAL_PRESET_MATRIX_TASK_ID,
    POSTCLOSE_LATE_DATA_REPAIR_CRON,
    POSTCLOSE_LATE_DATA_REPAIR_TASK_ID,
    POSTCLOSE_PIPELINE_CRON,
    POSTCLOSE_PIPELINE_TASK_ID,
    RetryablePostcloseReadinessError,
    SAFETY_NET_CRON,
    SAFETY_NET_MAX_ATTEMPTS,
    SAFETY_NET_TASK_ID,
    STANDALONE_BATCH_SIZE,
    TASK_TYPE_FORMAL_SAFETY_NET,
    TASK_TYPE_INTRADAY_LOCK_SHADOW,
    TASK_TYPE_OUTCOME_LABEL,
    TASK_TYPE_PRESET_MATRIX_RUN,
    TASK_TYPE_STRATEGY_EXPOSURE_RUN,
    _AUXILIARY_TRADE_DATE_BATCH_DATASETS,
    _KNOWN_AUX_SOURCE_GAPS,
    _NO_RUNNABLE_SKILL_MSG,
    _RECOVERABLE_POSTCLOSE_AUX_TABLES,
    _SLOW_KNOWN_GAPS,
    _action_summary_count,
    _batch_sync_item_status,
    _classify_postclose_blockers,
    _data_health_remediation_summary,
    _default_data_health_remediation_payload,
    _default_matrix_payload,
    _default_sync_scope,
    _is_task_running,
    _late_data_repair_payload,
    _mark_done_memory,
    _mark_running_memory,
    _parse_backfill_stdout,
    _parse_stats_line,
    _postclose_pipeline_payload,
    _recommendation_run_error_to_skip,
    _running_lock,
    _running_tasks,
    _safe_float,
    _skip_slow_known_gaps,
    _sync_step_summary,
    _task_result_error,
    _task_result_status,
    _to_float,
    _to_int,
    _try_mark_running_memory,
    _update_matrix_progress,
)

from app.core import metrics as _metrics  # noqa: E402

logger = logging.getLogger("scheduler")


# 曝光补跑默认阈值：近30天已兑现样本 < 此值的 enabled 策略才补跑。
# 取 20 与北极星 E 项样本置信度下限对齐——样本 <20 时胜率统计不可信（置信区间过宽），
# 补跑持续喂到此值才让每个策略胜率可被信任，进而能按胜率选策略。
DEFAULT_EXPOSURE_MIN_SAMPLE = 20


def _mark_running(task_id: str) -> None:
    with _running_lock:
        _running_tasks.add(task_id)
    _set_db_running(task_id, True)


def _mark_done(task_id: str) -> None:
    with _running_lock:
        _running_tasks.discard(task_id)
    _set_db_running(task_id, False)


def _set_db_running(task_id: str, running: bool) -> None:
    try:
        db = SessionLocal()
        task = db.get(ScheduledTask, task_id)
        if task:
            task.is_running = running
            db.commit()
        db.close()
    except Exception:
        logger.exception("更新 is_running 失败: %s", task_id)


BLOCK_TRADE_HOOK_TASK_IDS = frozenset({POSTCLOSE_LATE_DATA_REPAIR_TASK_ID, BLOCK_TRADE_BACKUP_TASK_ID})


def _run_block_trade_picks_hook(db, task_id: str) -> dict[str, Any]:
    """Call midterm_service.generate_block_trade_picks(db, T); never raises into the parent job.

    T = latest_allowed_bar_date(): at 21:00 this is the same trading day, at 08:03 the previous one.
    """
    trade_date = ""
    try:
        trade_date = _latest_allowed_bar_date()
        from app.services.midterm_service import generate_block_trade_picks

        out = generate_block_trade_picks(db, trade_date, apply_daily_cap=True)
        logger.warning("midterm block-trade picks (%s, T=%s): %s", task_id, trade_date, out)
        return {"trade_date": trade_date, "trigger": task_id, "result": out}
    except Exception as exc:  # noqa: BLE001 - must not break the parent job
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning("midterm block-trade picks failed (%s, T=%s): %s", task_id, trade_date, exc, exc_info=True)
        return {"trade_date": trade_date, "trigger": task_id, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _configure_late_data_repair_task(db) -> None:
    task = db.get(ScheduledTask, POSTCLOSE_LATE_DATA_REPAIR_TASK_ID)
    existing_payload: dict[str, Any] = {}
    if task is None:
        task = ScheduledTask(task_id=POSTCLOSE_LATE_DATA_REPAIR_TASK_ID)
        db.add(task)
    else:
        try:
            existing_payload = json.loads(task.payload_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            existing_payload = {}
    task.name = "晚间全域数据补数"
    task.task_type = "data_backfill"
    task.cron_expression = POSTCLOSE_LATE_DATA_REPAIR_CRON
    task.payload_json = json.dumps(_late_data_repair_payload(existing_payload), ensure_ascii=False)
    task.enabled = True


def _configure_postclose_pipeline_tasks(db) -> None:
    """幂等收敛旧盘后 cron，避免同步阶段与推荐再次并发。"""
    task = db.get(ScheduledTask, POSTCLOSE_PIPELINE_TASK_ID)
    existing_payload: dict[str, Any] = {}
    if task is None:
        task = ScheduledTask(task_id=POSTCLOSE_PIPELINE_TASK_ID)
        db.add(task)
    else:
        try:
            existing_payload = json.loads(task.payload_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            existing_payload = {}
    task.name = "盘后全量同步、健康检查与正式推荐"
    task.task_type = "recommendation_run"
    task.cron_expression = POSTCLOSE_PIPELINE_CRON
    task.payload_json = json.dumps(_postclose_pipeline_payload(existing_payload), ensure_ascii=False)
    task.enabled = True

    for task_id in LEGACY_POSTCLOSE_TASK_IDS:
        legacy = db.get(ScheduledTask, task_id)
        if legacy is not None:
            legacy.enabled = False
    _configure_late_data_repair_task(db)
    db.commit()


def _ensure_postclose_pipeline_task() -> None:
    try:
        db = SessionLocal()
        try:
            _configure_postclose_pipeline_tasks(db)
        finally:
            db.close()
    except Exception:
        logger.exception("注册盘后全量同步流水线失败")


def _ensure_block_trade_backup_task() -> None:
    """注册 08:03 盘前大宗交易备份出票任务，并禁用已退役的策略曝光补跑旧行（幂等）。

    曝光补跑（strategy_exposure_run）于 2026-10-19 退役：它在盘前对近30天样本<20 的策略
    额外跑 experiment 模式推荐，只为喂样本数；17:35 盘后流水线已为每个现役策略独立出 formal 票，
    正式胜率/KPI 只认 formal（outcome_settlement.OFFICIAL_RUN_MODE），且同一信号日重复跑会制造
    相关性很强的重复样本。原 08:03 任务上挂的大宗交易备份钩子迁到本独立任务。
    """
    try:
        db = SessionLocal()
        try:
            legacy = db.get(ScheduledTask, EXPOSURE_TASK_ID)
            if legacy is not None and legacy.enabled:
                legacy.enabled = False
                logger.info("已禁用退役的策略曝光补跑任务: %s", EXPOSURE_TASK_ID)
            if db.get(ScheduledTask, BLOCK_TRADE_BACKUP_TASK_ID) is None:
                db.add(
                    ScheduledTask(
                        task_id=BLOCK_TRADE_BACKUP_TASK_ID,
                        name="中线大宗交易溢价盘前备份出票",
                        task_type=TASK_TYPE_BLOCK_TRADE_BACKUP,
                        cron_expression=BLOCK_TRADE_BACKUP_CRON,
                        payload_json=json.dumps({}),
                        enabled=True,
                    )
                )
                logger.info("已注册大宗交易盘前备份任务: %s (cron=%s)", BLOCK_TRADE_BACKUP_TASK_ID, BLOCK_TRADE_BACKUP_CRON)
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("注册大宗交易盘前备份任务失败")


def _ensure_intraday_lock_tasks() -> None:
    """确保盘中锁利影子链路的两个表驱动任务存在（建监控 / 盘后回填）。

    盘中 poll 不走 ScheduledTask 表（cron 无法干净表达跨午休的交易时段轮询），
    由 Prefect intraday-lock-poll deployment 承担。幂等注册：已存在则不覆盖
    用户手动调整的 cron/payload。
    """
    try:
        db = SessionLocal()
        try:
            for task_id, name, cron, payload in (
                (
                    LOCK_CREATE_TASK_ID,
                    "盘中锁利建监控（盘前，首个可卖日持仓）",
                    LOCK_CREATE_CRON,
                    {"action": "create"},
                ),
                (
                    LOCK_SETTLE_TASK_ID,
                    "盘中锁利盘后回填（触发后走势+实际出场）",
                    LOCK_SETTLE_CRON,
                    {"action": "settle"},
                ),
            ):
                if db.get(ScheduledTask, task_id):
                    continue
                db.add(
                    ScheduledTask(
                        task_id=task_id,
                        name=name,
                        task_type=TASK_TYPE_INTRADAY_LOCK_SHADOW,
                        cron_expression=cron,
                        payload_json=json.dumps(payload),
                        enabled=True,
                    )
                )
                logger.info("已注册盘中锁利调度任务: %s (cron=%s)", task_id, cron)
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("注册盘中锁利调度任务失败")


def _ensure_formal_safety_net_task() -> None:
    """确保 formal 安全网任务存在（启动注册接线，2026-08-02 修复）。

    此前 safety_net 无 _ensure 注册函数，全新部署只能人工建行；本函数幂等
    注册：已存在则不覆盖用户手动调整的 cron/payload。
    """
    try:
        db = SessionLocal()
        try:
            if db.get(ScheduledTask, SAFETY_NET_TASK_ID):
                return
            db.add(
                ScheduledTask(
                    task_id=SAFETY_NET_TASK_ID,
                    name="正式推荐安全网（缺口补跑 + outcome 结算兜底）",
                    task_type=TASK_TYPE_FORMAL_SAFETY_NET,
                    cron_expression=SAFETY_NET_CRON,
                    payload_json=json.dumps({}),
                    enabled=True,
                )
            )
            db.commit()
            logger.info("已注册 formal 安全网调度任务: %s (cron=%s)", SAFETY_NET_TASK_ID, SAFETY_NET_CRON)
        finally:
            db.close()
    except Exception:
        logger.exception("注册 formal 安全网任务失败")


def _ensure_data_freshness_task() -> None:
    """注册 18:00 每日数据新鲜度只读巡检（幂等，不覆盖手动调整）。"""
    try:
        db = SessionLocal()
        try:
            if db.get(ScheduledTask, DATA_FRESHNESS_TASK_ID) is None:
                db.add(
                    ScheduledTask(
                        task_id=DATA_FRESHNESS_TASK_ID,
                        name="每日数据新鲜度巡检（滞后/行数异常/关键列失效，只读）",
                        task_type=TASK_TYPE_DATA_FRESHNESS_CHECK,
                        cron_expression=DATA_FRESHNESS_CRON,
                        payload_json=json.dumps({}),
                        enabled=True,
                    )
                )
                db.commit()
                logger.info("已注册数据新鲜度巡检任务: %s (cron=%s)", DATA_FRESHNESS_TASK_ID, DATA_FRESHNESS_CRON)
        finally:
            db.close()
    except Exception:
        logger.exception("注册数据新鲜度巡检任务失败")


def _ensure_data_quality_tasks() -> None:
    """注册盘前/晚间统一数据质量巡检（幂等，不覆盖手动调整；复用 data_freshness_check 任务类型，payload quality_only）。"""
    try:
        db = SessionLocal()
        try:
            for task_id, name, cron in DATA_QUALITY_CHECKS:
                if db.get(ScheduledTask, task_id) is None:
                    db.add(ScheduledTask(task_id=task_id, name=name, task_type=TASK_TYPE_DATA_FRESHNESS_CHECK,
                                         cron_expression=cron, payload_json=json.dumps({"quality_only": True}), enabled=True))
                    logger.info("已注册数据质量巡检任务: %s (cron=%s)", task_id, cron)
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("注册数据质量巡检任务失败")


def _ensure_simple_task(task_id: str, name: str, task_type: str, cron: str, payload: dict | None = None) -> None:
    """注册一个普通定时任务（幂等，已存在就不动，不覆盖手动调整）。"""
    try:
        db = SessionLocal()
        try:
            if db.get(ScheduledTask, task_id) is None:
                db.add(ScheduledTask(task_id=task_id, name=name, task_type=task_type, cron_expression=cron,
                                     payload_json=json.dumps(payload or {}), enabled=True))
                db.commit()
                logger.info("已注册调度任务: %s (cron=%s)", task_id, cron)
        finally:
            db.close()
    except Exception:
        logger.exception("注册调度任务失败: %s", task_id)


def _ensure_health_snapshot_task() -> None:
    _ensure_simple_task(DATA_HEALTH_SNAPSHOT_TASK_ID, "数据健康分每日快照（18:40：健康分、红黄项、盘后出票是否成功）",
                        TASK_TYPE_DATA_HEALTH_SNAPSHOT, DATA_HEALTH_SNAPSHOT_CRON)


def _ensure_rolling_r12_task() -> None:
    _ensure_simple_task(ROLLING_R12_TASK_ID, "现役策略近12个月指标每月滚动重算（每月第一个交易日 22:30）",
                        TASK_TYPE_ROLLING_R12, ROLLING_R12_CRON)


def _run_data_health_snapshot(db, payload: dict | None = None) -> dict:
    from app.services.data_health_snapshot import summarize, take_snapshot

    snap = take_snapshot(db)
    summ = summarize(20)
    text_ = (f"{snap['trade_date']} 健康分 {snap['score']}（红 {len(snap['red'])} / 黄 {len(snap['yellow'])}），"
             f"盘后出票{'成功' if snap['output_ok'] else '失败：' + snap['output']['reason']}；"
             f"近 {summ['days_with_snapshot']} 个交易日均值 {summ['avg_score']}，出票成功率 {summ['output_success_rate']}")
    return {"snapshot": {k: snap[k] for k in ("trade_date", "score", "score_db", "penalty", "output_ok")},
            "summary_20d": {k: summ[k] for k in ("days_with_snapshot", "avg_score", "output_success_rate", "red_days")},
            "status": "completed" if snap["output_ok"] and not snap["red"] else "partial_failed",
            "summary_text": text_}


def _run_rolling_r12(db, payload: dict | None = None) -> dict:
    from app.services.rolling_r12 import run_monthly

    return run_monthly(db, force=bool((payload or {}).get("force")))


def _ensure_stock_identity_task() -> None:
    """注册每日股票身份刷新任务（幂等，不覆盖手动调整）。"""
    try:
        db = SessionLocal()
        try:
            if db.get(ScheduledTask, STOCK_IDENTITY_TASK_ID) is None:
                db.add(
                    ScheduledTask(
                        task_id=STOCK_IDENTITY_TASK_ID,
                        name="股票身份每日刷新（stocks + 元数据区间：新股 / ST 状态 / 有效期）",
                        task_type=TASK_TYPE_STOCK_IDENTITY_REFRESH,
                        cron_expression=STOCK_IDENTITY_CRON,
                        payload_json=json.dumps({}),
                        enabled=True,
                    )
                )
                db.commit()
                logger.info("已注册股票身份刷新任务: %s (cron=%s)", STOCK_IDENTITY_TASK_ID, STOCK_IDENTITY_CRON)
        finally:
            db.close()
    except Exception:
        logger.exception("注册股票身份刷新任务失败")


def _ensure_strategy_profile_task() -> None:
    """注册每日策略回测画像补跑任务（幂等，不覆盖手动调整）。"""
    try:
        db = SessionLocal()
        try:
            if db.get(ScheduledTask, STRATEGY_PROFILE_TASK_ID) is None:
                db.add(
                    ScheduledTask(
                        task_id=STRATEGY_PROFILE_TASK_ID,
                        name="策略回测画像补跑（新策略版本无画像时，每次最多补 1 个）",
                        task_type=TASK_TYPE_STRATEGY_PROFILE_REFRESH,
                        cron_expression=STRATEGY_PROFILE_CRON,
                        payload_json=json.dumps({"max_runs": 1}),
                        enabled=True,
                    )
                )
                db.commit()
                logger.info("已注册策略画像补跑任务: %s (cron=%s)", STRATEGY_PROFILE_TASK_ID, STRATEGY_PROFILE_CRON)
        finally:
            db.close()
    except Exception:
        logger.exception("注册策略画像补跑任务失败")


def _run_strategy_profile_refresh(db, payload: dict | None = None) -> dict:
    """给「当前版本没有任何可用画像（精确或向后滚动）」的策略补跑 180 天 canonical 回测，每次最多 max_runs 个。

    已有画像的策略（含窗口随日滚动的）不重跑，所以稳态下这个任务几乎是空操作。
    """
    from app.services.backtest.strategy_profile import StrategyBacktestProfiles

    from app.engine.skill_loader import lenient_missing_manifest

    max_runs = max(0, int((payload or {}).get("max_runs", 1)))
    profiles = StrategyBacktestProfiles(db)
    # 缺 manifest.json 的策略目录（退役残留空壳/半成品，2026-10-26 main-line-leader-resonance 曾令整个任务 failed 并让
    # /api/presets 500）：本任务里跳过并告警，其余策略照常补画像；主链路加载器的严格契约不变。
    skipped_dirs: list[str] = []
    with lenient_missing_manifest(skipped_dirs):
        pending = [t for t in profiles.current_targets() if profiles.read_rolled(t) is None]
        results = _refresh_pending_profiles(db, profiles, pending, max_runs)
    skipped_dirs = sorted(set(skipped_dirs))
    try:
        from app.core.alerts import emit_alert, resolve_alert

        if skipped_dirs:
            names = ",".join(os.path.basename(p) for p in skipped_dirs)
            emit_alert("strategy_profile_missing_manifest", "skills", f"策略目录缺少 manifest.json，已跳过其画像补跑：{names}")
        else:
            resolve_alert("strategy_profile_missing_manifest", "skills", "所有策略目录均有 manifest.json")
    except Exception:  # noqa: BLE001 - 告警失败不影响任务
        logger.warning("缺 manifest 告警同步失败", exc_info=True)
    return _summarize_profile_refresh(pending, results, skipped_dirs)


def _refresh_pending_profiles(db, profiles, pending, max_runs) -> list[dict]:
    results = []
    for target in pending[:max_runs]:
        try:
            outcome = profiles.refresh(target)
            results.append({"strategy_id": target.strategy_id, "strategy_version": target.strategy_version,
                            "status": outcome.get("status"), "reused": bool(outcome.get("reused"))})
        except Exception as exc:  # noqa: BLE001 - 单个策略失败不影响其余，记入结果
            db.rollback()
            logger.warning("策略画像补跑失败 %s: %s", target.strategy_id, exc, exc_info=True)
            results.append({"strategy_id": target.strategy_id, "strategy_version": target.strategy_version,
                            "status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    return results


def _summarize_profile_refresh(pending, results, skipped_dirs) -> dict:
    failed = [r for r in results if r["status"] != "completed"]
    deferred = max(0, len(pending) - len(results))
    summary = f"策略画像补跑：待补 {len(pending)}，本次执行 {len(results)}，失败 {len(failed)}，顺延 {deferred}"
    if skipped_dirs:
        summary += f"，跳过缺 manifest 目录 {len(skipped_dirs)}"
    out = {
        "pending_before": len(pending), "executed": len(results), "deferred": deferred, "results": results,
        "skipped_missing_manifest": sorted(skipped_dirs),
        "summary_text": summary,
    }
    if failed:
        out["status"] = "partial_failed"
    return out


def _run_stock_identity_refresh(db) -> dict:
    """刷新 stocks / stock_metadata_history，并立即做三方对账；对账有问题走 emit_alert。"""
    from app.core.alerts import emit_alert
    from app.services.stock_identity import StockIdentityError, reconcile_stock_identity, refresh_stock_identity

    try:
        out = refresh_stock_identity(db)
    except StockIdentityError as exc:
        emit_alert("stock_identity_refresh_failed", datetime.now().strftime("%Y-%m-%d %H"), f"股票身份刷新放弃: {exc}")
        return {"status": "failed", "error": str(exc), "summary_text": f"股票身份刷新放弃: {exc}"}
    except Exception as exc:  # noqa: BLE001 - 取数/写库异常：告警并让任务记失败
        db.rollback()
        emit_alert("stock_identity_refresh_failed", datetime.now().strftime("%Y-%m-%d %H"), f"股票身份刷新失败: {exc}")
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "summary_text": f"股票身份刷新失败: {exc}"}
    rec = reconcile_stock_identity(db)
    out["reconcile"] = rec
    for warning in out.get("warnings") or []:
        emit_alert("stock_identity_refresh_warning", datetime.now().strftime("%Y-%m-%d %H"), warning)
    # key 不带日期，与 run_freshness_check 的「每表一条」一致；对账恢复正常自动解除
    if rec["issues"]:
        emit_alert("data_freshness", "stock_identity:reconcile", f"stock_identity:{rec['latest']}: " + "；".join(rec["issues"][:5]))
    else:
        from app.core.alerts import resolve_alert

        resolve_alert("data_freshness", "stock_identity:reconcile", f"stock_identity 对账恢复正常（{rec['latest']}）")
    stocks, intervals = out["stocks"], out["intervals"]
    out["summary_text"] = (
        f"股票身份刷新：stocks +{stocks['insert']}/~{stocks['update']}，区间 +{intervals['insert']}/~{intervals['update']}/-{intervals['delete']}；"
        f"对账 {'正常' if not rec['issues'] else '异常 ' + str(len(rec['issues'])) + ' 项'}"
    )
    if out.get("warnings"):
        out["status"] = "partial_completed"
    return out


_BAD_TASK_STATUSES = frozenset({"failed", "partial_failed", "diagnosis_failed", "post_diagnosis_failed"})


def _sync_task_status_alert(task_id: str, task_run_id: str | None, status: str, summary: str) -> None:
    """partial_failed / failed 作为一等告警：任务业务状态不好就 emit_alert，恢复（completed）后自动解除。

    skipped 不改变告警状态。告警写入失败不影响任务本身。
    """
    try:
        from app.core.alerts import emit_alert, resolve_alert

        if status in _BAD_TASK_STATUSES:
            emit_alert("task_status", task_id, f"调度任务 {task_id} 状态 {status}: {summary[:300]}（task_run_id={task_run_id}）")
        elif status in {"completed", "partial_completed"}:
            resolve_alert("task_status", task_id, f"调度任务 {task_id} 恢复为 {status}")
    except Exception:  # noqa: BLE001
        logger.warning("任务状态告警同步失败: %s", task_id, exc_info=True)


def _run_data_freshness_check(db, payload: dict | None = None) -> dict:
    from app.services.data_freshness import run_freshness_check

    quality_only = bool((payload or {}).get("quality_only"))
    out: dict = {"problems": {}} if quality_only else run_freshness_check(db)
    problems = out.get("problems") or {}
    if problems:
        out["status"] = "partial_failed"  # 巡检发现问题即业务状态 partial_failed（同时每张问题表已 emit_alert）
    out["summary_text"] = (
        ("数据新鲜度正常" if not problems
         else f"数据新鲜度异常 {len(problems)} 张表: " + "；".join(f"{t}: {v[0]}" for t, v in list(problems.items())[:8]))
        if not quality_only else ""
    )
    # 统一数据质量巡检（只读，覆盖文件数据集/research 文件/调度/日志）：失败只记日志，绝不影响任务和选股主流程
    try:
        from app.services.data_quality_monitor import run_data_quality_check

        dq = run_data_quality_check(db)
        s = dq.get("summary") or {}
        out["data_quality"] = {"overall": dq.get("overall"), "summary": s, "expected_trade_date": dq.get("expected_trade_date"),
                               "red": [i["key"] for i in dq.get("items", []) if i["severity"] == "red"],
                               "yellow": [i["key"] for i in dq.get("items", []) if i["severity"] == "yellow"]}
        out["summary_text"] = (out["summary_text"] + "；" if out["summary_text"] else "") + (
            f"数据质量巡检 红 {s.get('red', 0)} / 黄 {s.get('yellow', 0)} / 绿 {s.get('green', 0)}")
        if s.get("red"):
            out["status"] = "partial_failed"
    except Exception:  # noqa: BLE001
        logger.warning("数据质量巡检异常（已忽略，不影响主流程）", exc_info=True)
    return out


def ensure_scheduled_task_registrations() -> None:
    """启动接线入口：注册全部关键调度任务（幂等）。

    2026-08-02 系统诊断发现：_ensure_postclose_pipeline_task /
    _ensure_block_trade_backup_task / _ensure_intraday_lock_tasks /
    _configure_postclose_pipeline_tasks 在生产代码中零调用点，全新部署依赖
    人工建行；safety_net 连 _ensure 函数都没有。此处统一接线，由 FastAPI
    startup 调用，确保新部署自动获得完整调度。
    """
    _ensure_postclose_pipeline_task()
    _ensure_block_trade_backup_task()
    _ensure_intraday_lock_tasks()
    _ensure_formal_safety_net_task()
    _ensure_data_freshness_task()
    _ensure_data_quality_tasks()
    _ensure_stock_identity_task()
    _ensure_strategy_profile_task()
    _ensure_health_snapshot_task()
    _ensure_rolling_r12_task()


def _current_code_revision() -> str:
    """当前主工作树 git HEAD（不走进程缓存：worker 每个任务从磁盘加载代码，HEAD 可能在任务运行中移动）。"""
    try:
        from app.core.deployment_identity import _git_head_revision

        return _git_head_revision() or "unknown"
    except Exception:  # noqa: BLE001 - 观测字段，失败不影响任务
        return "unknown"


def _stamp_code_revision(result: dict, start_revision: str) -> None:
    """把任务开始/结束时的 code_revision 写进 result_json（scheduled_task_runs），暴露 worker 版本混用。

    worker 常驻，每个任务从磁盘主工作树加载代码；合并/切分支发生在任务中间时，同一次运行前后半段会跑不同版本。
    """
    if not isinstance(result, dict):
        return
    end_revision = _current_code_revision()
    result.setdefault("code_revision", start_revision)
    if end_revision != start_revision:
        result["code_revision_end"] = end_revision
        result["code_revision_changed_during_run"] = True
        logger.warning("调度任务运行期间 code_revision 变化: %s -> %s", start_revision[:12], end_revision[:12])


def _execute_task(
    task_id: str,
    *,
    force: bool = False,
    trigger_source: str = "schedule",
    scheduled_at: datetime | None = None,
) -> None:
    """执行单个调度任务（同步包装异步）。

    trigger_source=schedule 时必须带上 Prefect 计划触发时刻 scheduled_at，
    用它判定休市日和迟到超时（见 schedule_gate）。手动触发不受这两条限制。
    """
    if trigger_source == "schedule" and scheduled_at is None:
        raise ValueError(f"定时触发的调度任务 {task_id} 缺少计划触发时刻 scheduled_at")
    if _is_task_running(task_id):
        logger.warning("调度任务 %s 正在执行中，跳过重复触发", task_id)
        return

    start_revision = _current_code_revision()
    logger.info("调度任务开始: %s（code_revision=%s）", task_id, start_revision[:12])
    _mark_running(task_id)

    db = SessionLocal()
    task_run_id: str | None = None
    try:
        task = db.get(ScheduledTask, task_id)
        if not task or (not task.enabled and not force):
            logger.info("调度任务 %s 已禁用或不存在，跳过", task_id)
            return

        payload = json.loads(task.payload_json or "{}")
        task_type = task.task_type
        task_run_id = start_task_run(db, task, trigger_source=trigger_source).task_run_id

        if trigger_source == "schedule":
            from app.services.schedule_gate import schedule_skip_reason
            from app.services.tushare_client import is_open_trade_date

            skip_reason = schedule_skip_reason(
                scheduled_at, datetime.now(timezone.utc), is_open_trade_date
            )
            if skip_reason is not None:
                logger.info("调度任务 %s %s", task_id, skip_reason)
                finish_task_run(
                    db,
                    task_run_id,
                    status="skipped",
                    summary=skip_reason,
                    result={
                        "status": "skipped",
                        "skipped": True,
                        "reason": skip_reason,
                        "scheduled_at": scheduled_at.isoformat(),
                        "summary_text": skip_reason,
                    },
                )
                return

        # 正式链路（出票/中长线/大宗钩子/结算/日更）：数据网关一律拒绝旧缓存（stale）数据。
        from app.services.datasource.cache import formal_chain

        with formal_chain():
            if task_type == "recommendation_run":
                try:
                    result = asyncio.run(
                        _run_postclose_pipeline(db, payload)
                        if payload.get("pre_run_full_sync")
                        else _run_recommendation(db, payload)
                    )
                except ValueError as ve:
                    # 预设下 Skill 全部被禁用 → 优雅跳过而非报错。其它 ValueError 透传。
                    skip = _recommendation_run_error_to_skip(task_id, payload, ve)
                    if skip is not None:
                        result = skip
                    else:
                        raise
            elif task_type == TASK_TYPE_FORMAL_SAFETY_NET:
                result = asyncio.run(_safety_net_run(db, payload))
            elif task_type == TASK_TYPE_PRESET_MATRIX_RUN:
                result = asyncio.run(_run_preset_matrix(db, payload, task_run_id=task_run_id))
            elif task_type == TASK_TYPE_BLOCK_TRADE_BACKUP:
                # 实际出票由下方 BLOCK_TRADE_HOOK_TASK_IDS 钩子完成（T=上一交易日，幂等）。
                result = {"summary_text": "盘前大宗交易备份出票"}
            elif task_type == TASK_TYPE_STRATEGY_EXPOSURE_RUN:
                result = {"skipped": True, "status": "skipped", "summary_text": "策略曝光补跑已退役（2026-10-19）"}
            elif task_type == TASK_TYPE_OUTCOME_LABEL:
                result = _run_outcome_label(db, payload)
            elif task_type == TASK_TYPE_DATA_FRESHNESS_CHECK:
                result = _run_data_freshness_check(db, payload)
            elif task_type == TASK_TYPE_STOCK_IDENTITY_REFRESH:
                result = _run_stock_identity_refresh(db)
            elif task_type == TASK_TYPE_STRATEGY_PROFILE_REFRESH:
                result = _run_strategy_profile_refresh(db, payload)
            elif task_type == TASK_TYPE_DATA_HEALTH_SNAPSHOT:
                result = _run_data_health_snapshot(db, payload)
            elif task_type == TASK_TYPE_ROLLING_R12:
                result = _run_rolling_r12(db, payload)
            elif task_type == TASK_TYPE_INTRADAY_LOCK_SHADOW:
                result = _run_intraday_lock_shadow(db, payload)
            elif task_type in ("data_sync", "pool_refresh", "data_backfill"):
                result = _run_data_sync(db, payload, task_type=task_type)
            else:
                raise RuntimeError(f"未知任务类型: {task_type}")

            # [中线] mid-block-trade-premium：Tushare 大宗交易约 21:00 发布。
            # 主：21:00 晚间全域数据补数完成后跑当日；备：08:03 midterm_block_trade_premarket 补跑上一交易日（幂等）。
            if task_id in BLOCK_TRADE_HOOK_TASK_IDS and isinstance(result, dict):
                result["midterm_block_trade"] = _run_block_trade_picks_hook(db, task_id)

        _stamp_code_revision(result, start_revision)
        finish_task_run(
            db,
            task_run_id,
            status=_task_result_status(result),
            error=_task_result_error(result),
            summary=str(result.get("summary_text") or ""),
            result=result,
        )
        logger.info("调度任务完成: %s", task_id)
        _sync_task_status_alert(task_id, task_run_id, _task_result_status(result), str(result.get("summary_text") or ""))
    except RetryablePostcloseReadinessError as exc:
        # 只把结构化确认的当日辅助数据滞后传播给 Prefect。每次重试都会重新进入
        # _execute_task、创建新 Session；这里先完整落盘本次同步/刷新证据。
        logger.warning("盘后辅助数据暂未就绪，等待 Prefect 重试: %s", exc)
        db.rollback()
        try:
            if task_run_id:
                finish_task_run(
                    db,
                    task_run_id,
                    status="failed",
                    error=str(exc),
                    summary=str(exc),
                    result=exc.result,
                )
        except Exception:
            logger.exception("记录盘后可重试失败状态时出错: %s", task_id)
        raise
    except Exception as e:
        logger.exception("调度任务失败: %s", task_id)
        db.rollback()
        try:
            if task_run_id:
                finish_task_run(
                    db,
                    task_run_id,
                    status="failed",
                    error=str(e),
                    summary=str(e),
                    result={"summary_text": str(e)},
                )
        except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
            logger.warning("_execute_task: ignored non-fatal error: %s", exc)
    finally:
        db.close()
        _mark_done(task_id)


def start_preset_matrix_now(payload: dict | None = None) -> dict:
    """异步启动一次手工预设矩阵批次，立即返回任务运行状态。"""
    if not _try_mark_running_memory(MANUAL_PRESET_MATRIX_TASK_ID):
        running = get_latest_preset_matrix_task_run()
        if running and running.get("status") == "running":
            return {"started": False, "task_run": running}
        raise RuntimeError("预设矩阵正在执行中，请等待当前批次完成")

    run_payload = _default_matrix_payload(payload or {})
    db = SessionLocal()
    task_run_id: str | None = None
    try:
        existing_running = _latest_running_preset_matrix_task_run(db)
        if existing_running is not None:
            _mark_done_memory(MANUAL_PRESET_MATRIX_TASK_ID)
            return {"started": False, "task_run": serialize_scheduled_task_run(existing_running)}

        task = _manual_preset_matrix_task(run_payload)
        task_run = start_task_run(db, task, trigger_source="manual")
        task_run_id = task_run.task_run_id
    except Exception:
        _mark_done_memory(MANUAL_PRESET_MATRIX_TASK_ID)
        raise
    finally:
        db.close()

    from app.orchestration import trigger_manual_matrix
    trigger_manual_matrix(task_run_id, run_payload)
    latest = get_latest_preset_matrix_task_run()
    return {"started": True, "task_run": latest}


def get_latest_preset_matrix_task_run() -> dict | None:
    db = SessionLocal()
    try:
        run = db.scalars(
            select(ScheduledTaskRun)
            .where(ScheduledTaskRun.task_type == TASK_TYPE_PRESET_MATRIX_RUN)
            .order_by(ScheduledTaskRun.started_at.desc())
            .limit(1)
        ).first()
        return serialize_scheduled_task_run(run) if run is not None else None
    finally:
        db.close()


def _latest_running_preset_matrix_task_run(db) -> ScheduledTaskRun | None:
    return db.scalars(
        select(ScheduledTaskRun)
        .where(
            ScheduledTaskRun.task_type == TASK_TYPE_PRESET_MATRIX_RUN,
            ScheduledTaskRun.status == "running",
            ScheduledTaskRun.finished_at.is_(None),
        )
        .order_by(ScheduledTaskRun.started_at.desc())
        .limit(1)
    ).first()


def _execute_manual_preset_matrix(task_run_id: str, payload: dict) -> None:
    db = SessionLocal()
    try:
        result = asyncio.run(_run_preset_matrix(db, payload, task_run_id=task_run_id))
        finish_task_run(
            db,
            task_run_id,
            status=_task_result_status(result),
            summary=str(result.get("summary_text") or ""),
            result=result,
        )
    except Exception as exc:
        logger.exception("手工预设矩阵运行失败")
        db.rollback()
        try:
            finish_task_run(
                db,
                task_run_id,
                status="failed",
                error=str(exc),
                summary=str(exc),
                result={"summary_text": str(exc)},
            )
        except Exception:
            logger.exception("手工预设矩阵失败状态写入失败")
    finally:
        db.close()
        _mark_done_memory(MANUAL_PRESET_MATRIX_TASK_ID)


def start_data_health_remediation_now(payload: dict | None = None) -> dict:
    """异步启动一次手工数据健康补数，立即返回任务运行状态。"""
    if not _try_mark_running_memory(MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID):
        running = get_latest_data_health_remediation_task_run()
        if running and running.get("status") == "running":
            return {"started": False, "task_run": running}
        raise RuntimeError("数据补数正在执行中，请等待当前批次完成")

    run_payload = _default_data_health_remediation_payload(payload or {})
    db = SessionLocal()
    task_run_id: str | None = None
    try:
        existing_running = _latest_running_task_run(db, MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)
        if existing_running is not None:
            _mark_done_memory(MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)
            return {"started": False, "task_run": serialize_scheduled_task_run(existing_running)}

        task = _manual_data_health_remediation_task(run_payload)
        task_run = start_task_run(db, task, trigger_source="manual")
        task_run_id = task_run.task_run_id
    except Exception:
        _mark_done_memory(MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)
        raise
    finally:
        db.close()

    from app.orchestration import trigger_manual_remediation
    trigger_manual_remediation(task_run_id, run_payload)
    latest = get_latest_data_health_remediation_task_run()
    return {"started": True, "task_run": latest}


def get_latest_data_health_remediation_task_run() -> dict | None:
    db = SessionLocal()
    try:
        run = db.scalars(
            select(ScheduledTaskRun)
            .where(ScheduledTaskRun.task_id == MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)
            .order_by(ScheduledTaskRun.started_at.desc())
            .limit(1)
        ).first()
        return serialize_scheduled_task_run(run) if run is not None else None
    finally:
        db.close()


def _latest_running_task_run(db, task_id: str) -> ScheduledTaskRun | None:
    return db.scalars(
        select(ScheduledTaskRun)
        .where(
            ScheduledTaskRun.task_id == task_id,
            ScheduledTaskRun.status == "running",
            ScheduledTaskRun.finished_at.is_(None),
        )
        .order_by(ScheduledTaskRun.started_at.desc())
        .limit(1)
    ).first()


def _execute_manual_data_health_remediation(task_run_id: str, payload: dict) -> None:
    db = SessionLocal()
    try:
        result = _run_manual_data_health_remediation(db, payload)
        finish_task_run(
            db,
            task_run_id,
            status=_task_result_status(result),
            summary=str(result.get("summary_text") or ""),
            result=result,
        )
    except Exception as exc:
        logger.exception("手工数据健康补数失败")
        db.rollback()
        try:
            finish_task_run(
                db,
                task_run_id,
                status="failed",
                error=str(exc),
                summary=str(exc),
                result={"summary_text": str(exc), "remediation_type": "data_health"},
            )
        except Exception:
            logger.exception("手工数据健康补数失败状态写入失败")
    finally:
        db.close()
        _mark_done_memory(MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)


def _run_manual_data_health_remediation(db, payload: dict) -> dict:
    from app.services.data_gap_remediation_service import (
        DEFAULT_DAILY_KBAR_CODE_LIMIT,
        DEFAULT_REMEDIABLE_TABLES,
        remediate_data_health_gaps,
    )

    target_tables = payload.get("target_tables")
    result = remediate_data_health_gaps(
        db,
        target_tables=list(target_tables) if target_tables is not None else None,
        dry_run=False,
        capital_flow_code_limit=int(payload.get("capital_flow_code_limit", 200)),
        daily_kbar_code_limit=int(payload.get("daily_kbar_code_limit", DEFAULT_DAILY_KBAR_CODE_LIMIT)),
        minute_kbar_code_limit=int(payload.get("minute_kbar_code_limit", 300)),
        post_diagnosis=bool(payload.get("post_diagnosis", True)),
    )
    return {
        **result,
        "summary_text": _data_health_remediation_summary(result),
        "remediation_type": "data_health",
        "target_tables": target_tables or list(DEFAULT_REMEDIABLE_TABLES),
    }


def _manual_data_health_remediation_task(payload: dict) -> ScheduledTask:
    task = ScheduledTask(task_id=MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)
    task.name = "手动数据补数"
    task.task_type = "data_backfill"
    task.cron_expression = "0 0 * * *"
    task.payload_json = json.dumps(payload, ensure_ascii=False)
    task.enabled = False
    task.is_running = True
    return task


def _manual_preset_matrix_task(payload: dict) -> ScheduledTask:
    task = ScheduledTask(task_id=MANUAL_PRESET_MATRIX_TASK_ID)
    task.name = str(payload.get("matrix_batch_name") or payload.get("run_name_prefix") or "手工预设矩阵")
    task.task_type = TASK_TYPE_PRESET_MATRIX_RUN
    task.cron_expression = "0 0 * * *"
    task.payload_json = json.dumps(payload, ensure_ascii=False)
    task.enabled = False
    task.is_running = True
    return task


def _run_outcome_label(db, payload: dict) -> dict:
    """结算 pending 推荐 outcome（D 项前瞻验证前置基础设施）。

    收盘后日K同步完成（postclose_daily_kbar_batch 17:40 + auxiliary 18:05）后触发，
    调 label_pending_outcomes 用 T+1 日K结算昨日推荐的执行收益/胜负标签。
    无此前置：自检 D 项前瞻窗口永远报"等结算"，上几轮毒区改动无法前瞻确认。
    """
    from app.services.outcome_service import label_pending_outcomes

    horizon_days = int(payload.get("horizon_days") or 5)
    result = label_pending_outcomes(db, horizon_days=horizon_days)
    labeled = int(result.get("labeled") or 0)
    logger.info("outcome 结算完成: %d 笔（horizon=%d）", labeled, horizon_days)
    return {
        "summary_text": f"结算 {labeled} 笔",
        "labeled": labeled,
        "horizon_days": horizon_days,
    }


def _run_intraday_lock_shadow(db, payload: dict) -> dict:
    """盘中锁利影子链路调度入口（只记录不交易）。

    按 payload.action 分发两个表驱动入口（盘中 poll 走 Prefect deployment 的
    interval job，不经此 handler）：
      - create: 盘前为「前天推荐、昨天入场、今天首个可卖日」的持仓建监控。
                trade_date 缺省取前一工作日（推荐日）——昨天是 T+1 入场日、今天 T+2 可卖；
                outcome 入场的 17:30 数据闸门由 service 内 create_monitors_for_trade_date
                空结果兜底（未确认入场则不建，幂等，下一可卖日补建）。
      - settle: 盘后回填今日触发信号的 post_trigger 走势 + 当时实际出场占位。
    """
    from app.services.intraday_lock_service import (
        create_monitors_for_trade_date,
        settle_signals,
    )
    from app.services.tushare_client import get_pro

    action = str(payload.get("action") or "").strip().lower()
    if action == "create":
        trade_date = str(
            payload.get("trade_date") or previous_weekday(datetime.now().date()).isoformat()
        )
        created = create_monitors_for_trade_date(db, trade_date)
        logger.info("盘中锁利建监控完成: trade_date=%s 新建 %d 个", trade_date, created)
        return {
            "summary_text": f"建监控 {created} 个（{trade_date}）",
            "action": action,
            "trade_date": trade_date,
            "created": created,
        }
    if action == "settle":
        stats = settle_signals(db, get_pro())
        logger.info("盘中锁利盘后回填完成: %s", stats)
        return {
            "summary_text": (
                f"回填 {stats.get('settled', 0)} 个信号"
                f"（跳过 {stats.get('skipped', 0)} / 错误 {stats.get('errors', 0)}）"
            ),
            "action": action,
            **stats,
        }
    raise RuntimeError(f"未知盘中锁利 action: {action!r}（支持 create/settle）")


def _run_data_sync(db, payload: dict, *, task_type: str = "data_sync") -> dict:
    """统一数据同步入口，按 sync_scope 执行可恢复的小阶段。"""
    from app.services.cache_hooks import invalidate as _invalidate_cache

    def invalidate_pool_status_cache() -> None:
        _invalidate_cache("pool_status")
    from app.services.data_health_service import clear_data_health_cache
    from app.services.market_pool import refresh_quote_pool

    sync_scope = str(payload.get("sync_scope") or _default_sync_scope(task_type))
    result: dict[str, object] = {"sync_scope": sync_scope, "steps": []}
    step_timings: dict[str, float] = {}
    summary_parts: list[str] = []
    # 纯观测：每阶段耗时（秒），不影响任何写库逻辑，用于盘后流水线 profile。
    import time as _time

    _t0 = _time.perf_counter()

    def _lap() -> float:
        nonlocal _t0
        now = _time.perf_counter()
        elapsed, _t0 = round(now - _t0, 1), now
        return elapsed

    # 1. 刷新行情池
    if sync_scope in ("quotes", "all"):
        _lap()
        quotes, meta = refresh_quote_pool(db)
        invalidate_pool_status_cache()
        clear_data_health_cache()
        logger.info("行情池刷新: %d 条行情, %s", len(quotes), meta)
        result["quotes"] = {"refreshed": len(quotes), "meta": meta}
        quote_trade_dates = [str(row.get("trade_date") or "") for row in quotes if row.get("trade_date")]
        if quote_trade_dates:
            result["trade_date"] = max(quote_trade_dates)
        summary_parts.append(f"行情池 {len(quotes)} 条")
        result["steps"].append({"name": "quotes", "status": "completed", "row_count": len(quotes)})
        step_timings["quotes"] = _lap()

    # 2. 按交易日批量回填日K，避免逐股票脚本在全市场 force 模式下长时间超时。
    if sync_scope in ("daily_bars", "all"):
        _lap()
        daily_payload = dict(payload)
        if sync_scope == "all":
            daily_payload["health_post_diagnosis"] = False
        daily_kbar_batch = _run_daily_kbar_batch(daily_payload)
        invalidate_pool_status_cache()
        clear_data_health_cache()
        result["daily_kbar_batch"] = daily_kbar_batch
        summary_parts.append(_sync_step_summary("日K按日批量补数", daily_kbar_batch))
        result["steps"].append({"name": "daily_kbar_batch", "status": daily_kbar_batch.get("status")})
        step_timings["daily_kbar_batch"] = _lap()

    # 3. 回填辅助数据。all 模式必须先完成辅助数据，再做最终健康诊断。
    if sync_scope in ("auxiliary", "all"):
        _lap()
        auxiliary_payload = dict(payload)
        auxiliary_payload["trade_date"] = _bounded_auxiliary_trade_date(
            auxiliary_payload.get("trade_date"),
            _latest_allowed_bar_date(),
        )
        auxiliary = _run_auxiliary_remediation(auxiliary_payload)
        invalidate_pool_status_cache()
        clear_data_health_cache()
        result["auxiliary"] = auxiliary
        summary_parts.append(_sync_step_summary("辅助数据补数完成", auxiliary))
        result["steps"].append({"name": "auxiliary", "status": auxiliary.get("status")})
        step_timings["auxiliary"] = _lap()
        # xiaoshi 另类数据回退（margin_detail 缺口 / 解禁日历）；按日各一次分页调用，失败不影响主流程。
        try:
            from app.services.xiaoshi_alt_data import sync_xiaoshi_alt_data

            result["xiaoshi_alt"] = sync_xiaoshi_alt_data(db)
        except Exception as exc:  # pragma: no cover - 防御  # noqa: BLE001
            result["xiaoshi_alt"] = {"errors": [str(exc)[:160]]}
        step_timings["xiaoshi_alt"] = _lap()
        # 东财另类数据备份（margin_detail 缺口 / 未来解禁日历），flag eastmoney.alt 默认关闭。
        try:
            from app.services.datasource.eastmoney import sync_eastmoney_alt

            result["eastmoney_alt"] = sync_eastmoney_alt(db, str(payload.get("trade_date") or datetime.now().strftime("%Y%m%d")))
        except Exception as exc:  # pragma: no cover  # noqa: BLE001
            result["eastmoney_alt"] = {"errors": [str(exc)[:160]]}
        # margin_detail 截断补缺（promax 分页上限 2001 行只有沪市）：独立于 eastmoney.alt 总开关，
        # 只插缺失代码、不覆盖已有行；盘中只记待补，盘后处理。开关 ASHARE_MARGIN_FILL_ENABLED（默认开）。
        try:
            from app.services.margin_detail_fill import run_margin_detail_fill

            result["margin_detail_fill"] = run_margin_detail_fill(
                db, str(payload.get("trade_date") or datetime.now().strftime("%Y%m%d"))
            )
        except Exception as exc:  # pragma: no cover  # noqa: BLE001
            result["margin_detail_fill"] = {"errors": [str(exc)[:160]]}

    # 4. 最终健康补数与诊断，确保后续推荐读取的是所有同步阶段之后的快照。
    if sync_scope in ("health_remediation", "all") and not bool(payload.get("skip_health_remediation")):
        _lap()
        health_remediation = _run_data_health_remediation(payload)
        invalidate_pool_status_cache()
        clear_data_health_cache()
        result["health_remediation"] = health_remediation
        summary_parts.append(_sync_step_summary("健康补数完成", health_remediation))
        result["steps"].append({"name": "health_remediation", "status": health_remediation.get("status")})
        step_timings["health_remediation"] = _lap()
        if isinstance(health_remediation.get("timings_seconds"), dict):
            result["health_remediation_timings_seconds"] = health_remediation["timings_seconds"]

    result["status"] = _task_result_status(result)
    result["summary_text"] = "；".join(summary_parts) if summary_parts else "自动任务执行完成"
    if step_timings:
        result["step_timings_seconds"] = step_timings
    return result


def _bounded_auxiliary_trade_date(requested: object, latest_closed: object) -> str:
    def normalize(value: object) -> str:
        raw = str(value or "").strip()
        if len(raw) == 8 and raw.isdigit():
            raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()

    closed = normalize(latest_closed)
    if requested in (None, ""):
        return closed
    return min(normalize(requested), closed)


def _run_daily_kbar_batch(payload: dict) -> dict:
    from app.services.data_gap_remediation_service import (
        DEFAULT_DAILY_KBAR_CODE_LIMIT,
        remediate_data_health_gaps,
    )

    db = SessionLocal()
    try:
        result = remediate_data_health_gaps(
            db,
            target_tables=["daily_kbar"],
            daily_kbar_code_limit=int(payload.get("health_daily_kbar_code_limit") or DEFAULT_DAILY_KBAR_CODE_LIMIT),
            post_diagnosis=bool(payload.get("health_post_diagnosis", True)),
        )
        # K 线同步后修本地可算指标（ma5/ma10/ma20/vol_ratio/bias/breakout/max_dd）。
        # remediate_data_health_gaps 只检测行数缺失，不检测 ma5=0——批量同步写入 0 指标时
        # 不会被自动修复，导致 _build_stock_from_bars fallback 重算（策略能跑但数据质量差）。
        # try/except 保护：指标补齐是 best-effort 增强，不能因它失败而让 K 线同步整体失败。
        try:
            indicator_fix = _backfill_missing_local_indicators(db, days=7)
            if indicator_fix.get("fixed_dates"):
                result.setdefault("indicator_backfill", indicator_fix)
                logger.info("daily_kbar_batch 后补本地指标: %s", indicator_fix)
        except Exception as exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
            logger.warning("daily_kbar_batch 后补本地指标失败（不影响 K 线同步）: %s", exc)
        return result
    finally:
        db.close()


def _backfill_missing_local_indicators(db, days: int = 7) -> dict:
    """检测最近 N 天 ma5=0 占比 >50% 的交易日，调 backfill_indicators.backfill_date 修复。

    与 scripts/backfill_indicators.py 同逻辑，供 scheduler 在 K 线同步后自动调用，
    避免 ma5=0 全市场缺失（07-07/07-03 曾发生 5505 只全 0）。
    """
    import sys as _sys
    from pathlib import Path as _Path
    scripts_dir = _Path(__file__).resolve().parent.parent.parent / "scripts"
    if str(scripts_dir) not in _sys.path:
        _sys.path.insert(0, str(scripts_dir))
    try:
        from backfill_indicators import _dates_needing_indicators, backfill_date
    except ImportError:
        return {"fixed_dates": [], "error": "backfill_indicators 不可导入"}

    dates = _dates_needing_indicators(db, days)
    fixed: list[dict] = []
    for trade_date in dates:
        stats = backfill_date(db, trade_date)
        fixed.append({"trade_date": trade_date, **stats})
    return {"fixed_dates": fixed}


def _run_data_health_remediation(payload: dict) -> dict:
    from app.services.data_gap_remediation_service import (
        DEFAULT_DAILY_KBAR_CODE_LIMIT,
        HEALTH_REMEDIATION_TABLES,
        remediate_data_health_gaps,
    )

    target_tables = payload.get("health_remediation_tables")
    if target_tables is None:
        target_tables = list(HEALTH_REMEDIATION_TABLES)
    db = SessionLocal()
    try:
        return remediate_data_health_gaps(
            db,
            target_tables=list(target_tables),
            capital_flow_code_limit=int(payload.get("health_capital_flow_code_limit") or payload.get("flow_top_n") or 300),
            daily_kbar_code_limit=int(payload.get("health_daily_kbar_code_limit") or DEFAULT_DAILY_KBAR_CODE_LIMIT),
            minute_kbar_code_limit=int(payload.get("health_minute_kbar_code_limit") or payload.get("tushare_aux_top_n") or 300),
            post_diagnosis=bool(payload.get("health_post_diagnosis", True)),
        )
    finally:
        db.close()


def _run_auxiliary_remediation(payload: dict) -> dict:
    from app.services.data_gap_remediation_service import (
        AUXILIARY_REMEDIATION_TABLES,
        remediate_data_health_gaps,
    )
    from app.services.auxiliary.production import AUXILIARY_DATASET_INVENTORY

    target_tables = payload.get("auxiliary_remediation_tables")
    if target_tables is None:
        target_tables = list(AUXILIARY_REMEDIATION_TABLES)
    db = SessionLocal()
    try:
        result = remediate_data_health_gaps(
            db,
            target_tables=list(target_tables),
            capital_flow_code_limit=int(payload.get("auxiliary_capital_flow_code_limit") or payload.get("flow_top_n") or 300),
            post_diagnosis=bool(payload.get("auxiliary_post_diagnosis", True)),
        )

        # 独立辅助任务也必须拥有明确交易日；此前缺失该字段导致整个批量分支静默跳过。
        trade_date = str(payload.get("trade_date") or result.get("trade_date") or _latest_allowed_bar_date())
        from app.services.auxiliary.application import run_auxiliary_application

        datasets = tuple(
            payload.get("auxiliary_batch_datasets")
            or [entry.key for entry in AUXILIARY_DATASET_INVENTORY]
        )
        if not payload.get("auxiliary_batch_datasets"):
            datasets, skipped_gaps = _skip_slow_known_gaps(datasets, trade_date)
            if skipped_gaps:
                result["skipped_known_gaps"] = skipped_gaps
        auxiliary = run_auxiliary_application(
            db,
            instruments=(),
            trade_date=trade_date,
            refresh=True,
            datasets=datasets,
        )
        result["trade_date"] = trade_date
        result["batch_sync"] = {
            item.dataset_key: {
                "status": _batch_sync_item_status(item.dataset_key, item.errors),
                "count": item.coverage.record_count,
                "errors": [error.code.value for error in item.errors],
                # 此前只留错误码（fetch_failed / persist_failed），根因无从查起；补上阶段、异常类型与上游原文。
                **({"error_details": [_aux_error_detail(error) for error in item.errors]} if item.errors else {}),
                **({"known_gap": _KNOWN_AUX_SOURCE_GAPS[item.dataset_key]}
                   if item.errors and item.dataset_key in _KNOWN_AUX_SOURCE_GAPS else {}),
            }
            for item in auxiliary.orchestrated.results
        }
        known = sorted(k for k, v in result["batch_sync"].items() if v["status"] == "known_gap")
        if known:
            result["known_source_gaps"] = known
            logger.warning("[WARN:AUX_KNOWN_GAP] 已知数据源长期缺口（不计失败）：%s", ", ".join(known))
        # S4 application boundary 明确把事务所有权交给调用方；调度入口必须提交，
        # 否则 Session close 会把已经拉取和 upsert 的批量数据全部回滚。
        db.commit()
        # 先提交再等限流窗口，避免事务悬挂；重试成功的数据集会覆盖 batch_sync 里的失败条目
        _retry_rate_limited_aux_datasets(db, auxiliary, result, trade_date, payload)
        db.commit()
        if any(item["status"] == "failed" for item in result["batch_sync"].values()):
            result["status"] = "partial_failed"

        # 回填最近窗口缺口日：run_auxiliary_application 只按参考日单日拉取，
        # 若前几个交易日的数据在当日盘后未拉到（管线失败 / Tushare 当日延迟），
        # 这些缺口会长期滞留——只补参考日永远填不满。这里对窗口内早于参考日
        # 的交易日逐日回填「按 trade_date 批量」的数据集，让历史缺口自愈。
        window_days = int(payload.get("auxiliary_backfill_window_days") or 5)
        window_backfill = _backfill_auxiliary_window_gaps(
            db,
            reference_trade_date=trade_date,
            window_days=window_days,
        )
        if window_backfill.get("backfilled_dates"):
            result["window_backfill"] = window_backfill

        # stk_limit（涨跌停价）不在 S4 AUXILIARY_DATASET_INVENTORY，此前只靠
        # 未调度的 daily_data_cron（已删除）补，长期 stale。它按 trade_date 批量
        # (pro.stk_limit(trade_date=))，与辅助窗口回填同节奏，这里一并补缺口日。
        if not bool(payload.get("skip_stk_limit_backfill")):
            stk_limit_backfill = _backfill_stk_limit_window(
                db,
                reference_trade_date=trade_date,
                window_days=window_days,
            )
            if stk_limit_backfill.get("backfilled_dates"):
                result["stk_limit_backfill"] = stk_limit_backfill

        return result
    finally:
        db.close()


def _aux_error_detail(error) -> dict[str, Any]:
    return {
        "code": error.code.value,
        "stage": getattr(error.stage, "value", str(error.stage)),
        "exception_type": error.exception_type,
        "message": str(error.message)[:400],
    }


_AUX_RETRYABLE_MARKERS = ("rate_limit", "circuit", "timeout", "timed out", "temporarily")


def _aux_error_is_rate_limited(error) -> bool:
    text_ = str(error.message).lower()
    return any(m in text_ for m in _AUX_RETRYABLE_MARKERS)


def _retry_rate_limited_aux_datasets(db, auxiliary, result: dict, trade_date: str, payload: dict) -> None:
    """辅助数据集因 promax 限流窗口（5 分钟/30 分钟/1 小时）/熔断失败时，等窗口滚动后只重试这些数据集一次。

    quota_exhausted（quantdata 日额度）不是等一等能好的，不重试。重试成功的数据集覆盖原结果并记 retried=True。
    """
    import time as _time

    failed = {
        item.dataset_key: item for item in auxiliary.orchestrated.results
        if result["batch_sync"].get(item.dataset_key, {}).get("status") == "failed"
        and item.errors and all(_aux_error_is_rate_limited(e) for e in item.errors)
    }
    wait = float(payload.get("auxiliary_retry_wait_seconds", 330))
    if not failed or wait < 0 or payload.get("skip_auxiliary_retry"):
        return
    logger.warning("辅助数据集限流失败 %s，%.0fs 后重试一次", sorted(failed), wait)
    _time.sleep(wait)
    from app.services.auxiliary.application import run_auxiliary_application

    try:
        retry = run_auxiliary_application(db, instruments=(), trade_date=trade_date, refresh=True,
                                          datasets=tuple(failed))
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("辅助数据集重试失败: %s", exc, exc_info=True)
        for key in failed:
            result["batch_sync"][key]["retry_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return
    for item in retry.orchestrated.results:
        entry = {
            "status": _batch_sync_item_status(item.dataset_key, item.errors),
            "count": item.coverage.record_count,
            "errors": [e.code.value for e in item.errors],
            "retried": True,
        }
        if item.errors:
            entry["error_details"] = [_aux_error_detail(e) for e in item.errors]
        result["batch_sync"][item.dataset_key] = entry


def _backfill_auxiliary_window_gaps(
    db,
    *,
    reference_trade_date: str,
    window_days: int,
) -> dict[str, Any]:
    """对最近 window_days 个交易日中早于参考日的缺口日逐日回填辅助数据集。

    只回填「按 trade_date 批量」的数据集（见 _AUXILIARY_TRADE_DATE_BATCH_DATASETS），
    避免对事件型/季频数据集做无意义的逐日拉取。每个缺口日一次
    run_auxiliary_application 调用，内部按数据集并行拉取全市场批量，upsert 去重，
    已有数据不会被重复写入。
    """
    from app.services.auxiliary.application import run_auxiliary_application
    from app.models.tables import DailyKbar
    from sqlalchemy import func, select

    backfilled: list[dict[str, Any]] = []
    if not reference_trade_date or window_days <= 0:
        return {"backfilled_dates": backfilled}

    # 取最近 window_days+1 个交易日（含参考日），用于逐日回填窗口内的缺口。
    ref_compact = reference_trade_date.replace("-", "")
    trade_days = _recent_trade_day_compacts(db, ref_compact, window_days + 1)
    # 去掉参考日本身（已在上一步跑过），按时间正序回填，保证时序。
    # 去掉参考日本身（已在上一步跑过），按时间正序回填，保证时序。
    gap_dates = [d for d in trade_days if d != ref_compact]
    gap_dates.reverse()
    if not gap_dates:
        return {"backfilled_dates": backfilled}

    for compact in gap_dates:
        iso = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
        try:
            aux = run_auxiliary_application(
                db,
                instruments=(),
                trade_date=iso,
                refresh=True,
                datasets=_AUXILIARY_TRADE_DATE_BATCH_DATASETS,
            )
            db.commit()
            backfilled.append({
                "trade_date": iso,
                "status": "success" if not any(item.errors for item in aux.orchestrated.results) else "partial",
                "datasets": {
                    item.dataset_key: {
                        "status": "failed" if item.errors else "success",
                        "count": item.coverage.record_count,
                    }
                    for item in aux.orchestrated.results
                },
            })
        except Exception as exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
            logger.warning("辅助数据窗口回填 %s 失败: %s", iso, str(exc)[:120])
            try:
                db.rollback()
            except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
                logger.debug("_backfill_auxiliary_window_gaps: ignored non-fatal error: %s", exc)
            backfilled.append({"trade_date": iso, "status": "failed", "error": str(exc)[:120]})
    return {"backfilled_dates": backfilled}


def _backfill_stk_limit_window(
    db,
    *,
    reference_trade_date: str,
    window_days: int,
) -> dict[str, Any]:
    """对最近 window_days 个交易日回填 stk_limit（涨跌停价）。

    stk_limit 不在 S4 辅助数据集清单，此前无盘后补数路径。按 trade_date 批量
    (pro.stk_limit(trade_date=)) 一次拉全市场，逐日 upsert。已有数据不会被
    重复写入（on_duplicate_key_update）。
    """
    from sqlalchemy.dialects.mysql import insert as mysql_insert
    from app.models.tables import StkLimit
    from app.services.tushare_client import get_pro

    backfilled: list[dict[str, Any]] = []
    if not reference_trade_date or window_days <= 0:
        return {"backfilled_dates": backfilled}

    ref_compact = reference_trade_date.replace("-", "")
    trade_days = _recent_trade_day_compacts(db, ref_compact, window_days + 1)
    # 含参考日；按正序回填，参考日也要补（此前管线从不补 stk_limit）。
    gap_dates = list(reversed([d for d in trade_days]))
    if not gap_dates:
        return {"backfilled_dates": backfilled}

    pro = get_pro()
    for compact in gap_dates:
        iso = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
        try:
            df = pro.stk_limit(trade_date=compact)
        except Exception as exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
            logger.warning("stk_limit(%s) 拉取失败: %s", iso, str(exc)[:120])
            backfilled.append({"trade_date": iso, "status": "failed", "error": str(exc)[:120]})
            continue
        if df is None or df.empty:
            backfilled.append({"trade_date": iso, "status": "empty", "count": 0})
            continue
        rows = []
        for _, r in df.iterrows():
            code = str(r.get("ts_code", "")).split(".")[0]
            if len(code) != 6:
                continue
            rows.append({
                "code": code,
                "trade_date": iso,
                "up_limit": _safe_float(r.get("up_limit")),
                "down_limit": _safe_float(r.get("down_limit")),
            })
        if not rows:
            backfilled.append({"trade_date": iso, "status": "empty", "count": 0})
            continue
        stmt = mysql_insert(StkLimit).values(rows)
        stmt = stmt.on_duplicate_key_update(
            up_limit=stmt.inserted.up_limit,
            down_limit=stmt.inserted.down_limit,
        )
        db.execute(stmt)
        db.commit()
        backfilled.append({"trade_date": iso, "status": "success", "count": len(rows)})
    return {"backfilled_dates": backfilled}


def _recent_trade_day_compacts(db, ref_compact: str, limit: int) -> list[str]:
    """最近 limit 个交易日（含 ref_compact），紧凑 YYYYMMDD 格式，倒序。"""
    from app.models.tables import DailyKbar
    from sqlalchemy import func, select

    normalized_col = func.replace(DailyKbar.trade_date, "-", "")
    rows = db.scalars(
        select(normalized_col)
        .where(DailyKbar.trade_date.is_not(None), normalized_col <= ref_compact)
        .group_by(normalized_col)
        .order_by(normalized_col.desc())
        .limit(max(1, limit))
    ).all()
    return [str(r) for r in rows if r]


def _run_backfill_script(payload: dict) -> dict:
    """执行日K线回填脚本。"""
    import subprocess
    import sys
    from pathlib import Path

    from app.core.config import get_settings

    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    script = scripts_dir / "backfill_daily_bars.py"
    if not script.exists():
        raise RuntimeError(f"回填脚本不存在: {script}")

    args = [sys.executable, str(script)]
    if payload.get("days"):
        args.extend(["--days", str(payload["days"])])
    if payload.get("min_bars"):
        args.extend(["--min-bars", str(payload["min_bars"])])
    if payload.get("limit"):
        args.extend(["--limit", str(payload["limit"])])
    if payload.get("offset"):
        args.extend(["--offset", str(payload["offset"])])
    if payload.get("commit_interval"):
        args.extend(["--commit-interval", str(payload["commit_interval"])])
    if payload.get("rate_limit") is not None:
        args.extend(["--rate-limit", str(payload["rate_limit"])])
    if payload.get("force"):
        args.append("--force")
    if payload.get("reset_progress"):
        args.append("--reset-progress")

    timeout = get_settings().script_timeout_seconds or None
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"日K回填脚本失败: {result.stderr[:500]}")
    logger.info("backfill_daily_bars.py 完成:\n%s", result.stdout[-500:])
    return _parse_backfill_stdout(result.stdout)


def _run_auxiliary_script(payload: dict) -> dict:
    """执行辅助数据回填脚本。"""
    import subprocess
    import sys
    from pathlib import Path

    from app.core.config import get_settings

    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    script = scripts_dir / "backfill_auxiliary_data.py"
    if not script.exists():
        raise RuntimeError(f"回填脚本不存在: {script}")

    args = [sys.executable, str(script)]
    if payload.get("skip_concepts"):
        args.append("--skip-concepts")
    if payload.get("skip_dragon_tiger"):
        args.append("--skip-dragon-tiger")
    if payload.get("skip_capital_flows"):
        args.append("--skip-capital-flows")
    if payload.get("skip_news"):
        args.append("--skip-news")
    if payload.get("skip_tushare_aux"):
        args.append("--skip-tushare-aux")
    if payload.get("flow_top_n"):
        args.extend(["--flow-top-n", str(payload["flow_top_n"])])
    if payload.get("tushare_aux_top_n"):
        args.extend(["--tushare-aux-top-n", str(payload["tushare_aux_top_n"])])
    if payload.get("trade_date"):
        args.extend(["--trade-date", str(payload["trade_date"])])

    timeout = get_settings().script_timeout_seconds or None
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"辅助数据回填脚本失败: {result.stderr[:500]}")
    logger.info("backfill_auxiliary_data.py 完成:\n%s", result.stdout[-500:])
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return {
        "stdout_tail": "\n".join(lines[-10:]),
        "summary_text": lines[-1] if lines else "辅助数据回填完成",
    }


def _diagnose_postclose_readiness(db, trade_date: str) -> dict[str, Any]:
    from app.services.data_health_service import (
        diagnose_all,
        recommendation_readiness_from_diagnosis,
    )

    diagnosis = diagnose_all(db, force_refresh=True)
    readiness = recommendation_readiness_from_diagnosis(
        diagnosis,
        expected_trade_date=trade_date,
    )
    return {
        **readiness,
        "score": diagnosis.get("score"),
        "issues": diagnosis.get("issues") or [],
    }


async def _wait_for_recommendation_run(
    db,
    run_id: str,
    *,
    timeout_seconds: int = 3600,
    poll_seconds: float = 2.0,
) -> dict[str, Any]:
    """等待独立 worker 的最终状态，让调度任务不再把“已入队”误报为完成。

    轮询必须绕开连接池、每次用物理全新连接读取。生产 MySQL 是 REPEATABLE-READ 且
    SessionLocal 走连接池（pool_size=20）：盘后同步阶段的长事务会把某个池连接的
    一致性快照钉在同步开始时刻，归还池后快照不随 commit 推进；``SessionLocal()``
    大概率复用到这同一个连接，于是 ``poll_db.get()`` 一直读到旧的 queued/running，
    把早已完成并 commit 的 worker 误报为超时（实测推荐 2 分钟跑完却空转 3600s）。
    ``engine.connect()`` 每次建立物理新连接，事务快照从当前时刻开始，必然读到最新状态。
    """
    from app.db.session import engine as _engine

    deadline = time.monotonic() + max(1, timeout_seconds)
    terminal = {"completed", "failed", "interrupted", "cancelled"}
    stmt = (
        select(
            RecommendationRun.run_id,
            RecommendationRun.status,
            RecommendationRun.recommended_count,
            RecommendationRun.error_message,
            RecommendationRun.finished_at,
        )
        .where(RecommendationRun.run_id == run_id)
    )
    while time.monotonic() < deadline:
        with _engine.connect() as conn:
            row = conn.execute(stmt).first()
        if row is None:
            raise RuntimeError(f"推荐 Run 不存在: {run_id}")
        status = row.status
        if status in terminal:
            return {
                "run_id": row.run_id,
                "status": status,
                "recommended_count": int(row.recommended_count or 0),
                "error": row.error_message,
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            }
        await asyncio.sleep(max(0.1, poll_seconds))
    raise TimeoutError(f"推荐 Run {run_id} 在 {timeout_seconds} 秒内未结束")


def _refresh_recoverable_postclose_aux(
    db,
    *,
    trade_date: str,
    tables: list[str],
) -> dict[str, Any]:
    """逐表短事务刷新；先检查 provider 错误，成功后才提交。

    刷新本身抛错代表数据库、程序或 provider 故障，必须保留原异常与 traceback，
    不能伪装成“数据尚未发布”。只有刷新正常返回但闸门仍只缺当天时才允许退避。
    """
    from app.services.data_gap_remediation_service import (
        _backfill_index_daily_dates,
        _index_ts_codes,
        _refresh_auxiliary_dataset,
    )
    from app.services.data_health_service import clear_data_health_cache

    results: dict[str, Any] = {}
    try:
        for table in tables:
            if table == "index_daily":
                # index_daily 不是 S4 辅助数据集（orchestrator 无此 descriptor，
                # 走 _refresh_auxiliary_dataset 会 KeyError 并让整个盘后正式推荐失败，
                # 2026-10-15 17:35 实测）。与健康补数同一实现：按指数代码拉当日日线 INSERT IGNORE。
                backfill = _backfill_index_daily_dates(db, [trade_date], _index_ts_codes(db))
                results[table] = {
                    "status": "completed",
                    "record_count": int(backfill.get("row_count") or 0),
                    "audit": backfill,
                }
                continue
            count, audit = _refresh_auxiliary_dataset(
                db,
                dataset=table,
                trade_date=trade_date,
            )
            results[table] = {"status": "completed", "record_count": count, "audit": audit}
    finally:
        clear_data_health_cache()
    return results


async def _run_postclose_pipeline(db, payload: dict) -> dict[str, Any]:
    """单一盘后流水线：全量同步 → 健康闸门 → outcome → 正式推荐。

    当且仅当结构化闸门表明 ``index_daily``/``limit_up_board`` 只缺当前交易日时，
    先以短事务立即刷新一次；仍未就绪则抛出可重试异常，由 Prefect 在新 Session 中
    有界退避。任何行情、诊断、历史覆盖或混合缺口均立即硬失败。
    """
    # 纯观测：流水线各阶段耗时（秒），写入 result_json.pipeline_timings_seconds，
    # 用于 17:35 准点 profile（同步之外的闸门/补刷/outcome/缓存/推荐）。不改任何逻辑。
    _pt_start = time.perf_counter()
    _pt_last = _pt_start
    pipeline_timings: dict[str, float] = {}

    def _plap(name: str) -> None:
        nonlocal _pt_last
        now = time.perf_counter()
        pipeline_timings[name] = round(pipeline_timings.get(name, 0.0) + now - _pt_last, 2)
        _metrics.observe("postclose_stage_duration", now - _pt_last, stage=name)
        pipeline_timings["total"] = round(now - _pt_start, 2)
        _pt_last = now

    trade_date = _latest_allowed_bar_date()
    sync_payload = dict(payload)
    sync_payload.update({"sync_scope": "all", "trade_date": trade_date})
    sync_result = _run_data_sync(db, sync_payload)
    _plap("sync")

    # 交易日历每日盘后刷新一次（经网关 tushare→akshare→baostock）；失败只告警，沿用本地表。
    try:
        from app.services.trade_calendar import refresh_trade_calendar

        sync_result_calendar = refresh_trade_calendar()
    except Exception as exc:  # noqa: BLE001
        sync_result_calendar = {"status": "failed", "error": str(exc)[:300]}
    logger.info("盘后交易日历刷新: %s", sync_result_calendar)
    _plap("trade_calendar")

    # 补齐 Tushare stk_factor 缺失的 RSI/MACD（通达信口径，只填 NULL/0）；失败只告警。
    indicator_fill: dict[str, Any] = {}
    try:
        from app.services.tdx_indicator_fill import fill_recent_indicator_gaps

        indicator_fill = fill_recent_indicator_gaps(db, trade_date)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        indicator_fill = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        logger.warning("tdx indicator gap fill failed: %s", exc)
    _plap("indicator_fill")

    # ma5/10/20/60/250 本地计算：未复权收盘均值，窗口不足写 NULL（不写 0）；失败只告警。
    try:
        from app.services.ma250_fill import fill_moving_averages

        _since = (_dt_mod.date.fromisoformat(str(trade_date)[:10]) - _dt_mod.timedelta(days=15)).isoformat()
        indicator_fill["moving_averages"] = fill_moving_averages(db, since=_since)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("moving-average fill failed: %s", exc)

    # L-093/L-109/L-121 risk vetoes read daily_kbars.ivol20/ivol_dn20; nothing computed them
    # after 2026-09-11, so the vetoes silently stopped. Incremental fill + coverage alert.
    try:
        from app.services.ivol_fill import fill_ivol_incremental

        indicator_fill["ivol"] = fill_ivol_incremental(db, str(trade_date)[:10])
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        indicator_fill["ivol"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        logger.warning("ivol fill failed: %s", exc)

    # bias_10/max_dd60/atr/ema/obv 等本地可算指标：此前日更无人写（只 09-08 补过一次），只补 NULL，失败只告警。
    try:
        from app.services.local_indicator_fill import fill_local_indicators

        _li_since = (_dt_mod.date.fromisoformat(str(trade_date)[:10]) - _dt_mod.timedelta(days=15)).isoformat()
        indicator_fill["local_indicators"] = fill_local_indicators(db, since=_li_since, end=str(trade_date)[:10])
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        indicator_fill["local_indicators"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        logger.warning("local indicator fill failed: %s", exc)

    # KDJ/BOLL/CCI/vol_ratio_5/20：quantdata 额度耗尽 / promax 回退只给部分列时整列 NULL（10-20~10-23 事故）。
    # 本地精确复算（已用源数据逐行核对），只填 NULL；失败只告警。窗口取近 15 天以覆盖补数/重跑。
    try:
        from app.services.kdj_boll_cci_fill import fill_kdj_boll_cci

        _kb_since = (_dt_mod.date.fromisoformat(str(trade_date)[:10]) - _dt_mod.timedelta(days=15)).isoformat()
        indicator_fill["kdj_boll_cci"] = fill_kdj_boll_cci(db, since=_kb_since, end=str(trade_date)[:10])
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        indicator_fill["kdj_boll_cci"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        logger.warning("kdj/boll/cci fill failed: %s", exc)

    # daily_basic（市值/PE/换手）此前从未接入日更，2026-10-09 后停更；补当日 + 停更告警。
    try:
        from app.services.daily_basic_ingest import ingest_daily_basic_with_alert

        indicator_fill["daily_basic"] = ingest_daily_basic_with_alert(db, str(trade_date)[:10])
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        indicator_fill["daily_basic"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        logger.warning("daily_basic ingest failed: %s", exc)

    # 机构调研 institutional_surveys / 两融汇总 margin_data 回到日更（2026-10-19）；失败只记录不阻断。
    try:
        from app.services.survey_margin_ingest import daily_survey_margin_update

        indicator_fill["survey_margin"] = daily_survey_margin_update(db, str(trade_date)[:10])
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        indicator_fill["survey_margin"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        logger.warning("survey/margin_data daily update failed: %s", exc)

    # [中长线] 中线策略选股（周五信号日落盘，独立于短线出票）；失败只告警。
    # 结果写入 result_json.midterm_picks（之前只打 INFO，worker 日志不收，完全不可观测）。
    midterm_picks: dict[str, Any] = {}
    try:
        from app.services.midterm_service import generate_picks as _mid_generate_picks

        midterm_picks = {"trade_date": str(trade_date)[:10], "result": _mid_generate_picks(db, trade_date, apply_daily_cap=True)}
        logger.warning("midterm picks (T=%s): %s", trade_date, midterm_picks["result"])
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        midterm_picks = {"trade_date": str(trade_date)[:10], "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        logger.warning("midterm picks failed: %s", exc)
    _plap("midterm_picks")

    readiness = _diagnose_postclose_readiness(db, trade_date)
    _plap("readiness")
    aux_refresh: dict[str, Any] = {}
    if not readiness.get("can_run_local_recommendation"):
        classification = _classify_postclose_blockers(readiness, trade_date=trade_date)
        recoverable_tables = classification["recoverable_tables"]
        retry_window_open = _latest_allowed_bar_date() == trade_date
        if recoverable_tables and not classification["hard_blockers"] and retry_window_open:
            aux_refresh = _refresh_recoverable_postclose_aux(
                db,
                trade_date=trade_date,
                tables=recoverable_tables,
            )
            _plap("aux_refresh")
            readiness = _diagnose_postclose_readiness(db, trade_date)
            _plap("readiness_recheck")
            if not readiness.get("can_run_local_recommendation"):
                classification = _classify_postclose_blockers(readiness, trade_date=trade_date)
                recoverable_tables = classification["recoverable_tables"]
                retry_window_open = _latest_allowed_bar_date() == trade_date

        if not readiness.get("can_run_local_recommendation"):
            blockers = "；".join(readiness.get("blockers") or ["未知数据健康问题"])
            retryable = bool(
                recoverable_tables
                and not classification["hard_blockers"]
                and retry_window_open
            )
            result = {
                "status": "failed",
                "retryable": retryable,
                "retry_reason": "postclose_aux_upstream_lag" if retryable else None,
                "trade_date": trade_date,
                "sync": sync_result,
                "readiness": readiness,
                "blocker_classification": classification,
                "aux_refresh": aux_refresh,
                "indicator_fill": indicator_fill,
                "midterm_picks": midterm_picks,
                "pipeline_timings_seconds": pipeline_timings,
                "summary_text": (
                    f"盘后辅助数据尚未就绪，等待有界重试：{blockers}"
                    if retryable
                    else f"盘后同步完成，但健康闸门存在硬阻断：{blockers}"
                ),
            }
            if retryable:
                raise RetryablePostcloseReadinessError(result)
            return result

    outcome = _run_outcome_label(
        db,
        {"horizon_days": int(payload.get("horizon_days") or 5)},
    )
    _plap("outcome")

    # 日K Parquet 缓存增量刷新（按交易日指纹，只重拉新增/变化日）；失败只告警，读取端会回退 SQL。
    kbar_cache_refresh: dict[str, Any] | None = None
    try:
        from app.services.market_pool import kbar_cache

        if kbar_cache.enabled():
            kbar_cache_refresh = kbar_cache.refresh_kbar_cache(db)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        kbar_cache_refresh = {"error": str(exc)[:200]}
        try:
            from app.core.alerts import emit_alert
            emit_alert("kbar_cache_refresh_failed", "postclose", f"日K缓存刷新失败: {str(exc)[:200]}")
        except Exception:  # noqa: BLE001, S110
            pass
    _plap("kbar_cache_refresh")

    # 策略页单策略指标：盘后后台重算（独立子进程，不阻塞推荐；读最新研究面板，失败只记日志）。
    try:
        import os as _os
        import subprocess
        import sys as _sys

        if _os.environ.get("PYTEST_CURRENT_TEST"):
            raise RuntimeError("skip in tests")
        from pathlib import Path as _Path

        _b = _Path(__file__).resolve().parents[2]
        _log = open(_b / "data" / "strategy_metrics_refresh.log", "ab")
        subprocess.Popen(
            [_sys.executable, str(_b / "scripts" / "compute_standalone_metrics.py")],
            cwd=str(_b), stdout=_log, stderr=_log, start_new_session=True,
        )
    except Exception:  # noqa: BLE001
        logger.info("standalone metrics refresh not spawned", exc_info=True)

    # 推荐阶段失败（入队失败 / 等待超时 / Run 异常）不抛错——此前 raise 会丢掉
    # 已完成的 sync/auxiliary/readiness/outcome 中间结果，failed run 的 result_json
    # 只剩一句 error，无法诊断数据同步是否成功。这里捕获并把中间结果一并返回。
    try:
        # readiness 已确认最新已收盘数据完整；即使旧调度记录或手工调用
        # 携带了 local_pool_preferred/auto，也必须在正式推荐阶段强制纯本地。
        # 同步阶段已覆盖辅助数据；这里同时禁止 workbench 再次刷新辅助源。
        # 2026-10-14 起不再跑「全策略组合」：每个有效策略各自一个 formal run（单策略独立运行）。
        recommendation_payload = {
            **payload,
            "data_source": DATA_SOURCE_LOCAL_POOL,
            "skip_aux_refresh": True,
        }
        recommendation = await _run_standalone_recommendations(db, recommendation_payload)
        run_id = str(recommendation.get("related_run_id") or "")
        if not run_id:
            raise RuntimeError("盘后单策略正式推荐未返回任何 Run ID")
        final_run = recommendation["final_run"]
        _plap("recommendation")
    except Exception as exc:
        _plap("recommendation")
        logger.exception("盘后推荐阶段失败，保留已完成的数据同步结果: %s", exc)
        return {
            "status": "failed",
            "trade_date": trade_date,
            "sync": sync_result,
            "readiness": readiness,
            "aux_refresh": aux_refresh,
            "indicator_fill": indicator_fill,
            "midterm_picks": midterm_picks,
            "outcome": outcome,
            "pipeline_timings_seconds": pipeline_timings,
            "recommendation": recommendation if "recommendation" in locals() else None,
            "related_run_id": run_id if "run_id" in locals() else "",
            "error": str(exc),
            "summary_text": f"盘后同步已完成，但推荐阶段失败：{str(exc)[:120]}",
        }

    final_status = str(final_run.get("status") or "failed")
    if final_status != "completed":
        return {
            "status": "failed",
            "trade_date": trade_date,
            "sync": sync_result,
            "readiness": readiness,
            "aux_refresh": aux_refresh,
            "indicator_fill": indicator_fill,
            "midterm_picks": midterm_picks,
            "outcome": outcome,
            "pipeline_timings_seconds": pipeline_timings,
            "recommendation": recommendation,
            "final_run": final_run,
            "related_run_id": run_id,
            "summary_text": f"盘后推荐 Run {run_id} 最终状态为 {final_status}",
        }

    recommended_count = int(final_run.get("recommended_count") or 0)
    return {
        "status": "completed",
        "trade_date": trade_date,
        "sync": sync_result,
        "readiness": readiness,
        "aux_refresh": aux_refresh,
        "indicator_fill": indicator_fill,
        "midterm_picks": midterm_picks,
        "outcome": outcome,
        "pipeline_timings_seconds": pipeline_timings,
        "recommendation": recommendation,
        "final_run": final_run,
        "related_run_id": run_id,
        "recommended_count": recommended_count,
        "strategy_runs": recommendation.get("strategy_runs") or [],
        "summary_text": (
            f"盘后数据已同步并通过健康检查，单策略正式推荐 "
            f"{recommendation.get('completed_count', 0)}/{recommendation.get('strategy_count', 0)} 个策略完成，"
            f"共 {recommended_count} 条"
        ),
    }


async def _run_recommendation(db, payload: dict) -> dict:
    """执行推荐流水线（异步入队到独立子进程，后端重启不影响执行）。

    T12 稳定性修复：原先在此处用 asyncio.run(run_workbench(...)) 在后端进程内
    同步执行，后端一旦重启/热重载会直接杀死正在跑的 formal run
    （error=「服务重启或进程退出」），导致 A 项（每日可见推荐）断档。

    现改为与 API 手动触发一致的 enqueue_workbench_run 路径：创建 RecommendationRun
    后由 _spawn_run_subprocess(start_new_session=True) 派发到独立子进程执行。子进程与
    uvicorn 进程解耦，后端重启后子进程仍继续更新心跳，recover_interrupted_runs 按心跳
    超时判定不会误杀存活任务。本函数立即返回入队结果（recommended_count=0），实际推荐
    数量由子进程完成后写入 recommendation_runs，安全网据此检查断档。
    """
    from app.services.workbench_service import enqueue_workbench_run

    run_payload: dict[str, Any] = {
        "preset_id": payload.get("preset_id"),
        "run_name": payload.get("run_name", f"定时推荐 {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y%m%d')}"),
    }
    # Preserve every explicitly scheduled RunRequest input.  Defaults remain owned
    # by the canonical parser/preset merge, but a scheduler must never silently
    # discard an explicitly selected review, date, scope, or matrix policy.
    for key in (
        "data_source",
        "risk_template",
        "score_line",
        "output_count",
        "selected_skill_ids",
        "skills",
        "single_skill_id",
        "skip_aux_refresh",
        "disable_agent_review",
        "trade_date_override",
        "market_scope",
        "market_scope_value",
        "agent_review_limit",
        "agent_review_concurrency",
        "hide_recent_repeats",
        "dedup_same_day",
        "matrix_batch_id",
        "matrix_batch_name",
        "matrix_run",
    ):
        if key in payload:
            run_payload[key] = payload.get(key)

    # Scheduler emits the same immutable request shape as API callers.  Keep the
    # no-preset in-process fallback below, but never let it bypass request parsing.
    from app.services.runs.contracts import parse_run_request, validate_run_request_no_credentials

    validate_run_request_no_credentials(payload)
    if "selected_skill_ids" in run_payload:
        run_payload["skills"] = [
            {"skill_id": str(skill_id), "weight": 1.0, "sort_order": index}
            for index, skill_id in enumerate(run_payload.pop("selected_skill_ids") or [])
            if str(skill_id).strip()
        ]
    # Validate the durable shape, but keep only explicitly scheduled fields in
    # ``run_payload`` so Pydantic defaults never overwrite a preset during merge.
    # ``RunRequest`` owns the formal default; an explicitly supplied value must
    # survive unchanged so invalid values are rejected rather than silently
    # rewritten and experiment/backtest modes cross every dispatch boundary.
    validation_payload = dict(run_payload)
    if "run_mode" in payload:
        validation_payload["run_mode"] = payload["run_mode"]
    parse_run_request(validation_payload)
    if "run_mode" in payload:
        run_payload["run_mode"] = payload["run_mode"]

    # 无 preset_id 时无法入队独立子进程（enqueue 前置条件），退化为原 in-process 执行，
    # 保留历史行为，避免破坏潜在的非预设触发路径。
    if not run_payload.get("preset_id") and not run_payload.get("single_skill_id"):
        from app.services.workbench_service import run_workbench

        logger.warning("定时推荐缺少 preset_id，退化为进程内执行: %s", run_payload)
        result = await run_workbench(db, run_payload)
        run = result.get("run") or {}
        related_run_id = str(run.get("run_id") or "")
        recommended_count = int(run.get("recommended_count") or result.get("recommended_count") or 0)
        return {
            "related_run_id": related_run_id,
            "recommended_count": recommended_count,
            "summary_text": (
                f"关联 Run {related_run_id}，推荐 {recommended_count} 条（进程内）"
                if related_run_id
                else f"自动推荐 {recommended_count} 条（进程内）"
            ),
        }

    result = enqueue_workbench_run(db, run_payload)
    run = result.get("run") or {}
    related_run_id = str(run.get("run_id") or "")
    logger.info("定时推荐已入队独立子进程: run_id=%s", related_run_id)
    return {
        "related_run_id": related_run_id,
        "recommended_count": 0,
        "queued": True,
        "summary_text": (
            f"已入队 Run {related_run_id}，由独立子进程异步执行"
            if related_run_id
            else "已触发推荐（独立子进程）"
        ),
    }


STANDALONE_ENQUEUE_STAGGER_SECONDS = 5


def _standalone_run_payloads(db, payload: dict) -> list[dict[str, Any]]:
    """为每个有效策略生成一份单策略 formal run 请求（不含组合 skills 列表）。"""
    from app.services.standalone_strategies import standalone_run_settings, standalone_strategy_ids
    from app.services.workbench_service import sync_skills

    skills = sync_skills(db)
    name_by_sid = {s.skill_id: s.name for s in skills}
    only = {str(x) for x in (payload.get("standalone_skill_ids") or []) if str(x).strip()}
    base = {
        k: v
        for k, v in payload.items()
        if k not in {"selected_skill_ids", "skills", "single_skill_id", "score_line", "output_count", "run_name", "preset_id"}
    }
    # 2026-10-19 用户删除 preset_formal_opening：单策略 run 不再挂组合预设。原预设贡献的
    # 口径显式写死（与 10-14..10-16 live run 的 params_json 一致）。
    base.setdefault("hide_recent_repeats", False)
    base.setdefault("dedup_same_day", True)
    label = str(payload.get("trade_date_override") or datetime.now().strftime("%Y%m%d"))
    out: list[dict[str, Any]] = []
    for sid in standalone_strategy_ids(skills):
        if only and sid not in only:
            continue
        score_line, output_count = standalone_run_settings(sid)
        out.append({
            **base,
            "single_skill_id": sid,
            "score_line": score_line,
            "output_count": output_count,
            "run_mode": "formal",
            # 正式出票不等 LLM：管线内关闭审查，出票后再异步挂风险备注（L-171）
            "disable_agent_review": True,
            "run_name": f"单策略 {name_by_sid.get(sid, sid)} {label}",
        })
    return out


def _completed_standalone_runs(db, payload: dict) -> dict[str, dict[str, Any]]:
    """目标交易日已 completed 的单策略 formal run：{skill_id: {run_id, recommended_count}}。"""
    try:
        from sqlalchemy import func, select
        from app.models.tables import DailyKbar, RecommendationRun
        from app.services.standalone_strategies import run_skill_ids

        target = str(payload.get("trade_date_override") or "") or str(
            db.scalar(select(func.max(DailyKbar.trade_date))) or ""
        )
        if len(target) == 8 and target.isdigit():
            target = f"{target[:4]}-{target[4:6]}-{target[6:]}"
        if not target:
            return {}
        rows = db.execute(
            select(RecommendationRun.run_id, RecommendationRun.selected_skills_json, RecommendationRun.recommended_count)
            .where(
                RecommendationRun.trade_date == target,
                RecommendationRun.run_mode == "formal",
                RecommendationRun.status == "completed",
            )
        ).all()
    except Exception:  # noqa: BLE001 - 幂等检查失败时按原逻辑全跑
        logger.exception("单策略幂等检查失败，按全量运行")
        return {}
    out: dict[str, dict[str, Any]] = {}
    for run_id, sel, cnt in rows:
        ids = run_skill_ids(sel)
        if len(ids) == 1:
            out[ids[0]] = {"run_id": run_id, "recommended_count": int(cnt or 0)}
    return out

async def _run_standalone_recommendations(db, payload: dict) -> dict[str, Any]:
    """每个策略各跑一个 formal run（自己的分数线/出票上限/推荐/T+1 结算），分批并发。

    返回 strategy_runs 明细；final_run 汇总状态：至少一个策略 completed 即 completed
    （单个策略失败不拖垮其它策略，失败明细保留在 strategy_runs）。
    """
    payloads = _standalone_run_payloads(db, payload)
    if not payloads:
        raise ValueError(_NO_RUNNABLE_SKILL_MSG)
    timeout = int(payload.get("recommendation_wait_timeout_seconds") or 3600)
    strategy_runs: list[dict[str, Any]] = []
    # 幂等：同一交易日该策略已有 completed formal run 时跳过（重跑/安全网补跑不重复出票）。
    if not payload.get("force_rerun"):
        done = _completed_standalone_runs(db, payload)
        kept = []
        for item in payloads:
            prev = done.get(item["single_skill_id"])
            if prev:
                strategy_runs.append({**prev, "skill_id": item["single_skill_id"], "status": "completed", "skipped": "already_completed"})
            else:
                kept.append(item)
        payloads_to_run = kept
    else:
        payloads_to_run = payloads
    for start in range(0, len(payloads_to_run), STANDALONE_BATCH_SIZE):
        batch = payloads_to_run[start:start + STANDALONE_BATCH_SIZE]
        queued: list[tuple[str, str]] = []
        for index, item in enumerate(batch):
            sid = item["single_skill_id"]
            try:
                res = await _run_recommendation(db, item)
                queued.append((sid, str(res.get("related_run_id") or "")))
            except Exception as exc:  # noqa: BLE001 - one strategy must not block others
                logger.exception("单策略正式推荐入队失败: %s", sid)
                db.rollback()
                strategy_runs.append({"skill_id": sid, "status": "failed", "error": str(exc)[:300]})
            if index < len(batch) - 1:
                await asyncio.sleep(STANDALONE_ENQUEUE_STAGGER_SECONDS)
        for sid, run_id in queued:
            if not run_id:
                strategy_runs.append({"skill_id": sid, "status": "failed", "error": "未返回 Run ID"})
                continue
            try:
                final = await _wait_for_recommendation_run(db, run_id, timeout_seconds=timeout)
            except Exception as exc:  # noqa: BLE001
                strategy_runs.append({"skill_id": sid, "run_id": run_id, "status": "failed", "error": str(exc)[:300]})
                continue
            status = str(final.get("status") or "failed")
            # LLM 风险备注已停用（2026-10-19 用户决定：低 QPM、无明确收益，不得拖累主流水线）。
            # 原 daemon 线程会随 Prefect 子进程退出被杀（10-14 起静默），已移除，不再调度。
            strategy_runs.append({
                "skill_id": sid,
                "run_id": run_id,
                "status": status,
                "recommended_count": int(final.get("recommended_count") or 0),
                "error": final.get("error"),
                "llm_risk_notes": "disabled",
            })
    failed = [r for r in strategy_runs if r.get("status") != "completed"]
    if failed:
        try:
            from app.core.alerts import emit_alert
            emit_alert(
                "standalone_run_failed",
                ",".join(sorted(str(r.get("skill_id")) for r in failed)),
                "单策略正式推荐失败: " + "；".join(f"{r.get('skill_id')}: {str(r.get('error'))[:120]}" for r in failed),
            )
        except Exception:  # noqa: BLE001
            logger.exception("单策略失败告警写入失败")
    completed = [r for r in strategy_runs if r.get("status") == "completed"]
    total_recs = sum(int(r.get("recommended_count") or 0) for r in completed)
    first_run_id = next((r["run_id"] for r in completed if r.get("run_id")), None) or next(
        (r["run_id"] for r in strategy_runs if r.get("run_id")), ""
    )
    final_run = {
        "status": "completed" if completed else "failed",
        "recommended_count": total_recs,
        "error": None if completed else (
            "；".join(
                (f"{r['skill_id']}: {r['error']}" if len(strategy_runs) > 1 else str(r["error"]))
                for r in strategy_runs if r.get("error")
            ) or "全部单策略 Run 失败"
        ),
    }
    return {
        "related_run_id": first_run_id,
        "related_run_ids": [r["run_id"] for r in strategy_runs if r.get("run_id")],
        "strategy_runs": strategy_runs,
        "strategy_count": len(payloads),
        "completed_count": len(completed),
        "recommended_count": total_recs,
        "final_run": final_run,
        "summary_text": f"单策略正式推荐 {len(completed)}/{len(payloads)} 个策略完成，共 {total_recs} 条",
    }


# ═══════════════════════════════════════════════════════════════
#  Formal Run 安全网：检测断档并自动补跑
# ═══════════════════════════════════════════════════════════════

def check_formal_recommendation_gap(db) -> dict:
    """检查最近 5 个已收盘交易日是否都有 completed formal。

    旧实现只分组读取 ``recommendation_runs`` 的最新一个 ``trade_date``，因此后一天
    一旦有成功运行，夹在中间的历史缺口就永久不可见；SQL 还未限定 ``run_mode``，
    experiment/backtest 也可能误阻止 formal 补跑。现在以 ``daily_kbars`` 的真实交易日
    为基准逐日核对，并返回最早缺口供历史只读行情重放。completed 的保护性 0 推荐也
    视为 pipeline 完成，质量原因由 filter audit 门禁负责，不通过反复重跑刷推荐数量。
    """
    from sqlalchemy import text as _text
    latest_allowed = _latest_allowed_bar_date()
    trade_dates = [str(r[0]) for r in db.execute(_text("""
        SELECT DISTINCT trade_date
        FROM daily_kbars
        WHERE trade_date <= :latest_allowed
        ORDER BY trade_date DESC
        LIMIT 5
    """), {"latest_allowed": latest_allowed}).fetchall()]
    if not trade_dates:
        return {"needs_retry": True, "trade_date": None, "ok_count": 0, "total_count": 0,
                "missing_dates": [], "reason": "最近无已收盘交易日数据"}

    run_rows = db.execute(_text("""
        SELECT trade_date,
               SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed_count,
               SUM(CASE WHEN status='completed' AND COALESCE(recommended_count,0)>0 THEN 1 ELSE 0 END) AS productive_count,
               SUM(CASE WHEN status IN ('queued','running') THEN 1 ELSE 0 END) AS inflight_count,
               COUNT(*) AS total_count
        FROM recommendation_runs
        WHERE run_mode='formal'
          AND trade_date IN (
              SELECT d.trade_date FROM (
                  SELECT DISTINCT trade_date FROM daily_kbars
                  WHERE trade_date <= :latest_allowed
                  ORDER BY trade_date DESC LIMIT 5
              ) d
          )
        GROUP BY trade_date
    """), {"latest_allowed": latest_allowed}).fetchall()
    by_date = {
        str(r[0]): {
            "completed_count": int(r[1] or 0),
            "productive_count": int(r[2] or 0),
            "inflight_count": int(r[3] or 0),
            "total_count": int(r[4] or 0),
        }
        for r in run_rows
    }
    missing_dates = [d for d in trade_dates if by_date.get(d, {}).get("completed_count", 0) == 0]
    if not missing_dates:
        latest = trade_dates[0]
        stats = by_date.get(latest, {})
        # 只看「有没有任何 completed」会漏掉部分策略缺批次的日子（2026-10-26：短线 4 个策略批次缺失、其余已出）。
        # 对最新交易日再按策略核对（历史日不核对：之后新增的策略不应回头补历史批次）；补跑按策略幂等，已完成的不重跑。
        missing_sids = _missing_standalone_strategies(db, latest)
        if missing_sids and not int(stats.get("inflight_count", 0)):
            return {
                "needs_retry": True,
                "trade_date": latest,
                "ok_count": int(stats.get("completed_count", 0)),
                "productive_count": int(stats.get("productive_count", 0)),
                "total_count": int(stats.get("total_count", 0)),
                "missing_dates": [],
                "missing_strategies": missing_sids,
                "reason": f"{latest} 缺 {len(missing_sids)} 个策略的 completed formal 批次：{','.join(missing_sids)}；仅补这些策略",
            }
        return {
            "needs_retry": False,
            "trade_date": latest,
            "ok_count": int(stats.get("completed_count", 0)),
            "productive_count": int(stats.get("productive_count", 0)),
            "total_count": int(stats.get("total_count", 0)),
            "missing_dates": [],
            "reason": "最近 5 个交易日均有 completed formal",
        }

    # Oldest first: one invocation repairs one session, so repeated startup/cron
    # passes converge without leaving an older hole behind newer successful days.
    target = missing_dates[-1]
    target_stats = by_date.get(target, {})
    inflight = int(target_stats.get("inflight_count", 0))
    total = int(target_stats.get("total_count", 0))
    if inflight:
        return {
            "needs_retry": False,
            "trade_date": target,
            "ok_count": 0,
            "productive_count": 0,
            "total_count": total,
            "missing_dates": missing_dates,
            "reason": f"{target} formal 补跑已在途（{inflight} 个），跳过重复派发",
        }
    return {
        "needs_retry": True,
        "trade_date": target,
        "ok_count": 0,
        "productive_count": 0,
        "total_count": total,
        "missing_dates": missing_dates,
        "reason": f"最近 5 个交易日缺 formal：{','.join(missing_dates)}；先补 {target}",
    }


def _missing_standalone_strategies(db, trade_date: str) -> list[str]:
    """trade_date 当天缺 completed formal 单策略批次的有效策略（排序）。取不到信息（假库/异常）时返回空 = 不触发。"""
    try:
        from app.services.standalone_strategies import standalone_strategy_ids
        from app.services.workbench_service import sync_skills

        expected = set(standalone_strategy_ids(sync_skills(db)))
        if not expected:
            return []
        done = _completed_standalone_runs(db, {"trade_date_override": trade_date})
        return sorted(expected - set(done))
    except Exception:  # noqa: BLE001 - 核对失败不影响原有安全网判断
        logger.debug("_missing_standalone_strategies 核对失败", exc_info=True)
        return []


def _safety_net_midlong_repair(db) -> dict:
    """盘前补跑最新已收盘交易日里 data_error（或从未记录）的中长线策略。

    只补最新一个交易日：信号日 D 收盘选股、D+1 开盘买，08:30 补出的批次仍赶得上当天开盘；更早的日期买点已过，不回头补。
    generate_picks 幂等（同日覆盖同一文件，非信号日只记 not_signal_day），实盘路径开每日新推荐上限。
    非致命：任何异常只记日志并返回 error。
    """
    out: dict = {"date": None, "retried": [], "still_error": []}
    try:
        import json as _json

        from app.services import midterm_service as ms

        latest = _latest_allowed_bar_date()
        out["date"] = latest
        status_file = ms._status_dir() / f"{latest}.json"
        stored = _json.loads(status_file.read_text()) if status_file.exists() else {}
        todo = [sid for sid in ms.STRATEGIES if (stored.get(sid) or {}).get("status") in (None, "data_error")]
        if not todo:
            return out
        out["retried"] = todo
        res = ms.generate_picks(db, latest, strategy_ids=todo, apply_daily_cap=True)
        out["still_error"] = [sid for sid in todo if ms.classify_daily_status(res.get(sid)) == "data_error"]
        out["picks"] = {sid: res.get(sid) for sid in todo if isinstance(res.get(sid), int)}
        if out["still_error"]:
            from app.core.alerts import emit_alert

            emit_alert(
                "midlong_repair_data_error",
                latest,
                f"盘前安全网补跑 {latest} 中长线后仍 data_error：{','.join(out['still_error'])}",
            )
    except Exception as exc:  # noqa: BLE001 - 补跑失败不阻断 formal 安全网主流程
        logger.warning("安全网中长线补跑失败（非阻塞）: %s", exc, exc_info=True)
        out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return out


async def _safety_net_run(db, payload: dict) -> dict:
    """安全网任务：检查 formal 推荐断档，若需要则自动补跑。

    另：若盘后流水线（post_sync_formal_recommendation）上次 failed 或超过 1 交易日
    未跑，自动触发 label_pending_outcomes 结算 pending 推荐——盘后流水线含 outcome
    结算步骤，failed/漏跑时 outcome 不结算 → 前瞻样本无法积累（D 项阻塞）。
    safety_net 每日 08:30 跑，确保至少每天补一次 outcome 结算。
    """
    # 补 outcome 结算：盘后流水线 failed/漏跑时，pending 推荐不会被自动结算。
    outcome_result = None
    try:
        postclose_task = db.get(ScheduledTask, "post_sync_formal_recommendation")
        if postclose_task:
            needs_outcome = (
                str(postclose_task.last_status or "") == "failed"
                or postclose_task.last_run_at is None
            )
            if not needs_outcome and postclose_task.last_run_at:
                from datetime import datetime as _dt
                # last_run_at 超过 1 个交易日（按 25h 估算含隔夜）未跑 → 补 outcome
                try:
                    last = postclose_task.last_run_at
                    if hasattr(last, "timestamp"):
                        age_hours = (_dt.now() - last.replace(tzinfo=None)).total_seconds() / 3600
                    else:
                        age_hours = 999
                    needs_outcome = age_hours > 25
                except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
                    logger.debug("_safety_net_run: best-effort fallback after %r", _ble_exc)
                    needs_outcome = False
            if needs_outcome:
                from app.services.outcome_service import label_pending_outcomes
                outcome_result = label_pending_outcomes(db, horizon_days=5)
                logger.info("安全网补 outcome 结算: %s", outcome_result)
    except Exception as exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
        logger.warning("安全网补 outcome 结算失败（非阻塞）: %s", exc)

    midlong = _safety_net_midlong_repair(db)
    check = check_formal_recommendation_gap(db)
    if not check["needs_retry"]:
        logger.info("安全网跳过: %s", check["reason"])
        return {"skipped": True, **check, "outcome": outcome_result, "midlong_repair": midlong,
                "summary_text": f"安全网跳过: {check['reason']}" + (f"；中长线补跑 {len(midlong['retried'])} 个" if midlong.get("retried") else "")}
    if not check.get("trade_date"):
        return {
            "status": "failed",
            "skipped": False,
            **check,
            "outcome": outcome_result,
            "error": "无已收盘交易日，无法派发 formal 补跑",
            "summary_text": "formal 安全网失败：无已收盘交易日数据",
        }

    logger.info("安全网触发补跑: %s", check["reason"])
    # 复用 post_sync_formal_recommendation 的 payload，确保参数一致
    task = db.get(ScheduledTask, "post_sync_formal_recommendation")
    if task and task.payload_json:
        run_payload = json.loads(task.payload_json)
    else:
        run_payload = {
            "run_name": "安全网补跑推荐",
            "data_source": DEFAULT_RECOMMENDATION_DATA_SOURCE,
            "risk_template": "balanced",
            "score_line": 72,
            "output_count": 6,
            "skip_aux_refresh": True,
        }
    run_payload["run_name"] = f"安全网补跑 {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%m%d_%H%M')}"
    # Historical recovery must read the target session's persisted quotes/bars,
    # never today's pool.  MarketDataStage treats trade_date_override as read-only
    # and disables upstream refresh for that snapshot.
    run_payload["trade_date_override"] = check["trade_date"]
    run_payload["data_source"] = DATA_SOURCE_LOCAL_POOL
    run_payload["skip_aux_refresh"] = True
    run_payload["run_mode"] = "formal"

    try:
        result = await _run_standalone_recommendations(db, run_payload)
    except Exception as exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
        result = {"error": str(exc)}
    run_id = str(result.get("related_run_id") or "")
    if not run_id:
        return {
            "status": "failed",
            "skipped": False,
            **check,
            **result,
            "error": "formal 安全网未返回关联 Run ID",
            "summary_text": "formal 安全网补跑派发失败：未返回关联 Run ID",
        }
    try:
        final_run = result["final_run"]
    except Exception as exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
        return {
            "status": "failed",
            "skipped": False,
            **check,
            **result,
            "related_run_id": run_id,
            "error": str(exc),
            "summary_text": f"formal 安全网补跑 {check['trade_date']} 未收敛：{str(exc)[:120]}",
        }
    if str(final_run.get("status")) != "completed":
        error = str(final_run.get("error") or f"Run 最终状态 {final_run.get('status')}")
        return {
            "status": "failed",
            "skipped": False,
            **check,
            **result,
            "related_run_id": run_id,
            "final_run": final_run,
            "error": error,
            "summary_text": f"formal 安全网补跑 {check['trade_date']} 失败：{error[:120]}",
        }
    return {
        "status": "completed",
        "skipped": False,
        **check,
        **result,
        "related_run_id": run_id,
        "final_run": final_run,
        "recommended_count": int(final_run.get("recommended_count") or 0),
        "midlong_repair": midlong,
        "summary_text": f"formal 安全网已补齐 {check['trade_date']}，Run {run_id} completed",
    }


# ═══════════════════════════════════════════════════════════════
#  低曝光策略补跑：对近30天样本不足的 enabled 策略单独跑 single_skill_id，
#  产出 experiment run，仅供 metrics_by_strategy 统计，不进记录中心/驾驶舱。
#  目的：打破「低曝光策略没样本 → 永远不被选 → 永远没样本」的死循环。
# ═══════════════════════════════════════════════════════════════


def ensure_formal_recommendation_safely() -> dict:
    """线程安全的包装：在后台线程中调用安全网检查并补跑。

    用于 startup 和外部调用。返回检查结果。
    """
    db = SessionLocal()
    try:
        check = check_formal_recommendation_gap(db)
        if not check["needs_retry"]:
            logger.info("启动安全网跳过: %s", check["reason"])
            return check

        logger.info("启动安全网触发补跑: %s", check["reason"])
        result = asyncio.run(_safety_net_run(db, {}))
        logger.info("启动安全网完成: skipped=%s", result.get("skipped", False))
        return result
    except Exception:
        logger.exception("启动安全网执行失败")
        return {"error": "safety_net_failed"}
    finally:
        db.close()


async def _run_preset_matrix(db, payload: dict, task_run_id: str | None = None) -> dict:
    """串行执行全部启用预设，并给每个推荐 Run 写入同一个矩阵批次标签。

    若提供 task_run_id，会在每个预设开始时更新 ScheduledTaskRun.summary，
    供前端矩阵页轮询显示阶段进度（已完成 N/M 预设: 当前预设名）。
    """
    from app.services.workbench_service import run_workbench

    presets = db.scalars(
        select(Preset)
        .where(Preset.status == "active")
        .order_by(Preset.created_at.asc(), Preset.name.asc())
    ).all()
    matrix_batch_id = str(
        payload.get("matrix_batch_id")
        or f"matrix_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y%m%d_%H%M%S')}"
    )
    matrix_batch_name = str(payload.get("matrix_batch_name") or "盘后预设矩阵")
    run_name_prefix = str(payload.get("run_name_prefix") or matrix_batch_name)
    data_source = payload.get("data_source") or DEFAULT_RECOMMENDATION_DATA_SOURCE
    stop_on_error = bool(payload.get("stop_on_error", False))

    total = len(presets)
    rows: list[dict[str, object]] = []
    recommended_total = 0
    for index, preset in enumerate(presets, start=1):
        if task_run_id:
            _update_matrix_progress(
                db,
                task_run_id,
                summary=f"执行预设 {index}/{total}: {preset.name}（已完成 {index - 1}/{total}）",
            )
        run_payload = {
            "preset_id": preset.preset_id,
            "run_name": f"{run_name_prefix} · {preset.name}",
            "data_source": data_source,
            "matrix_batch_id": matrix_batch_id,
            "matrix_batch_name": matrix_batch_name,
            "matrix_run": True,
        }
        for key in (
            "risk_template",
            "score_line",
            "output_count",
            "selected_skill_ids",
            "skip_aux_refresh",
            "disable_agent_review",
            "agent_review_limit",
            "agent_review_concurrency",
            "run_mode",
        ):
            if key in payload:
                run_payload[key] = payload.get(key)
        try:
            result = await run_workbench(db, run_payload)
            run = result.get("run") or {}
            run_summary = run.get("result_summary") or {}
            recommended_count = int(run.get("recommended_count") or 0)
            recommended_total += recommended_count
            _slm = run_summary.get("score_line_meta") or {}
            _pipeline = run_summary.get("pipeline") or {}
            _scored = (
                _pipeline.get("scored_count")
                or _pipeline.get("skill_pass_count")
                or _pipeline.get("skill_gate_candidate_count")
                or _pipeline.get("actual_scan_count")
            )
            rows.append(
                {
                    "preset_id": preset.preset_id,
                    "preset_name": preset.name,
                    "run_id": str(run.get("run_id") or ""),
                    "status": "completed",
                    "recommended_count": recommended_count,
                    "universe_count": int(run.get("universe_count") or 0),
                    "scored_count": int(_scored) if _scored is not None else None,
                    "effective_score_line": _slm.get("effective_score_line"),
                    "market_base": _slm.get("market_base"),
                    "market_sentiment": _slm.get("market_sentiment"),
                    "top_candidate_score": run_summary.get("top_candidate_score"),
                    "climax_watchlist": run_summary.get("climax_watchlist") or [],
                    "result_summary": run_summary,
                }
            )
        except Exception as exc:
            logger.exception("预设矩阵批次 %s 运行失败: %s", matrix_batch_id, preset.preset_id)
            db.rollback()
            rows.append(
                {
                    "preset_id": preset.preset_id,
                    "preset_name": preset.name,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            if stop_on_error:
                break

    completed_count = sum(1 for row in rows if row.get("status") == "completed")
    failed_count = sum(1 for row in rows if row.get("status") == "failed")
    # 聚合行情状态与观察池，供前端矩阵报告展示「正确静默」而非误判为 bug
    _regime_rows = [r for r in rows if r.get("market_base")]
    market_base = _regime_rows[0]["market_base"] if _regime_rows else None
    market_sentiment = _regime_rows[0]["market_sentiment"] if _regime_rows else None
    market_regime = f"{market_base}·{market_sentiment}" if market_base and market_sentiment else None
    aggregated_watchlist: list[dict[str, object]] = []
    _seen_codes: set[str] = set()
    for r in rows:
        for c in (r.get("climax_watchlist") or []):
            code = c.get("code")
            if code and code not in _seen_codes:
                _seen_codes.add(code)
                aggregated_watchlist.append(c)
    aggregated_watchlist.sort(key=lambda x: float(x.get("final_score") or 0), reverse=True)
    top_candidate_score = max(
        (float(c.get("final_score") or 0) for c in aggregated_watchlist), default=0.0
    )
    if not top_candidate_score:
        top_candidate_score = max(
            (float(r.get("top_candidate_score") or 0) for r in rows if r.get("top_candidate_score")),
            default=0.0,
        )
    return {
        "matrix_batch_id": matrix_batch_id,
        "matrix_batch_name": matrix_batch_name,
        "preset_count": len(presets),
        "run_count": len(rows),
        "completed_count": completed_count,
        "failed_count": failed_count,
        "recommended_count": recommended_total,
        "runs": rows,
        "market_regime": market_regime,
        "market_base": market_base,
        "market_sentiment": market_sentiment,
        "top_candidate_score": round(top_candidate_score, 2) if top_candidate_score else None,
        "aggregated_watchlist": aggregated_watchlist,
        "all_zero": recommended_total == 0,
        "summary_text": (
            f"矩阵批次 {completed_count}/{len(presets)} 个预设完成，"
            f"失败 {failed_count} 个，推荐合计 {recommended_total} 条"
        ),
    }
