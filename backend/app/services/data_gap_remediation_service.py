from __future__ import annotations

from collections import OrderedDict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar, IndexDaily, LimitUpBoard, StockQuote
from app.services.data_health_service import diagnose_all
from app.services.market_pool import (
    refresh_capital_flows_for_codes,
    refresh_concept_sectors,
    refresh_limit_up_board,
)
from app.services.tushare_client import fetch_index_daily

DEFAULT_REMEDIABLE_TABLES = (
    "index_daily",
    "limit_up_board",
    "capital_flow",
    "concept_sector",
)
DEFAULT_INDEX_TS_CODES = (
    "000001.SH",
    "000300.SH",
    "000905.SH",
    "000852.SH",
    "399001.SZ",
    "399006.SZ",
)


def remediate_data_health_gaps(
    db: Session,
    *,
    target_tables: list[str] | None = None,
    dry_run: bool = False,
    capital_flow_code_limit: int = 200,
) -> dict[str, Any]:
    requested_tables = _normalize_target_tables(target_tables)
    before = diagnose_all(db)
    plan = _build_remediation_plan(
        db,
        before,
        requested_tables=requested_tables,
        capital_flow_code_limit=capital_flow_code_limit,
    )

    actions = [_with_rollback_hint(_mark_planned(item)) for item in plan]
    if not dry_run:
        actions = [_with_rollback_hint(_execute_plan_item_safely(db, item)) for item in plan]

    after = before if dry_run else diagnose_all(db)
    failed_count = sum(1 for item in actions if item.get("status") == "failed")
    return {
        "status": _remediation_status(dry_run=dry_run, actions=actions, failed_count=failed_count),
        "requested_tables": requested_tables,
        "before": _diagnosis_snapshot(before),
        "after": _diagnosis_snapshot(after),
        "actions": actions,
        "operation_log": _operation_log(actions),
        "action_summary": {
            "planned_count": len(plan),
            "completed_count": sum(1 for item in actions if item.get("status") == "completed"),
            "empty_count": sum(1 for item in actions if item.get("status") == "empty"),
            "skipped_count": sum(1 for item in actions if item.get("status") == "skipped"),
            "failed_count": failed_count,
        },
        "comparison": _compare_diagnosis(before, after, requested_tables=requested_tables),
        "rollback_guidance": _rollback_guidance(dry_run=dry_run, actions=actions),
        "remaining_issues": after.get("issues") or [],
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


def _build_remediation_plan(
    db: Session,
    diagnosis: dict[str, Any],
    *,
    requested_tables: list[str],
    capital_flow_code_limit: int,
) -> list[dict[str, Any]]:
    recent_tables = ((diagnosis.get("recent_trade_day_coverage") or {}).get("tables") or {})
    lag_days = diagnosis.get("lag_days") or {}
    reference_trade_date = str(diagnosis.get("reference_trade_date") or "")
    plan: list[dict[str, Any]] = []

    if "index_daily" in requested_tables:
        missing_dates = list((recent_tables.get("index_daily") or {}).get("missing_dates") or [])
        if reference_trade_date and lag_days.get("index_daily", 0) > 0 and reference_trade_date not in missing_dates:
            missing_dates.append(reference_trade_date)
        missing_dates = sorted(set(date for date in missing_dates if date))
        if missing_dates:
            plan.append(
                {
                    "table": "index_daily",
                    "target_dates": missing_dates,
                    "ts_codes": _index_ts_codes(db),
                }
            )

    if "limit_up_board" in requested_tables:
        missing_dates = sorted(set((recent_tables.get("limit_up_board") or {}).get("missing_dates") or []))
        for trade_date in missing_dates:
            plan.append({"table": "limit_up_board", "trade_date": trade_date})

    if "capital_flow" in requested_tables:
        missing_dates = sorted(set((recent_tables.get("capital_flow") or {}).get("missing_dates") or []))
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

    return plan


def _mark_planned(item: dict[str, Any]) -> dict[str, Any]:
    action = dict(item)
    action["status"] = "planned"
    return action


def _with_rollback_hint(action: dict[str, Any]) -> dict[str, Any]:
    hint = _rollback_hint(action)
    return {**action, "rollback_hint": hint} if hint else action


def _operation_log(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in actions:
        row = {
            "table": item.get("table") or item.get("scope") or "unknown",
            "status": item.get("status"),
            "target": _action_target_label(item),
            "row_count": int(item.get("row_count") or 0),
            "rollback_hint": item.get("rollback_hint"),
        }
        if item.get("error"):
            row["error"] = item.get("error")
            row["error_type"] = item.get("error_type")
        rows.append(row)
    return rows


def _action_target_label(item: dict[str, Any]) -> str:
    dates = [str(date) for date in item.get("target_dates") or [] if str(date or "").strip()]
    if dates:
        return ", ".join(dates)
    trade_date = str(item.get("trade_date") or "").strip()
    if trade_date:
        return trade_date
    start = str(item.get("trade_date_start") or "").strip()
    end = str(item.get("trade_date_end") or "").strip()
    if start and end:
        return f"{start} ~ {end}"
    return "-"


def _rollback_hint(item: dict[str, Any]) -> str:
    table = str(item.get("table") or "").strip()
    target = _action_target_label(item)
    if table == "index_daily":
        return f"按 index_daily.ts_code + trade_date 精确删除本次目标日期（{target}）写入行，再重新诊断。"
    if table == "limit_up_board":
        return f"按 limit_up_board.trade_date = {target} 删除本次写入行，再重新诊断。"
    if table == "capital_flow":
        codes = [str(code) for code in item.get("codes") or [] if str(code or "").strip()]
        code_scope = f"且 code 限定在本次 {len(codes)} 个目标代码内" if codes else "并核对本次目标代码"
        return f"按 capital_flow.trade_date = {target} {code_scope}删除本次写入行，再重新诊断。"
    if table == "concept_sector":
        return f"按 concept_sector / concept_sector_stock 的 trade_date = {target} 删除本次写入行，再重新诊断。"
    return ""


def _rollback_guidance(*, dry_run: bool, actions: list[dict[str, Any]]) -> dict[str, Any]:
    if dry_run:
        return {
            "mode": "no_write",
            "message": "本次为预演，未写入数据库，无需回滚。",
            "keys": [],
        }
    keys = sorted(
        {
            f"{item.get('table') or item.get('scope')}: {_action_target_label(item)}"
            for item in actions
            if item.get("table") or item.get("scope")
        }
    )
    return {
        "mode": "delete_by_operation_log",
        "message": "本次补数为按表、日期和目标代码的增量写入；如需撤回，请按操作日志中的表名、日期和代码范围精确删除本次写入行，再重新运行数据健康诊断。",
        "keys": keys,
    }


def _remediation_status(*, dry_run: bool, actions: list[dict[str, Any]], failed_count: int) -> str:
    if dry_run:
        return "dry_run"
    if not actions:
        return "no_action"
    if failed_count:
        return "partial_failed"
    return "completed"


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


def _execute_plan_item(db: Session, item: dict[str, Any]) -> dict[str, Any]:
    table = item["table"]
    if table == "index_daily":
        return {
            **item,
            "status": "completed",
            **_backfill_index_daily_dates(db, item.get("target_dates") or [], item.get("ts_codes") or []),
        }
    if table == "limit_up_board":
        rows = refresh_limit_up_board(db, trade_date=item["trade_date"])
        return {
            **item,
            "status": "completed" if rows else "empty",
            "row_count": len(rows),
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
        rows = refresh_capital_flows_for_codes(db, codes, trade_date=item["trade_date"])
        return {
            **item,
            "status": "completed" if rows else "empty",
            "row_count": len(rows),
            "requested_code_count": len(codes),
        }
    if table == "concept_sector":
        rows = refresh_concept_sectors(db, trade_date=item["trade_date"])
        return {
            **item,
            "status": "completed" if rows else "empty",
            "row_count": len(rows),
        }
    return {**item, "status": "skipped", "reason": "unsupported_table"}


def _index_ts_codes(db: Session) -> list[str]:
    rows = db.scalars(select(IndexDaily.ts_code).distinct().order_by(IndexDaily.ts_code.asc())).all()
    existing = [str(code) for code in rows if str(code or "").strip()]
    return existing or list(DEFAULT_INDEX_TS_CODES)


def _backfill_index_daily_dates(
    db: Session,
    target_dates: list[str],
    ts_codes: list[str],
) -> dict[str, Any]:
    normalized_dates = sorted(set(str(date) for date in target_dates if str(date or "").strip()))
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


def _capital_flow_target_codes(db: Session, trade_date: str, *, limit: int) -> list[str]:
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


def _diagnosis_snapshot(diagnosis: dict[str, Any]) -> dict[str, Any]:
    recent = diagnosis.get("recent_trade_day_coverage") or {}
    summary = recent.get("summary") or {}
    tables = diagnosis.get("tables") or {}
    lag_days = diagnosis.get("lag_days") or {}
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
        "tables": {
            key: {
                "max_date": (tables.get(key) or {}).get("max_date"),
                "lag_days": lag_days.get(key),
                "missing_dates": list(((recent.get("tables") or {}).get(key) or {}).get("missing_dates") or []),
            }
            for key in DEFAULT_REMEDIABLE_TABLES
        },
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
        resolved_missing_dates = [date for date in before_missing_dates if date not in after_missing_dates]
        unresolved_missing_dates = [date for date in before_missing_dates if date in after_missing_dates]
        target_missing_day_count = len(before_missing_dates)
        resolved_missing_day_count = len(resolved_missing_dates)
        table_deltas[key] = {
            "before_lag_days": before_table.get("lag_days"),
            "after_lag_days": after_table.get("lag_days"),
            "lag_days_delta": _numeric_delta(after_table.get("lag_days"), before_table.get("lag_days")),
            "before_missing_days": len(before_missing_dates),
            "after_missing_days": len(after_missing_dates),
            "target_missing_day_count": target_missing_day_count,
            "resolved_missing_day_count": resolved_missing_day_count,
            "unresolved_missing_day_count": len(unresolved_missing_dates),
            "remediation_coverage_rate": round(resolved_missing_day_count / target_missing_day_count, 3)
            if target_missing_day_count
            else 1.0,
            "before_missing_dates": before_missing_dates,
            "after_missing_dates": after_missing_dates,
            "resolved_missing_dates": resolved_missing_dates,
            "unresolved_missing_dates": unresolved_missing_dates,
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


def _normalize_trade_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return raw[:10]
