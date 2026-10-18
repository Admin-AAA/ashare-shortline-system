"""Process-local observability metrics (counters + duration summaries).

Pure observation: recording never raises and never alters control flow.
Exposed at GET /api/metrics/runtime.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

_lock = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_timings: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = {}  # [count, sum, max]


def _key(name: str, labels: dict[str, Any]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return name, tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def inc(name: str, value: float = 1.0, **labels: Any) -> None:
    try:
        k = _key(name, labels)
        with _lock:
            _counters[k] = _counters.get(k, 0.0) + value
    except Exception:  # noqa: BLE001 - metrics must never break callers
        return


def observe(name: str, seconds: float, **labels: Any) -> None:
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


def snapshot() -> dict[str, Any]:
    with _lock:
        counters = [{"name": n, "labels": dict(l), "value": v} for (n, l), v in sorted(_counters.items())]
        timings = [{"name": n, "labels": dict(l), "count": int(s[0]), "sum_seconds": round(s[1], 6),
                    "avg_seconds": round(s[1] / s[0], 6) if s[0] else 0.0, "max_seconds": round(s[2], 6)}
                   for (n, l), s in sorted(_timings.items())]
        gauges = [{"name": n, "labels": dict(l), "value": round(v, 4)} for (n, l), v in sorted(_gauges.items())]
    return {"counters": counters, "timings": timings, "gauges": gauges}


def reset() -> None:
    with _lock:
        _counters.clear()
        _timings.clear()
        _gauges.clear()
