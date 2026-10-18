"""Pure helpers extracted from app.services.scheduler_service (no monkeypatched names; re-exported there)."""
from __future__ import annotations
import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.tables import Preset, RecommendationRun, ScheduledTask, ScheduledTaskRun
from app.services.cost_service import finish_task_run, serialize_scheduled_task_run, start_task_run
from app.engine.trading_calendar import latest_allowed_bar_date as _latest_allowed_bar_date
from app.engine.trading_calendar import previous_weekday
from app.services.registry.data_source import DATA_SOURCE_LOCAL_POOL, DEFAULT_RECOMMENDATION_DATA_SOURCE
import logging

logger = logging.getLogger('app.services.scheduler_service')


_running_lock = threading.Lock()


_running_tasks: set[str] = set()


TASK_TYPE_PRESET_MATRIX_RUN = "preset_matrix_run"


TASK_TYPE_FORMAL_SAFETY_NET = "formal_safety_net"


TASK_TYPE_STRATEGY_EXPOSURE_RUN = "strategy_exposure_run"


TASK_TYPE_OUTCOME_LABEL = "outcome_label"


MANUAL_PRESET_MATRIX_TASK_ID = "manual_preset_matrix_run"


MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID = "manual_data_health_remediation"


SAFETY_NET_TASK_ID = "formal_run_safety_net"


# Kept as a compatibility/export constant for callers that display the old
# retry budget.  Missing trading sessions are no longer abandoned permanently
# after this many failed runs; the safety net suppresses only an in-flight retry.
SAFETY_NET_MAX_ATTEMPTS = 3


POSTCLOSE_PIPELINE_TASK_ID = "post_sync_formal_recommendation"


POSTCLOSE_LATE_DATA_REPAIR_TASK_ID = "postclose_late_data_repair"


# 工作日使用 mon-fri，避免不同 cron 实现对数字星期的解释差异。
# 与标准 crontab 的 0=Sunday 差一天，"1-5" 会被解析成「周二到周六」——周一永不触发、
# 周六反而跑。名称形式在两种语义下都无歧义，是唯一安全写法。
POSTCLOSE_PIPELINE_CRON = "35 17 * * mon-fri"


POSTCLOSE_LATE_DATA_REPAIR_CRON = "0 21 * * mon-fri"


LEGACY_POSTCLOSE_TASK_IDS = (
    "postclose_quote_refresh",
    "postclose_daily_kbar_batch",
    "postclose_data_health_remediation",
    "postclose_auxiliary_data_sync",
    "postclose_outcome_label",
)


# 仅这两类当日辅助数据存在盘后上游延迟，允许交给 Prefect 有界重试。
# daily_kbar/stock_quote 以及历史覆盖缺口都属于硬阻断，不能靠重复刷新掩盖。
_RECOVERABLE_POSTCLOSE_AUX_TABLES = frozenset({"index_daily", "limit_up_board"})


class RetryablePostcloseReadinessError(RuntimeError):
    """盘后辅助数据尚未发布；由 Prefect 在新短会话中有界重试。"""

    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__(str(result.get("summary_text") or "盘后辅助数据暂未就绪"))


# 策略曝光补跑调度任务（E 项样本可信前置）：盘前对近30天样本不足的策略单独补跑。
# 任务 ID 固定，由 Prefect deployments 幂等同步（已存在则不覆盖用户手动调整的 cron/payload）。
EXPOSURE_TASK_ID = "strategy_exposure_run"


EXPOSURE_CRON = "3 8 * * mon-fri"  # 周一到周五 08:03（盘前，避免与盘后结算/回填争抢资源）


# ── 盘中锁利影子链路（只记录不交易，见 intraday_lock_service）────────────────
# 调度时机由 outcome 出入场时序决定（见 outcome_simulation.py §51-53 + T+1 锁仓）：
# 盘后推荐（post_sync_formal_recommendation 17:35）→ T+1 入场（当天锁仓不可卖，
# outcome 暂记 provisional=今收占位）→ T+2 才是首个可卖日。因此：
#   create  每日 09:20 盘前建监控，目标=trade_date 为前天（T+2）的已入场持仓
#           —— 昨天入场、今天首个可卖日，正是盘中冲高回落的高发日；
#   poll    交易时段每 5min 由 Prefect intraday-lock-poll flow 轮询，
#           内部自判交易时段+交易日，跨过午休，无需 cron 罗列交易时刻；
#   settle  每日 17:40 盘后回填今日触发信号的 post_trigger 走势 + 当时实际出场占位
#           （持仓未结时占位=provisional 今收；待 outcome 翻新为真实 exit 后重算 lock_delta）。
TASK_TYPE_INTRADAY_LOCK_SHADOW = "intraday_lock_shadow"


LOCK_CREATE_TASK_ID = "intraday_lock_create_monitors"


LOCK_CREATE_CRON = "20 9 * * mon-fri"   # 盘前 09:20 建监控（当天首个可卖日持仓）


LOCK_SETTLE_TASK_ID = "intraday_lock_settle_signals"


LOCK_SETTLE_CRON = "40 17 * * mon-fri"  # 盘后 17:40 回填（当日K已就绪，outcome 占位可读）


# 盘中轮询间隔（秒）：与 stk_mins 5min 频率对齐。
LOCK_POLL_INTERVAL_SECONDS = 300


def _is_task_running(task_id: str) -> bool:
    with _running_lock:
        return task_id in _running_tasks


def _mark_running_memory(task_id: str) -> None:
    with _running_lock:
        _running_tasks.add(task_id)


def _try_mark_running_memory(task_id: str) -> bool:
    with _running_lock:
        if task_id in _running_tasks:
            return False
        _running_tasks.add(task_id)
        return True


def _mark_done_memory(task_id: str) -> None:
    with _running_lock:
        _running_tasks.discard(task_id)


def _postclose_pipeline_payload(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.services.data_gap_remediation_service import (
        AUXILIARY_REMEDIATION_TABLES,
        HEALTH_REMEDIATION_TABLES,
    )

    payload = dict(existing or {})
    payload.update(
        {
            "pre_run_full_sync": True,
            "sync_scope": "all",
            "preset_id": payload.get("preset_id") or "preset_formal_opening",
            "run_name": payload.get("run_name") or "盘后全量同步后正式推荐",
            "run_mode": "formal",
            # 同步和健康闸门已在推荐前完成；正式推荐只读本地池，
            # 避免 local_pool_preferred 在查询阶段再次访问上游。
            "data_source": DATA_SOURCE_LOCAL_POOL,
            "risk_template": payload.get("risk_template") or "balanced",
            "score_line": float(payload.get("score_line") or 72),
            "output_count": int(payload.get("output_count") or 6),
            "skip_aux_refresh": True,
            # 两份清单从服务侧权威定义派生，不写字面量：往 DEFAULT_REMEDIABLE_TABLES
            # 新登记可修复项时自动生效，避免自动作业规划不出动作（L-150）。
            "health_remediation_tables": list(HEALTH_REMEDIATION_TABLES),
            "auxiliary_remediation_tables": list(AUXILIARY_REMEDIATION_TABLES),
            "health_post_diagnosis": True,
            "auxiliary_post_diagnosis": False,
            "horizon_days": int(payload.get("horizon_days") or 5),
            "recommendation_wait_timeout_seconds": int(
                payload.get("recommendation_wait_timeout_seconds") or 3600
            ),
        }
    )
    return payload


def _late_data_repair_payload(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.services.data_gap_remediation_service import (
        AUXILIARY_REMEDIATION_TABLES,
        HEALTH_REMEDIATION_TABLES,
    )

    payload = dict(existing or {})
    payload.update(
        {
            "sync_scope": "all",
            # 与盘后推荐同一口径，从服务侧权威定义派生（L-150）。
            "health_remediation_tables": list(HEALTH_REMEDIATION_TABLES),
            "auxiliary_remediation_tables": list(AUXILIARY_REMEDIATION_TABLES),
            "health_post_diagnosis": True,
            "auxiliary_post_diagnosis": False,
        }
    )
    return payload


# 安全网任务 cron（每日 08:30 检查最近 5 个交易日的 formal 缺口并补跑）。
SAFETY_NET_CRON = "30 8 * * mon-fri"


# 预设下的 Skill 全部被禁用时，enqueue_workbench_run 抛出的错误文案。
# 这是「预期跳过」而非「故障」：定时任务不应因此污染 ERROR 日志或标记为 failed。
_NO_RUNNABLE_SKILL_MSG = "没有可运行的 Skill"


def _recommendation_run_error_to_skip(task_id: str, payload: dict, exc: Exception) -> dict | None:
    """将『预设无可用 Skill』的 ValueError 转化为优雅跳过结果。

    返回 skip 结果 dict（调用方应直接使用），或 None（表示异常需继续抛出）。
    仅匹配无可用 Skill 这一特定、可预期的配置态；其它 ValueError 一律透传。
    """
    if isinstance(exc, ValueError) and _NO_RUNNABLE_SKILL_MSG in str(exc):
        preset_id = payload.get("preset_id")
        logger.warning(
            "调度任务 %s 跳过：预设 %s 下无可用(已启用) Skill，属预期跳过而非故障",
            task_id, preset_id,
        )
        return {
            "status": "skipped",
            "skipped": True,
            "summary_text": f"预设 {preset_id} 无可用 Skill（已禁用），已跳过",
        }
    return None


def _default_data_health_remediation_payload(payload: dict) -> dict:
    from app.services.data_gap_remediation_service import DEFAULT_DAILY_KBAR_CODE_LIMIT, DEFAULT_REMEDIABLE_TABLES

    return {
        "target_tables": payload.get("target_tables") or list(DEFAULT_REMEDIABLE_TABLES),
        "capital_flow_code_limit": int(payload.get("capital_flow_code_limit", 200)),
        "daily_kbar_code_limit": int(payload.get("daily_kbar_code_limit", DEFAULT_DAILY_KBAR_CODE_LIMIT)),
        "minute_kbar_code_limit": int(payload.get("minute_kbar_code_limit", 300)),
        "post_diagnosis": bool(payload.get("post_diagnosis", True)),
    }


def _data_health_remediation_summary(result: dict) -> str:
    actions = result.get("actions") if isinstance(result, dict) else None
    action_count = len(actions) if isinstance(actions, list) else 0
    summary = result.get("action_summary") or {} if isinstance(result, dict) else {}
    failed_count = int(summary.get("failed_count") or 0)
    empty_count = int(summary.get("empty_count") or 0)
    comparison = result.get("comparison") if isinstance(result, dict) else None
    empty_suffix = f"，空结果待重试 {empty_count}" if empty_count else ""
    if isinstance(comparison, dict) and comparison.get("status") != "skipped":
        resolved = int(comparison.get("resolved_missing_day_count") or 0)
        target = int(comparison.get("target_missing_day_count") or 0)
        unresolved = int(comparison.get("unresolved_missing_day_count") or 0)
        unresolved_suffix = f"，仍缺 {unresolved}" if unresolved else ""
        return f"数据补数完成 {action_count} 项，修复缺口 {resolved}/{target}{unresolved_suffix}，失败 {failed_count}{empty_suffix}"
    return f"数据补数完成 {action_count} 项，失败 {failed_count}{empty_suffix}"


def _default_matrix_payload(payload: dict) -> dict:
    result = {
        "matrix_batch_name": str(payload.get("matrix_batch_name") or "手工预设矩阵"),
        "run_name_prefix": str(payload.get("run_name_prefix") or payload.get("matrix_batch_name") or "手工预设矩阵"),
        "data_source": payload.get("data_source") or DEFAULT_RECOMMENDATION_DATA_SOURCE,
    }
    for key, value in payload.items():
        if key not in result:
            result[key] = value
    return result


MANUAL_STRATEGY_EXPOSURE_TASK_ID = "manual_strategy_exposure_run"


# Restored helpers (dropped by 9c308a6 while still referenced -> NameError at runtime).
def _default_sync_scope(task_type: str) -> str:
    if task_type == "data_backfill":
        return "daily_bars"
    if task_type == "pool_refresh":
        return "quotes"
    return "all"


def _parse_backfill_stdout(stdout: str) -> dict:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    parsed_scope = _parse_stats_line(next((line for line in lines if line.startswith("scope=")), ""))
    parsed_result = _parse_stats_line(next((line for line in reversed(lines) if line.startswith("processed=")), ""))
    processed = _to_int(parsed_result.get("processed"))
    persisted = _to_int(parsed_result.get("persisted"))
    short_history = _to_int(parsed_result.get("short_history"))
    failed = _to_int(parsed_result.get("failed"))
    summary_text = (
        f"处理 {processed}，入库 {persisted}，短历史 {short_history}，失败 {failed}"
        if processed is not None
        else (lines[-1] if lines else "日K回填完成")
    )
    return {
        "scope": parsed_scope.get("scope"),
        "universe": _to_int(parsed_scope.get("universe")),
        "ready_or_progress": _to_int(parsed_scope.get("ready_or_progress")),
        "pending": _to_int(parsed_scope.get("pending")),
        "processed": processed,
        "persisted": persisted,
        "short_history": short_history,
        "failed": failed,
        "elapsed_seconds": _to_float(parsed_result.get("elapsed")),
        "stdout_tail": "\n".join(lines[-10:]),
        "summary_text": summary_text,
    }


def _parse_stats_line(line: str) -> dict[str, str]:
    if not line:
        return {}
    parts: dict[str, str] = {}
    for token in line.replace("|", " ").split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        parts[key.strip()] = value.strip()
    return parts


def _to_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value.rstrip("s"))
    except ValueError:
        return None


# 已知的长期数据源缺口：上游永久无权限或接口长期不可用，且已有替代来源。
# 这些数据集失败只告警（status=known_gap），不再把整次补数任务记为 partial_failed；
# 其余数据集失败（含可重试的覆盖不足）仍按 failed 处理。
_KNOWN_AUX_SOURCE_GAPS: dict[str, str] = {
    "news_announcement": "Tushare anns_d 40203 无权限；公告改由东方财富/巨潮/xiaoshi 补数链覆盖",
    "share_float": "promax share_float invalid_params、官方无权限；改由 xiaoshi restricted_unlock 兜底",
    "holder_trade": "promax stk_holdertrade invalid_params / unknown_schema，长期不可用",
    "broker_recommend": "promax invalid_params、官方无权限，已从这两源路由排除；仅 quantdata 可用但该源在数据源管理中停用（未被任何策略使用）",
}


# 已知长期缺口中耗时显著的数据集（实测 2026-10-15：holder_trade 单独约 17.5s，结果恒为 0 行失败）。
# 非周一跳过（DB 输出不变：失败时本就 0 行写入）；周一照常请求，用于发现上游恢复。
_SLOW_KNOWN_GAPS: frozenset[str] = frozenset({"holder_trade"})


def _skip_slow_known_gaps(datasets: tuple[str, ...], trade_date: str) -> tuple[tuple[str, ...], list[str]]:
    from datetime import date as _date

    try:
        probe_day = _date.fromisoformat(str(trade_date)[:10]).weekday() == 0
    except ValueError:
        probe_day = True
    if probe_day:
        return datasets, []
    skipped = [d for d in datasets if d in _SLOW_KNOWN_GAPS and d in _KNOWN_AUX_SOURCE_GAPS]
    return tuple(d for d in datasets if d not in skipped), skipped


def _batch_sync_item_status(dataset_key: str, errors) -> str:
    if not errors:
        return "success"
    return "known_gap" if dataset_key in _KNOWN_AUX_SOURCE_GAPS else "failed"


def _task_result_status(result: dict | None) -> str:
    if not isinstance(result, dict):
        return "completed"
    status = str(result.get("status") or "").strip()
    if status == "skipped":
        return "skipped"
    if status == "completed_post_diagnosis_failed":
        return "partial_completed"
    if status in {"failed", "partial_failed", "partial_completed", "diagnosis_failed", "post_diagnosis_failed"}:
        return status
    step_statuses = [
        str(step.get("status") or "").strip()
        for step in (result.get("steps") or [])
        if isinstance(step, dict)
    ]
    if any(status == "failed" for status in step_statuses):
        return "failed"
    if any(status in {"partial_failed", "diagnosis_failed", "post_diagnosis_failed"} for status in step_statuses):
        return "partial_failed"
    if any(status in {"partial_completed", "completed_post_diagnosis_failed"} for status in step_statuses):
        return "partial_completed"
    if any(_action_summary_count(result.get(key), "empty_count") for key in ("daily_kbar_batch", "health_remediation", "auxiliary")):
        return "partial_completed"
    return "completed"


def _task_result_error(result: dict | None) -> str | None:
    """Preserve a returned failure's cause in scheduler lifecycle columns.

    Pipeline stages intentionally return structured ``status=failed`` results so
    completed sync/readiness evidence is not lost.  Treating that as a normal
    return previously left ``scheduled_tasks.last_error`` empty, making the next
    recovery pass unable to distinguish a data gate from a worker timeout.
    """
    if _task_result_status(result) not in {
        "failed",
        "partial_failed",
        "diagnosis_failed",
        "post_diagnosis_failed",
    }:
        return None
    if not isinstance(result, dict):
        return None
    return str(result.get("error") or result.get("summary_text") or "调度任务返回失败")


def _sync_step_summary(label: str, result: dict) -> str:
    summary = result.get("action_summary") or {}
    completed = int(summary.get("completed_count") or 0)
    failed = int(summary.get("failed_count") or 0)
    empty = int(summary.get("empty_count") or 0)
    skipped = int(summary.get("skipped_count") or 0)
    parts = [f"{label} {completed}"]
    if empty:
        parts.append(f"空结果待重试 {empty}")
    if skipped:
        parts.append(f"跳过 {skipped}")
    parts.append(f"失败 {failed}")
    return " / ".join(parts)


def _action_summary_count(result: object, key: str) -> int:
    if not isinstance(result, dict):
        return 0
    summary = result.get("action_summary")
    if not isinstance(summary, dict):
        return 0
    return int(summary.get(key) or 0)


# 按 trade_date 批量拉取的数据集：state_adapters 对这些数据集用
# pro.xxx(trade_date=target) 一次拉全市场，天然支持逐日回填。
# 排除事件型/月度型/季频数据集（broker_recommend/holder_number/fina_indicator/
# pledge_stat/share_float/holder_trade/news_announcement/auction），它们不适合
# 也不需要按交易日逐日批量回填。auction 受 API 权限限制，单独跳过。
_AUXILIARY_TRADE_DATE_BATCH_DATASETS = (
    "limit_up_board",
    "concept_sector",
    "concept_sector_stock",
    "capital_flow",
    "dragon_tiger",
    "margin_detail",
    "hsgt_flow",
    "hsgt_top10",
    "block_trade",
)


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        import math
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _classify_postclose_blockers(
    readiness: dict[str, Any],
    *,
    trade_date: str,
) -> dict[str, Any]:
    """按结构化契约区分可恢复的当日辅助缺口与硬阻断。

    仅 ``table_not_current`` 不足以证明是发布滞后；同一辅助表还必须有最近覆盖
    诊断，且明确显示唯一缺失日期就是本次 ``trade_date``。
    """
    details = list(readiness.get("blocker_details") or [])
    if not details:
        return {
            "recoverable_tables": [],
            "hard_blockers": [
                {
                    "code": "unstructured_blocker",
                    "message": message,
                }
                for message in (readiness.get("blockers") or ["未知数据健康问题"])
            ],
        }

    by_table: dict[str, list[dict[str, Any]]] = {}
    hard_blockers: list[dict[str, Any]] = []
    for detail in details:
        table = str(detail.get("table") or "")
        if not table:
            hard_blockers.append(detail)
            continue
        by_table.setdefault(table, []).append(detail)

    recoverable_tables: list[str] = []
    for table, table_details in by_table.items():
        current_detail = next(
            (
                detail
                for detail in table_details
                if detail.get("code") == "table_not_current"
                and str(detail.get("expected_trade_date") or "") == trade_date
            ),
            None,
        )
        coverage_detail = next(
            (
                detail
                for detail in table_details
                if detail.get("code") == "recent_coverage_gap"
                and str(detail.get("expected_trade_date") or "") == trade_date
                and set(detail.get("missing_dates") or []) == {trade_date}
                and not detail.get("incomplete_dates")
            ),
            None,
        )
        allowed_codes = {"table_not_current", "recent_coverage_gap"}
        only_expected_codes = all(detail.get("code") in allowed_codes for detail in table_details)
        if (
            table in _RECOVERABLE_POSTCLOSE_AUX_TABLES
            and current_detail is not None
            and coverage_detail is not None
            and only_expected_codes
        ):
            recoverable_tables.append(table)
        else:
            hard_blockers.extend(table_details)

    return {
        "recoverable_tables": sorted(recoverable_tables),
        "hard_blockers": hard_blockers,
    }


# 单策略独立运行：同时最多并发几个全市场扫描子进程（避免 CPU 争用/心跳超时）。
STANDALONE_BATCH_SIZE = 4


# 串行 enqueue 时每个策略之间的间隔，避免同时拉起多个全市场扫描子进程造成 CPU 尖峰/心跳超时。
EXPOSURE_ENQUEUE_STAGGER_SECONDS = 8


def _update_matrix_progress(db, task_run_id: str, summary: str) -> None:
    """更新矩阵任务的阶段进度摘要，供前端轮询显示。

    单独 commit 避免长事务占着连接；失败只记日志，不中断矩阵运行。
    """
    try:
        run = db.get(ScheduledTaskRun, task_run_id)
        if run is None or run.status != "running":
            return
        run.summary = summary[:500]
        run.finished_at = run.finished_at  # 触发 ORM 脏标记
        db.commit()
    except Exception:
        logger.exception("更新矩阵进度失败: %s", task_run_id)
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
            logger.debug("_update_matrix_progress: ignored non-fatal error: %s", exc)
