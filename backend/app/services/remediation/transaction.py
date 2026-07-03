from __future__ import annotations

from typing import Any


def action_target_label(item: dict[str, Any]) -> str:
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


def rollback_hint(item: dict[str, Any]) -> str:
    table = str(item.get("table") or "").strip()
    target = action_target_label(item)
    if table == "daily_kbar":
        codes = [str(code) for code in item.get("codes") or [] if str(code or "").strip()]
        code_scope = f"且 code 限定在本次 {len(codes)} 个目标代码内" if codes else "并核对本次目标代码"
        return f"按 daily_kbars.trade_date = {target} {code_scope}删除本次写入行，再重新诊断。"
    if table == "minute_kbar":
        codes = [str(code) for code in item.get("codes") or [] if str(code or "").strip()]
        code_scope = f"且 code 限定在本次 {len(codes)} 个目标代码内" if codes else "并核对本次目标代码"
        return f"按 minute_kbars.trade_date = {target} {code_scope}删除本次写入行，再重新诊断。"
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


def with_rollback_hint(action: dict[str, Any]) -> dict[str, Any]:
    hint = rollback_hint(action)
    return {**action, "rollback_hint": hint} if hint else action


def operation_log(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in actions:
        row = {
            "table": item.get("table") or item.get("scope") or "unknown",
            "status": item.get("status"),
            "target": action_target_label(item),
            "row_count": int(item.get("row_count") or 0),
            "rollback_hint": item.get("rollback_hint"),
        }
        if item.get("error"):
            row["error"] = item.get("error")
            row["error_type"] = item.get("error_type")
        rows.append(row)
    return rows


def rollback_guidance(*, dry_run: bool, actions: list[dict[str, Any]]) -> dict[str, Any]:
    if dry_run:
        return {
            "mode": "no_write",
            "message": "本次为预演，未写入数据库，无需回滚。",
            "keys": [],
        }
    keys = sorted(
        {
            f"{item.get('table') or item.get('scope')}: {action_target_label(item)}"
            for item in actions
            if item.get("table") or item.get("scope")
        }
    )
    return {
        "mode": "delete_by_operation_log",
        "message": "本次补数为按表、日期和目标代码的增量写入；如需撤回，请按操作日志中的表名、日期和代码范围精确删除本次写入行，再重新运行数据健康诊断。",
        "keys": keys,
    }


def remediation_status(*, dry_run: bool, actions: list[dict[str, Any]], failed_count: int) -> str:
    if dry_run:
        return "dry_run"
    if not actions:
        return "no_action"
    if failed_count:
        return "partial_failed"
    if any(item.get("status") in {"empty", "short_history", "stale"} for item in actions):
        return "partial_completed"
    return "completed"


def action_summary(plan: list[dict[str, Any]], actions: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "planned_count": len(plan),
        "completed_count": sum(1 for item in actions if item.get("status") == "completed"),
        "empty_count": sum(1 for item in actions if item.get("status") == "empty"),
        "skipped_count": sum(1 for item in actions if item.get("status") == "skipped"),
        "failed_count": sum(1 for item in actions if item.get("status") == "failed"),
    }
