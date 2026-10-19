"""数据源页旧注册表行（source='tushare' 的 concept_sector/dragon_tiger/limit_up/kline/quote，xiaoshi）的真实口径。

data_source_health 里这些行只在配置页手动「测试」时更新（_record_source_test），
迁到统一网关后真实流量不再写它们，于是页面「最近成功 7/15、日K 9/9」、
「xiaoshi 日K 54%」都是手动测试的历史累计，而非真实取数状况。
这里用网关 trace（data_source_request_traces，所有上游合计）近 7 天的真实结果覆盖展示口径，
并保留原测试计数到 probe_* 字段。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

WINDOW_DAYS = 7
# (source, source_type) -> 网关 operation_name 列表
LEGACY_OPERATIONS: dict[tuple[str, str], tuple[str, ...]] = {
    ("tushare", "concept_sector"): ("ths_index", "ths_member"),
    ("tushare", "dragon_tiger"): ("top_list",),
    ("tushare", "limit_up"): ("limit_list_d",),
    ("tushare", "kline"): ("daily",),
    ("tushare", "quote"): ("rt_k",),
}


def _live_stats(db, ops: tuple[str, ...]) -> dict[str, Any] | None:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=WINDOW_DAYS)
    params = {f"o{i}": op for i, op in enumerate(ops)}
    params["since"] = since
    placeholders = ",".join(f":o{i}" for i in range(len(ops)))
    row = db.execute(text(
        "SELECT SUM(final_outcome='success'), SUM(final_outcome='failure'), SUM(final_outcome='valid_empty'), "
        "MAX(CASE WHEN final_outcome IN ('success','valid_empty') THEN completed_at END), "
        "MAX(CASE WHEN final_outcome='failure' THEN completed_at END) "
        f"FROM data_source_request_traces WHERE operation_name IN ({placeholders}) AND started_at >= :since"),
        params).one()
    ok, fail, empty = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
    if ok + fail + empty == 0:
        return None
    return {"success_count": ok, "failure_count": fail, "empty_count": empty,
            "success_rate": round(ok / (ok + fail), 3) if ok + fail else None,
            "last_success_at": row[3].isoformat() if row[3] else None,
            "last_failure_at": row[4].isoformat() if row[4] else None}


def apply_live_stats(db, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for r in rows:
        key = (r.get("source"), r.get("source_type"))
        is_legacy = key in LEGACY_OPERATIONS or r.get("source") == "xiaoshi"
        if not is_legacy:
            continue
        r["probe_success_count"] = r.get("success_count")
        r["probe_failure_count"] = r.get("failure_count")
        r["probe_success_rate"] = r.get("success_rate")
        r["probe_last_success_at"] = r.get("last_success_at")
        live = None
        if key in LEGACY_OPERATIONS:
            try:
                live = _live_stats(db, LEGACY_OPERATIONS[key])
            except Exception:  # noqa: BLE001 - display-only; fall back to probe numbers
                db.rollback()
                live = None
        if live:
            r.update(live)
            r["stats_basis"] = f"gateway_traces_{WINDOW_DAYS}d"
        else:
            r["stats_basis"] = "manual_probe_cumulative"
    return rows
