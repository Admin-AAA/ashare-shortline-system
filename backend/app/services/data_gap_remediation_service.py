from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.trading_calendar import (
    latest_allowed_bar_date as _latest_allowed_bar_date,
    normalize_trade_date as _normalize_trade_date,
)
from app.services.market_pool.history_maturity import (
    is_natural_short_history as _is_natural_short_history,
    load_history_maturity_map as _load_history_maturity_map,
)
from app.models.tables import DailyKbar, IndexDaily, LimitUpBoard, MinuteKbar, NewsAnnouncement, Recommendation, RecommendationRun, StockQuote
from app.services.data_health_service import clear_data_health_cache, diagnose_all
from app.services.feature_builder import _compute_authenticity, _enrich_with_auxiliary_data, _tag_features
from app.services.auxiliary.application import (
    load_auxiliary_feature_views,
    run_auxiliary_application,
)
from app.services.market_pool.core import (
    _candidate_recall_plan,
    _eligible_market_quotes,
    _execution_recall_count,
    _load_cached_bars_summary,
    _load_cached_quotes,
    _quote_sources,
    _select_recall_candidates,
)
from app.engine.trading_calendar import (
    latest_allowed_bar_date as _pool_latest_allowed_bar_date,
)
from app.services.market_pool.core import (
    _required_bar_date_for_quote as _pool_required_bar_date_for_quote,
)
from app.services.research_context import build_research_context
from app.services.data_sources import enabled_sources
from app.services.remediation import transaction as remediation_transaction
from app.adapters.market_source import TushareMarketSourceAdapter
from app.services.market_source.port import DailyBarRequest, MarketSourcePort, QuoteRef
from app.services.tushare_client import (
    fetch_index_daily,
    fetch_minute_kbars,
    get_pro,
    get_tushare_provider_health,
)
from app.services.daily_bar_write_policy import (
    normalize_quote_metric,
    quote_metrics_look_unready,
)

logger = logging.getLogger(__name__)

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


def _refresh_auxiliary_dataset(
    db: Session,
    *,
    dataset: str,
    trade_date: str,
    codes: list[str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Refresh one frozen S4 descriptor through the sole application boundary."""
    result = run_auxiliary_application(
        db,
        instruments=tuple(codes or ()),
        trade_date=trade_date,
        refresh=True,
        datasets=(dataset,),
    )
    item = result.orchestrated.result_for(dataset)
    if item.errors:
        raise RuntimeError(", ".join(error.message for error in item.errors))
    # run_auxiliary_application 把事务所有权交给调用方。补数动作以单数据集为
    # 恢复单元，成功后必须立即提交；否则 Session close 会回滚，任务却仍报告成功。
    db.commit()
    return item.coverage.record_count, dict(result.audit_payload[dataset])


def _backfill_news_announcements(
    db: Session,
    *,
    trade_date: str,
    codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    row_limit: int = 1000,
) -> dict[str, Any]:
    """Fetch a provably bounded announcement range per target symbol and upsert it.

    The current ai-tool gateway caps global ``anns_d`` responses at 1,000 rows and
    ignores ``offset``. Per-symbol month ranges stay below that cap and therefore avoid
    treating a repeated full page as complete.
    """
    target_start = _normalize_trade_date(start_date or trade_date)
    target_end = _normalize_trade_date(end_date or trade_date)
    target_codes = list(OrderedDict.fromkeys(
        str(code or "").strip() for code in (codes or []) if str(code or "").strip()
    ))
    if not target_start or not target_end or target_start > target_end or not target_codes:
        return {
            "row_count": 0,
            "query_count": 0,
            "provider_row_count": 0,
            "failed_code_count": 0,
            "failed_codes": [],
        }

    effective_limit = max(1, min(1000, int(row_limit or 1000)))
    compact_start = target_start.replace("-", "")
    compact_end = target_end.replace("-", "")
    payload_by_id: dict[str, dict[str, Any]] = {}
    provider_row_count = 0
    failed_codes: list[str] = []
    pro = get_pro()

    # 逐股票 Tushare 调用必须限速（L-044：盘后流水线曾因逐股票 anns_d 无界
    # 调用卡死数小时，阻塞 outcome 结算 → 前瞻样本 → D 项闭环）。
    # 每笔间隔 0.35s（约 3 req/s，低于 Tushare 公开限速），累计失败超过
    # NEWS_BACKFILL_MAX_FAILURES 中止，避免全市场网络异常时无限空转。
    NEWS_BACKFILL_SLEEP = 0.35
    NEWS_BACKFILL_MAX_FAILURES = 200
    consecutive_failures = 0
    for code in target_codes:
        if consecutive_failures >= NEWS_BACKFILL_MAX_FAILURES:
            break
        # 循环顶部 sleep：无论成功/失败/空返回，每笔调用后都限速，
        # 避免 provider 快速失败时以无限循环速率打爆上游限流。
        time.sleep(NEWS_BACKFILL_SLEEP)
        suffix = "BJ" if code.startswith(("4", "8")) else "SH" if code.startswith(("5", "6", "9")) else "SZ"
        try:
            frame = pro.anns_d(
                ts_code=f"{code}.{suffix}",
                start_date=compact_start,
                end_date=compact_end,
                limit=effective_limit,
            )
        except Exception:
            failed_codes.append(code)
            consecutive_failures += 1
            continue
        if frame is None or frame.empty:
            consecutive_failures += 1
            continue
        consecutive_failures = 0
        if len(frame) >= effective_limit:
            failed_codes.append(code)
            continue
        provider_row_count += len(frame)
        for _, row in frame.iterrows():
            announce_date = _normalize_trade_date(row.get("ann_date"))
            if not announce_date or not (target_start <= announce_date <= target_end):
                continue
            raw_code = str(row.get("ts_code") or row.get("symbol") or "").strip()
            row_code = raw_code.split(".")[0]
            title = str(row.get("title") or "").strip()
            url = str(row.get("url") or "").strip()
            if len(row_code) != 6 or not title:
                continue
            identity = f"{url}|{raw_code}|{announce_date}|{title}"
            article_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            category = str(row.get("ann_type") or "announcement").strip()[:40]
            risk_warning = any(
                keyword in title
                for keyword in ("风险", "退市", "处罚", "立案", "亏损", "减持", "质押", "诉讼")
            )
            payload_by_id[article_id] = {
                "source": "tushare_anns_d",
                "article_id": article_id,
                "code": row_code,
                "announce_date": announce_date,
                "title": title,
                "summary": None,
                "category": category or "announcement",
                "sentiment": "neutral",
                "is_risk_warning": risk_warning,
            }

    payload = list(payload_by_id.values())
    if payload:
        stmt = mysql_insert(NewsAnnouncement).values(payload)
        db.execute(
            stmt.on_duplicate_key_update(
                code=stmt.inserted.code,
                announce_date=stmt.inserted.announce_date,
                title=stmt.inserted.title,
                category=stmt.inserted.category,
                sentiment=stmt.inserted.sentiment,
                is_risk_warning=stmt.inserted.is_risk_warning,
            )
        )
        db.commit()
    return {
        "row_count": len(payload),
        "query_count": len(target_codes),
        "provider_row_count": provider_row_count,
        "failed_code_count": len(failed_codes),
        "failed_codes": failed_codes[:10],
    }


def _resolve_market_source(market_source: MarketSourcePort | None) -> MarketSourcePort:
    """Use the typed production adapter unless a Port is explicitly injected."""

    return market_source if market_source is not None else TushareMarketSourceAdapter()


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


def _provider_health_snapshot() -> dict[str, dict[str, Any]]:
    try:
        return {
            str(item.get("provider_name") or item.get("source")): item
            for item in get_tushare_provider_health()
        }
    except Exception:
        logger.warning("provider health snapshot failed during remediation", exc_info=True)
        return {}


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


def remediate_data_health_gaps(
    db: Session,
    *,
    target_tables: list[str] | None = None,
    dry_run: bool = False,
    capital_flow_code_limit: int = 200,
    daily_kbar_code_limit: int = DEFAULT_DAILY_KBAR_CODE_LIMIT,
    minute_kbar_code_limit: int = 300,
    post_diagnosis: bool = True,
) -> dict[str, Any]:
    requested_tables = _normalize_target_tables(target_tables)
    try:
        before = diagnose_all(db)
    except Exception as exc:
        db.rollback()
        return _diagnosis_failed_remediation_result(
            requested_tables=requested_tables,
            stage="pre",
            error=exc,
        )

    plan = _build_remediation_plan(
        db,
        before,
        requested_tables=requested_tables,
        capital_flow_code_limit=capital_flow_code_limit,
        daily_kbar_code_limit=daily_kbar_code_limit,
        minute_kbar_code_limit=minute_kbar_code_limit,
    )

    provider_before = _provider_health_snapshot()
    actions = [_with_rollback_hint(_mark_planned(item)) for item in plan]
    if not dry_run:
        actions = [_with_rollback_hint(_execute_plan_item_safely(db, item)) for item in plan]
    provider_activity = _provider_activity_delta(
        provider_before,
        _provider_health_snapshot(),
    )

    after_diagnosis_skipped = bool(not dry_run and not post_diagnosis)
    post_diagnosis_failed = False
    if dry_run or after_diagnosis_skipped:
        after = before
        after_snapshot = _diagnosis_snapshot(after)
        comparison = (
            {"status": "skipped", "reason": "post_diagnosis=false，已跳过执行后完整诊断；请用轻量行数或后续诊断验证补数效果。"}
            if after_diagnosis_skipped
            else _compare_diagnosis(before, after, requested_tables=requested_tables)
        )
        remaining_issues = None if after_diagnosis_skipped else after.get("issues") or []
    else:
        try:
            # 修复已写入数据，必须跳过缓存重算，否则 after 仍是 before 的旧快照
            clear_data_health_cache()
            after = diagnose_all(db, force_refresh=True)
            after_snapshot = _diagnosis_snapshot(after)
            comparison = _compare_diagnosis(before, after, requested_tables=requested_tables)
            remaining_issues = after.get("issues") or []
        except Exception as exc:
            db.rollback()
            post_diagnosis_failed = True
            after_snapshot = _diagnosis_failure_snapshot(exc, stage="post")
            comparison = _skipped_comparison(
                reason="post_diagnosis_failed",
                message="补数动作已执行，但后置诊断失败；请用操作日志和后续诊断复核补数效果。",
                error=exc,
            )
            remaining_issues = None

    if after_diagnosis_skipped:
        after_snapshot["diagnosis_skipped"] = True
    failed_count = sum(1 for item in actions if item.get("status") == "failed")
    status = _remediation_status(dry_run=dry_run, actions=actions, failed_count=failed_count)
    if post_diagnosis_failed:
        if failed_count:
            status = "partial_failed"
        elif actions:
            status = "completed_post_diagnosis_failed"
        else:
            status = "post_diagnosis_failed"
    return {
        "status": status,
        "requested_tables": requested_tables,
        "before": _diagnosis_snapshot(before),
        "after": after_snapshot,
        "actions": actions,
        "operation_log": _operation_log(actions),
        "action_summary": remediation_transaction.action_summary(plan, actions),
        "provider_activity": provider_activity,
        "comparison": comparison,
        "rollback_guidance": _rollback_guidance(dry_run=dry_run, actions=actions),
        "remaining_issues": remaining_issues,
    }


def sync_local_pool_kline_gaps(
    db: Session,
    *,
    code_limit: int = DEFAULT_LOCAL_POOL_GAP_CODE_LIMIT,
    days: int = DEFAULT_LOCAL_POOL_GAP_DAYS,
    min_bars: int = DEFAULT_LOCAL_POOL_MIN_BARS,
    dry_run: bool = False,
    market_source: MarketSourcePort | None = None,
) -> dict[str, Any]:
    """Backfill daily K-line history for local-pool candidates with readiness gaps."""
    fetch_days = max(min_bars, int(days or DEFAULT_LOCAL_POOL_GAP_DAYS))
    required_bars = max(1, int(min_bars or DEFAULT_LOCAL_POOL_MIN_BARS))
    before_state = _local_pool_kline_gap_state(db, min_bars=required_bars)
    requested_limit = int(code_limit if code_limit is not None else DEFAULT_LOCAL_POOL_GAP_CODE_LIMIT)
    effective_limit = len(before_state["gaps"]) if requested_limit <= 0 else max(1, requested_limit)
    targets = before_state["gaps"][:effective_limit]

    if dry_run or not targets:
        operation_log = [
            {**_public_gap_item(item), "status": "planned", "row_count": 0}
            for item in targets
        ]
        comparison = _local_pool_gap_comparison(
            before_counts=before_state["counts"],
            after_counts=before_state["counts"],
            target_count=len(targets),
            succeeded_count=0,
            unresolved_count=0,
            failed_count=0,
        )
        return {
            "status": "planned" if targets else "no_gap",
            "dry_run": dry_run,
            "days": fetch_days,
            "min_bars": required_bars,
            "code_limit": requested_limit,
            "effective_code_limit": effective_limit,
            "target_count": len(targets),
            "processed_count": 0,
            "succeeded_count": 0,
            "unresolved_count": 0,
            "failed_count": 0,
            "row_count": 0,
            "before": before_state["counts"],
            "after": before_state["counts"],
            "comparison": comparison,
            "sync_summary": _local_pool_operation_summary(operation_log),
            "operation_log": operation_log,
        }

    batch_result = _sync_same_day_stale_kline_gaps(
        db,
        before_state,
        targets,
        requested_limit,
        fetch_days,
        required_bars,
        market_source=market_source,
    )
    if batch_result is not None:
        return batch_result

    latest_allowed = _pool_latest_allowed_bar_date()
    operation_log: list[dict[str, Any]] = []
    payload: list[dict[str, Any]] = []
    source = _resolve_market_source(market_source)
    history_start_date = (date.fromisoformat(latest_allowed) - timedelta(days=fetch_days * 3)).isoformat()

    for item in targets:
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        source_audit: dict[str, Any] | None = None
        try:
            batch = source.fetch_daily_bars(
                DailyBarRequest(
                    instruments=(QuoteRef(code),),
                    start_date=history_start_date,
                    end_date=latest_allowed,
                )
            )
            source_audit = _market_source_audit_payload(batch)
            if batch.audit.failed_count:
                raise RuntimeError("market source failed to retrieve daily-bar history")
            rows = [
                bar.as_legacy_row()
                for bar in batch.bars_by_instrument.get(QuoteRef(code), ())
            ]
            normalized_rows = [
                {**row, "code": code, "trade_date": _normalize_trade_date(row.get("trade_date"))}
                for row in rows
                if _normalize_trade_date(row.get("trade_date"))
                and _normalize_trade_date(row.get("trade_date")) <= latest_allowed
            ]
            latest_row_date = max((str(row.get("trade_date") or "") for row in normalized_rows), default=None)
            status = _kline_fetch_status(
                row_count=len(normalized_rows),
                latest_trade_date=latest_row_date,
                quote_date=str(item.get("quote_date") or ""),
                min_bars=required_bars,
            )
            payload.extend(_daily_kbar_payload(row) for row in normalized_rows)
            operation_log.append(
                {
                    **_public_gap_item(item),
                    "status": status,
                    "row_count": len(normalized_rows),
                    "latest_trade_date": latest_row_date,
                    "market_source_audit": source_audit,
                }
            )
        except Exception as exc:  # noqa: BLE001 - keep one bad code from aborting the batch
            db.rollback()
            operation_log.append(
                {
                    **_public_gap_item(item),
                    "status": "failed",
                    "row_count": 0,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    **({"market_source_audit": source_audit} if source_audit else {}),
                }
            )

    if payload:
        _insert_daily_kbar_payload_ignore(db, payload)
        _backfill_missing_indicators(db)

    after_state = _local_pool_kline_gap_state(db, min_bars=required_bars)
    failed_count = sum(1 for item in operation_log if item.get("status") == "failed")
    succeeded_count = sum(1 for item in operation_log if item.get("status") == "completed")
    unresolved_count = sum(1 for item in operation_log if item.get("status") in {"empty", "short_history", "stale"})
    return {
        "status": _local_pool_sync_status(
            target_count=len(targets),
            failed_count=failed_count,
            unresolved_count=unresolved_count,
        ),
        "dry_run": False,
        "days": fetch_days,
        "min_bars": required_bars,
        "code_limit": requested_limit,
        "effective_code_limit": effective_limit,
        "target_count": len(targets),
        "processed_count": len(operation_log),
        "succeeded_count": succeeded_count,
        "unresolved_count": unresolved_count,
        "failed_count": failed_count,
        "row_count": len(payload),
        "before": before_state["counts"],
        "after": after_state["counts"],
        "comparison": _local_pool_gap_comparison(
            before_counts=before_state["counts"],
            after_counts=after_state["counts"],
            target_count=len(targets),
            succeeded_count=succeeded_count,
            unresolved_count=unresolved_count,
            failed_count=failed_count,
        ),
        "sync_summary": _local_pool_operation_summary(operation_log),
        "operation_log": operation_log[:50],
    }


def _sync_same_day_stale_kline_gaps(
    db: Session,
    before_state: dict[str, Any],
    targets: list[dict[str, Any]],
    requested_limit: int,
    fetch_days: int,
    required_bars: int,
    *,
    market_source: MarketSourcePort | None = None,
) -> dict[str, Any] | None:
    """Use one Tushare trade-date batch fetch when many candidates are stale on the same date."""
    if not targets:
        return None
    quote_dates = {
        _normalize_trade_date(item.get("quote_date"))
        for item in targets
        if _normalize_trade_date(item.get("quote_date"))
    }
    reasons = {str(item.get("reason") or "") for item in targets}
    if len(quote_dates) != 1 or not reasons <= {"stale", "missing"}:
        return None

    trade_date = next(iter(quote_dates))
    target_codes = [str(item.get("code") or "").strip() for item in targets if str(item.get("code") or "").strip()]
    try:
        result = _backfill_daily_kbar_date(
            db,
            trade_date,
            target_codes,
            market_source=market_source,
        )
        operation_log = [
            {
                "table": "daily_kbar",
                "trade_date": trade_date,
                "status": "completed" if result.get("row_count") else "empty",
                "row_count": int(result.get("row_count") or 0),
                "provider_row_count": int(result.get("provider_row_count") or 0),
                "requested_code_count": len(target_codes),
                "target_hit_count": int(result.get("target_hit_count") or 0),
                "missing_code_count": int(result.get("missing_code_count") or 0),
                "missing_codes": result.get("missing_codes") or [],
                "mode": "trade_date_batch",
            }
        ]
    except Exception as exc:  # noqa: BLE001 - keep API response structured for operators
        db.rollback()
        operation_log = [
            {
                "table": "daily_kbar",
                "trade_date": trade_date,
                "status": "failed",
                "row_count": 0,
                "requested_code_count": len(target_codes),
                "error": str(exc),
                "error_type": type(exc).__name__,
                "mode": "trade_date_batch",
            }
        ]

    after_state = _local_pool_kline_gap_state(db, min_bars=required_bars)
    failed_count = sum(1 for item in operation_log if item.get("status") == "failed")
    succeeded_count = int(operation_log[0].get("target_hit_count") or 0) if not failed_count else 0
    unresolved_count = max(0, len(targets) - succeeded_count - failed_count)
    return {
        "status": _local_pool_sync_status(
            target_count=len(targets),
            failed_count=failed_count,
            unresolved_count=unresolved_count,
        ),
        "dry_run": False,
        "days": fetch_days,
        "min_bars": required_bars,
        "code_limit": requested_limit,
        "effective_code_limit": len(targets) if requested_limit <= 0 else max(1, requested_limit),
        "target_count": len(targets),
        "processed_count": len(targets),
        "succeeded_count": succeeded_count,
        "unresolved_count": unresolved_count,
        "failed_count": failed_count,
        "row_count": sum(int(item.get("row_count") or 0) for item in operation_log),
        "before": before_state["counts"],
        "after": after_state["counts"],
        "comparison": _local_pool_gap_comparison(
            before_counts=before_state["counts"],
            after_counts=after_state["counts"],
            target_count=len(targets),
            succeeded_count=succeeded_count,
            unresolved_count=unresolved_count,
            failed_count=failed_count,
        ),
        "sync_summary": _local_pool_operation_summary(operation_log),
        "operation_log": operation_log,
    }


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


def remediate_recommendation_run_auxiliary_data(
    db: Session,
    run_id: str,
    *,
    target_tables: list[str] | None = None,
    code_limit: int = 0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Backfill auxiliary evidence for already-generated recommendations in one run."""
    run = db.get(RecommendationRun, run_id)
    if run is None:
        return {"status": "run_not_found", "run_id": run_id}

    recs = db.scalars(
        select(Recommendation)
        .where(Recommendation.run_id == run_id)
        .order_by(Recommendation.final_score.desc(), Recommendation.recommended_at.asc())
    ).all()
    requested_tables = _normalize_auxiliary_target_tables(target_tables)
    if code_limit and int(code_limit) > 0:
        recs = recs[: max(1, int(code_limit))]
    codes = [str(rec.code or "").strip() for rec in recs if str(rec.code or "").strip()]
    before = _recommendation_auxiliary_snapshot(recs)
    actions = _build_recommendation_auxiliary_actions(
        before=before,
        codes=codes,
        trade_date=str(run.trade_date or ""),
        requested_tables=requested_tables,
    )
    if dry_run or not actions:
        return {
            "status": "planned" if actions else "no_auxiliary_gap",
            "dry_run": dry_run,
            "run_id": run_id,
            "trade_date": run.trade_date,
            "requested_tables": requested_tables,
            "target_recommendation_count": len(recs),
            "target_code_count": len(codes),
            "before": before,
            "after": before,
            "comparison": _compare_recommendation_auxiliary_snapshots(before, before),
            "actions": [_mark_planned(item) for item in actions],
            "action_summary": _auxiliary_action_summary([_mark_planned(item) for item in actions]),
        }

    executed_actions = [_execute_recommendation_auxiliary_action_safely(db, item) for item in actions]
    snapshot = _loads_json(run.market_snapshot_json, {})
    snapshot.setdefault("trade_date", run.trade_date)
    snapshot.setdefault("breadth", {})["auxiliary_data_audit"] = _actions_to_auxiliary_audit(executed_actions, codes)
    run.market_snapshot_json = _dumps_json(snapshot)
    aux = load_auxiliary_feature_views(db, codes, trade_date=run.trade_date) if codes else {}
    _rewrite_recommendation_auxiliary_snapshots(recs, snapshot=snapshot, aux=aux)
    db.commit()

    after = _recommendation_auxiliary_snapshot(recs)
    return {
        "status": "completed" if not any(item.get("status") == "failed" for item in executed_actions) else "partial_failed",
        "dry_run": False,
        "run_id": run_id,
        "trade_date": run.trade_date,
        "requested_tables": requested_tables,
        "target_recommendation_count": len(recs),
        "target_code_count": len(codes),
        "before": before,
        "after": after,
        "comparison": _compare_recommendation_auxiliary_snapshots(before, after),
        "actions": executed_actions,
        "action_summary": _auxiliary_action_summary(executed_actions),
    }


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


def _recommendation_auxiliary_snapshot(recs: list[Recommendation]) -> dict[str, Any]:
    field_counter: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    completeness_scores: list[float] = []
    evidence_scores: list[float] = []
    for rec in recs:
        feature_snapshot = _loads_json(rec.feature_snapshot_json, {})
        quality = feature_snapshot.get("data_quality") if isinstance(feature_snapshot.get("data_quality"), dict) else {}
        missing_fields = sorted(str(item) for item in quality.get("missing_fields") or [] if str(item or "").strip())
        field_counter.update(missing_fields)
        completeness = _optional_float(feature_snapshot.get("data_completeness_score") or quality.get("data_completeness_score"))
        evidence = _optional_float(feature_snapshot.get("evidence_quality_score") or quality.get("evidence_quality_score"))
        if completeness is not None:
            completeness_scores.append(completeness)
        if evidence is not None:
            evidence_scores.append(evidence)
        records.append(
            {
                "recommendation_id": rec.recommendation_id,
                "code": rec.code,
                "name": rec.name,
                "missing_fields": missing_fields,
                "auxiliary_missing_fields": sorted(set(missing_fields) & AUXILIARY_GAP_FIELDS),
                "data_completeness_score": completeness,
                "evidence_quality_score": evidence,
            }
        )
    auxiliary_missing_total = sum(
        count for field, count in field_counter.items() if field in AUXILIARY_GAP_FIELDS
    )
    return {
        "recommendation_count": len(recs),
        "missing_field_total": sum(field_counter.values()),
        "auxiliary_missing_field_total": auxiliary_missing_total,
        "missing_field_counts": dict(sorted(field_counter.items())),
        "average_data_completeness_score": _average(completeness_scores),
        "average_evidence_quality_score": _average(evidence_scores),
        "examples": [row for row in records if row["auxiliary_missing_fields"]][:8],
    }


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


def _execute_recommendation_auxiliary_action_safely(db: Session, item: dict[str, Any]) -> dict[str, Any]:
    try:
        table = item["table"]
        if table == "capital_flow":
            codes = item.get("codes") or []
            row_count, _audit = _refresh_auxiliary_dataset(
                db, dataset="capital_flow", trade_date=item["trade_date"], codes=list(codes)
            )
            return {
                **item,
                "status": "completed" if row_count else "empty",
                "row_count": row_count,
                "requested_code_count": len(codes),
                "checked_codes": codes,
            }
        if table == "concept_sector":
            row_count, _audit = _refresh_auxiliary_dataset(
                db, dataset="concept_sector", trade_date=item["trade_date"]
            )
            return {**item, "status": "completed" if row_count else "empty", "row_count": row_count}
        if table == "dragon_tiger":
            row_count, _audit = _refresh_auxiliary_dataset(
                db, dataset="dragon_tiger", trade_date=item["trade_date"]
            )
            return {
                **item,
                "status": "completed" if row_count else "empty",
                "row_count": row_count,
                "covered_trade_date": item["trade_date"],
            }
        if table == "news":
            codes = item.get("codes") or []
            row_count, _audit = _refresh_auxiliary_dataset(
                db, dataset="news_announcement", trade_date=item["trade_date"], codes=list(codes)
            )
            checked_codes = list(codes)
            return {
                **item,
                "status": "completed" if row_count else "empty",
                "row_count": row_count,
                "requested_code_count": len(codes),
                "checked_codes": checked_codes,
            }
    except Exception as exc:
        db.rollback()
        return {**item, "status": "failed", "row_count": 0, "error": str(exc), "error_type": type(exc).__name__}
    return {**item, "status": "skipped", "row_count": 0, "reason": "unsupported_table"}


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


def _rewrite_recommendation_auxiliary_snapshots(
    recs: list[Recommendation],
    *,
    snapshot: dict[str, Any],
    aux: dict[str, Any],
) -> None:
    for rec in recs:
        feature_snapshot = _loads_json(rec.feature_snapshot_json, {})
        feature_snapshot.setdefault("code", rec.code)
        feature_snapshot.setdefault("name", rec.name)
        feature_snapshot.setdefault("industry", rec.industry)
        _enrich_with_auxiliary_data([feature_snapshot], None, snapshot, _aux_override=aux)
        _tag_features(feature_snapshot)
        feature_snapshot["data_authenticity"] = _compute_authenticity(feature_snapshot)
        context = build_research_context(feature_snapshot, snapshot)
        updated_snapshot = context.to_feature_snapshot()
        rec.feature_snapshot_json = _dumps_json(updated_snapshot)
        quality = updated_snapshot.get("data_quality") if isinstance(updated_snapshot.get("data_quality"), dict) else {}
        rec.info_tags_json = _dumps_json(
            _updated_auxiliary_info_tags(
                _loads_json(rec.info_tags_json, []),
                missing_fields=set(quality.get("missing_fields") or []),
                data_completeness_score=float(updated_snapshot.get("data_completeness_score") or 0),
                evidence_quality_score=float(updated_snapshot.get("evidence_quality_score") or 0),
            )
        )


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


def _loads_json(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None



def _local_pool_kline_gap_state(db: Session, *, min_bars: int) -> dict[str, Any]:
    settings = get_settings()
    active_sources = enabled_sources(db, "quote", _quote_sources(settings))
    quotes = _load_cached_quotes(
        db,
        allowed_sources=set(active_sources) if active_sources else None,
        skip_metrics=True,
    )
    eligible_quotes = _eligible_market_quotes(quotes) if quotes else []
    recall_plan = _candidate_recall_plan(len(quotes), len(eligible_quotes), settings)
    execution_recall_count = _execution_recall_count(recall_plan, len(eligible_quotes))
    candidates = _select_recall_candidates(eligible_quotes, execution_recall_count)
    candidate_codes = [str(item.get("code") or "").strip() for item in candidates if str(item.get("code") or "").strip()]
    bar_summaries = _load_cached_bars_summary(db, candidate_codes, min_bars) if candidate_codes else {}
    maturity_map = _load_history_maturity_map(db, candidate_codes) if candidate_codes else {}
    latest_allowed = _pool_latest_allowed_bar_date()

    counts = {
        "candidate_count": len(candidates),
        "checked_count": 0,
        "ready_count": 0,
        "missing_count": 0,
        "short_history_count": 0,
        "immature_history_count": 0,
        "stale_count": 0,
        "gap_count": 0,
        "latest_allowed_bar_date": latest_allowed,
    }
    gaps: list[dict[str, Any]] = []
    for quote in candidates:
        code = str(quote.get("code") or "").strip()
        if not code:
            continue
        counts["checked_count"] += 1
        summary = bar_summaries.get(code, {})
        bar_count = int(summary.get("bar_count") or 0)
        latest_bar_date = _normalize_trade_date(summary.get("latest_trade_date"))
        quote_date = _candidate_quote_date(quote, latest_allowed)
        reason = ""
        if bar_count <= 0:
            reason = "missing"
            counts["missing_count"] += 1
        elif bar_count < min_bars:
            counts["short_history_count"] += 1
            maturity = maturity_map.get(code, {})
            if _is_natural_short_history(
                bar_count=bar_count,
                latest_bar_date=latest_bar_date,
                quote_date=quote_date,
                first_bar_date=_normalize_trade_date(maturity.get("first_bar_date")),
                list_date=_normalize_trade_date(maturity.get("list_date")),
            ):
                counts["immature_history_count"] += 1
                continue
            else:
                reason = "short_history"
        elif not latest_bar_date or latest_bar_date < quote_date:
            reason = "stale"
            counts["stale_count"] += 1

        if reason:
            gaps.append(
                {
                    "code": code,
                    "name": quote.get("name"),
                    "reason": reason,
                    "bar_count": bar_count,
                    "latest_bar_date": latest_bar_date or None,
                    "quote_date": quote_date,
                    "amount_yi": quote.get("amount_yi"),
                }
            )
        else:
            counts["ready_count"] += 1

    counts["gap_count"] = len(gaps)
    return {"counts": counts, "gaps": gaps}


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


def _build_remediation_plan(
    db: Session,
    diagnosis: dict[str, Any],
    *,
    requested_tables: list[str],
    capital_flow_code_limit: int,
    daily_kbar_code_limit: int,
    minute_kbar_code_limit: int,
) -> list[dict[str, Any]]:
    recent_tables = ((diagnosis.get("recent_trade_day_coverage") or {}).get("tables") or {})
    lag_days = diagnosis.get("lag_days") or {}
    reference_trade_date = str(diagnosis.get("reference_trade_date") or "")
    plan: list[dict[str, Any]] = []
    max_closed_trade_date = _latest_allowed_bar_date()

    if "daily_kbar" in requested_tables:
        target_dates = _target_dates_with_closed_reference_gap(
            _gap_dates(recent_tables.get("daily_kbar") or {}, include_incomplete=True),
            lag_days=lag_days,
            table_key="daily_kbar",
            reference_trade_date=reference_trade_date,
        )
        target_dates = _closed_target_dates(target_dates, reference_trade_date, max_closed_trade_date)
        for trade_date in target_dates:
            codes = _daily_kbar_target_codes(db, trade_date, limit=daily_kbar_code_limit)
            plan.append(
                {
                    "table": "daily_kbar",
                    "trade_date": trade_date,
                    "codes": codes,
                    "code_limit": daily_kbar_code_limit,
                }
            )

    if "chip" in requested_tables:
        for trade_date in _chip_target_dates(
            diagnosis,
            max_closed_trade_date=max_closed_trade_date,
        ):
            plan.append({"table": "chip", "trade_date": trade_date})

    if "minute_kbar" in requested_tables:
        target_dates = _target_dates_with_closed_reference_gap(
            (recent_tables.get("minute_kbar") or {}).get("missing_dates") or [],
            lag_days=lag_days,
            table_key="minute_kbar",
            reference_trade_date=reference_trade_date,
        )
        missing_dates = _closed_target_dates(
            target_dates,
            reference_trade_date,
            max_closed_trade_date,
        )
        for trade_date in missing_dates:
            codes = _minute_kbar_target_codes(db, trade_date, limit=minute_kbar_code_limit)
            plan.append(
                {
                    "table": "minute_kbar",
                    "trade_date": trade_date,
                    "codes": codes,
                    "code_limit": minute_kbar_code_limit,
                }
            )

    if "index_daily" in requested_tables:
        missing_dates = _target_dates_with_closed_reference_gap(
            (recent_tables.get("index_daily") or {}).get("missing_dates") or [],
            lag_days=lag_days,
            table_key="index_daily",
            reference_trade_date=reference_trade_date,
        )
        missing_dates = _closed_target_dates(missing_dates, reference_trade_date, max_closed_trade_date)
        if missing_dates:
            plan.append(
                {
                    "table": "index_daily",
                    "target_dates": missing_dates,
                    "ts_codes": _index_ts_codes(db),
                }
            )

    if "limit_up_board" in requested_tables:
        target_dates = _target_dates_with_closed_reference_gap(
            (recent_tables.get("limit_up_board") or {}).get("missing_dates") or [],
            lag_days=lag_days,
            table_key="limit_up_board",
            reference_trade_date=reference_trade_date,
        )
        missing_dates = _closed_target_dates(
            target_dates,
            reference_trade_date,
            max_closed_trade_date,
        )
        for trade_date in missing_dates:
            plan.append({"table": "limit_up_board", "trade_date": trade_date})

    if "capital_flow" in requested_tables:
        target_dates = _target_dates_with_closed_reference_gap(
            (recent_tables.get("capital_flow") or {}).get("missing_dates") or [],
            lag_days=lag_days,
            table_key="capital_flow",
            reference_trade_date=reference_trade_date,
        )
        missing_dates = _closed_target_dates(
            target_dates,
            reference_trade_date,
            max_closed_trade_date,
        )
        for trade_date in missing_dates:
            codes = _capital_flow_target_codes(db, trade_date, limit=capital_flow_code_limit)
            plan.append(
                {
                    "table": "capital_flow",
                    "trade_date": trade_date,
                    "codes": codes,
                    "code_limit": capital_flow_code_limit,
                }
            )

    if "concept_sector" in requested_tables:
        lag = int(lag_days.get("concept_sector", 0) or 0)
        if reference_trade_date and lag > 0:
            plan.append({"table": "concept_sector", "trade_date": reference_trade_date})

    if "dragon_tiger" in requested_tables:
        for trade_date in _reference_date_targets(diagnosis, "dragon_tiger"):
            plan.append({"table": "dragon_tiger", "trade_date": trade_date})

    if "news" in requested_tables:
        for trade_date in _reference_date_targets(diagnosis, "news"):
            codes = _auxiliary_target_codes(db, trade_date, limit=capital_flow_code_limit)
            plan.append(
                {
                    "table": "news",
                    "trade_date": trade_date,
                    "codes": codes,
                    "code_limit": capital_flow_code_limit,
                }
            )

    if "concept_member" in requested_tables and reference_trade_date:
        stale_keys = {
            str(domain.get("key") or "")
            for domain in ((diagnosis.get("data_domains") or {}).get("stale_domains") or [])
        }
        if "concept_member" in stale_keys:
            plan.append({"table": "concept_member", "trade_date": reference_trade_date})

    if "value_distribution" in requested_tables and reference_trade_date:
        sentinel = diagnosis.get("value_distribution") or {}
        distorted_columns = [
            str(item.get("column") or "")
            for item in (sentinel.get("distorted") or [])
            if item.get("column")
        ]
        quote_columns = [c for c in distorted_columns if c in QUOTE_METRIC_REPAIR_COLUMNS]
        local_columns = [c for c in distorted_columns if c in LOCAL_DERIVED_REPAIR_COLUMNS]
        unsupported = [
            c
            for c in distorted_columns
            if c not in QUOTE_METRIC_REPAIR_COLUMNS and c not in LOCAL_DERIVED_REPAIR_COLUMNS
        ]
        if quote_columns or local_columns:
            plan.append(
                {
                    "table": "value_distribution",
                    "trade_date": str(sentinel.get("reference_trade_date") or reference_trade_date),
                    "quote_columns": quote_columns,
                    "local_columns": local_columns,
                    "unsupported_columns": unsupported,
                }
            )

    return plan


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


def _execute_plan_item_safely(db: Session, item: dict[str, Any]) -> dict[str, Any]:
    try:
        return _execute_plan_item(db, item)
    except Exception as exc:
        db.rollback()
        return {
            **item,
            "status": "failed",
            "error": str(exc),
            "error_type": type(exc).__name__,
            "row_count": 0,
        }


def _repair_value_distribution(
    db: Session,
    trade_date: str,
    *,
    quote_columns: list[str],
    local_columns: list[str],
) -> dict[str, Any]:
    """按列修复值分布失真：权威行情列重拉当日日线覆盖，本地派生列重算。"""
    target = _normalize_trade_date(trade_date)
    if not target:
        return {"row_count": 0, "reason": "缺少参考交易日，未执行值分布修复。"}

    result: dict[str, Any] = {
        "quote_row_count": 0,
        "local_row_count": 0,
        "repaired_quote_columns": [],
        "repaired_local_columns": [],
    }

    if quote_columns:
        quote_result = _repair_quote_metric_columns(db, target, quote_columns)
        result["quote_row_count"] = quote_result["row_count"]
        result["repaired_quote_columns"] = quote_result["columns"]
        result["provider_row_count"] = quote_result["provider_row_count"]

    if local_columns:
        from scripts.backfill_indicators import backfill_date

        fixed = backfill_date(db, target)
        result["local_row_count"] = sum(int(value or 0) for value in fixed.values())
        result["repaired_local_columns"] = list(local_columns)
        result["local_backfill_detail"] = fixed

    result["row_count"] = int(result["quote_row_count"]) + int(result["local_row_count"])
    return result


def _repair_quote_metric_columns(
    db: Session,
    trade_date: str,
    columns: list[str],
) -> dict[str, Any]:
    """用当日全市场日线批量覆盖指定的权威行情列，其余列一律不动。

    取数走与日K补数同一个类型化数据源端口（一次全市场批量），不直接调 tushare_client 的
    日线助手——生产市场边界只允许经端口取数。
    """
    targets = [name for name in columns if name in QUOTE_METRIC_REPAIR_COLUMNS]
    if not targets:
        return {"row_count": 0, "columns": [], "provider_row_count": 0}

    codes = [
        str(code or "").strip()
        for code in db.scalars(
            select(DailyKbar.code).where(DailyKbar.trade_date == trade_date).distinct()
        ).all()
        if str(code or "").strip()
    ]
    if not codes:
        return {
            "row_count": 0,
            "columns": targets,
            "provider_row_count": 0,
            "reason": "该交易日库内没有日K行，无需覆盖。",
        }

    source = _resolve_market_source(None)
    batch = source.fetch_daily_bars(
        DailyBarRequest(
            instruments=tuple(QuoteRef(code) for code in codes),
            trade_date=trade_date,
        )
    )
    if batch.audit.failed_count:
        raise RuntimeError("market source failed to retrieve the daily-bar trade-date batch")
    rows = [
        bar.as_legacy_row()
        for bars in batch.bars_by_instrument.values()
        for bar in bars
        if _normalize_trade_date(bar.as_legacy_row().get("trade_date")) == trade_date
    ]
    if not rows:
        return {"row_count": 0, "columns": targets, "provider_row_count": 0}

    # 与日线批量同步同口径的防线：整批都没有非零涨跌幅说明数据源当日还没出数据，
    # 此时覆盖等于把假值写进库，直接放弃本次修复（L-146）。
    if quote_metrics_look_unready(rows):
        return {
            "row_count": 0,
            "columns": targets,
            "provider_row_count": len(rows),
            "reason": "数据源当日 pct_chg 整批为空，判定未就绪，未覆盖任何列。",
        }

    assignments = ", ".join(f"{name} = :{name}" for name in targets)
    statement = text(
        f"UPDATE daily_kbars SET {assignments} WHERE code = :code AND trade_date = :trade_date"
    )
    updated = 0
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        params: dict[str, Any] = {"code": code, "trade_date": trade_date}
        missing = False
        for name in targets:
            value = normalize_quote_metric(row.get(name))
            if value is None:
                missing = True
                break
            params[name] = value
        if missing:
            continue
        updated += db.execute(statement, params).rowcount or 0
    db.commit()
    return {"row_count": updated, "columns": targets, "provider_row_count": len(rows)}


def _execute_plan_item(db: Session, item: dict[str, Any]) -> dict[str, Any]:
    table = item["table"]
    if table == "daily_kbar":
        codes = item.get("codes") or []
        if not codes:
            return {
                **item,
                "status": "skipped",
                "reason": "目标交易日缺少股票样本，未执行日K补数。",
                "row_count": 0,
            }
        result = _backfill_daily_kbar_date(db, item["trade_date"], codes)
        return {
            **item,
            "status": "completed" if result.get("row_count") else "empty",
            **result,
            "requested_code_count": len(codes),
        }
    if table == "chip":
        result = _backfill_chip_date(db, item["trade_date"])
        return {
            **item,
            "status": "completed" if result.get("row_count") else "empty",
            **result,
        }
    if table == "minute_kbar":
        codes = item.get("codes") or []
        if not codes:
            return {
                **item,
                "status": "skipped",
                "reason": "目标交易日缺少活跃股票样本，未执行分钟K线补数。",
                "row_count": 0,
            }
        result = _backfill_minute_kbar_date(db, item["trade_date"], codes)
        return {
            **item,
            "status": "completed" if result.get("row_count") else "empty",
            **result,
            "requested_code_count": len(codes),
        }
    if table == "index_daily":
        return {
            **item,
            "status": "completed",
            **_backfill_index_daily_dates(db, item.get("target_dates") or [], item.get("ts_codes") or []),
        }
    if table == "limit_up_board":
        row_count, _audit = _refresh_auxiliary_dataset(
            db, dataset="limit_up_board", trade_date=item["trade_date"]
        )
        return {
            **item,
            "status": "completed" if row_count else "empty",
            "row_count": row_count,
        }
    if table == "capital_flow":
        codes = item.get("codes") or []
        if not codes:
            return {
                **item,
                "status": "skipped",
                "reason": "目标交易日缺少活跃股票样本，未执行资金流向补数。",
                "row_count": 0,
            }
        row_count, _audit = _refresh_auxiliary_dataset(
            db, dataset="capital_flow", trade_date=item["trade_date"], codes=list(codes)
        )
        return {
            **item,
            "status": "completed" if row_count else "empty",
            "row_count": row_count,
            "requested_code_count": len(codes),
        }
    if table == "concept_sector":
        row_count, _audit = _refresh_auxiliary_dataset(
            db, dataset="concept_sector", trade_date=item["trade_date"]
        )
        return {
            **item,
            "status": "completed" if row_count else "empty",
            "row_count": row_count,
        }
    if table == "concept_member":
        row_count, _audit = _refresh_auxiliary_dataset(
            db, dataset="concept_sector_stock", trade_date=item["trade_date"]
        )
        return {
            **item,
            "status": "completed" if row_count else "empty",
            "row_count": row_count,
        }
    if table == "value_distribution":
        result = _repair_value_distribution(
            db,
            item["trade_date"],
            quote_columns=list(item.get("quote_columns") or []),
            local_columns=list(item.get("local_columns") or []),
        )
        return {
            **item,
            "status": "completed" if result.get("row_count") else "empty",
            **result,
        }
    if table == "dragon_tiger":
        row_count, _audit = _refresh_auxiliary_dataset(
            db, dataset="dragon_tiger", trade_date=item["trade_date"]
        )
        return {
            **item,
            "status": "completed" if row_count else "empty",
            "row_count": row_count,
        }
    if table == "news":
        codes = item.get("codes") or []
        if not codes:
            return {
                **item,
                "status": "skipped",
                "reason": "目标交易日缺少活跃股票样本，未执行新闻公告补数。",
                "row_count": 0,
            }
        fallback: dict[str, Any] | None = None
        try:
            row_count, _audit = _refresh_auxiliary_dataset(
                db, dataset="news_announcement", trade_date=item["trade_date"], codes=list(codes)
            )
        except RuntimeError as contract_error:
            # The immutable event contract deliberately rejects unknown all-field schemas.
            # Roll back that attempt, then use the canonical Router's explicit pagination
            # to keep the operational NewsAnnouncement table fresh.
            db.rollback()
            logger.warning(
                "news event snapshot unavailable; using paginated anns_d remediation: %s",
                contract_error,
            )
            fallback = _backfill_news_announcements(
                db,
                trade_date=item["trade_date"],
                codes=list(codes),
            )
            row_count = int(fallback.get("row_count") or 0)
        return {
            **item,
            "status": "completed" if row_count else "empty",
            "row_count": row_count,
            "requested_code_count": len(codes),
            "fallback": "bounded_symbol_anns_d" if fallback is not None else None,
            "provider_row_count": fallback.get("provider_row_count") if fallback else None,
            "query_count": fallback.get("query_count") if fallback else None,
            "failed_code_count": fallback.get("failed_code_count") if fallback else None,
            "failed_codes": fallback.get("failed_codes") if fallback else None,
        }
    return {**item, "status": "skipped", "reason": "unsupported_table"}


def _index_ts_codes(db: Session) -> list[str]:
    rows = db.scalars(select(IndexDaily.ts_code).distinct().order_by(IndexDaily.ts_code.asc())).all()
    existing = [str(code) for code in rows if str(code or "").strip()]
    return existing or list(DEFAULT_INDEX_TS_CODES)


def _data_domain_by_key(diagnosis: dict[str, Any], key: str) -> dict[str, Any]:
    categories = ((diagnosis.get("data_domains") or {}).get("categories") or [])
    for category in categories:
        for domain in category.get("domains") or []:
            if domain.get("key") == key:
                return dict(domain)
    return {}


def _chip_target_dates(
    diagnosis: dict[str, Any],
    *,
    max_closed_trade_date: str,
) -> list[str]:
    domain = _data_domain_by_key(diagnosis, "chip")
    if not domain:
        return []
    if domain.get("status") == "covered":
        return []

    reference_trade_date = _normalize_trade_date(diagnosis.get("reference_trade_date"))
    max_chip_date = _normalize_trade_date(domain.get("max_date"))
    trade_days = [
        normalized
        for value in ((diagnosis.get("recent_trade_day_coverage") or {}).get("trade_days") or [])
        if (normalized := _normalize_trade_date(value))
    ]
    if max_chip_date:
        trade_days = [value for value in trade_days if value > max_chip_date]
    if not trade_days and reference_trade_date and (
        not max_chip_date or max_chip_date < reference_trade_date
    ):
        trade_days = [reference_trade_date]
    return _closed_target_dates(
        trade_days,
        reference_trade_date or max_closed_trade_date,
        max_closed_trade_date,
    )


def _backfill_chip_date(db: Session, trade_date: str) -> dict[str, Any]:
    target = _normalize_trade_date(trade_date)
    if not target:
        return {"row_count": 0, "provider_row_count": 0}

    frame = get_pro().cyq_perf(
        trade_date=target.replace("-", ""),
        fields="ts_code,trade_date,cost_15pct,cost_50pct,cost_85pct,weight_avg,winner_rate",
    )
    if frame is None or frame.empty:
        return {"row_count": 0, "provider_row_count": 0}

    rows_by_code: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        code = str(row.get("ts_code") or "").split(".")[0].strip()
        cost_50 = _optional_float(row.get("cost_50pct"))
        if len(code) != 6 or cost_50 is None:
            continue
        rows_by_code[code] = {
            "code": code,
            "trade_date": target,
            "chip_cost_15": _optional_float(row.get("cost_15pct")),
            "chip_cost_50": cost_50,
            "chip_cost_85": _optional_float(row.get("cost_85pct")),
            "chip_weight_avg": _optional_float(row.get("weight_avg")),
            "chip_winner_rate": _optional_float(row.get("winner_rate")),
        }
    if not rows_by_code:
        return {"row_count": 0, "provider_row_count": len(frame)}

    existing_codes = set(
        db.scalars(
            select(DailyKbar.code).where(
                DailyKbar.trade_date == target,
                DailyKbar.code.in_(list(rows_by_code)),
            )
        ).all()
    )
    payload = [rows_by_code[code] for code in rows_by_code if code in existing_codes]
    for start in range(0, len(payload), _DAILY_KBAR_UPSERT_BATCH_SIZE):
        chunk = payload[start:start + _DAILY_KBAR_UPSERT_BATCH_SIZE]
        stmt = mysql_insert(DailyKbar).values(chunk)
        db.execute(
            stmt.on_duplicate_key_update(
                chip_cost_15=stmt.inserted.chip_cost_15,
                chip_cost_50=stmt.inserted.chip_cost_50,
                chip_cost_85=stmt.inserted.chip_cost_85,
                chip_weight_avg=stmt.inserted.chip_weight_avg,
                chip_winner_rate=stmt.inserted.chip_winner_rate,
            )
        )
    db.commit()
    return {
        "row_count": len(payload),
        "provider_row_count": len(frame),
        "missing_daily_kbar_count": len(rows_by_code) - len(payload),
    }


def _backfill_daily_kbar_date(
    db: Session,
    trade_date: str,
    codes: list[str],
    *,
    market_source: MarketSourcePort | None = None,
) -> dict[str, Any]:
    """Backfill target symbols from one typed, full-market trade-date batch."""
    target = _normalize_trade_date(trade_date)
    if not target or not codes:
        return {"row_count": 0, "requested_code_count": len(codes), "target_hit_count": 0}

    target_codes = [str(code or "").strip() for code in codes if str(code or "").strip()]
    target_code_set = set(target_codes)
    source = _resolve_market_source(market_source)
    batch = source.fetch_daily_bars(
        DailyBarRequest(
            instruments=tuple(QuoteRef(code) for code in target_codes),
            trade_date=target,
        )
    )
    if batch.audit.failed_count:
        raise RuntimeError("market source failed to retrieve the daily-bar trade-date batch")
    source_rows = [
        bar.as_legacy_row()
        for bars in batch.bars_by_instrument.values()
        for bar in bars
    ]
    rows = [
        {**row, "code": str(row.get("code") or "").strip(), "trade_date": _normalize_trade_date(row.get("trade_date"))}
        for row in source_rows
        if str(row.get("code") or "").strip() in target_code_set and _normalize_trade_date(row.get("trade_date")) == target
    ]
    audit_payload = _market_source_audit_payload(batch)

    if rows:
        # 防线 B：若数据源返回的整批 pct_chg 全为 0/None，判定为
        # 「Tushare 当日 daily 尚未就绪」（盘后数据未出），不落库——
        # 避免用 0 覆盖已有的真实值或写入假数据，导致收益计算全 0。
        # 阈值 >=100 行才触发，小批量逐股票补数（停牌/新股正常 0）不误杀。
        if len(rows) >= 100 and not any(_float(r.get("pct_chg")) for r in rows):
            logger.warning(
                "daily_kbar 批量同步 %s: %d 行 pct_chg 全 0，判定数据源未就绪，跳过写库",
                target, len(rows),
            )
            return {
                "row_count": 0,
                "provider_row_count": batch.audit.returned_count,
                "requested_code_count": len(target_codes),
                "target_hit_count": 0,
                "missing_code_count": len(target_codes),
                "missing_codes": target_codes[:10],
                "status": "skipped",
                "reason": "pct_chg_all_zero_data_not_ready",
                "market_source_audit": audit_payload,
            }
        payload = [_daily_kbar_payload(row) for row in rows]
        _upsert_daily_kbar_payload(db, payload)

    hit_codes = {row["code"] for row in rows}
    missing_codes = [code for code in target_codes if code not in hit_codes]
    return {
        "row_count": len(rows),
        "provider_row_count": batch.audit.returned_count,
        "requested_code_count": len(target_codes),
        "target_hit_count": len(hit_codes),
        "missing_code_count": len(missing_codes),
        "missing_codes": missing_codes[:10],
        "market_source_audit": audit_payload,
    }


def _upsert_daily_kbar_payload(
    db: Session,
    payload: list[dict[str, Any]],
    *,
    batch_size: int = _DAILY_KBAR_UPSERT_BATCH_SIZE,
) -> None:
    if not payload:
        return
    size = max(1, int(batch_size or _DAILY_KBAR_UPSERT_BATCH_SIZE))
    for start in range(0, len(payload), size):
        chunk = payload[start:start + size]
        stmt = mysql_insert(DailyKbar).values(chunk)
        db.execute(stmt.on_duplicate_key_update(
            open=stmt.inserted.open,
            high=stmt.inserted.high,
            low=stmt.inserted.low,
            close=stmt.inserted.close,
            volume=stmt.inserted.volume,
            amount=stmt.inserted.amount,
            pct_chg=stmt.inserted.pct_chg,
            turnover_rate=stmt.inserted.turnover_rate,
            ma5=stmt.inserted.ma5,
            ma10=stmt.inserted.ma10,
            ma20=stmt.inserted.ma20,
            ma60=stmt.inserted.ma60,
            ma250=stmt.inserted.ma250,
            macd_dif=stmt.inserted.macd_dif,
            macd_dea=stmt.inserted.macd_dea,
            macd_bar=stmt.inserted.macd_bar,
            kdj_k=stmt.inserted.kdj_k,
            kdj_d=stmt.inserted.kdj_d,
            kdj_j=stmt.inserted.kdj_j,
            rsi_6=stmt.inserted.rsi_6,
            rsi_12=stmt.inserted.rsi_12,
            rsi_24=stmt.inserted.rsi_24,
            boll_upper=stmt.inserted.boll_upper,
            boll_mid=stmt.inserted.boll_mid,
            boll_lower=stmt.inserted.boll_lower,
            cci=stmt.inserted.cci,
            adj_factor=stmt.inserted.adj_factor,
            asi=stmt.inserted.asi,
            asit=stmt.inserted.asit,
            atr=stmt.inserted.atr,
            bbi=stmt.inserted.bbi,
            brar_ar=stmt.inserted.brar_ar,
            brar_br=stmt.inserted.brar_br,
            dpo=stmt.inserted.dpo,
            ema_12=stmt.inserted.ema_12,
            ema_50=stmt.inserted.ema_50,
            ma_volume_5=stmt.inserted.ma_volume_5,
            ma_volume_10=stmt.inserted.ma_volume_10,
            mtm=stmt.inserted.mtm,
            obv=stmt.inserted.obv,
            psy=stmt.inserted.psy,
            trix=stmt.inserted.trix,
            vr=stmt.inserted.vr,
            wr_6=stmt.inserted.wr_6,
            wr_10=stmt.inserted.wr_10,
            bias_5=stmt.inserted.bias_5,
            bias_10=stmt.inserted.bias_10,
            bias_20=stmt.inserted.bias_20,
            vol_ratio_5=stmt.inserted.vol_ratio_5,
            vol_ratio_20=stmt.inserted.vol_ratio_20,
            breakout20=stmt.inserted.breakout20,
            breakout60=stmt.inserted.breakout60,
            max_dd20=stmt.inserted.max_dd20,
            max_dd60=stmt.inserted.max_dd60,
            chip_cost_15=stmt.inserted.chip_cost_15,
            chip_cost_50=stmt.inserted.chip_cost_50,
            chip_cost_85=stmt.inserted.chip_cost_85,
            chip_weight_avg=stmt.inserted.chip_weight_avg,
            chip_winner_rate=stmt.inserted.chip_winner_rate,
            source=stmt.inserted.source,
            updated_at=stmt.inserted.updated_at,
        ))
    db.commit()


def _insert_daily_kbar_payload_ignore(
    db: Session,
    payload: list[dict[str, Any]],
    *,
    batch_size: int = _DAILY_KBAR_UPSERT_BATCH_SIZE,
) -> None:
    if not payload:
        return
    size = max(1, int(batch_size or _DAILY_KBAR_UPSERT_BATCH_SIZE))
    for start in range(0, len(payload), size):
        chunk = payload[start:start + size]
        db.execute(mysql_insert(DailyKbar).values(chunk).prefix_with("IGNORE"))
    db.commit()


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



def _backfill_missing_indicators(db: Session, lookback_days: int = 14) -> None:
    """补齐近期缺失的技术指标（MACD/RSI/Bias 等）。

    当 stk_factor API 未被调用或返回空数据时，DailyKbar 中的指标字段为 0。
    本函数从历史 K 线本地计算缺失指标并 upsert 回数据库。
    """
    from collections import defaultdict
    from datetime import date, timedelta

    from sqlalchemy import select, text

    from app.models.tables import DailyKbar
    from app.services.indicator_computer import compute_daily_bar_indicators, compute_daily_indicators
    from app.services.market_pool.core import _persist_kbars

    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    # 找出近期指标缺失的日期（macd_dif 全为 0 且记录数 > 100）
    gap_dates = db.execute(text(
        "SELECT trade_date FROM daily_kbars "
        "WHERE trade_date >= :cutoff "
        "GROUP BY trade_date "
        "HAVING SUM(CASE WHEN macd_dif IS NOT NULL AND macd_dif != 0 THEN 1 ELSE 0 END) = 0 "
        "AND COUNT(*) > 100 "
        "ORDER BY trade_date"
    ), {"cutoff": cutoff}).scalars().all()

    if not gap_dates:
        return

    for target_date in gap_dates:
        codes = db.execute(text(
            "SELECT DISTINCT code FROM daily_kbars WHERE trade_date = :td"
        ), {"td": target_date}).scalars().all()
        if not codes:
            continue

        all_rows = db.scalars(
            select(DailyKbar)
            .where(DailyKbar.code.in_(codes), DailyKbar.trade_date <= target_date)
            .order_by(DailyKbar.code, DailyKbar.trade_date.asc())
        ).all()

        bars_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in all_rows:
            bars_by_code[row.code].append({
                "code": row.code,
                "trade_date": str(row.trade_date),
                "open": float(row.open or 0),
                "high": float(row.high or 0),
                "low": float(row.low or 0),
                "close": float(row.close or 0),
                "volume": float(row.volume or 0),
                "amount": float(row.amount or 0),
                "pct_chg": float(row.pct_chg or 0),
                "turnover_rate": float(row.turnover_rate or 0),
            })

        enriched: list[dict[str, Any]] = []
        for code, bars in bars_by_code.items():
            bars = bars[-60:]
            if len(bars) < 5:
                continue
            bar_ind = compute_daily_bar_indicators(code, bars)
            daily_ind = compute_daily_indicators(code, bars)
            merged = {**bar_ind.get(target_date, {}), **daily_ind.get(target_date, {})}
            if merged:
                target_bar = next((b for b in bars if b["trade_date"] == target_date), None)
                if target_bar:
                    enriched.append({**target_bar, **merged})

        if enriched:
            _persist_kbars(db, enriched)
            db.commit()
            logger.info("补齐 %s 指标: %d/%d codes", target_date, len(enriched), len(codes))


def _backfill_index_daily_dates(
    db: Session,
    target_dates: list[str],
    ts_codes: list[str],
) -> dict[str, Any]:
    normalized_dates = sorted(
        {
            normalized
            for date in target_dates
            if (normalized := _normalize_trade_date(date))
        }
    )
    if not normalized_dates or not ts_codes:
        return {"row_count": 0, "requested_date_count": len(normalized_dates), "requested_ts_code_count": len(ts_codes)}

    payload: list[dict[str, Any]] = []
    start_date = min(normalized_dates).replace("-", "")
    end_date = max(normalized_dates).replace("-", "")
    target_date_set = set(normalized_dates)
    for ts_code in ts_codes:
        rows = fetch_index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date) or []
        for row in rows:
            trade_date = _normalize_trade_date(row.get("trade_date"))
            if trade_date not in target_date_set:
                continue
            payload.append(
                {
                    "ts_code": str(row.get("ts_code") or ts_code),
                    "trade_date": trade_date,
                    "close": float(row.get("close") or 0),
                    "pct_chg": float(row.get("pct_chg") or 0),
                    "open": float(row.get("open") or 0),
                    "high": float(row.get("high") or 0),
                    "low": float(row.get("low") or 0),
                    "volume": float(row.get("volume") or 0),
                }
            )
    if payload:
        stmt = mysql_insert(IndexDaily).values(payload).prefix_with("IGNORE")
        db.execute(stmt)
        db.commit()
    return {
        "row_count": len(payload),
        "requested_date_count": len(normalized_dates),
        "requested_ts_code_count": len(ts_codes),
    }


def _backfill_minute_kbar_date(
    db: Session,
    trade_date: str,
    codes: list[str],
    freq: str = "1min",
) -> dict[str, Any]:
    target = _normalize_trade_date(trade_date)
    target_codes = [str(code or "").strip() for code in codes if str(code or "").strip()]
    normalized_freq = str(freq or "1min").lower()
    if not target or not target_codes:
        return {"row_count": 0, "requested_code_count": len(target_codes), "target_hit_count": 0}

    payload: list[dict[str, Any]] = []
    provider_row_count = 0
    failed_codes: list[str] = []

    def _fetch_code(code: str) -> tuple[str, list[dict[str, Any]], Exception | None]:
        try:
            return code, fetch_minute_kbars(code, trade_date=target, freq=normalized_freq) or [], None
        except Exception as exc:  # noqa: BLE001 - one provider failure must not abort the batch
            return code, [], exc

    probe_codes = target_codes[:MINUTE_REMEDIATION_PROBE_COUNT]
    probe_results = _fetch_minute_code_batch(_fetch_code, probe_codes)
    if _minute_probe_failed(probe_results):
        errors = [str(error) for _, _, error in probe_results if error is not None]
        return {
            "status": "failed",
            "row_count": 0,
            "provider_row_count": 0,
            "requested_code_count": len(target_codes),
            "target_hit_count": 0,
            "missing_code_count": len(target_codes),
            "missing_codes": target_codes[:10],
            "failed_code_count": len(probe_results),
            "failed_codes": [code for code, _, _ in probe_results],
            "skipped_code_count": len(target_codes) - len(probe_results),
            "error_type": "MinuteProviderUnavailable",
            "error": f"分钟K线供应商探测失败：{errors[0][:160]}",
        }

    fetch_results = [
        *probe_results,
        *_fetch_minute_code_batch(_fetch_code, target_codes[len(probe_codes):]),
    ]
    for code, rows, error in fetch_results:
        if error is not None:
            failed_codes.append(code)
        provider_row_count += len(rows)
        for row in rows:
            trade_date_value = _normalize_trade_date(row.get("trade_date"))
            if trade_date_value != target:
                continue
            trade_time = str(row.get("trade_time") or "").strip()
            if not trade_time:
                continue
            payload.append(
                {
                    "code": code,
                    "freq": normalized_freq,
                    "trade_date": trade_date_value,
                    "trade_time": trade_time,
                    "open": _float(row.get("open")),
                    "high": _float(row.get("high")),
                    "low": _float(row.get("low")),
                    "close": _float(row.get("close")),
                    "volume": _float(row.get("volume")),
                    "amount": _float(row.get("amount")),
                }
            )

    if payload:
        stmt = mysql_insert(MinuteKbar).values(payload)
        db.execute(stmt.on_duplicate_key_update(
            open=stmt.inserted.open,
            high=stmt.inserted.high,
            low=stmt.inserted.low,
            close=stmt.inserted.close,
            volume=stmt.inserted.volume,
            amount=stmt.inserted.amount,
        ))
        db.commit()

    hit_codes = {row["code"] for row in payload}
    missing_codes = [code for code in target_codes if code not in hit_codes]
    return {
        "row_count": len(payload),
        "provider_row_count": provider_row_count,
        "requested_code_count": len(target_codes),
        "target_hit_count": len(hit_codes),
        "missing_code_count": len(missing_codes),
        "missing_codes": missing_codes[:10],
        "failed_code_count": len(failed_codes),
        "failed_codes": failed_codes[:10],
    }


def _fetch_minute_code_batch(
    fetch_code: Callable[[str], tuple[str, list[dict[str, Any]], Exception | None]],
    codes: list[str],
) -> list[tuple[str, list[dict[str, Any]], Exception | None]]:
    if not codes:
        return []
    results: list[tuple[str, list[dict[str, Any]], Exception | None]] = []
    with ThreadPoolExecutor(
        max_workers=min(MINUTE_REMEDIATION_WORKERS, len(codes)),
        thread_name_prefix="minute-remediation",
    ) as pool:
        futures = [pool.submit(fetch_code, code) for code in codes]
        for future in as_completed(futures):
            results.append(future.result())
    return results


def _minute_probe_failed(
    results: list[tuple[str, list[dict[str, Any]], Exception | None]],
) -> bool:
    return bool(results) and all(not rows and error is not None for _, rows, error in results)


def _capital_flow_target_codes(db: Session, trade_date: str, *, limit: int) -> list[str]:
    return _auxiliary_target_codes(db, trade_date, limit=limit)


def _minute_kbar_target_codes(db: Session, trade_date: str, *, limit: int) -> list[str]:
    target = _normalize_trade_date(trade_date)
    if not target:
        return []
    target_variants = _trade_date_variants(target)
    existing_codes = {
        str(code or "").strip()
        for code in db.scalars(select(MinuteKbar.code).where(MinuteKbar.trade_date.in_(target_variants))).all()
        if str(code or "").strip()
    }
    ordered_codes = OrderedDict()

    active_codes = db.scalars(
        select(DailyKbar.code)
        .where(DailyKbar.trade_date.in_(target_variants))
        .order_by(DailyKbar.amount.desc(), DailyKbar.code.asc())
        .limit(max(limit * 4, limit))
    ).all()
    for code in active_codes:
        normalized = str(code or "").strip()
        if normalized and normalized not in existing_codes:
            ordered_codes[normalized] = True
        if len(ordered_codes) >= limit:
            break

    if len(ordered_codes) < limit:
        quote_codes = db.scalars(
            select(StockQuote.code)
            .where(StockQuote.trade_date.in_(target_variants))
            .order_by(StockQuote.amount_yi.desc(), StockQuote.code.asc())
            .limit(max(limit * 4, limit))
        ).all()
        for code in quote_codes:
            normalized = str(code or "").strip()
            if normalized and normalized not in existing_codes:
                ordered_codes[normalized] = True
            if len(ordered_codes) >= limit:
                break

    return list(ordered_codes.keys())[: max(1, limit)]


def _daily_kbar_target_codes(db: Session, trade_date: str, *, limit: int) -> list[str]:
    target = _normalize_trade_date(trade_date)
    if not target:
        return []
    target_variants = _trade_date_variants(target)
    ordered_codes = OrderedDict()
    quote_codes = db.scalars(
        select(StockQuote.code)
        .where(StockQuote.trade_date.in_(target_variants))
        .order_by(StockQuote.amount_yi.desc(), StockQuote.code.asc())
        .limit(max(limit * 4, limit))
    ).all()
    for code in quote_codes:
        normalized = str(code or "").strip()
        if normalized:
            ordered_codes[normalized] = True

    if len(ordered_codes) < limit:
        recent_codes = db.scalars(
            select(DailyKbar.code)
            .where(DailyKbar.trade_date < target)
            .order_by(DailyKbar.trade_date.desc(), DailyKbar.amount.desc(), DailyKbar.code.asc())
            .limit(max(limit * 4, limit))
        ).all()
        for code in recent_codes:
            normalized = str(code or "").strip()
            if normalized:
                ordered_codes[normalized] = True
            if len(ordered_codes) >= limit:
                break
    return list(ordered_codes.keys())[: max(1, limit)]


def _reference_date_targets(diagnosis: dict[str, Any], table_key: str) -> list[str]:
    reference_trade_date = str(diagnosis.get("reference_trade_date") or "")
    if not reference_trade_date:
        return []
    table_info = (diagnosis.get("tables") or {}).get(table_key) or {}
    lag = int((diagnosis.get("lag_days") or {}).get(table_key) or 0)
    if lag > 0 or not table_info.get("max_date"):
        return [reference_trade_date]
    return []


def _auxiliary_target_codes(db: Session, trade_date: str, *, limit: int) -> list[str]:
    ordered_codes = OrderedDict()

    board_codes = db.scalars(
        select(LimitUpBoard.code)
        .where(LimitUpBoard.trade_date == trade_date)
        .order_by(LimitUpBoard.code.asc())
    ).all()
    for code in board_codes:
        normalized = str(code or "").strip()
        if normalized:
            ordered_codes[normalized] = True

    active_codes = db.scalars(
        select(DailyKbar.code)
        .where(DailyKbar.trade_date == trade_date)
        .order_by(DailyKbar.amount.desc(), DailyKbar.code.asc())
        .limit(max(1, limit))
    ).all()
    for code in active_codes:
        normalized = str(code or "").strip()
        if normalized:
            ordered_codes[normalized] = True

    if len(ordered_codes) < limit:
        quote_codes = db.scalars(
            select(StockQuote.code)
            .where(StockQuote.trade_date == trade_date)
            .order_by(StockQuote.amount_yi.desc(), StockQuote.code.asc())
            .limit(max(1, limit))
        ).all()
        for code in quote_codes:
            normalized = str(code or "").strip()
            if normalized:
                ordered_codes[normalized] = True

    return list(ordered_codes.keys())[: max(1, limit)]


def _gap_dates(coverage: dict[str, Any], *, include_incomplete: bool = False) -> list[str]:
    dates = list(coverage.get("missing_dates") or [])
    if include_incomplete:
        dates.extend(coverage.get("incomplete_dates") or [])
    return sorted(set(_normalize_trade_date(date) for date in dates if _normalize_trade_date(date)))


def _target_dates_with_closed_reference_gap(
    dates: list[str],
    *,
    lag_days: dict[str, Any],
    table_key: str,
    reference_trade_date: str,
) -> list[str]:
    target_dates = [_normalize_trade_date(date) for date in dates if _normalize_trade_date(date)]
    if reference_trade_date and int(lag_days.get(table_key) or 0) > 0:
        target_dates.append(reference_trade_date)
    return sorted(set(target_dates))


def _closed_target_dates(dates: list[str], reference_trade_date: str, max_closed_trade_date: str) -> list[str]:
    cutoff = min(
        _normalize_trade_date(reference_trade_date) or max_closed_trade_date,
        _normalize_trade_date(max_closed_trade_date) or reference_trade_date,
    )
    return sorted(
        set(
            normalized
            for date in dates
            if (normalized := _normalize_trade_date(date)) and normalized <= cutoff
        )
    )


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


def _compare_diagnosis(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    requested_tables: list[str],
) -> dict[str, Any]:
    before_snapshot = _diagnosis_snapshot(before)
    after_snapshot = _diagnosis_snapshot(after)
    table_deltas = {}
    for key in requested_tables:
        before_table = before_snapshot["tables"].get(key) or {}
        after_table = after_snapshot["tables"].get(key) or {}
        before_missing_dates = sorted(set(str(item) for item in before_table.get("missing_dates") or [] if item))
        after_missing_dates = sorted(set(str(item) for item in after_table.get("missing_dates") or [] if item))
        before_incomplete_dates = sorted(set(str(item) for item in before_table.get("incomplete_dates") or [] if item))
        after_incomplete_dates = sorted(set(str(item) for item in after_table.get("incomplete_dates") or [] if item))
        before_gap_dates = sorted(set(before_missing_dates + before_incomplete_dates))
        after_gap_dates = sorted(set(after_missing_dates + after_incomplete_dates))
        resolved_missing_dates = [date for date in before_missing_dates if date not in after_missing_dates]
        unresolved_missing_dates = [date for date in before_missing_dates if date in after_missing_dates]
        resolved_incomplete_dates = [date for date in before_incomplete_dates if date not in after_incomplete_dates]
        unresolved_incomplete_dates = [date for date in before_incomplete_dates if date in after_incomplete_dates]
        resolved_gap_dates = [date for date in before_gap_dates if date not in after_gap_dates]
        unresolved_gap_dates = [date for date in before_gap_dates if date in after_gap_dates]
        target_missing_day_count = len(before_gap_dates)
        resolved_missing_day_count = len(resolved_gap_dates)
        table_deltas[key] = {
            "before_lag_days": before_table.get("lag_days"),
            "after_lag_days": after_table.get("lag_days"),
            "lag_days_delta": _numeric_delta(after_table.get("lag_days"), before_table.get("lag_days")),
            "before_missing_days": len(before_gap_dates),
            "after_missing_days": len(after_gap_dates),
            "before_incomplete_days": len(before_incomplete_dates),
            "after_incomplete_days": len(after_incomplete_dates),
            "target_missing_day_count": target_missing_day_count,
            "resolved_missing_day_count": resolved_missing_day_count,
            "unresolved_missing_day_count": len(unresolved_gap_dates),
            "remediation_coverage_rate": round(resolved_missing_day_count / target_missing_day_count, 3)
            if target_missing_day_count
            else 1.0,
            "before_missing_dates": before_missing_dates,
            "after_missing_dates": after_missing_dates,
            "resolved_missing_dates": resolved_missing_dates,
            "unresolved_missing_dates": unresolved_missing_dates,
            "before_incomplete_dates": before_incomplete_dates,
            "after_incomplete_dates": after_incomplete_dates,
            "resolved_incomplete_dates": resolved_incomplete_dates,
            "unresolved_incomplete_dates": unresolved_incomplete_dates,
        }
    target_total = sum(int(item.get("target_missing_day_count") or 0) for item in table_deltas.values())
    resolved_total = sum(int(item.get("resolved_missing_day_count") or 0) for item in table_deltas.values())
    unresolved_total = sum(int(item.get("unresolved_missing_day_count") or 0) for item in table_deltas.values())
    return {
        "issue_count_delta": after_snapshot["issue_count"] - before_snapshot["issue_count"],
        "score_delta": after_snapshot["score"] - before_snapshot["score"],
        "max_missing_days_delta": (
            after_snapshot["recent_summary"]["max_missing_days"]
            - before_snapshot["recent_summary"]["max_missing_days"]
        ),
        "target_missing_day_count": target_total,
        "resolved_missing_day_count": resolved_total,
        "unresolved_missing_day_count": unresolved_total,
        "remediation_coverage_rate": round(resolved_total / target_total, 3) if target_total else 1.0,
        "tables_fully_remediated": [
            key for key, item in table_deltas.items() if int(item.get("target_missing_day_count") or 0) > 0 and int(item.get("unresolved_missing_day_count") or 0) == 0
        ],
        "tables_still_missing": [
            key for key, item in table_deltas.items() if int(item.get("unresolved_missing_day_count") or 0) > 0
        ],
        "table_deltas": table_deltas,
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
