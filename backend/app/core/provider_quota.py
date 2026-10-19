"""Cross-process daily request counter per upstream provider (runtime SQLite), for backfill budgets.

Every upstream attempt made by TushareHttpProvider (any process: backend, worker, scripts) is counted
under the Asia/Shanghai calendar day. Increments are buffered in memory and flushed every
FLUSH_EVERY requests / FLUSH_SECONDS, plus at exit, so the store is touched rarely.

Backfill scripts call ``backfill_budget_check`` before each unit of work and stop once a provider has
used BACKFILL_BUDGET_FRACTION (default 0.6) of its configured daily quota
(env ASHARE_TUSHARE_<PROVIDER>_DAILY_QUOTA), or once the provider reported its daily limit as
exhausted today — leaving the rest of the quota for the 17:35 formal run.
If the store is unavailable (pytest, ASHARE_METRICS_DB=off) counting is a no-op and the check passes
on quota grounds (the exhausted flag then lives only in this process).
"""
from __future__ import annotations

import atexit
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

FLUSH_EVERY = 20
FLUSH_SECONDS = 10.0
BACKFILL_BUDGET_FRACTION = 0.6
_TZ = timezone(timedelta(hours=8))

_lock = threading.Lock()
_pending: dict[tuple[str, str], int] = {}
_last_flush = time.monotonic()
_local_exhausted: set[tuple[str, str]] = set()


def _today() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def _conn() -> sqlite3.Connection | None:
    try:
        from app.core import metrics

        if metrics._process_role() == "pytest":
            return None
        path = metrics._store_path()
        if path is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=1.0)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS provider_daily_usage (day TEXT NOT NULL, provider TEXT NOT NULL, "
            "requests INTEGER NOT NULL DEFAULT 0, exhausted INTEGER NOT NULL DEFAULT 0, "
            "PRIMARY KEY (day, provider))"
        )
        return conn
    except Exception:  # noqa: BLE001 - telemetry must never break data fetching
        logger.debug("provider_quota store unavailable", exc_info=True)
        return None


def flush() -> None:
    global _last_flush
    with _lock:
        items = list(_pending.items())
        _pending.clear()
        _last_flush = time.monotonic()
    if not items:
        return
    conn = _conn()
    if conn is None:
        return
    try:
        with conn:
            for (day, provider), n in items:
                conn.execute(
                    "INSERT INTO provider_daily_usage(day, provider, requests) VALUES (?,?,?) "
                    "ON CONFLICT(day, provider) DO UPDATE SET requests = requests + excluded.requests",
                    (day, provider, n),
                )
    except Exception:  # noqa: BLE001
        logger.debug("provider_quota flush failed", exc_info=True)
    finally:
        conn.close()


atexit.register(flush)


def record_request(provider: str) -> None:
    key = (_today(), provider)
    with _lock:
        _pending[key] = _pending.get(key, 0) + 1
        due = sum(_pending.values()) >= FLUSH_EVERY or time.monotonic() - _last_flush >= FLUSH_SECONDS
    if due:
        flush()


def mark_exhausted(provider: str) -> None:
    day = _today()
    _local_exhausted.add((day, provider))
    conn = _conn()
    if conn is None:
        return
    try:
        with conn:
            conn.execute(
                "INSERT INTO provider_daily_usage(day, provider, requests, exhausted) VALUES (?,?,0,1) "
                "ON CONFLICT(day, provider) DO UPDATE SET exhausted = 1",
                (day, provider),
            )
    except Exception:  # noqa: BLE001
        logger.debug("provider_quota mark_exhausted failed", exc_info=True)
    finally:
        conn.close()


def usage(provider: str) -> tuple[int, bool]:
    """(requests today across all processes incl. this one's unflushed, exhausted flag)."""
    day = _today()
    with _lock:
        pending = _pending.get((day, provider), 0)
    exhausted = (day, provider) in _local_exhausted
    conn = _conn()
    if conn is None:
        return pending, exhausted
    try:
        row = conn.execute(
            "SELECT requests, exhausted FROM provider_daily_usage WHERE day=? AND provider=?", (day, provider)
        ).fetchone()
    except Exception:  # noqa: BLE001
        row = None
    finally:
        conn.close()
    if row:
        return int(row[0]) + pending, exhausted or bool(row[1])
    return pending, exhausted


def daily_quota(provider: str) -> int | None:
    raw = os.environ.get(f"ASHARE_TUSHARE_{provider.upper()}_DAILY_QUOTA", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def backfill_budget_check(
    providers: list[str] | tuple[str, ...], fraction: float = BACKFILL_BUDGET_FRACTION
) -> tuple[bool, str]:
    """True while every provider is below ``fraction`` of its daily quota and not exhausted today.

    A backfill should stop when *any* provider it may hit is over budget: routing falls back to the
    other provider, so continuing would just drain that one too.
    """
    for provider in providers:
        used, exhausted = usage(provider)
        if exhausted:
            return False, f"{provider} 今日额度已耗尽"
        quota = daily_quota(provider)
        if quota is not None and used >= fraction * quota:
            return False, f"{provider} 今日已用 {used}/{quota}（≥{fraction:.0%}），为正式运行留余量"
    return True, "ok"
