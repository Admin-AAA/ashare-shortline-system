"""Backfill full-market unlock history (share_float) from quantdata into share_floats.

* Monthly float_date windows 2019-01..today (window split in halves if a response
  looks capped), streamed: one month in memory at a time.
* Goes through the data gateway (dataset ``tushare.share_float``) pinned to the
  quantdata provider -> shared cross-process limiter, breaker, metrics.
* Same columns as ``share_floats`` existing rows: dates stored compact YYYYMMDD (as all 159k existing rows).
* Idempotent: a row is inserted only if its natural key
  (code, ann_date, float_date, holder_name, float_share) is not already present;
  xiaoshi calendar rows (holder_name 'xiaoshi:*') are never touched.

Usage: PYTHONPATH=. .venv/bin/python scripts/backfill_share_float_history.py [--start 201901] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CAP_SUSPECT = 5000


def _router():
    from app.services.tushare_client import _build_provider_router
    from app.services.tushare_transport import TushareProviderRouter

    full, _ = _build_provider_router()
    q = full._providers.get("quantdata")
    if q is None:
        raise SystemExit("quantdata provider not configured")
    return TushareProviderRouter([q], api_routes={})


def _months(start: str, end: date):
    y, m = int(start[:4]), int(start[4:6])
    while (y, m) <= (end.year, end.month):
        first = date(y, m, 1)
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        yield first, min(nxt - timedelta(days=1), end)
        y, m = nxt.year, nxt.month


def _fetch(gw, lo: date, hi: date) -> list[dict]:
    from app.services.datasource.gateway import DataSourceError, ErrorKind

    res = gw.fetch_first("tushare.share_float", {"params": {"start_date": lo.strftime("%Y%m%d"), "end_date": hi.strftime("%Y%m%d")}})
    if res.provider is None:
        if any(a["outcome"] not in ("empty",) for a in res.attempts):
            raise DataSourceError(ErrorKind.UPSTREAM, str(res.attempts[-1]))
        return []
    data = res.rows["data"]
    items = data.get("items") or []
    if len(items) >= CAP_SUSPECT and lo < hi:
        mid = lo + (hi - lo) // 2
        return _fetch(gw, lo, mid) + _fetch(gw, mid + timedelta(days=1), hi)
    fields = data["fields"]
    return [dict(zip(fields, it)) for it in items]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="201901")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    from sqlalchemy import text

    from app.db.session import SessionLocal
    from app.services.datasource.gateway import DatasetGateway, TushareApiProvider
    from app.services.tushare_client import _f

    def _fmt_date(v: str) -> str:
        v = (v or "").replace("-", "").strip()
        return v[:8] if v not in ("None", "nan") else ""

    router = _router()
    gw = DatasetGateway([TushareApiProvider(lambda: router)], enabled=lambda p: True)
    per_year: Counter = Counter()
    fetched_year: Counter = Counter()
    db = SessionLocal()
    try:
        for lo, hi in _months(a.start, date.today()):
            raw = _fetch(gw, lo, hi)
            rows = []
            for r in raw:
                code = str(r.get("ts_code") or "").split(".")[0]
                if len(code) != 6:
                    continue
                rows.append({
                    "code": code, "ann_date": _fmt_date(str(r.get("ann_date", ""))),
                    "float_date": _fmt_date(str(r.get("float_date", ""))),
                    "float_share": _f(r.get("float_share")), "float_ratio": _f(r.get("float_ratio")),
                    "holder_name": str(r.get("holder_name", "")),
                })
            fetched_year[lo.year] += len(rows)
            lo_s, hi_s = _fmt_date(lo.strftime("%Y%m%d")), _fmt_date(hi.strftime("%Y%m%d"))
            existing = {
                (c, ad, fd, hn, round(float(fs or 0), 2))
                for c, ad, fd, hn, fs in db.execute(text(
                    "SELECT code, ann_date, float_date, holder_name, float_share FROM share_floats "
                    "WHERE float_date BETWEEN :lo AND :hi"), {"lo": lo_s, "hi": hi_s}).all()
            }
            new, seen = [], set()
            for r in rows:
                key = (r["code"], r["ann_date"], r["float_date"], r["holder_name"], round(float(r["float_share"] or 0), 2))
                if key in existing or key in seen:
                    continue
                seen.add(key)
                new.append(r)
            if new and not a.dry_run:
                db.execute(text(
                    "INSERT INTO share_floats (code, ann_date, float_date, float_share, float_ratio, holder_name, created_at) "
                    "VALUES (:code, :ann_date, :float_date, :float_share, :float_ratio, :holder_name, UTC_TIMESTAMP())"), new)
                db.commit()
            per_year[lo.year] += len(new)
            print(f"{lo:%Y-%m} fetched={len(rows)} inserted={len(new)}", flush=True)
            del raw, rows, existing, new, seen
    finally:
        db.close()
    print("YEAR fetched inserted")
    for y in sorted(fetched_year):
        print(y, fetched_year[y], per_year[y])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
