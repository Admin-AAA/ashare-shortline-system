from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar, IndexDaily, LimitUpBoard, StockQuote
from app.services.data_health_service import diagnose_all
from app.services.market_pool import (
    refresh_capital_flows_for_codes,
    refresh_concept_sectors,
    refresh_dragon_tiger,
    refresh_limit_up_board,
    refresh_news_for_codes,
)
from app.services.tushare_client import fetch_daily_kbars_for_trade_date, fetch_index_daily

DEFAULT_REMEDIABLE_TABLES = (
    "daily_kbar",
    "index_daily",
    "limit_up_board",
    "capital_flow",
    "concept_sector",
    "dragon_tiger",
    "news",
)
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


def remediate_data_health_gaps(
    db: Session,
    *,
    target_tables: list[str] | None = None,
    dry_run: bool = False,
    capital_flow_code_limit: int = 200,
    daily_kbar_code_limit: int = DEFAULT_DAILY_KBAR_CODE_LIMIT,
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
    )

    actions = [_with_rollback_hint(_mark_planned(item)) for item in plan]
    if not dry_run:
        actions = [_with_rollback_hint(_execute_plan_item_safely(db, item)) for item in plan]

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
            after = diagnose_all(db)
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
        "action_summary": {
            "planned_count": len(plan),
            "completed_count": sum(1 for item in actions if item.get("status") == "completed"),
            "empty_count": sum(1 for item in actions if item.get("status") == "empty"),
            "skipped_count": sum(1 for item in actions if item.get("status") == "skipped"),
            "failed_count": failed_count,
        },
        "comparison": comparison,
        "rollback_guidance": _rollback_guidance(dry_run=dry_run, actions=actions),
        "remaining_issues": remaining_issues,
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
    daily_kbar_code_limit: int,
) -> list[dict[str, Any]]:
    recent_tables = ((diagnosis.get("recent_trade_day_coverage") or {}).get("tables") or {})
    lag_days = diagnosis.get("lag_days") or {}
    reference_trade_date = str(diagnosis.get("reference_trade_date") or "")
    plan: list[dict[str, Any]] = []
    max_closed_trade_date = _latest_allowed_bar_date()

    if "daily_kbar" in requested_tables:
        target_dates = _gap_dates(recent_tables.get("daily_kbar") or {}, include_incomplete=True)
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

    if "index_daily" in requested_tables:
        missing_dates = list((recent_tables.get("index_daily") or {}).get("missing_dates") or [])
        if reference_trade_date and lag_days.get("index_daily", 0) > 0 and reference_trade_date not in missing_dates:
            missing_dates.append(reference_trade_date)
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
        missing_dates = _closed_target_dates(
            (recent_tables.get("limit_up_board") or {}).get("missing_dates") or [],
            reference_trade_date,
            max_closed_trade_date,
        )
        for trade_date in missing_dates:
            plan.append({"table": "limit_up_board", "trade_date": trade_date})

    if "capital_flow" in requested_tables:
        missing_dates = _closed_target_dates(
            (recent_tables.get("capital_flow") or {}).get("missing_dates") or [],
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
    if table == "daily_kbar":
        codes = [str(code) for code in item.get("codes") or [] if str(code or "").strip()]
        code_scope = f"且 code 限定在本次 {len(codes)} 个目标代码内" if codes else "并核对本次目标代码"
        return f"按 daily_kbars.trade_date = {target} {code_scope}删除本次写入行，再重新诊断。"
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
    if table == "dragon_tiger":
        return f"按 dragon_tiger.trade_date = {target} 删除本次写入行，再重新诊断。"
    if table == "news":
        codes = [str(code) for code in item.get("codes") or [] if str(code or "").strip()]
        code_scope = f"且 code 限定在本次 {len(codes)} 个目标代码内" if codes else "并核对本次目标代码"
        return f"按 news_announcement.announce_date 覆盖窗口且 {code_scope}删除本次写入行，再重新诊断。"
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
    if table == "dragon_tiger":
        rows = refresh_dragon_tiger(db, trade_date=item["trade_date"])
        return {
            **item,
            "status": "completed" if rows else "empty",
            "row_count": len(rows),
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
        rows = refresh_news_for_codes(db, codes, trade_date=item["trade_date"])
        return {
            **item,
            "status": "completed" if rows else "empty",
            "row_count": sum(len(items) for items in rows.values()),
            "requested_code_count": len(codes),
        }
    return {**item, "status": "skipped", "reason": "unsupported_table"}


def _index_ts_codes(db: Session) -> list[str]:
    rows = db.scalars(select(IndexDaily.ts_code).distinct().order_by(IndexDaily.ts_code.asc())).all()
    existing = [str(code) for code in rows if str(code or "").strip()]
    return existing or list(DEFAULT_INDEX_TS_CODES)


def _backfill_daily_kbar_date(db: Session, trade_date: str, codes: list[str]) -> dict[str, Any]:
    target = _normalize_trade_date(trade_date)
    if not target or not codes:
        return {"row_count": 0, "requested_code_count": len(codes), "target_hit_count": 0}

    target_codes = [str(code or "").strip() for code in codes if str(code or "").strip()]
    target_code_set = set(target_codes)
    source_rows = fetch_daily_kbars_for_trade_date(target)
    rows = [
        {**row, "code": str(row.get("code") or "").strip(), "trade_date": _normalize_trade_date(row.get("trade_date"))}
        for row in source_rows
        if str(row.get("code") or "").strip() in target_code_set and _normalize_trade_date(row.get("trade_date")) == target
    ]

    if rows:
        payload = [_daily_kbar_payload(row) for row in rows]
        _upsert_daily_kbar_payload(db, payload)

    hit_codes = {row["code"] for row in rows}
    missing_codes = [code for code in target_codes if code not in hit_codes]
    return {
        "row_count": len(rows),
        "provider_row_count": len(source_rows),
        "requested_code_count": len(target_codes),
        "target_hit_count": len(hit_codes),
        "missing_code_count": len(missing_codes),
        "missing_codes": missing_codes[:10],
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
    return _auxiliary_target_codes(db, trade_date, limit=limit)


def _daily_kbar_target_codes(db: Session, trade_date: str, *, limit: int) -> list[str]:
    target = _normalize_trade_date(trade_date)
    if not target:
        return []
    target_variants = _trade_date_variants(target)
    existing_codes = {
        str(code or "").strip()
        for code in db.scalars(select(DailyKbar.code).where(DailyKbar.trade_date.in_(target_variants))).all()
        if str(code or "").strip()
    }
    ordered_codes = OrderedDict()
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

    if len(ordered_codes) < limit:
        recent_codes = db.scalars(
            select(DailyKbar.code)
            .where(DailyKbar.trade_date < target)
            .order_by(DailyKbar.trade_date.desc(), DailyKbar.amount.desc(), DailyKbar.code.asc())
            .limit(max(limit * 4, limit))
        ).all()
        for code in recent_codes:
            normalized = str(code or "").strip()
            if normalized and normalized not in existing_codes:
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


def _latest_allowed_bar_date(today: date | None = None) -> str:
    if today is None:
        now = datetime.now()
        today = now.date()
        if today.weekday() < 5 and now.hour < 16:
            today = _previous_weekday(today)
    if today.weekday() == 5:
        return (today - timedelta(days=1)).isoformat()
    if today.weekday() == 6:
        return (today - timedelta(days=2)).isoformat()
    return today.isoformat()


def _previous_weekday(day: date) -> date:
    current = day - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


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
                "incomplete_dates": list(((recent.get("tables") or {}).get(key) or {}).get("incomplete_dates") or []),
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
