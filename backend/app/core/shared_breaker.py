"""Cross-process circuit-breaker state (backend, Prefect worker, scripts) via the runtime SQLite store.

Each process keeps its own local CircuitBreaker (failure counting, half-open probe). When a breaker
OPENS, the open-until wall-clock time is published to SQLite under a stable key; every other process
then refuses requests for that key until it expires. A success anywhere clears the shared row.
Reads are cached for SHARED_READ_TTL seconds, writes happen only on state changes, so the store is
touched rarely. If the store is disabled/unavailable (pytest, ASHARE_METRICS_DB=off, locked file)
this degrades to the plain local breaker.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

SHARED_READ_TTL = 2.0
_cache: dict[str, tuple[float, float]] = {}  # key -> (read_at_monotonic, open_until_epoch)
_cache_lock = threading.Lock()


def _conn() -> sqlite3.Connection | None:
    from app.core import metrics

    if metrics._process_role() == "pytest":
        return None
    path = metrics._store_path()
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=1.0)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS breaker_state (key TEXT PRIMARY KEY, open_until REAL NOT NULL, "
        "updated_at REAL NOT NULL, role TEXT, pid INTEGER, reason TEXT)"
    )
    return conn


def _publish_open(key: str, open_until: float, reason: str = "") -> None:
    try:
        conn = _conn()
        if conn is None:
            return
        import os

        from app.core import metrics

        with conn:
            conn.execute(
                "INSERT INTO breaker_state (key, open_until, updated_at, role, pid, reason) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET open_until=MAX(open_until, excluded.open_until), "
                "updated_at=excluded.updated_at, role=excluded.role, pid=excluded.pid, reason=excluded.reason",
                (key, open_until, time.time(), metrics._process_role(), os.getpid(), reason[:200]),
            )
        conn.close()
        with _cache_lock:
            _cache[key] = (time.monotonic(), open_until)
    except Exception as exc:  # noqa: BLE001
        logger.debug("shared breaker publish failed %s: %s", key, exc)


def _publish_closed(key: str) -> None:
    with _cache_lock:
        cached = _cache.get(key)
        _cache[key] = (time.monotonic(), 0.0)
    if cached is not None and cached[1] <= 0:
        return  # already known closed: avoid a write on every success
    try:
        conn = _conn()
        if conn is None:
            return
        with conn:
            conn.execute("DELETE FROM breaker_state WHERE key=?", (key,))
        conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("shared breaker clear failed %s: %s", key, exc)


def shared_open_until(key: str) -> float:
    now_m = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and now_m - cached[0] < SHARED_READ_TTL:
            return cached[1]
    value = 0.0
    try:
        conn = _conn()
        if conn is not None:
            row = conn.execute("SELECT open_until FROM breaker_state WHERE key=?", (key,)).fetchone()
            conn.close()
            value = float(row[0]) if row else 0.0
    except Exception as exc:  # noqa: BLE001
        logger.debug("shared breaker read failed %s: %s", key, exc)
    with _cache_lock:
        _cache[key] = (now_m, value)
    return value


def shared_breaker_states() -> list[dict[str, Any]]:
    """For /api/metrics/runtime: currently open shared breakers."""
    try:
        conn = _conn()
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT key, open_until, updated_at, role, pid, reason FROM breaker_state WHERE open_until > ?",
            (time.time(),),
        ).fetchall()
        conn.close()
    except Exception:  # noqa: BLE001
        return []
    return [
        {"key": r[0], "open_until": r[1], "updated_at": r[2], "role": r[3], "pid": r[4], "reason": r[5]}
        for r in rows
    ]


class SharedCircuitBreaker:
    """Drop-in for tushare_transport.CircuitBreaker with cross-process open state."""

    def __init__(self, key: str, failure_threshold: int, recovery_seconds: float) -> None:
        from app.services.tushare_transport import CircuitBreaker

        self.key = key
        self._local = CircuitBreaker(failure_threshold, recovery_seconds)

    def allow_request(self) -> bool:
        if not self._local.allow_request():
            return False
        return shared_open_until(self.key) <= time.time()

    def record_success(self) -> None:
        self._local.record_success()
        _publish_closed(self.key)

    def record_failure(self, *, force_open: bool = False, recovery_seconds: float | None = None) -> None:
        before = self._local.state
        self._local.record_failure(force_open=force_open, recovery_seconds=recovery_seconds)
        if self._local.state == "open" and before != "open":
            remaining = max(0.0, self._local._open_until - time.monotonic())
            _publish_open(self.key, time.time() + remaining, reason="force_open" if force_open else "threshold")

    @property
    def state(self) -> str:
        local = self._local.state
        if local != "closed":
            return local
        return "open" if shared_open_until(self.key) > time.time() else "closed"
