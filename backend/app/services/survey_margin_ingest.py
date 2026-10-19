"""机构调研 institutional_surveys / 两融汇总 margin_data 回到日更（2026-10-19）。

- institutional_surveys：经统一网关 get_pro().stk_surv(trade_date=…) 按自然日全市场拉取，offset/limit 400 分页；
  只在当日拉取成功且非空时按 surv_date 幂等覆盖（DELETE+INSERT）。上游空（例如 2025-09-12~11-06
  stk_surv 本就为空）不删已有行。akshare 备源（东财调研统计，一行一次调研、无机构明细/基金访客数）
  与 Tushare 一行一机构的口径不同，不写入本表，当日记为 backup_only 待重试。
- margin_data：旧版逐股两融汇总表（2026-07-08 后停更）。数据与 margin_detail 同源同口径（亿元），
  这里在 margin_detail 当日入库后按 (code, trade_date) upsert 同步，不额外请求上游。
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

SURV_PAGE = 400
SURV_MAX_PAGES = 60


def _int_or_none(v: Any) -> int | None:
    try:
        if v is None or v != v:  # NaN
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _str_or_none(v: Any, n: int) -> str | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    s = str(v).strip()
    return s[:n] if s else None


def fetch_surv_day(pro: Any, day: str) -> tuple[list[dict[str, Any]], str]:
    """返回 (rows, status)。status: ok / empty / backup_only。异常向上抛。"""
    parts: list[Any] = []
    offset = 0
    for _ in range(SURV_MAX_PAGES):
        kw: dict[str, Any] = {"trade_date": day}
        if offset:
            kw.update(offset=offset, limit=SURV_PAGE)
        df = pro.stk_surv(**kw)
        if df is None or df.empty:
            break
        if "source" in df.columns and str(df["source"].iloc[0]).startswith("akshare"):
            return [], "backup_only"
        if parts and df.head(3).astype(str).reset_index(drop=True).equals(
                parts[-1].head(3).astype(str).reset_index(drop=True)):
            raise RuntimeError(f"stk_surv {day} offset {offset} 返回上一页（上游忽略 offset）")
        parts.append(df.reset_index(drop=True))
        if len(df) < SURV_PAGE:
            break
        offset += SURV_PAGE
        time.sleep(0.5)
    else:
        raise RuntimeError(f"stk_surv {day} 超过 {SURV_MAX_PAGES} 页")
    rows: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for frame in parts:
        for rec in frame.to_dict("records"):
            code = str(rec.get("ts_code") or "").split(".")[0]
            sd = str(rec.get("surv_date") or "").replace("-", "")
            if len(code) != 6 or len(sd) != 8:
                continue
            row = {
                "code": code, "name": _str_or_none(rec.get("name"), 80),
                "surv_date": f"{sd[:4]}-{sd[4:6]}-{sd[6:]}",
                "fund_visitors": _int_or_none(rec.get("fund_visitors")),
                "rece_org": _str_or_none(rec.get("rece_org"), 120),
                "org_type": _str_or_none(rec.get("org_type"), 40),
            }
            key = (row["code"], row["surv_date"], row["rece_org"], row["org_type"], row["fund_visitors"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows, ("ok" if rows else "empty")


def ingest_institutional_surveys(db, start: str, end: str, pro: Any = None) -> dict[str, Any]:
    from app.services.tushare_client import get_pro

    pro = pro or get_pro()
    d0, d1 = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    out: dict[str, Any] = {"start": start[:10], "end": end[:10], "days": {}, "written": 0}
    d = d0
    while d <= d1:
        iso = d.isoformat()
        try:
            rows, status = fetch_surv_day(pro, d.strftime("%Y%m%d"))
        except Exception as exc:  # noqa: BLE001 - per-day isolation
            db.rollback()
            out["days"][iso] = f"error:{type(exc).__name__}"
            logger.warning("institutional_surveys %s fetch failed: %s", iso, exc)
            d += timedelta(days=1)
            continue
        if rows:
            # 一个 surv_date 的行可能分布在相邻的 trade_date 请求里；按本次返回的 surv_date 覆盖
            for sd in sorted({r["surv_date"] for r in rows}):
                db.execute(text("DELETE FROM institutional_surveys WHERE surv_date=:d"), {"d": sd})
            db.execute(text(
                "INSERT INTO institutional_surveys (code, name, surv_date, fund_visitors, rece_org, org_type, created_at) "
                "VALUES (:code, :name, :surv_date, :fund_visitors, :rece_org, :org_type, UTC_TIMESTAMP())"), rows)
            db.commit()
            out["written"] += len(rows)
        out["days"][iso] = f"{status}:{len(rows)}"
        d += timedelta(days=1)
    return out


def sync_margin_data_from_detail(db, trade_date: str) -> dict[str, Any]:
    d = str(trade_date)[:10]
    n_detail = db.execute(text("SELECT COUNT(*) FROM margin_detail WHERE trade_date=:d"), {"d": d}).scalar() or 0
    if not n_detail:
        return {"trade_date": d, "status": "no_margin_detail", "rows": 0}
    db.execute(text(
        "INSERT INTO margin_data (code, trade_date, rzye, rqye, rzmre, rzche, rzrqye, created_at) "
        "SELECT code, trade_date, rzye, rqye, rzmre, rzche, rzrqye, UTC_TIMESTAMP() FROM margin_detail WHERE trade_date=:d "
        "ON DUPLICATE KEY UPDATE rzye=VALUES(rzye), rqye=VALUES(rqye), rzmre=VALUES(rzmre), "
        "rzche=VALUES(rzche), rzrqye=VALUES(rzrqye)"), {"d": d})
    db.commit()
    n = db.execute(text("SELECT COUNT(*) FROM margin_data WHERE trade_date=:d"), {"d": d}).scalar() or 0
    return {"trade_date": d, "status": "ok", "rows": int(n), "margin_detail_rows": int(n_detail)}


def daily_survey_margin_update(db, trade_date: str, survey_lookback_days: int = 7) -> dict[str, Any]:
    """日更钩子：调研回看最近 7 个自然日（公告滞后披露），两融汇总同步当日与上一交易日。"""
    d = date.fromisoformat(str(trade_date)[:10])
    result: dict[str, Any] = {}
    try:
        result["institutional_surveys"] = ingest_institutional_surveys(
            db, (d - timedelta(days=survey_lookback_days)).isoformat(), d.isoformat())
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        result["institutional_surveys"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    try:
        prev = db.execute(text("SELECT MAX(trade_date) FROM margin_detail WHERE trade_date<=:d"), {"d": d.isoformat()}).scalar()
        dates = sorted({str(prev)[:10]} | ({d.isoformat()} if prev and str(prev)[:10] != d.isoformat() else set())) if prev else []
        result["margin_data"] = [sync_margin_data_from_detail(db, x) for x in dates]
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        result["margin_data"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    return result
