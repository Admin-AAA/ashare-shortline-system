"""Resume-able news backfill since a start date.

Sources:
  - eastmoney (primary daily path; may HTTP 567)
  - cninfo (巨潮, secondary; free, needs orgId)
  - auto: probe eastmoney once; on 567 use cninfo

Skips codes already covered by *any* source since ``--start`` unless
``--only-source`` is set.
"""
from __future__ import annotations

import argparse
import random
import time

import httpx
from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.session import SessionLocal
from app.models.tables import NewsAnnouncement
from app.services.eastmoney_news import (
    EastmoneyBlockedError,
    HEADERS as EM_HEADERS,
    fetch_announcements_for_code as fetch_eastmoney,
)
from app.services.cninfo_news import fetch_announcements_for_code as fetch_cninfo


def probe_eastmoney(url_code: str = "000001") -> int:
    url = (
        "https://np-anotice-stock.eastmoney.com/api/security/ann"
        f"?page_size=1&page_index=1&ann_type=A&stock_list={url_code}&column=NOTICE_COLUMN"
    )
    try:
        with httpx.Client(headers=EM_HEADERS, follow_redirects=True, timeout=10.0) as client:
            return client.get(url).status_code
    except Exception:
        return -1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-09-21")
    ap.add_argument("--end", default=None, help="default: today Asia/Shanghai")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--source", choices=("auto", "eastmoney", "cninfo"), default="auto")
    ap.add_argument("--max-blocks", type=int, default=3)
    ap.add_argument("--probe-only", action="store_true")
    a = ap.parse_args()

    from datetime import datetime
    from zoneinfo import ZoneInfo
    end = a.end or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")

    source = a.source
    if source == "auto":
        status = probe_eastmoney()
        print(f"eastmoney probe status={status}", flush=True)
        if a.probe_only:
            raise SystemExit(0 if status == 200 else 3)
        source = "eastmoney" if status == 200 else "cninfo"
        if status != 200:
            print(f"eastmoney blocked; switching secondary source={source}", flush=True)

    db = SessionLocal()
    all_codes = [r[0] for r in db.execute(text(
        "SELECT DISTINCT code FROM daily_kbars WHERE trade_date=(SELECT MAX(trade_date) FROM daily_kbars)"
    )).fetchall()]
    covered = {
        r[0]
        for r in db.execute(
            text(
                "SELECT DISTINCT code FROM news_announcement WHERE announce_date>=:s"
            ),
            {"s": a.start},
        ).fetchall()
    }
    codes = [c for c in all_codes if c not in covered]
    # 早号段很多停牌/无公告股，小批量时打乱以免全空。
    random.shuffle(codes)
    if a.limit:
        codes = codes[: a.limit]
    print(
        f"source={source} remaining={len(codes)} covered={len(covered)} "
        f"total={len(all_codes)} window={a.start}~{end}",
        flush=True,
    )
    fetch = fetch_eastmoney if source == "eastmoney" else fetch_cninfo
    total = 0
    blocks = 0
    t0 = time.time()
    buf: list[dict] = []
    i = 0
    for i, code in enumerate(codes, 1):
        try:
            if source == "eastmoney":
                rows = fetch(code, start_date=a.start, end_date=end, max_pages=3)
            else:
                rows = fetch(code, start_date=a.start, end_date=end, max_pages=2)
        except EastmoneyBlockedError as exc:
            blocks += 1
            print(f"BLOCKED at {code}: {exc} ({blocks}/{a.max_blocks})", flush=True)
            if blocks >= a.max_blocks:
                break
            time.sleep(30)
            continue
        except Exception as exc:
            if i <= 5:
                print(f"fail {code}: {exc}", flush=True)
            continue
        buf.extend(rows)
        if len(buf) >= 100 or i == len(codes):
            if buf:
                dedup = {(r["source"], r["article_id"]): r for r in buf}
                stmt = mysql_insert(NewsAnnouncement).values(list(dedup.values()))
                db.execute(
                    stmt.on_duplicate_key_update(
                        announce_date=stmt.inserted.announce_date,
                        title=stmt.inserted.title,
                        sentiment=stmt.inserted.sentiment,
                        is_risk_warning=stmt.inserted.is_risk_warning,
                    )
                )
                db.commit()
                total += len(dedup)
                buf = []
        if i % 50 == 0:
            print(f"{i}/{len(codes)} rows={total} {time.time()-t0:.0f}s", flush=True)
    print(f"DONE source={source} rows={total} processed={i} elapsed={time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
