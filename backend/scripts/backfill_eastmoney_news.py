"""Backfill eastmoney announcements into news_announcement since a start date.

Uses the same parsing rules as market_auxiliary._fetch_eastmoney_news
(article_id = art_code, legacy notice_id fallback). Rows with empty article_id
are dropped (they collide on uq(source, article_id)). Idempotent upsert.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import threading
import time

import httpx
from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.session import SessionLocal
from app.models.tables import NewsAnnouncement
from app.services.market_auxiliary import _eastmoney_article_id

RISK_KW = ["风险", "警示", "退市", "ST", "处罚", "监管", "立案", "亏损", "减持", "问询", "通报"]
URL = ("https://np-anotice-stock.eastmoney.com/api/security/ann?page_size={ps}&page_index={pi}"
       "&ann_type=A&stock_list={code}&column=NOTICE_COLUMN")
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
_lock = threading.Lock()


def fetch_code(client: httpx.Client, code: str, start: str, page_size: int = 50, max_pages: int = 4) -> list[dict]:
    out: list[dict] = []
    for pi in range(1, max_pages + 1):
        for attempt in range(3):
            try:
                r = client.get(URL.format(ps=page_size, pi=pi, code=code), timeout=15)
                r.raise_for_status()
                rows = (r.json().get("data") or {}).get("list") or []
                break
            except Exception:
                time.sleep(1 + attempt)
        else:
            return out
        oldest = None
        for row in rows:
            d = str(row.get("notice_date", ""))[:10]
            oldest = d if oldest is None or d < oldest else oldest
            aid = _eastmoney_article_id(row)
            if not aid or d < start:
                continue
            title = str(row.get("title_ch", ""))
            risk = any(k in title for k in RISK_KW)
            out.append({"source": "eastmoney", "article_id": aid[:80], "code": code, "announce_date": d,
                        "title": title[:500], "summary": str(row.get("summary", ""))[:500] or None,
                        "category": "announcement", "sentiment": "negative" if risk else "neutral",
                        "is_risk_warning": risk})
        if len(rows) < page_size or (oldest and oldest < start):
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-09-21")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    db = SessionLocal()
    codes = [r[0] for r in db.execute(text(
        "SELECT DISTINCT code FROM daily_kbars WHERE trade_date=(SELECT MAX(trade_date) FROM daily_kbars)"))]
    if a.limit:
        codes = codes[: a.limit]
    print(f"codes={len(codes)} start={a.start}", flush=True)
    total = 0
    t0 = time.time()
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client, cf.ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(fetch_code, client, c, a.start): c for c in codes}
        buf: list[dict] = []
        for i, f in enumerate(cf.as_completed(futs), 1):
            buf.extend(f.result())
            if len(buf) >= 500 or i == len(codes):
                if buf:
                    dedup = {(r["source"], r["article_id"]): r for r in buf}
                    stmt = mysql_insert(NewsAnnouncement).values(list(dedup.values()))
                    db.execute(stmt.on_duplicate_key_update(
                        announce_date=stmt.inserted.announce_date, title=stmt.inserted.title,
                        sentiment=stmt.inserted.sentiment, is_risk_warning=stmt.inserted.is_risk_warning))
                    db.commit()
                    total += len(dedup)
                    buf = []
            if i % 500 == 0:
                print(f"{i}/{len(codes)} rows={total} {time.time()-t0:.0f}s", flush=True)
    print(f"DONE rows={total} elapsed={time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
