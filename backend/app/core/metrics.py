"""Observability metrics (counters + duration summaries + gauges).

Pure observation: recording never raises and never alters control flow.
Exposed at GET /api/metrics/runtime.

Each process aggregates in memory and a daemon thread periodically publishes its
snapshot to a shared SQLite file (``ASHARE_METRICS_DB``; default
``backend/data/runtime/runtime_metrics.sqlite``) keyed by role+pid, so the backend
endpoint can also show the Prefect worker's metrics and survive restarts
(``processes`` list).  ``process.started_at`` tells whether local counters were
just reset by a restart.  Set ``ASHARE_METRICS_DB=off`` to disable publishing.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Iterator

_lock = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_timings: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = {}  # [count, sum, max]

# Sliding-window rates (2026-11-15): counters are process-lifetime totals, so a restart zeroes
# them and "requests today" is unknowable. Per-counter 1-second buckets keep the last
# RATE_WINDOW_SECONDS so the ops view can answer "现在每分钟打了多少次" — the number that
# actually matters against a provider's RPM budget. Bounded: one deque (maxlen 130) per counter.
RATE_WINDOW_SECONDS = 60.0
_recent: dict[tuple[str, tuple[tuple[str, str], ...]], "deque[list[float]]"] = {}
_RATE_MAX_BUCKETS = 130


def _rate_bump_locked(k: tuple[str, tuple[tuple[str, str], ...]], value: float) -> None:
    """Append ``value`` to the current second's bucket (caller holds ``_lock``)."""
    if value <= 0:
        return
    sec = int(time.time())
    dq = _recent.get(k)
    if dq is None:
        dq = _recent[k] = deque(maxlen=_RATE_MAX_BUCKETS)
    if dq and dq[-1][0] == sec:
        dq[-1][1] += value
    else:
        dq.append([sec, value])
    cutoff = sec - RATE_WINDOW_SECONDS - 2
    while dq and dq[0][0] < cutoff:
        dq.popleft()


def _rates_locked(window_seconds: float = RATE_WINDOW_SECONDS) -> list[dict[str, Any]]:
    """Per-counter rate over the trailing window (caller holds ``_lock``)."""
    now = time.time()
    floor = now - window_seconds
    out: list[dict[str, Any]] = []
    for (name, labels), dq in _recent.items():
        while dq and dq[0][0] < floor - 2:
            dq.popleft()
        total = sum(v for sec, v in dq if sec >= floor - 1)
        out.append({
            "name": name,
            "labels": dict(labels),
            "count": round(total, 3),
            "per_minute": round(total * 60.0 / window_seconds, 3),
        })
    out.sort(key=lambda i: (i["name"], str(sorted(i["labels"].items()))))
    return out


def rates(window_seconds: float = RATE_WINDOW_SECONDS) -> list[dict[str, Any]]:
    """Trailing-window (default 60s) rate per counter, sorted by name."""
    try:
        with _lock:
            return _rates_locked(window_seconds)
    except Exception:  # noqa: BLE001 - metrics must never break callers
        return []


def _key(name: str, labels: dict[str, Any]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return name, tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def inc(name: str, value: float = 1.0, **labels: Any) -> None:
    if not _publisher_started:
        start_publisher()
    try:
        k = _key(name, labels)
        with _lock:
            _counters[k] = _counters.get(k, 0.0) + value
            _rate_bump_locked(k, value)
    except Exception:  # noqa: BLE001 - metrics must never break callers
        return


def observe(name: str, seconds: float, **labels: Any) -> None:
    if not _publisher_started:
        start_publisher()
    try:
        k = _key(name, labels)
        with _lock:
            s = _timings.setdefault(k, [0.0, 0.0, 0.0])
            s[0] += 1
            s[1] += seconds
            s[2] = max(s[2], seconds)
    except Exception:  # noqa: BLE001 - metrics must never break callers
        return


_gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}


def set_gauge(name: str, value: float, **labels: Any) -> None:
    try:
        k = _key(name, labels)
        with _lock:
            _gauges[k] = float(value)
    except Exception:  # noqa: BLE001 - metrics must never break callers
        return


@contextmanager
def timed(name: str, **labels: Any) -> Iterator[None]:
    t0 = time.perf_counter()
    try:
        yield
    finally:
        observe(name, time.perf_counter() - t0, **labels)


def _local_snapshot() -> dict[str, Any]:
    with _lock:
        counters = [{"name": n, "labels": dict(l), "value": v} for (n, l), v in sorted(_counters.items())]
        timings = [{"name": n, "labels": dict(l), "count": int(s[0]), "sum_seconds": round(s[1], 6),
                    "avg_seconds": round(s[1] / s[0], 6) if s[0] else 0.0, "max_seconds": round(s[2], 6)}
                   for (n, l), s in sorted(_timings.items())]
        gauges = [{"name": n, "labels": dict(l), "value": round(v, 4)} for (n, l), v in sorted(_gauges.items())]
        rates = _rates_locked()
    return {"counters": counters, "timings": timings, "gauges": gauges, "rates": rates}


_PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")
_PUBLISH_INTERVAL_S = 15.0
_STALE_ROW_S = 7 * 86400
_publisher_started = False


def _process_role() -> str:
    explicit = os.environ.get("ASHARE_PROCESS_ROLE", "").strip()
    if explicit:
        return explicit
    argv = " ".join(sys.argv).lower()
    if "pytest" in argv:
        return "pytest"
    if "prefect" in argv or "worker" in argv:
        return "prefect-worker"
    if "uvicorn" in argv or "app.main" in argv:
        return "backend"
    return Path(sys.argv[0] or "python").name or "python"


def _store_path() -> Path | None:
    raw = os.environ.get("ASHARE_METRICS_DB", "").strip()
    if raw.lower() in {"off", "0", "false", "none"}:
        return None
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2] / "data" / "runtime" / "runtime_metrics.sqlite"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=2.0)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS process_metrics (role TEXT NOT NULL, pid INTEGER NOT NULL, "
        "started_at TEXT NOT NULL, updated_at TEXT NOT NULL, snapshot_json TEXT NOT NULL, "
        "PRIMARY KEY (role, pid, started_at))"
    )
    return conn


def publish() -> bool:
    """Write this process's snapshot to the shared store. Never raises."""
    path = _store_path()
    if path is None or _process_role() == "pytest":
        return False
    try:
        now = datetime.now(timezone.utc)
        payload = json.dumps(_local_snapshot(), ensure_ascii=False)
        conn = _connect(path)
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO process_metrics VALUES (?, ?, ?, ?, ?)",
                    (_process_role(), os.getpid(), _PROCESS_STARTED_AT, now.isoformat(timespec="seconds"), payload),
                )
                cutoff = datetime.fromtimestamp(now.timestamp() - _STALE_ROW_S, timezone.utc).isoformat(timespec="seconds")
                conn.execute("DELETE FROM process_metrics WHERE updated_at < ?", (cutoff,))
        finally:
            conn.close()
        return True
    except Exception:  # noqa: BLE001 - metrics must never break callers
        return False


def _publisher_loop() -> None:
    while True:
        time.sleep(_PUBLISH_INTERVAL_S)
        publish()


def start_publisher() -> None:
    """Start the background publisher once per process (idempotent, lazy)."""
    global _publisher_started
    if _publisher_started or _store_path() is None or _process_role() == "pytest":
        return
    with _lock:
        if _publisher_started:
            return
        _publisher_started = True
    threading.Thread(target=_publisher_loop, name="metrics-publisher", daemon=True).start()


def _stored_processes() -> list[dict[str, Any]]:
    path = _store_path()
    if path is None or not path.exists():
        return []
    try:
        conn = _connect(path)
        try:
            rows = conn.execute(
                "SELECT role, pid, started_at, updated_at, snapshot_json FROM process_metrics ORDER BY updated_at DESC"
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for role, pid, started_at, updated_at, raw in rows:
        try:
            snap = json.loads(raw)
        except ValueError:
            continue
        out.append({"role": role, "pid": pid, "started_at": started_at, "updated_at": updated_at,
                    "current": pid == os.getpid() and started_at == _PROCESS_STARTED_AT, **snap})
    return out


def snapshot(include_processes: bool = True) -> dict[str, Any]:
    """Local (this process) metrics at top level, for backward compatibility, plus
    process identity and every process published to the shared store."""
    start_publisher()
    publish()
    out = _local_snapshot()
    out["process"] = {"role": _process_role(), "pid": os.getpid(), "started_at": _PROCESS_STARTED_AT}
    if include_processes:
        out["processes"] = _stored_processes()
        try:
            from app.core.shared_breaker import shared_breaker_states

            states = shared_breaker_states()
            # 限频只暂停不熔断：暂停行单独列，避免监控把一次正常限频当成源故障。
            out["open_breakers"] = [s for s in states if s.get("kind") != "pause"]
            out["rate_limit_pauses"] = [s for s in states if s.get("kind") == "pause"]
        except Exception:  # noqa: BLE001
            out["open_breakers"] = []
            out["rate_limit_pauses"] = []
    return out


def reset() -> None:
    with _lock:
        _counters.clear()
        _timings.clear()
        _gauges.clear()
        _recent.clear()
