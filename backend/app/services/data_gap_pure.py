"""Pure helpers extracted from app.services.data_gap_remediation_service (no monkeypatched names; re-exported there)."""
from __future__ import annotations
import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta
from typing import Any, Callable
from sqlalchemy import func, select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.engine.trading_calendar import latest_allowed_bar_date as _latest_allowed_bar_date, normalize_trade_date as _normalize_trade_date
from app.services.market_pool.history_maturity import is_natural_short_history as _is_natural_short_history, load_history_maturity_map as _load_history_maturity_map
from app.models.tables import DailyKbar, IndexDaily, LimitUpBoard, MinuteKbar, NewsAnnouncement, Recommendation, RecommendationRun, StockQuote
from app.services.data_health_service import clear_data_health_cache, diagnose_all
from app.services.feature_builder import _compute_authenticity, _enrich_with_auxiliary_data, _tag_features
from app.services.auxiliary.application import load_auxiliary_feature_views, run_auxiliary_application
from app.services.market_pool.core import _candidate_recall_plan, _eligible_market_quotes, _execution_recall_count, _load_cached_bars_summary, _load_cached_quotes, _quote_sources, _select_recall_candidates
from app.engine.trading_calendar import latest_allowed_bar_date as _pool_latest_allowed_bar_date
from app.services.market_pool.core import _required_bar_date_for_quote as _pool_required_bar_date_for_quote
from app.services.research_context import build_research_context
from app.services.data_sources import enabled_sources
from app.services.remediation import transaction as remediation_transaction
from app.adapters.market_source import TushareMarketSourceAdapter
from app.services.market_source.port import DailyBarRequest, MarketSourcePort, QuoteRef
from app.services.tushare_client import fetch_index_daily, fetch_minute_kbars, get_pro, get_tushare_provider_health
from app.services.daily_bar_write_policy import normalize_quote_metric, quote_metrics_look_unready


DEFAULT_REMEDIABLE_TABLES = (
    "daily_kbar",
    "chip",
    "minute_kbar",
    "index_daily",
    "limit_up_board",
    "capital_flow",
    "concept_sector",
    "dragon_tiger",
    "news",
    # 概念板块成分是独立数据域，此前未登记进可修复清单，最新日期滞后时按钮无从下手。
    "concept_member",
    # 值分布失真：把健康度的值分布哨兵接到修复路径，让「有数据但值是错的」也能被按钮修
    # （此前哨兵只接评分路径，13 分扣分按一次也修不掉，L-146）。
    "value_distribution",
)


# minute_kbar 由计划显式处理并保留失败审计；缺少供应商权限时不会阻止其余
# 日线与辅助数据的补数动作。
DEFAULT_INDEX_TS_CODES = (
    "000001.SH",
    "000300.SH",
    "000905.SH",
    "000852.SH",
    "399001.SZ",
    "399006.SZ",
)


_DAILY_KBAR_UPSERT_BATCH_SIZE = 200


DEFAULT_DAILY_KBAR_CODE_LIMIT = 6000


DEFAULT_LOCAL_POOL_GAP_CODE_LIMIT = 0


DEFAULT_LOCAL_POOL_GAP_DAYS = 90


DEFAULT_LOCAL_POOL_MIN_BARS = 30


# 分钟补数的外部请求彼此独立；有界并行显著缩短 300 只/交易日的等待时间，
# 同时避免无限制并发冲击 xiaoshi/Tushare 的上游限流。
MINUTE_REMEDIATION_WORKERS = 8


MINUTE_REMEDIATION_PROBE_COUNT = 3


AUXILIARY_GAP_FIELDS = {
    "announcements",
    "capital_flow",
    "dragon_tiger",
    "main_net_inflow",
    "news",
    "theme_tags",
}


AUXILIARY_REMEDIABLE_TABLES = {"capital_flow", "concept_sector", "dragon_tiger", "news", "concept_member"}


# 盘后作业把可修复清单分成两半分别交给两个执行器：辅助数据集走
# `_run_auxiliary_remediation`（补完还要跑 run_auxiliary_application），其余走
# `_run_data_health_remediation`。两半必须并起来等于 DEFAULT_REMEDIABLE_TABLES，
# 否则新登记的可修复项在自动路径上永远规划不出动作——L-146 把 concept_member 与
# value_distribution 加进默认清单后，盘后作业因为传的是硬编码显式清单而依旧修不了
# turnover_rate 恒同值，手工按按钮却能修（L-150）。
AUXILIARY_REMEDIATION_TABLES = tuple(
    name for name in DEFAULT_REMEDIABLE_TABLES if name in AUXILIARY_REMEDIABLE_TABLES
)


HEALTH_REMEDIATION_TABLES = tuple(
    name for name in DEFAULT_REMEDIABLE_TABLES if name not in AUXILIARY_REMEDIABLE_TABLES
)


# 值分布哨兵报出的失真列按修复手段分两类：权威行情列重新拉当日全市场日线覆盖回去，
# 本地派生列交给本地指标补数脚本重算。两类合起来正好覆盖哨兵登记的全部 12 列（L-146）。
QUOTE_METRIC_REPAIR_COLUMNS = ("close", "volume", "amount", "pct_chg", "turnover_rate")


LOCAL_DERIVED_REPAIR_COLUMNS = (
    "ma5",
    "ma20",
    "bias_5",
    "vol_ratio_5",
    "max_dd20",
    "upper_shadow_count20",
    "breakout20",
)


def _market_source_audit_payload(batch: Any) -> dict[str, Any]:
    """Project typed Port evidence into remediation-safe operation logs."""

    audit = batch.audit
    return {
        "source": audit.source,
        "operation": audit.operation.value,
        "status": audit.status.value,
        "requested_count": audit.requested_count,
        "returned_count": audit.returned_count,
        "empty_count": audit.empty_count,
        "failed_count": audit.failed_count,
        "failure_policy": audit.failure_policy.value,
        "errors": [
            {
                "code": error.code.value,
                "message": error.message,
                "reference": error.reference.code if error.reference else None,
                "retryable": error.retryable,
            }
            for error in batch.errors
        ],
    }


def _provider_activity_delta(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    providers: list[dict[str, Any]] = []
    used_providers: list[str] = []
    for name in sorted(set(before) | set(after)):
        before_row = before.get(name, {})
        after_row = after.get(name, {})
        success_delta = max(
            0,
            int(after_row.get("success_count") or 0)
            - int(before_row.get("success_count") or 0),
        )
        failure_delta = max(
            0,
            int(after_row.get("failure_count") or 0)
            - int(before_row.get("failure_count") or 0),
        )
        empty_delta = max(
            0,
            int(after_row.get("empty_count") or 0)
            - int(before_row.get("empty_count") or 0),
        )
        request_count = success_delta + failure_delta + empty_delta
        source = str(after_row.get("source") or before_row.get("source") or name)
        if request_count:
            used_providers.append(source)
        providers.append(
            {
                "source": source,
                "provider_name": name,
                "request_count": request_count,
                "success_count": success_delta,
                "failure_count": failure_delta,
                "empty_count": empty_delta,
                "last_api": after_row.get("last_api"),
                "status": after_row.get("status"),
                "breaker_state": after_row.get("breaker_state"),
            }
        )
    return {
        "used_providers": used_providers,
        "providers": providers,
        "route_policy": "通用接口按数据源配置优先级路由；特殊 API 按能力路由；失败时安全 fallback。",
        "minute_scope": "仅修复诊断窗口内缺失交易日，默认最多 300 只；不触发三年回填。",
    }


def _action_timing_entry(item: dict[str, Any], executed: dict[str, Any], elapsed: float) -> dict[str, Any]:
    """单个补数动作的计时摘要（纯观测，不进入任何写库/选股逻辑）。"""
    entry: dict[str, Any] = {
        "table": str(item.get("table") or "?"),
        "status": str(executed.get("status") or ""),
        "seconds": round(elapsed, 2),
    }
    if item.get("trade_date"):
        entry["trade_date"] = str(item.get("trade_date"))
    target_dates = item.get("target_dates")
    if isinstance(target_dates, (list, tuple)):
        entry["target_date_count"] = len(target_dates)
    codes = item.get("codes")
    if isinstance(codes, (list, tuple)):
        entry["code_count"] = len(codes)
    return entry


def _local_pool_operation_summary(operation_log: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(item.get("status") or "unknown") for item in operation_log)
    failure_counts: Counter[tuple[str, str]] = Counter()
    for item in operation_log:
        if item.get("status") != "failed":
            continue
        error_type = str(item.get("error_type") or "Error")
        message = _compact_error_message(item.get("error"))
        failure_counts[(error_type, message)] += 1

    return {
        "total_log_count": len(operation_log),
        "visible_log_count": min(len(operation_log), 50),
        "status_counts": dict(status_counts),
        "failure_reasons": [
            {"error_type": error_type, "message": message, "count": count}
            for (error_type, message), count in failure_counts.most_common(3)
        ],
    }


def _compact_error_message(value: Any) -> str:
    raw = str(value or "").strip().replace("\n", " ")
    if len(raw) <= 140:
        return raw
    return f"{raw[:137]}..."


def _normalize_target_tables(target_tables: list[str] | None) -> list[str]:
    if not target_tables:
        return list(DEFAULT_REMEDIABLE_TABLES)
    normalized = []
    for item in target_tables:
        key = str(item or "").strip()
        if key and key in DEFAULT_REMEDIABLE_TABLES and key not in normalized:
            normalized.append(key)
    return normalized or list(DEFAULT_REMEDIABLE_TABLES)


def _normalize_auxiliary_target_tables(target_tables: list[str] | None) -> list[str]:
    if not target_tables:
        return sorted(AUXILIARY_REMEDIABLE_TABLES)
    normalized = []
    for item in target_tables:
        key = str(item or "").strip()
        if key in AUXILIARY_REMEDIABLE_TABLES and key not in normalized:
            normalized.append(key)
    return normalized or sorted(AUXILIARY_REMEDIABLE_TABLES)


def _build_recommendation_auxiliary_actions(
    *,
    before: dict[str, Any],
    codes: list[str],
    trade_date: str,
    requested_tables: list[str],
) -> list[dict[str, Any]]:
    missing_counts = before.get("missing_field_counts") if isinstance(before.get("missing_field_counts"), dict) else {}
    actions: list[dict[str, Any]] = []
    if "capital_flow" in requested_tables and (
        int(missing_counts.get("capital_flow") or 0) or int(missing_counts.get("main_net_inflow") or 0)
    ):
        actions.append({"table": "capital_flow", "trade_date": trade_date, "codes": codes})
    if "concept_sector" in requested_tables and int(missing_counts.get("theme_tags") or 0):
        actions.append({"table": "concept_sector", "trade_date": trade_date})
    if "dragon_tiger" in requested_tables and int(missing_counts.get("dragon_tiger") or 0):
        actions.append({"table": "dragon_tiger", "trade_date": trade_date})
    if "news" in requested_tables and (
        int(missing_counts.get("news") or 0) or int(missing_counts.get("announcements") or 0)
    ):
        actions.append({"table": "news", "trade_date": trade_date, "codes": codes})
    return actions


def _actions_to_auxiliary_audit(actions: list[dict[str, Any]], codes: list[str]) -> dict[str, Any]:
    action_by_table = {str(item.get("table")): item for item in actions}
    return {
        "concept_sectors": _auxiliary_audit_row(action_by_table.get("concept_sector"), default_checked_codes=[]),
        "dragon_tiger": _auxiliary_audit_row(action_by_table.get("dragon_tiger"), default_checked_codes=[]),
        "capital_flows": _auxiliary_audit_row(action_by_table.get("capital_flow"), default_checked_codes=codes),
        "news": _auxiliary_audit_row(action_by_table.get("news"), default_checked_codes=codes),
    }


def _auxiliary_audit_row(action: dict[str, Any] | None, *, default_checked_codes: list[str]) -> dict[str, Any]:
    if not action:
        return {"status": "skipped", "count": 0, "elapsed_ms": 0}
    status = str(action.get("status") or "unknown")
    audit_status = "success" if status in {"completed", "empty"} else status
    row = {
        "status": audit_status,
        "count": int(action.get("row_count") or 0),
        "requested_count": int(action.get("requested_code_count") or 0),
        "checked_codes": list(action.get("checked_codes") or default_checked_codes),
        "elapsed_ms": 0,
    }
    if action.get("covered_trade_date"):
        row["covered_trade_date"] = action.get("covered_trade_date")
    if action.get("error"):
        row["error"] = action.get("error")
    return row


def _updated_auxiliary_info_tags(
    tags: list[Any],
    *,
    missing_fields: set[str],
    data_completeness_score: float,
    evidence_quality_score: float,
) -> list[str]:
    normalized = [str(tag) for tag in tags if str(tag or "").strip()]
    result: list[str] = []
    for tag in normalized:
        if "多维度数据缺失" in tag and not (missing_fields & {"announcements", "dragon_tiger", "news", "theme_tags"}):
            continue
        if "样本或证据覆盖不足" in tag and data_completeness_score >= 75 and evidence_quality_score >= 50:
            continue
        result.append(tag)
    return result


def _compare_recommendation_auxiliary_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_counts = before.get("missing_field_counts") if isinstance(before.get("missing_field_counts"), dict) else {}
    after_counts = after.get("missing_field_counts") if isinstance(after.get("missing_field_counts"), dict) else {}
    resolved_by_field = {
        field: max(0, int(before_counts.get(field) or 0) - int(after_counts.get(field) or 0))
        for field in sorted(set(before_counts) | set(after_counts))
    }
    return {
        "missing_field_total_delta": int(after.get("missing_field_total") or 0) - int(before.get("missing_field_total") or 0),
        "auxiliary_missing_field_total_delta": int(after.get("auxiliary_missing_field_total") or 0)
        - int(before.get("auxiliary_missing_field_total") or 0),
        "resolved_missing_field_count": sum(resolved_by_field.values()),
        "resolved_by_field": {field: count for field, count in resolved_by_field.items() if count},
        "average_data_completeness_score_delta": round(
            float(after.get("average_data_completeness_score") or 0)
            - float(before.get("average_data_completeness_score") or 0),
            3,
        ),
        "average_evidence_quality_score_delta": round(
            float(after.get("average_evidence_quality_score") or 0)
            - float(before.get("average_evidence_quality_score") or 0),
            3,
        ),
    }


def _auxiliary_action_summary(actions: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "planned_count": len(actions),
        "completed_count": sum(1 for item in actions if item.get("status") == "completed"),
        "empty_count": sum(1 for item in actions if item.get("status") == "empty"),
        "failed_count": sum(1 for item in actions if item.get("status") == "failed"),
        "row_count": sum(int(item.get("row_count") or 0) for item in actions),
    }


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _candidate_quote_date(quote: dict[str, Any], latest_allowed: str) -> str:
    raw = _normalize_trade_date(quote.get("trade_date")) or latest_allowed
    try:
        return _pool_required_bar_date_for_quote({"trade_date": raw})
    except ValueError:
        return latest_allowed


def _public_gap_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": item.get("code"),
        "name": item.get("name"),
        "reason": item.get("reason"),
        "bar_count_before": item.get("bar_count"),
        "latest_bar_date_before": item.get("latest_bar_date"),
        "quote_date": item.get("quote_date"),
    }


def _kline_fetch_status(*, row_count: int, latest_trade_date: str | None, quote_date: str, min_bars: int) -> str:
    if row_count <= 0:
        return "empty"
    if row_count < min_bars:
        return "short_history"
    if latest_trade_date and quote_date and latest_trade_date < quote_date:
        return "stale"
    return "completed"


def _local_pool_sync_status(*, target_count: int, failed_count: int, unresolved_count: int) -> str:
    if target_count <= 0:
        return "no_gap"
    if failed_count and failed_count >= target_count:
        return "failed"
    if failed_count or unresolved_count:
        return "partial_completed"
    return "completed"


def _local_pool_gap_comparison(
    *,
    before_counts: dict[str, Any],
    after_counts: dict[str, Any],
    target_count: int,
    succeeded_count: int,
    unresolved_count: int,
    failed_count: int,
) -> dict[str, Any]:
    before_candidate_count = int(before_counts.get("candidate_count") or 0)
    after_candidate_count = int(after_counts.get("candidate_count") or before_candidate_count)
    before_ready_count = int(before_counts.get("ready_count") or 0)
    after_ready_count = int(after_counts.get("ready_count") or 0)
    before_gap_count = int(before_counts.get("gap_count") or 0)
    after_gap_count = int(after_counts.get("gap_count") or 0)
    resolved_gap_count = max(0, before_gap_count - after_gap_count)
    target_total = max(0, int(target_count or 0))
    return {
        "before_candidate_count": before_candidate_count,
        "after_candidate_count": after_candidate_count,
        "before_ready_count": before_ready_count,
        "after_ready_count": after_ready_count,
        "ready_count_delta": after_ready_count - before_ready_count,
        "before_gap_count": before_gap_count,
        "after_gap_count": after_gap_count,
        "gap_count_delta": after_gap_count - before_gap_count,
        "target_gap_count": target_total,
        "resolved_target_count": resolved_gap_count,
        "unresolved_target_count": max(0, after_gap_count),
        "failed_target_count": max(0, int(failed_count or 0)),
        "candidate_coverage_rate_before": round(before_ready_count / before_candidate_count, 3)
        if before_candidate_count
        else 0.0,
        "candidate_coverage_rate_after": round(after_ready_count / after_candidate_count, 3)
        if after_candidate_count
        else 0.0,
        "candidate_coverage_rate_delta": round(
            (
                (after_ready_count / after_candidate_count)
                if after_candidate_count
                else 0.0
            )
            - (
                (before_ready_count / before_candidate_count)
                if before_candidate_count
                else 0.0
            ),
            3,
        ),
        "remediation_coverage_rate": round(resolved_gap_count / target_total, 3)
        if target_total
        else 1.0,
    }


def _mark_planned(item: dict[str, Any]) -> dict[str, Any]:
    action = dict(item)
    action["status"] = "planned"
    if "requested_code_count" not in action and isinstance(action.get("codes"), list):
        action["requested_code_count"] = len(action["codes"])
    return action


def _with_rollback_hint(action: dict[str, Any]) -> dict[str, Any]:
    hint = _rollback_hint(action)
    return {**action, "rollback_hint": hint} if hint else action


def _operation_log(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return remediation_transaction.operation_log(actions)


def _action_target_label(item: dict[str, Any]) -> str:
    return remediation_transaction.action_target_label(item)


def _rollback_hint(item: dict[str, Any]) -> str:
    return remediation_transaction.rollback_hint(item)


def _rollback_guidance(*, dry_run: bool, actions: list[dict[str, Any]]) -> dict[str, Any]:
    return remediation_transaction.rollback_guidance(dry_run=dry_run, actions=actions)


def _remediation_status(*, dry_run: bool, actions: list[dict[str, Any]], failed_count: int) -> str:
    return remediation_transaction.remediation_status(dry_run=dry_run, actions=actions, failed_count=failed_count)


def _diagnosis_failed_remediation_result(
    *,
    requested_tables: list[str],
    stage: str,
    error: Exception,
) -> dict[str, Any]:
    snapshot = _diagnosis_failure_snapshot(error, stage=stage)
    return {
        "status": "diagnosis_failed",
        "requested_tables": requested_tables,
        "before": snapshot,
        "after": snapshot,
        "actions": [],
        "operation_log": [],
        "action_summary": {
            "planned_count": 0,
            "completed_count": 0,
            "empty_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
        },
        "comparison": _skipped_comparison(
            reason="pre_diagnosis_failed",
            message="前置诊断失败，未执行任何补数写入。",
            error=error,
        ),
        "rollback_guidance": {
            "mode": "no_write",
            "message": "前置诊断失败，本次未写入数据库，无需回滚。",
            "keys": [],
        },
        "remaining_issues": None,
    }


def _diagnosis_failure_snapshot(error: Exception, *, stage: str) -> dict[str, Any]:
    return {
        "score": 0,
        "reference_trade_date": None,
        "reference_trade_date_source": None,
        "issue_count": 0,
        "recent_summary": {
            "max_missing_days": 0,
            "high_impact_tables": [],
            "warning_tables": [],
        },
        "tables": {},
        "diagnosis_failed": True,
        "diagnosis_stage": stage,
        "error_type": type(error).__name__,
        "error": str(error),
    }


def _skipped_comparison(*, reason: str, message: str, error: Exception) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": reason,
        "message": message,
        "error_type": type(error).__name__,
        "error": str(error),
    }


def _data_domain_by_key(diagnosis: dict[str, Any], key: str) -> dict[str, Any]:
    categories = ((diagnosis.get("data_domains") or {}).get("categories") or [])
    for category in categories:
        for domain in category.get("domains") or []:
            if domain.get("key") == key:
                return dict(domain)
    return {}


# 全市场日线批量补数里除权威行情列外的全部指标列（stk_factor / cyq_perf / 本地派生）。
_BATCH_PRESERVED_INDICATOR_COLUMNS = (
    "ma5", "ma10", "ma20", "ma60", "ma250",
    "macd_dif", "macd_dea", "macd_bar",
    "kdj_k", "kdj_d", "kdj_j",
    "rsi_6", "rsi_12", "rsi_24",
    "boll_upper", "boll_mid", "boll_lower", "cci",
    "adj_factor", "asi", "asit", "atr", "bbi", "brar_ar", "brar_br", "dpo",
    "ema_12", "ema_50", "ma_volume_5", "ma_volume_10", "mtm", "obv", "psy", "trix", "vr",
    "wr_6", "wr_10",
    "bias_5", "bias_10", "bias_20",
    "vol_ratio_5", "vol_ratio_20",
    "max_dd20", "max_dd60",
    "chip_cost_15", "chip_cost_50", "chip_cost_85", "chip_weight_avg", "chip_winner_rate",
)


def _daily_kbar_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": str(row.get("code") or ""),
        "trade_date": _normalize_trade_date(row.get("trade_date")),
        "open": _float(row.get("open")),
        "high": _float(row.get("high")),
        "low": _float(row.get("low")),
        "close": _float(row.get("close")),
        "volume": _float(row.get("volume")),
        "amount": _float(row.get("amount")),
        "pct_chg": _float(row.get("pct_chg")),
        "turnover_rate": _float(row.get("turnover_rate")),
        "ma5": _optional_float(row.get("ma5")),
        "ma10": _optional_float(row.get("ma10")),
        "ma20": _optional_float(row.get("ma20")),
        "ma60": _optional_float(row.get("ma60")),
        "ma250": _optional_float(row.get("ma250")),
        "macd_dif": _optional_float(row.get("macd_dif")),
        "macd_dea": _optional_float(row.get("macd_dea")),
        "macd_bar": _optional_float(row.get("macd_bar", row.get("macd"))),
        "kdj_k": _optional_float(row.get("kdj_k")),
        "kdj_d": _optional_float(row.get("kdj_d")),
        "kdj_j": _optional_float(row.get("kdj_j")),
        "rsi_6": _optional_float(row.get("rsi_6")),
        "rsi_12": _optional_float(row.get("rsi_12")),
        "rsi_24": _optional_float(row.get("rsi_24")),
        "boll_upper": _optional_float(row.get("boll_upper")),
        "boll_mid": _optional_float(row.get("boll_mid")),
        "boll_lower": _optional_float(row.get("boll_lower")),
        "cci": _optional_float(row.get("cci")),
        "adj_factor": _optional_float(row.get("adj_factor")),
        "asi": _optional_float(row.get("asi")),
        "asit": _optional_float(row.get("asit")),
        "atr": _optional_float(row.get("atr")),
        "bbi": _optional_float(row.get("bbi")),
        "brar_ar": _optional_float(row.get("brar_ar")),
        "brar_br": _optional_float(row.get("brar_br")),
        "dpo": _optional_float(row.get("dpo")),
        "ema_12": _optional_float(row.get("ema_12")),
        "ema_50": _optional_float(row.get("ema_50")),
        "ma_volume_5": _optional_float(row.get("ma_volume_5")),
        "ma_volume_10": _optional_float(row.get("ma_volume_10")),
        "mtm": _optional_float(row.get("mtm")),
        "obv": _optional_float(row.get("obv")),
        "psy": _optional_float(row.get("psy")),
        "trix": _optional_float(row.get("trix")),
        "vr": _optional_float(row.get("vr")),
        "wr_6": _optional_float(row.get("wr_6")),
        "wr_10": _optional_float(row.get("wr_10")),
        "bias_5": _optional_float(row.get("bias_5")),
        "bias_10": _optional_float(row.get("bias_10")),
        "bias_20": _optional_float(row.get("bias_20")),
        "vol_ratio_5": _optional_float(row.get("vol_ratio_5")),
        "vol_ratio_20": _optional_float(row.get("vol_ratio_20")),
        "breakout20": int(row.get("breakout20") or 0),
        "breakout60": int(row.get("breakout60") or 0),
        "max_dd20": _optional_float(row.get("max_dd20")),
        "max_dd60": _optional_float(row.get("max_dd60")),
        "chip_cost_15": _optional_float(row.get("chip_cost_15")),
        "chip_cost_50": _optional_float(row.get("chip_cost_50")),
        "chip_cost_85": _optional_float(row.get("chip_cost_85")),
        "chip_weight_avg": _optional_float(row.get("chip_weight_avg")),
        "chip_winner_rate": _optional_float(row.get("chip_winner_rate")),
        "source": str(row.get("source") or "tushare"),
    }


def _minute_probe_failed(
    results: list[tuple[str, list[dict[str, Any]], Exception | None]],
) -> bool:
    return bool(results) and all(not rows and error is not None for _, rows, error in results)


def _reference_date_targets(diagnosis: dict[str, Any], table_key: str) -> list[str]:
    reference_trade_date = str(diagnosis.get("reference_trade_date") or "")
    if not reference_trade_date:
        return []
    table_info = (diagnosis.get("tables") or {}).get(table_key) or {}
    lag = int((diagnosis.get("lag_days") or {}).get(table_key) or 0)
    if lag > 0 or not table_info.get("max_date"):
        return [reference_trade_date]
    return []


def _diagnosis_snapshot(diagnosis: dict[str, Any]) -> dict[str, Any]:
    recent = diagnosis.get("recent_trade_day_coverage") or {}
    summary = recent.get("summary") or {}
    tables = diagnosis.get("tables") or {}
    lag_days = diagnosis.get("lag_days") or {}
    table_snapshots = {
        key: {
            "max_date": (tables.get(key) or {}).get("max_date"),
            "lag_days": lag_days.get(key),
            "missing_dates": list(((recent.get("tables") or {}).get(key) or {}).get("missing_dates") or []),
            "incomplete_dates": list(((recent.get("tables") or {}).get(key) or {}).get("incomplete_dates") or []),
        }
        for key in DEFAULT_REMEDIABLE_TABLES
    }
    chip_domain = _data_domain_by_key(diagnosis, "chip")
    chip_max_date = _normalize_trade_date(chip_domain.get("max_date"))
    if chip_domain:
        table_snapshots["chip"] = {
            "max_date": chip_max_date,
            "lag_days": chip_domain.get("gap_days"),
            "missing_dates": [
                trade_date
                for trade_date in recent.get("trade_days") or []
                if not chip_max_date or trade_date > chip_max_date
            ],
            "incomplete_dates": [],
        }
    return {
        "score": int(diagnosis.get("score") or 0),
        "reference_trade_date": diagnosis.get("reference_trade_date"),
        "reference_trade_date_source": diagnosis.get("reference_trade_date_source"),
        "issue_count": len(diagnosis.get("issues") or []),
        "recent_summary": {
            "max_missing_days": int(summary.get("max_missing_days") or 0),
            "high_impact_tables": list(summary.get("high_impact_tables") or []),
            "warning_tables": list(summary.get("warning_tables") or []),
        },
        "tables": table_snapshots,
    }


def _numeric_delta(after_value: Any, before_value: Any) -> int | None:
    if after_value is None or before_value is None:
        return None
    try:
        return int(after_value) - int(before_value)
    except (TypeError, ValueError):
        return None


def _trade_date_variants(value: Any) -> list[str]:
    normalized = _normalize_trade_date(value)
    if not normalized:
        return []
    return sorted({normalized, normalized.replace("-", "")})


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
