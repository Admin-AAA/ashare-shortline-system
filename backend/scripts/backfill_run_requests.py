#!/usr/bin/env python3
"""Backfill recommendation_runs.params_json into the V1 canonical request columns.

This is an additive, forward-only operation.  It never deletes or rewrites
``params_json`` and refuses worker cutover when active rows are invalid or when
row/mode counts drift.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.db.session import SessionLocal  # noqa: E402
from app.services.runs.request_persistence import (  # noqa: E402
    backfill_canonical_run_requests,
    verify_run_partition_counts,
    verify_run_request_cutover,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-batches", type=int, default=None)
    parser.add_argument(
        "--verify-cutover",
        action="store_true",
        help="also require every queued/running/interrupted row to have a valid canonical request",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        report = backfill_canonical_run_requests(
            db,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            commit_each_batch=True,
        )
        verify_run_partition_counts(db, report.initial_counts)
        if args.verify_cutover:
            verify_run_request_cutover(db, expected_counts=report.initial_counts)
        print(
            json.dumps(
                {
                    "scanned_rows": report.scanned_rows,
                    "backfilled_rows": report.backfilled_rows,
                    "skipped_invalid_rows": report.skipped_invalid_rows,
                    "skipped_mismatch_rows": report.skipped_mismatch_rows,
                    "remaining_unresolved_rows": report.remaining_unresolved_rows,
                    "counts_unchanged": report.counts_unchanged,
                    "cutover_ready": report.cutover_ready,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
