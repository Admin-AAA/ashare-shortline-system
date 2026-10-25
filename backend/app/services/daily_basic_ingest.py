"""daily_basic 表（换手率/PE/市值）的日更写入 + 停更告警。

2026-10-19 发现：daily_basic 表从未接入盘后日更，只靠研究/回填脚本手工写入，
最后一次手工回填停在 2026-10-09，回购策略（市值占比）、中长线 mcap 排名
（midterm_service / midterm_block_trade）从此读到陈旧市值。
本模块供盘后流水线每天调用，并在 daily_basic 落后于最近交易日时告警。
"""
from __future__ import annotations

import logging
import math
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

FIELDS = "ts_code,trade_date,turnover_rate,pe_ttm,total_mv,circ_mv"
# 全市场约 5500 只；低于该比例视为上游返回不完整，不覆盖已有数据
MIN_ROWS_RATIO = 0.9


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def latest_trade_date(db) -> str | None:
    """最近一个已收盘交易日：以日更最可靠的 daily_kbars 为准。"""
    v = db.execute(text("SELECT MAX(trade_date) FROM daily_kbars")).scalar()
    return str(v) if v else None


def ingest_daily_basic(db, trade_date: str, pro: Any = None) -> dict[str, Any]:
    """经统一网关拉 trade_date 全市场 daily_basic，按日幂等覆盖写入。缺值写 NULL。"""
    from app.services.tushare_client import fetch_trade_date_paged, get_pro

    pro = pro or get_pro()
    d = str(trade_date)[:10]
    df = fetch_trade_date_paged(pro, "daily_basic", trade_date=d.replace("-", ""), fields=FIELDS)
    rows: list[dict[str, Any]] = []
    if df is not None and not df.empty:
        for rec in df.to_dict("records"):
            code = str(rec.get("ts_code") or "").split(".")[0]
            if len(code) != 6 or not code.isdigit():
                continue
            rows.append({
                "code": code, "d": d,
                "t": _num(rec.get("turnover_rate")), "pe": _num(rec.get("pe_ttm")),
                "tm": _num(rec.get("total_mv")), "cm": _num(rec.get("circ_mv")),
            })
    expected = db.execute(text("SELECT COUNT(*) FROM daily_kbars WHERE trade_date=:d"), {"d": d}).scalar() or 0
    out: dict[str, Any] = {"trade_date": d, "fetched": len(rows), "expected": int(expected)}
    if not rows or (expected and len(rows) < MIN_ROWS_RATIO * expected):
        out["status"] = "incomplete"
        logger.warning("daily_basic ingest %s incomplete: %d/%d rows, not written", d, len(rows), expected)
        return out
    db.execute(text("DELETE FROM daily_basic WHERE trade_date=:d"), {"d": d})
    db.execute(text(
        "INSERT INTO daily_basic (code, trade_date, turnover_rate, pe_ttm, total_mv, circ_mv) "
        "VALUES (:code, :d, :t, :pe, :tm, :cm)"), rows)
    db.commit()
    out["status"] = "ok"
    out["coverage"] = round(len(rows) / expected, 4) if expected else None
    return out


def check_daily_basic_staleness(db, latest: str | None = None) -> dict[str, Any]:
    """daily_basic 最新日期落后于最近交易日即告警（同 ivol_coverage 写法，走 emit_alert）。"""
    from app.core.alerts import emit_alert, resolve_alert

    latest = latest or latest_trade_date(db)
    basic = db.execute(text("SELECT MAX(trade_date) FROM daily_basic")).scalar()
    basic = str(basic) if basic else None
    ok = bool(latest) and basic is not None and basic >= latest
    if not ok:
        emit_alert(
            "daily_basic_stale",
            "daily_basic",
            f"daily_basic 最新日期 {basic} 落后于最近交易日 {latest}，"
            "回购市值占比/中长线市值排名将读到陈旧 total_mv/circ_mv",
        )
    else:
        resolve_alert("daily_basic_stale", "daily_basic", f"daily_basic 已更新到 {basic}")
    return {"latest_trade_date": latest, "daily_basic_max": basic, "ok": ok}


def ingest_daily_basic_with_alert(db, trade_date: str) -> dict[str, Any]:
    """盘后流水线入口：先补最近交易日，再做停更检查；失败只告警不阻断。"""
    out: dict[str, Any] = {}
    try:
        out["ingest"] = ingest_daily_basic(db, trade_date)
    except Exception as exc:  # noqa: BLE001 - best-effort, staleness alert below reports it
        db.rollback()
        out["ingest"] = {"trade_date": str(trade_date)[:10], "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        logger.warning("daily_basic ingest failed: %s", exc)
    out["staleness"] = check_daily_basic_staleness(db)
    return out
