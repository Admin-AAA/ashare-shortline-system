"""Archive recommendation_run_filter_audits.payload_json older than N days to gzip files, then stub the row.

Each run -> data/archive/filter_audit/<run_id>.json.gz ; file is re-read and sha256-verified
before the DB value is replaced with {"_archived": {"path", "sha256", "bytes"}}.
Usage: python -m scripts.archive_filter_audit [--days 30] [--apply]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from sqlalchemy import text

from app.db.session import SessionLocal

ARCHIVE = Path(__file__).resolve().parents[1] / "data" / "archive" / "filter_audit"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-bytes", type=int, default=100_000)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    ids = [r[0] for r in db.execute(text(
        "select r.run_id from recommendation_runs r "
        "join recommendation_run_filter_audits a on a.run_id = r.run_id "
        "where r.started_at < utc_timestamp() - interval :d day "
        "and length(a.payload_json) > :m and a.payload_json not like '{\"_archived\"%'"
    ), {"d": a.days, "m": a.min_bytes})]
    total = 0
    for rid in ids:
        raw = db.execute(
            text("select payload_json from recommendation_run_filter_audits where run_id=:r"), {"r": rid}
        ).scalar()
        data = raw.encode("utf-8")
        sha = hashlib.sha256(data).hexdigest()
        path = ARCHIVE / f"{rid}.json.gz"
        if not a.apply:
            total += len(data)
            continue
        with gzip.open(path, "wb", compresslevel=6) as fh:
            fh.write(data)
        with gzip.open(path, "rb") as fh:
            if hashlib.sha256(fh.read()).hexdigest() != sha:
                raise SystemExit(f"verify failed for {rid}")
        stub = json.dumps({"_archived": {"path": str(path), "sha256": sha, "bytes": len(data)}})
        db.execute(
            text("update recommendation_run_filter_audits set payload_json=:s where run_id=:r"),
            {"s": stub, "r": rid},
        )
        db.commit()
        total += len(data)
    print(f"{'archived' if a.apply else 'would archive'} {len(ids)} runs, {total/1e9:.2f} GB raw")
    db.close()


if __name__ == "__main__":
    main()
