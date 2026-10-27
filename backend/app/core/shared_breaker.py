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
# Namespace isolation (owner, 2026-10-19): only the live backend and Prefect worker share the
# "live" namespace. Every other process (scripts/*, research runners, ad-hoc python) defaults to
# "research", so a research script tripping a breaker can never block live traffic.
BREAKER_NAMESPACE_ENV = "ASHARE_BREAKER_NAMESPACE"
LIVE_NAMESPACE = "live"
RESEARCH_NAMESPACE = "research"


def breaker_namespace() -> str:
    import os

    raw = os.environ.get(BREAKER_NAMESPACE_ENV, "").strip().lower()
    return LIVE_NAMESPACE if raw == LIVE_NAMESPACE else RESEARCH_NAMESPACE


def mark_live_process() -> None:
    """Called by the backend startup and the Prefect worker entrypoint (inherited by flow subprocesses)."""
    import os

    os.environ[BREAKER_NAMESPACE_ENV] = LIVE_NAMESPACE
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
    # 半开探测连续失败次数（跨进程）：探测失败后的冷却按次数指数拉长，见 probe_backoff_delay。
    conn.execute("CREATE TABLE IF NOT EXISTS breaker_probe_streak (key TEXT PRIMARY KEY, streak INTEGER NOT NULL, updated_at REAL NOT NULL)")
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


def probe_backoff_delay(streak: int, *, base: float | None = None, cap: float | None = None, rng=None) -> float:
    """半开探测失败后的下一次冷却：base × 2^streak，封顶 cap，再加 0–10% 抖动（避免多进程同一秒一起探）。

    2026-10-26：原先每次探测失败都固定再冷却 300 秒，一个真挂了的源（如小石日K）每 5 分钟被探一次、
    一直重开熔断。改为 5→10→20→（封顶）分钟；成功后清零。streak=0 即 base（兼容旧行为）。"""
    import random

    base = float(base if base is not None else PROBE_INTERVAL_SECONDS)
    cap = float(cap if cap is not None else PROBE_MAX_SECONDS)
    delay = min(cap, base * (2 ** max(0, int(streak))))
    return delay + (rng or random).uniform(0.0, 0.1) * delay


def _get_streak(key: str) -> int:
    try:
        conn = _conn()
        if conn is None:
            return 0
        row = conn.execute("SELECT streak FROM breaker_probe_streak WHERE key=?", (key,)).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def _bump_streak(key: str) -> int:
    try:
        conn = _conn()
        if conn is None:
            return 0
        with conn:
            conn.execute(
                "INSERT INTO breaker_probe_streak(key, streak, updated_at) VALUES (?,1,?) "
                "ON CONFLICT(key) DO UPDATE SET streak=streak+1, updated_at=excluded.updated_at", (key, time.time()))
            row = conn.execute("SELECT streak FROM breaker_probe_streak WHERE key=?", (key,)).fetchone()
        conn.close()
        return int(row[0]) if row else 1
    except Exception:  # noqa: BLE001
        return 0


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
            conn.execute("DELETE FROM breaker_probe_streak WHERE key=?", (key,))
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


# Unified cooldown / half-open policy (user, 2026-10-19): an open breaker stays open at least
# PROBE_INTERVAL_SECONDS; then exactly ONE process (per namespace) claims the half-open probe via an
# atomic UPDATE on the SQLite row. Probe success deletes the row (closed); failure re-opens it for
# another PROBE_INTERVAL_SECONDS. A crashed prober's claim expires after PROBE_LEASE_SECONDS.
PROBE_INTERVAL_SECONDS = 300.0
PROBE_LEASE_SECONDS = 60.0
# 探测连续失败时冷却指数拉长的上限（默认 20 分钟；实时源恢复不会被拖到小时级）。
PROBE_MAX_SECONDS = float(__import__("os").environ.get("ASHARE_BREAKER_PROBE_MAX_SECONDS", "1200") or 1200)


def _try_claim_probe(key: str) -> bool | None:
    """True = this process owns the probe; False = shared row still open / someone else probing;
    None = shared store unavailable (caller falls back to local breaker)."""
    try:
        conn = _conn()
        if conn is None:
            return None
        import os

        from app.core import metrics

        now = time.time()
        with conn:
            cur = conn.execute(
                "UPDATE breaker_state SET open_until=?, updated_at=?, role=?, pid=?, reason='half_open_probe' "
                "WHERE key=? AND open_until<=?",
                (now + PROBE_LEASE_SECONDS, now, metrics._process_role(), os.getpid(), key, now),
            )
            claimed = cur.rowcount == 1
            row = None if claimed else conn.execute("SELECT open_until FROM breaker_state WHERE key=?", (key,)).fetchone()
        conn.close()
        with _cache_lock:
            _cache[key] = (time.monotonic(), now + PROBE_LEASE_SECONDS if claimed else (float(row[0]) if row else 0.0))
        if claimed:
            return True
        return False if row else True  # row vanished = closed meanwhile
    except Exception as exc:  # noqa: BLE001
        logger.debug("shared breaker probe claim failed %s: %s", key, exc)
        return None


def is_open(key: str) -> bool:
    """Generic pre-request check for an un-namespaced breaker key (e.g. an upstream transport key):
    True while the current namespace's shared row says open (half-open probes not claimed here)."""
    return shared_open_until(f"{breaker_namespace()}:{key}") > time.time()


# Per-kind thresholds (configurable: ASHARE_BREAKER_THRESHOLD_TIMEOUT / _BAD_DATA).
def kind_threshold(kind: str | None) -> int | None:
    if kind not in ("timeout", "bad_data"):
        return None
    default = 2 if kind == "timeout" else 3
    try:
        from app.core.config import get_settings

        return max(1, int(getattr(get_settings(), f"breaker_threshold_{kind}", default)))
    except Exception:  # noqa: BLE001
        return default


class SharedCircuitBreaker:
    """Drop-in for tushare_transport.CircuitBreaker with cross-process open state and a single
    cross-process half-open probe every PROBE_INTERVAL_SECONDS."""

    def __init__(self, key: str, failure_threshold: int, recovery_seconds: float) -> None:
        from app.services.tushare_transport import CircuitBreaker

        self.base_key = key
        self.key = f"{breaker_namespace()}:{key}"
        self._recovery = max(float(recovery_seconds), PROBE_INTERVAL_SECONDS)
        self._local = CircuitBreaker(failure_threshold, self._recovery)
        self._probing = False
        self._kind_counts: dict[str, int] = {}
        self._kind_lock = threading.Lock()

    def allow_request(self) -> bool:
        open_until = shared_open_until(self.key)
        if open_until > time.time():
            return False
        if open_until > 0:  # cooldown elapsed: race for the one shared probe
            claimed = _try_claim_probe(self.key)
            if claimed is False:
                return False
            if claimed is True:
                self._probing = True
                self._local.record_success()  # shared state governs; local half-open would double-gate
                return True
        return self._local.allow_request()

    def record_success(self) -> None:
        self._probing = False
        with self._kind_lock:
            self._kind_counts.clear()
        self._local.record_success()
        _publish_closed(self.key)

    def record_failure(
        self, *, force_open: bool = False, recovery_seconds: float | None = None, kind: str | None = None
    ) -> None:
        """Per-kind thresholds (audit item 1): rate_limit opens immediately for the upstream
        retry-after (else PROBE_INTERVAL_SECONDS); timeout / bad_data open after their own
        configurable counts; anything else uses the breaker's failure_threshold."""
        kind = (kind or "").lower() or None
        if self._probing:
            self._probing = False
            streak = _bump_streak(self.key)
            self._open(probe_backoff_delay(streak - 1 if streak > 0 else 0), f"probe_failed#{streak}")
            return
        if kind == "rate_limit":
            delay = float(recovery_seconds) if recovery_seconds and recovery_seconds > 0 else PROBE_INTERVAL_SECONDS
            self._open(delay, "rate_limit")
            return
        if force_open:
            self._open(max(self._recovery, float(recovery_seconds or 0.0)), "force_open")
            return
        threshold = kind_threshold(kind)
        if threshold is None:
            before = self._local.state
            self._local.record_failure(recovery_seconds=recovery_seconds)
            if self._local.state == "open" and before != "open":
                self._publish_local("threshold")
            return
        with self._kind_lock:
            n = self._kind_counts.get(kind, 0) + 1
            self._kind_counts[kind] = n
        if n >= threshold:
            self._open(self._recovery, f"{kind}x{n}")

    def _open(self, delay: float, reason: str) -> None:
        loc = self._local
        with loc._lock:
            loc._open_until = time.monotonic() + max(0.1, float(delay))
            loc._half_open_probe = False
            loc._failure_count = 0
        with self._kind_lock:
            self._kind_counts.clear()
        self._publish_local(reason)

    def _publish_local(self, reason: str) -> None:
        remaining = max(0.0, self._local._open_until - time.monotonic())
        _publish_open(self.key, time.time() + remaining, reason=reason)

    @property
    def state(self) -> str:
        local = self._local.state
        if local != "closed":
            return local
        ou = shared_open_until(self.key)
        if ou <= 0:
            return "closed"
        return "open" if ou > time.time() else "half_open"
