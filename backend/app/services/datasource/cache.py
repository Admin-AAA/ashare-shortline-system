"""Gateway caching layer (audit item 3).

* **Fresh cache** — in-process ``cachetools.TTLCache`` per dataset (``DATASET_TTL``; 0 = no caching).
* **Single-flight** — concurrent identical requests share one upstream call.
* **Stale fallback** — every successful answer of a cache-enabled dataset is also written to a
  ``diskcache`` store (cross-process, survives restarts). When all providers fail, the gateway may
  return that last-known answer, ALWAYS flagged ``stale=True`` with its ``data_time``.
  Stale is opt-in: only datasets in ``STALE_OK_DATASETS`` (UI/realtime display) default to it, and
  never inside ``formal_chain()`` (formal selection / settlement / daily DB writes).
"""
from __future__ import annotations

import contextlib
import contextvars
import copy
import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

from cachetools import TTLCache

logger = logging.getLogger(__name__)

DATASET_TTL: dict[str, float] = {
    "quote.realtime": 3.0,
    "quote.realtime_frame": 3.0,
    "bar.minute": 30.0,
    "ann.search.cninfo": 300.0,
}
# quote.realtime_frame is NOT stale-ok: fetch_frame() callers only see the DataFrame, so the
# stale flag could not reach them.
STALE_OK_DATASETS: frozenset[str] = frozenset({"quote.realtime"})
STALE_MAX_AGE_SECONDS = 3 * 86400
_SH = ZoneInfo("Asia/Shanghai")

_formal: contextvars.ContextVar[bool] = contextvars.ContextVar("datasource_formal_chain", default=False)


@contextlib.contextmanager
def formal_chain() -> Iterator[None]:
    """Mark a formal-chain scope (formal picks, settlement, daily DB writes): stale data refused."""
    tok = _formal.set(True)
    try:
        yield
    finally:
        _formal.reset(tok)


def ctx_bound(fn):
    """Bind fn to a fresh copy of the *current* context (call once per submit/thread).

    contextvars do not cross into pool/Thread workers; without this the formal_chain marker
    is lost and the gateway would serve stale data on formal paths.
    """
    ctx = contextvars.copy_context()

    def _runner(*args, **kwargs):
        return ctx.run(fn, *args, **kwargs)

    return _runner


def submit_ctx(pool, fn, *args, **kwargs):
    """pool.submit with a per-submit copy of the current context."""
    return pool.submit(ctx_bound(fn), *args, **kwargs)


def in_formal_chain() -> bool:
    return _formal.get()


def ttl_for(dataset: str) -> float:
    try:
        from app.core.config import get_settings

        raw = str(getattr(get_settings(), "datasource_cache_ttl", "") or "")
        for part in raw.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() == dataset:
                    return max(0.0, float(v))
    except Exception:  # noqa: BLE001
        pass
    return DATASET_TTL.get(dataset, 0.0)


def stale_allowed(dataset: str, allow_stale: bool | None) -> bool:
    if in_formal_chain():
        return False
    if allow_stale is not None:
        return bool(allow_stale)
    return dataset in STALE_OK_DATASETS


def now_iso() -> str:
    return datetime.now(_SH).isoformat(timespec="seconds")


# ── fresh (in-process) ──────────────────────────────────────────────────────
_fresh: dict[str, TTLCache] = {}
_fresh_lock = threading.Lock()


def fresh_get(dataset: str, key: str) -> Any:
    ttl = ttl_for(dataset)
    if ttl <= 0:
        return None
    with _fresh_lock:
        c = _fresh.get(dataset)
        return None if c is None else c.get(key)


def fresh_put(dataset: str, key: str, value: Any) -> None:
    ttl = ttl_for(dataset)
    if ttl <= 0:
        return
    with _fresh_lock:
        c = _fresh.get(dataset)
        if c is None or c.ttl != ttl:
            c = _fresh[dataset] = TTLCache(maxsize=4096, ttl=ttl)
        c[key] = value


def clear_fresh() -> None:
    with _fresh_lock:
        _fresh.clear()


# ── stale (diskcache, cross-process) ───────────────────────────────────────
_disk: list[Any] = []
_disk_lock = threading.Lock()


def _disk_cache():
    with _disk_lock:
        if _disk:
            return _disk[0]
        from app.core import metrics

        if metrics._process_role() == "pytest":
            return None
        path = metrics._store_path()
        if path is None:
            return None
        import diskcache

        _disk.append(diskcache.Cache(str(path.parent / "gw_stale"), size_limit=512 * 1024 * 1024))
        return _disk[0]


def stale_put(dataset: str, key: str, value: Any, provider: str | None) -> None:
    if ttl_for(dataset) <= 0:
        return
    try:
        dc = _disk_cache()
        if dc is not None:
            dc.set(f"{dataset}|{key}", {"value": value, "data_time": now_iso(), "provider": provider},
                   expire=STALE_MAX_AGE_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.debug("stale_put failed %s: %s", dataset, exc)


def stale_get(dataset: str, key: str) -> dict[str, Any] | None:
    try:
        dc = _disk_cache()
        return None if dc is None else dc.get(f"{dataset}|{key}")
    except Exception as exc:  # noqa: BLE001
        logger.debug("stale_get failed %s: %s", dataset, exc)
        return None


def mark_stale_row(row: Any, data_time: str) -> Any:
    if isinstance(row, dict):
        out = copy.copy(row)
        out["stale"] = True
        out["data_time"] = data_time
        return out
    return row


# ── single-flight ───────────────────────────────────────────────────────────
class _Flight:
    __slots__ = ("event", "result", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None


_flights: dict[str, _Flight] = {}
_flights_lock = threading.Lock()


def single_flight(key: str, fn: Callable[[], Any], wait_timeout: float = 130.0) -> Any:
    with _flights_lock:
        f = _flights.get(key)
        leader = f is None
        if leader:
            f = _flights[key] = _Flight()
    if not leader:
        if f.event.wait(wait_timeout) and f.error is None:
            return f.result
        return fn()  # leader failed/hung: do our own call
    try:
        f.result = fn()
        return f.result
    except BaseException as exc:
        f.error = exc
        raise
    finally:
        f.event.set()
        with _flights_lock:
            _flights.pop(key, None)


_ = time  # keep import for monkeypatching in tests
