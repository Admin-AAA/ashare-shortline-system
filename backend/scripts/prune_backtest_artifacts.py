"""Prune bulky research-backtest evidence older than N days (default dry-run).

Deletes, for *unprotected* backtests created before the cutoff:
  - backtest_candidates / backtest_trades / backtest_failure_attributions
  - backtest_input_artifacts of snapshots no protected run/experiment uses

Always kept: backtest_runs_v2 (summary + result_json), backtest_data_snapshots,
backtest_experiments, legacy backtest_runs, and everything inside the window.

Protected = created within --keep-days, OR belongs to a row of
backtest_experiments (experiment_id / baseline / their parent chain), OR passed
via --protect. Pruned runs get their request claim's completed pointer cleared,
so an identical request re-runs instead of returning a hollow cached result.

Usage (from repo root):
  PYTHONPATH=backend backend/.venv/bin/python backend/scripts/prune_backtest_artifacts.py
  ... --apply            # actually delete (batched, resumable, idempotent)
  ... --apply --optimize # then rebuild tables to give disk back to the OS
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import bindparam, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import engine  # noqa: E402

# Small batches: the InnoDB lock memory lives in the buffer pool (512M here), see L-1206 lesson.
ROW_BATCH = 1000
ARTIFACT_BATCH = 20  # ~5MB payload per row
RUN_SCOPED_TABLES = ("backtest_candidates", "backtest_trades", "backtest_failure_attributions")
OPTIMIZE_TABLES = (*RUN_SCOPED_TABLES, "backtest_input_artifacts")


def _in(sql: str, name: str) -> "text":
    return text(sql).bindparams(bindparam(name, expanding=True))


def plan(conn, keep_days: int, extra_protect: set[str]) -> dict:
    cutoff = datetime.utcnow() - timedelta(days=keep_days)
    runs = conn.execute(
        text("SELECT backtest_id, parent_backtest_id, experiment_id, snapshot_id, created_at FROM backtest_runs_v2")
    ).fetchall()
    by_id = {r.backtest_id: r for r in runs}

    exps = conn.execute(
        text("SELECT experiment_id, baseline_backtest_id, snapshot_id FROM backtest_experiments")
    ).fetchall()
    exp_ids = {e.experiment_id for e in exps}
    protected = set(extra_protect) | {e.baseline_backtest_id for e in exps}
    protected_snapshots = {e.snapshot_id for e in exps}
    for r in runs:
        if r.created_at >= cutoff or r.experiment_id in exp_ids:
            protected.add(r.backtest_id)
    for bid in list(protected):  # parent chain
        while bid in by_id and by_id[bid].parent_backtest_id:
            bid = by_id[bid].parent_backtest_id
            protected.add(bid)
    protected_snapshots |= {by_id[b].snapshot_id for b in protected if b in by_id}

    prune_runs = sorted(b for b in by_id if b not in protected)
    prune_artifacts: list[str] = []
    for aid, snap, created in conn.execute(
        text("SELECT artifact_id, snapshot_id, created_at FROM backtest_input_artifacts")
    ):
        if snap not in protected_snapshots and created < cutoff:
            prune_artifacts.append(aid)
    return {
        "cutoff": cutoff,
        "protected_runs": len(protected),
        "prune_runs": prune_runs,
        "prune_artifacts": prune_artifacts,
    }


def _table_stats(conn, table: str) -> tuple[int, int]:
    """(approx rows, data bytes) from information_schema; cheap, no table scan."""
    row = conn.execute(
        text(
            "SELECT table_rows, data_length FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = :t"
        ),
        {"t": table},
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


def report(conn, p: dict) -> None:
    print(f"cutoff(UTC)={p['cutoff']:%Y-%m-%d %H:%M}  protected_runs={p['protected_runs']}  "
          f"prune_runs={len(p['prune_runs'])}  prune_artifacts={len(p['prune_artifacts'])}")
    total = 0
    for table in RUN_SCOPED_TABLES:
        n = 0
        if p["prune_runs"]:
            n = int(conn.scalar(_in(f"SELECT COUNT(*) FROM {table} WHERE backtest_id IN :ids", "ids"),
                                {"ids": p["prune_runs"]}) or 0)
        rows, size = _table_stats(conn, table)
        est = size * n // rows if rows else 0
        total += est
        print(f"  {table:<34} delete {n:>9,} rows  ~{est / 2**30:5.1f} GiB of {size / 2**30:5.1f}")
    rows, size = _table_stats(conn, "backtest_input_artifacts")
    est = size * len(p["prune_artifacts"]) // rows if rows else 0
    total += est
    print(f"  {'backtest_input_artifacts':<34} delete {len(p['prune_artifacts']):>9,} rows  "
          f"~{est / 2**30:5.1f} GiB of {size / 2**30:5.1f}")
    print(f"  estimated reclaimable after OPTIMIZE: ~{total / 2**30:.1f} GiB")


def _delete_batched(table: str, where: str, params: dict, limit: int) -> int:
    done = 0
    while True:
        with engine.begin() as conn:
            n = conn.execute(text(f"DELETE FROM {table} WHERE {where} LIMIT {limit}"), params).rowcount
        done += n
        if n < limit:
            return done


def apply(p: dict) -> None:
    for i, bid in enumerate(p["prune_runs"], 1):
        counts = {t: _delete_batched(t, "backtest_id = :b", {"b": bid}, ROW_BATCH) for t in RUN_SCOPED_TABLES}
        with engine.begin() as conn:
            conn.execute(text("UPDATE backtest_request_claims SET completed_backtest_id = NULL "
                              "WHERE completed_backtest_id = :b"), {"b": bid})
        print(f"[{i}/{len(p['prune_runs'])}] {bid[:24]} {counts}", flush=True)
    ids = p["prune_artifacts"]
    for i in range(0, len(ids), ARTIFACT_BATCH):
        with engine.begin() as conn:
            conn.execute(_in("DELETE FROM backtest_input_artifacts WHERE artifact_id IN :ids", "ids"),
                         {"ids": ids[i:i + ARTIFACT_BATCH]})
        if (i // ARTIFACT_BATCH) % 25 == 0:
            print(f"artifacts {min(i + ARTIFACT_BATCH, len(ids))}/{len(ids)}", flush=True)
    print("delete done (disk is not returned until --optimize)")


def optimize() -> None:
    for table in OPTIMIZE_TABLES:
        print(f"OPTIMIZE {table} ...", flush=True)
        with engine.connect() as conn:
            for line in conn.execute(text(f"OPTIMIZE TABLE {table}")):
                print("  ", tuple(line))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep-days", type=int, default=30)
    ap.add_argument("--protect", action="append", default=[], metavar="BACKTEST_ID",
                    help="extra backtest_id to keep (repeatable)")
    ap.add_argument("--apply", action="store_true", help="actually delete (default is dry-run)")
    ap.add_argument("--optimize", action="store_true",
                    help="OPTIMIZE TABLE afterwards; needs free disk ~ remaining table size, locks each table")
    args = ap.parse_args()

    with engine.connect() as conn:
        p = plan(conn, args.keep_days, set(args.protect))
        report(conn, p)
    if not args.apply:
        print("dry-run only; pass --apply to delete")
        return 0
    apply(p)
    if args.optimize:
        optimize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
