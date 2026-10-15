"""Resume-able eastmoney news backfill since a start date.

Primary path for historical gap fill. Daily remediation uses the same
``app.services.eastmoney_news`` client. Skip codes already covered since
``--start``. Single-thread by default; abort after ``--max-blocks`` HTTP 567s.
"""
from __future__ import annotations

import argparse
import time

import httpx
from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.session import SessionLocal
from app.models.tables import NewsAnnouncement
from app.services.eastmoney_news import (
    BLOCKED_STATUS_CODES,
    EastmoneyBlockedError,
    HEADERS,
    fetch_announcements_for_code,
)


def probe(url_code: str = "000001") -> int:
    url = (
        "https://np-anotice-stock.eastmoney.com/api/security/ann"
        f"?page_size=1&page_index=1&ann_type=A&stock_list={url_code}&column=NOTICE_COLUMN"
    )
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=10.0) as client:
            return client.get(url).status_code
    except Exception:
        return -1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-09-21")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-blocks", type=int, default=3, help="stop after this many 567/block errors")
    ap.add_argument("--probe-only", action="store_true")
    ap.add_argument("--probe-retries", type=int, default=3)
    ap.add_argument("--probe-sleep", type=float, default=20.0)
    a = ap.parse_args()

    for i in range(a.probe_retries):
        code = probe()
        print(f"probe {i+1}/{a.probe_retries} status={code}", flush=True)
        if code == 200:
            break
        if i + 1 < a.probe_retries:
            time.sleep(a.probe_sleep)
    else:
        print("BLOCKED: eastmoney still returning non-200; leaving resume script ready", flush=True)
        if a.probe_only or True:
            # Always stop if probes fail — do not hammer a blocked IP.
            raise SystemExit(3)

    if a.probe_only:
        return

    db = SessionLocal()
    all_codes = [r[0] for r in db.execute(text(
        "SELECT DISTINCT code FROM daily_kbars WHERE trade_date=(SELECT MAX(trade_date) FROM daily_kbars)"
    )).fetchall()]
    covered = {
        r[0]
        for r in db.execute(
            text(
                "SELECT DISTINCT code FROM news_announcement "
                "WHERE source='eastmoney' AND announce_date>=:s"
            ),
            {"s": a.start},
        ).fetchall()
    }
    codes = [c for c in all_codes if c not in covered]
    if a.limit:
        codes = codes[: a.limit]
    print(
        f"remaining={len(codes)} covered={len(covered)} total={len(all_codes)} start={a.start}",
        flush=True,
    )
    total = 0
    blocks = 0
    t0 = time.time()
    buf: list[dict] = []
    for i, code in enumerate(codes, 1):
        try:
            rows = fetch_announcements_for_code(code, start_date=a.start, max_pages=3)
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
        if len(buf) >= 200 or i == len(codes):
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
        if i % 100 == 0:
            print(f"{i}/{len(codes)} rows={total} {time.time()-t0:.0f}s", flush=True)
    print(f"DONE rows={total} remaining_untried={max(0, len(codes)-i)} elapsed={time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
