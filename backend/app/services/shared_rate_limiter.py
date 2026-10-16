"""Cross-process sliding-window limiter backed by SQLite.

Same semantics as ``SlidingWindowLimiter`` (N acquisitions per window,
wait = oldest + window - now + pad, floored at min_wait, no lock held while
sleeping) but the timestamps live in a SQLite file so the API process and the
Prefect worker share one quota. ``BEGIN IMMEDIATE`` serialises the
check-and-insert across processes. Wall clock (time.time) is used because
monotonic clocks are not comparable across processes.

Enabled by setting ``TUSHARE_SHARED_LIMITER_DB`` to a file path. Any SQLite
error falls back to the in-process limiter for 30s, then the shared backend is retried.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

ENV_VAR = "TUSHARE_SHARED_LIMITER_DB"
RETRY_SHARED_AFTER_SECONDS = 30.0


class SharedSlidingWindowLimiter:
    def __init__(self, key: str, requests_per_minute: int, db_path: str, *,
                 window_seconds: float = 60.0, pad_seconds: float = 0.01,
                 min_wait_seconds: float = 0.01, fallback=None) -> None:
        self._key = key
        self._limit = max(0, int(requests_per_minute))
        self._window = float(window_seconds)
        self._pad = float(pad_seconds)
        self._min_wait = float(min_wait_seconds)
        self._db_path = db_path
        self._fallback = fallback
        self._broken_until = 0.0
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            d = os.path.dirname(self._db_path)
            if d:
                os.makedirs(d, exist_ok=True)
            conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
            # Concurrent first-time init can hit SQLITE_BUSY outside the busy
            # handler (journal_mode switch); retry briefly instead of failing over.
            for attempt in range(50):
                try:
                    conn.execute("PRAGMA busy_timeout=30000")
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("CREATE TABLE IF NOT EXISTS hits (k TEXT NOT NULL, ts REAL NOT NULL)")
                    conn.execute("CREATE INDEX IF NOT EXISTS hits_k_ts ON hits(k, ts)")
                    break
                except sqlite3.OperationalError:
                    if attempt == 49:
                        conn.close()
                        raise
                    time.sleep(0.05)
            self._local.conn = conn
        return conn

    def _try_acquire(self) -> float | None:
        """Return None when a slot was taken, else seconds to wait."""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            now = time.time()
            conn.execute("DELETE FROM hits WHERE k=? AND ts<=?", (self._key, now - self._window))
            count, oldest = conn.execute(
                "SELECT COUNT(*), MIN(ts) FROM hits WHERE k=?", (self._key,)).fetchone()
            if count < self._limit:
                conn.execute("INSERT INTO hits(k, ts) VALUES (?, ?)", (self._key, now))
                conn.execute("COMMIT")
                return None
            conn.execute("COMMIT")
            return max(self._min_wait, oldest + self._window - now + self._pad)
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def acquire(self) -> None:
        if self._limit <= 0:
            return
        while True:
            if self._fallback is not None and time.monotonic() < self._broken_until:
                self._fallback.acquire()
                return
            try:
                wait = self._try_acquire()
            except sqlite3.Error as exc:
                if self._fallback is None:
                    raise
                logger.warning("shared rate limiter %s unavailable (%s); falling back to in-process limiter",
                               self._db_path, exc)
                self._broken_until = time.monotonic() + RETRY_SHARED_AFTER_SECONDS
                self._local.conn = None
                continue
            if wait is None:
                return
            time.sleep(wait)
