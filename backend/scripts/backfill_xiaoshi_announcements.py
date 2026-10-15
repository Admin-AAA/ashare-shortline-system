"""Backfill news_announcement from xiaoshi event-timeline (announcement partition).

One download-session per day (full market, source = 巨潮资讯), far cheaper than
per-stock fetching. Sequential with sleep. Days not yet published return 404
and are skipped. Idempotent upsert, source='xiaoshi_cninfo'.
"""
from __future__ import annotations

import argparse
import io
import json
import time
from datetime import date, timedelta

import httpx
import pandas as pd
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.session import SessionLocal
from app.models.tables import NewsAnnouncement
from app.services.xiaoshi_client import XiaoshiError, get_xiaoshi_client

RISK_KW = ("风险", "警示", "退市", "ST", "处罚", "监管", "立案", "亏损", "减持", "问询", "通报")


def rows_from_frame(df: pd.DataFrame) -> list[dict]:
    out: list[dict] = []
    for raw in df["payload_json"]:
        p = json.loads(raw)
        title = str(p.get("title") or "").strip()
        eid = str(p.get("event_id") or "").strip()
        day = str(p.get("event_time") or p.get("available_at") or "")[:10]
        if not title or not eid or len(day) < 10:
            continue
        risk = any(k in title for k in RISK_KW) or p.get("direction") == "利空"
        for st in p.get("stocks") or []:
            code = str(st.get("code") if isinstance(st, dict) else st)[:6]
            if len(code) != 6 or not code.isdigit():
                continue
            out.append({
                "source": "xiaoshi_cninfo",
                "article_id": f"{eid}:{code}"[:80],
                "code": code,
                "announce_date": day,
                "title": title[:500],
                "summary": (str(p.get("summary") or "")[:500] or None),
                "category": "announcement",
                "sentiment": "negative" if risk else "neutral",
                "is_risk_warning": bool(risk),
            })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-09-21")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--sleep", type=float, default=0.5)
    a = ap.parse_args()
    c = get_xiaoshi_client()
    db = SessionLocal()
    d = date.fromisoformat(a.start)
    end = date.fromisoformat(a.end)
    total = 0
    while d <= end:
        ds = d.isoformat()
        d += timedelta(days=1)
        time.sleep(a.sleep)
        try:
            s = c.get_history_download_session(dataset="event-timeline", date=ds)
        except XiaoshiError as exc:
            print(f"{ds} skip ({str(exc)[:50]})", flush=True)
            continue
        files = [f for f in s.get("files") or [] if f.get("event_type") == "announcement"]
        if not files:
            print(f"{ds} no announcement partition", flush=True)
            continue
        rows: list[dict] = []
        for f in files:
            df = pd.read_parquet(io.BytesIO(httpx.get(f["url"], timeout=60).content))
            rows.extend(rows_from_frame(df))
        dedup = {(r["source"], r["article_id"]): r for r in rows}
        vals = list(dedup.values())
        for i in range(0, len(vals), 1000):
            stmt = mysql_insert(NewsAnnouncement).values(vals[i:i + 1000])
            db.execute(stmt.on_duplicate_key_update(
                title=stmt.inserted.title, is_risk_warning=stmt.inserted.is_risk_warning,
                sentiment=stmt.inserted.sentiment))
        db.commit()
        total += len(vals)
        print(f"{ds} rows={len(vals)} codes={len({r['code'] for r in vals})}", flush=True)
    print(f"DONE total={total}", flush=True)


if __name__ == "__main__":
    main()
