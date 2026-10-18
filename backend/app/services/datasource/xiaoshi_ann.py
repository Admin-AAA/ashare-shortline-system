"""xiaoshi event-timeline announcement partition (full market per day, source 巨潮).

Last-resort ``ann.list`` fallback after eastmoney/cninfo (cutover flag
``ann.list.xiaoshi``, default off).  One download session per day; rows use the same
mapping as ``scripts/backfill_xiaoshi_announcements.py`` (source='xiaoshi_cninfo').
"""
from __future__ import annotations

import io
import json
from datetime import date, timedelta
from typing import Any

RISK_KW = ("风险", "警示", "退市", "ST", "处罚", "监管", "立案", "亏损", "减持", "问询", "通报")
MAX_DAYS = 10


def rows_from_payloads(payloads: list[str], codes: set[str] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in payloads:
        p = json.loads(raw)
        title = str(p.get("title") or "").strip()
        eid = str(p.get("event_id") or "").strip()
        day = str(p.get("event_time") or p.get("available_at") or "")[:10]
        if not title or not eid or len(day) < 10:
            continue
        risk = any(k in title for k in RISK_KW) or p.get("direction") == "利空"
        for st in p.get("stocks") or []:
            code = str(st.get("code") if isinstance(st, dict) else st)[:6]
            if len(code) != 6 or not code.isdigit() or (codes is not None and code not in codes):
                continue
            out.append({
                "source": "xiaoshi_cninfo", "article_id": f"{eid}:{code}"[:80], "code": code,
                "announce_date": day, "title": title[:500],
                "summary": (str(p.get("summary") or "")[:500] or None), "category": "announcement",
                "sentiment": "negative" if risk else "neutral", "is_risk_warning": bool(risk),
            })
    return out


def fetch_day(day: str, codes: set[str] | None = None, *, client: Any = None) -> list[dict[str, Any]]:
    import httpx
    import pandas as pd

    from app.services.xiaoshi_client import XiaoshiError, get_xiaoshi_client

    c = client or get_xiaoshi_client()
    try:
        s = c.get_history_download_session(dataset="event-timeline", date=day)
    except XiaoshiError:
        return []  # not yet published (404)
    out: list[dict[str, Any]] = []
    for f in s.get("files") or []:
        if f.get("event_type") != "announcement":
            continue
        df = pd.read_parquet(io.BytesIO(httpx.get(f["url"], timeout=60).content), columns=["payload_json"])
        out.extend(rows_from_payloads(list(df["payload_json"]), codes))
        del df
    return out


def fetch_range(start: str, end: str, codes: set[str], **kw: Any) -> list[dict[str, Any]]:
    from app.services.datasource.wrap import gw_call

    lo, hi = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    lo = max(lo, hi - timedelta(days=MAX_DAYS - 1))
    out: list[dict[str, Any]] = []
    while lo <= hi:
        out.extend(gw_call("ann.list", "xiaoshi_alt", fetch_day, lo.isoformat(), codes, **kw))
        lo += timedelta(days=1)
    return out
