#!/usr/bin/env python3
"""Incrementally refresh exact current-strategy six-month backtest profiles."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import sys
from typing import Any, Sequence


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_ROOT / ".env")

from app.db.session import SessionLocal  # noqa: E402
from app.services.backtest.strategy_profile import StrategyBacktestProfiles  # noqa: E402


_REFRESH_LOCK = BACKEND_ROOT.parent / ".strategy-profile-refresh.lock"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh version-bound 180-day canonical backtest profiles. "
            "Existing exact profiles are reused without rebuilding snapshots."
        )
    )
    parser.add_argument("--strategy-id", action="append", default=[])
    parser.add_argument("--as-of", help="Latest local market date to consider (YYYY-MM-DD).")
    parser.add_argument(
        "--max-runs",
        type=int,
        default=0,
        help="Maximum missing profiles to execute; 0 means all.",
    )
    return parser


def refresh_profiles(
    profiles: StrategyBacktestProfiles,
    *,
    strategy_ids: Sequence[str] = (),
    as_of: str | None = None,
    max_runs: int = 0,
) -> dict[str, Any]:
    if max_runs < 0:
        raise ValueError("max_runs must be non-negative")
    requested = frozenset(str(value) for value in strategy_ids if str(value))
    targets = tuple(
        target
        for target in profiles.current_targets(as_of=as_of)
        if not requested or target.strategy_id in requested
    )
    results = []
    executed = 0
    for target in targets:
        existing = profiles.read(target)
        if existing is not None:
            results.append(
                {
                    "strategy_id": target.strategy_id,
                    "strategy_version": target.strategy_version,
                    "status": "completed",
                    "reused": True,
                    "backtest_id": existing["backtest_id"],
                    "profile_fingerprint": existing["profile_fingerprint"],
                }
            )
            continue
        if max_runs and executed >= max_runs:
            results.append(
                {
                    "strategy_id": target.strategy_id,
                    "strategy_version": target.strategy_version,
                    "status": "deferred",
                    "reused": False,
                }
            )
            continue
        outcome = profiles.refresh(target)
        executed += 1
        profile = outcome.get("profile") or {}
        results.append(
            {
                "strategy_id": target.strategy_id,
                "strategy_version": target.strategy_version,
                "status": outcome["status"],
                "reused": bool(outcome.get("reused")),
                "backtest_id": profile.get("backtest_id"),
                "profile_fingerprint": profile.get("profile_fingerprint"),
                "shared_snapshot_id": outcome.get("shared_snapshot_id"),
            }
        )
    missing = sorted(requested - {target.strategy_id for target in targets})
    return {
        "status": "completed" if not missing else "partial",
        "window_days": 180,
        "executed_runs": executed,
        "missing_strategy_ids": missing,
        "profiles": results,
    }


@contextmanager
def _nonblocking_refresh_lock():
    _REFRESH_LOCK.touch(exist_ok=True)
    with _REFRESH_LOCK.open("r+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with _nonblocking_refresh_lock() as acquired:
        if not acquired:
            print(json.dumps({"status": "busy", "executed_runs": 0}, sort_keys=True))
            return 0
        session = SessionLocal()
        try:
            payload = refresh_profiles(
                StrategyBacktestProfiles(session),
                strategy_ids=tuple(args.strategy_id),
                as_of=args.as_of,
                max_runs=args.max_runs,
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False))
            return 0 if payload["status"] == "completed" else 2
        except Exception as exc:
            session.rollback()
            print(
                json.dumps(
                    {"status": "failed", "error": str(exc)},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 1
        finally:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
