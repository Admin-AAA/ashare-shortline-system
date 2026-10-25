"""Generic data-source gateway: dataset-level routing over pluggable provider adapters.

Layers
------
* **Dataset keys** (``quote.realtime``, ``bar.minute``, ...) are business datasets,
  independent of any upstream protocol.  Tushare pro APIs stay reachable through the
  compatibility key ``tushare.<api_name>`` (delegates to ``TushareProviderRouter``),
  so existing ``get_pro().<api>()`` callers are unchanged.
* **DataProvider adapters** own protocol/auth/native-error mapping and return rows
  already normalized to the dataset schema (``SCHEMAS``).
* **Gateway** tries enabled providers in order, with a per-(provider,dataset) circuit
  breaker, unified error taxonomy (``ErrorKind``) and metrics
  (``dataset_requests`` / ``dataset_latency``).  Two merge modes:
  ``first`` (first non-empty answer wins) and ``fill`` (keyed rows; later providers
  only fill keys still missing).
* **Enabled switches** come from ``data_source_config`` (admin page).  A routing
  table/order NEVER re-enables a provider: any matching row with enabled=False skips it.
* **Cutover / shadow**: per-dataset flags (``ASHARE_DATASOURCE_CUTOVER``,
  ``ASHARE_DATASOURCE_SHADOW``); default is the legacy path.  Shadow runs the gateway
  beside legacy on a sample, compares canonical JSON, logs + counts mismatches, and
  always returns the legacy result.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.core import metrics as _metrics

logger = logging.getLogger(__name__)


class ErrorKind(StrEnum):
    RATE_LIMIT = "rate_limit"
    NO_PERMISSION = "no_permission"
    INVALID_REQUEST = "invalid_request"
    NETWORK = "network"
    TIMEOUT = "timeout"
    UPSTREAM = "upstream"
    DATA_PENDING = "data_pending"
    CONFIGURATION = "configuration"
    DISABLED = "disabled"
    CIRCUIT_OPEN = "circuit_open"
    CONTRACT = "contract"
    BAD_DATA = "bad_data"  # empty where data is expected (e.g. realtime quotes in session)


# breaker failure kind per ErrorKind (thresholds in shared_breaker.kind_threshold)
BREAKER_KIND: dict[ErrorKind, str] = {
    ErrorKind.RATE_LIMIT: "rate_limit", ErrorKind.TIMEOUT: "timeout",
    ErrorKind.CONTRACT: "bad_data", ErrorKind.BAD_DATA: "bad_data",
}
# Not provider faults: never count toward a breaker.  CIRCUIT_OPEN = an upstream (Tushare router)
# breaker already refused the call; counting it here would keep this breaker open for another
# cooldown after the upstream one has recovered (double-gating).
NON_BREAKER_KINDS = frozenset({ErrorKind.INVALID_REQUEST, ErrorKind.DATA_PENDING, ErrorKind.DISABLED, ErrorKind.CIRCUIT_OPEN})

# Datasets that must be non-empty during the trading session (empty => BAD_DATA, not success).
EXPECT_NONEMPTY_IN_SESSION: frozenset[str] = frozenset({"quote.realtime", "quote.realtime_frame"})


def in_trading_session(now: Any = None) -> bool:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if now.weekday() >= 5:
        return False
    hm = (now.hour, now.minute)
    return (9, 30) <= hm < (11, 30) or (13, 0) <= hm < (15, 0)


class DataSourceError(RuntimeError):
    def __init__(self, kind: ErrorKind, message: str = "", *, provider: str = "", dataset: str = "") -> None:
        super().__init__(f"{provider}/{dataset}: {kind.value} {message}".strip())
        self.kind, self.provider, self.dataset = kind, provider, dataset


def classify_exception(exc: BaseException) -> ErrorKind:
    """Map native exceptions (Tushare FailureCategory, requests/httpx, timeouts) to ErrorKind."""
    if isinstance(exc, DataSourceError):
        return exc.kind
    cat = str(getattr(getattr(exc, "category", None), "value", getattr(exc, "category", "")) or "")
    table = {
        "http_rate_limit": ErrorKind.RATE_LIMIT, "business_rate_limit": ErrorKind.RATE_LIMIT,
        "quota_exhausted": ErrorKind.RATE_LIMIT,
        "permission": ErrorKind.NO_PERMISSION, "invalid_request": ErrorKind.INVALID_REQUEST,
        "http_client": ErrorKind.INVALID_REQUEST, "network": ErrorKind.NETWORK,
        "timeout": ErrorKind.TIMEOUT, "data_pending": ErrorKind.DATA_PENDING,
        "configuration": ErrorKind.CONFIGURATION, "circuit_open": ErrorKind.CIRCUIT_OPEN,
    }
    if cat in table:
        return table[cat]
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return ErrorKind.TIMEOUT
    if "connect" in name or "network" in name or "proxy" in name or "ssl" in name:
        return ErrorKind.NETWORK
    return ErrorKind.UPSTREAM


# Normalized output schemas: required keys of every row a provider returns.
SCHEMAS: dict[str, tuple[str, ...]] = {
    "quote.realtime": ("code", "ts_code", "name", "trade_date", "quote_time", "price", "open",
                       "pre_close", "high", "low", "volume", "amount", "source"),
    "bar.minute": ("code", "trade_date", "trade_time", "open", "high", "low", "close",
                   "volume", "amount", "source"),
    "ann.search.cninfo": ("secCode", "secName", "announcementTitle", "announcementTime",
                          "announcementId", "raw"),
}


def validate_rows(dataset: str, rows: Iterable[Mapping[str, Any]], provider: str = "") -> None:
    required = SCHEMAS.get(dataset)
    if not required:
        return
    for row in rows:
        missing = [k for k in required if k not in row]
        if missing:
            raise DataSourceError(ErrorKind.CONTRACT, f"missing {missing}", provider=provider, dataset=dataset)


@runtime_checkable
class DataProvider(Protocol):
    name: str
    # (source, source_type) rows in data_source_config that gate this provider.
    config_keys: tuple[tuple[str, str], ...]

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool: ...

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> Any: ...


# ── enabled switches (admin page / data_source_config) ─────────────────────
_ENABLED_CACHE: dict[str, Any] = {"value": None, "expires_at": 0.0}


def _db_disabled_keys() -> set[tuple[str, str]]:
    now = time.monotonic()
    if _ENABLED_CACHE["value"] is not None and _ENABLED_CACHE["expires_at"] > now:
        return _ENABLED_CACHE["value"]
    value: set[tuple[str, str]] = set()
    try:
        from sqlalchemy import select

        from app.db.session import SessionLocal
        from app.models.tables import DataSourceConfig

        db = SessionLocal()
        try:
            for r in db.scalars(select(DataSourceConfig)).all():
                if not bool(r.enabled):
                    value.add((str(r.source), str(r.source_type)))
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - DB down: keep previous view (fail-safe = legacy semantics)
        logger.debug("gateway enabled-switch query failed", exc_info=True)
        value = _ENABLED_CACHE["value"] or set()
    _ENABLED_CACHE.update(value=value, expires_at=now + 5.0)
    return value


def provider_enabled(provider: DataProvider, disabled: set[tuple[str, str]] | None = None) -> bool:
    disabled = _db_disabled_keys() if disabled is None else disabled
    return not any(key in disabled for key in getattr(provider, "config_keys", ()))


# ── deadlines / retry budget (audit item 2) ──────────────────────────────────
REALTIME_DATASETS: frozenset[str] = frozenset({"quote.realtime", "quote.realtime_frame"})
RETRYABLE_KINDS = frozenset({ErrorKind.NETWORK})
_FETCH_POOL: Any = None


def _pool():
    global _FETCH_POOL
    if _FETCH_POOL is None:
        from concurrent.futures import ThreadPoolExecutor

        _FETCH_POOL = ThreadPoolExecutor(max_workers=_POOL_WORKERS, thread_name_prefix="gw-fetch")
    return _FETCH_POOL


def _setting(attr: str, default: float) -> float:
    try:
        from app.core.config import get_settings

        return float(getattr(get_settings(), attr, default))
    except Exception:  # noqa: BLE001
        return default


def default_deadline(dataset: str) -> float:
    """Whole-request budget: realtime 3s, everything else (daily update etc.) 120s; configurable."""
    if dataset in REALTIME_DATASETS:
        return _setting("datasource_deadline_realtime_seconds", 3.0)
    return _setting("datasource_deadline_default_seconds", 120.0)


class _Budget:
    def __init__(self, dataset: str, deadline_s: float | None) -> None:
        self.until = time.monotonic() + (default_deadline(dataset) if deadline_s is None else float(deadline_s))
        self.retries_left = int(_setting("datasource_retry_budget", 2))

    def remaining(self) -> float:
        return self.until - time.monotonic()


_POOL_WORKERS = 32
ABANDONED_LIMIT = 24  # 留 8 个线程给正常请求
_ABANDONED: set = set()
_ABANDONED_LOCK = threading.Lock()


def _forget_abandoned(fut: Any) -> None:
    with _ABANDONED_LOCK:
        _ABANDONED.discard(fut)


def abandoned_call_count() -> int:
    with _ABANDONED_LOCK:
        return len(_ABANDONED)


def _call_with_timeout(fn: Callable[[], Any], timeout: float) -> Any:
    """Run ``fn`` in the shared pool, give up after ``timeout`` (the worker is abandoned)."""
    import contextvars
    from concurrent.futures import TimeoutError as FutTimeout

    # 超时的调用无法强杀，会一直占着线程直到上游返回；累计太多会把 32 线程池占满，
    # 让所有后续请求排队超时（雪崩）。放弃数达到上限时直接快速失败，让熔断/降级接管。
    abandoned = abandoned_call_count()
    if abandoned >= ABANDONED_LIMIT:
        raise DataSourceError(
            ErrorKind.TIMEOUT,
            f"gateway fetch pool saturated: {abandoned} timed-out calls still running (limit {ABANDONED_LIMIT})",
        )
    ctx = contextvars.copy_context()
    fut = _pool().submit(ctx.run, fn)
    try:
        return fut.result(timeout=max(0.001, timeout))
    except FutTimeout:
        if not fut.cancel():  # 已在运行：无法取消，记入"被放弃"集合，结束后自动移除
            with _ABANDONED_LOCK:
                _ABANDONED.add(fut)
            fut.add_done_callback(_forget_abandoned)
            logger.warning("gateway call abandoned after %.2fs; %d abandoned workers still running",
                           timeout, abandoned_call_count())
        raise DataSourceError(ErrorKind.TIMEOUT, f"deadline {timeout:.2f}s exceeded") from None


# ── gateway ────────────────────────────────────────────────────────────────
@dataclass
class GatewayResult:
    rows: Any
    provider: str | None
    attempts: list[dict[str, Any]]
    stale: bool = False  # True => (part of) rows are last-known data from the stale store
    data_time: str | None = None  # when stale: time the stale data was fetched (Asia/Shanghai)
    stale_keys: tuple[str, ...] = ()


def reset_all_breakers() -> None:
    """Drop every in-process breaker object — gateways and ``gw_call`` wrappers (tests / admin reset)."""
    for gw in list(_ALL_GATEWAYS):
        gw._breakers.clear()
    from app.services.datasource import wrap

    wrap._breakers.clear()


class DatasetGateway:
    def __init__(
        self,
        providers: Sequence[DataProvider],
        *,
        enabled: Callable[[DataProvider], bool] = provider_enabled,
        failure_threshold: int = 3,
        recovery_seconds: float = 300.0,
    ) -> None:
        from app.core.shared_breaker import SharedCircuitBreaker

        self._providers = list(providers)
        self._enabled = enabled
        _ALL_GATEWAYS.add(self)
        self._breakers: dict[tuple[str, str], Any] = {}
        # Shared (cross-process, per-namespace) breaker keyed like wrap.py: gw:<provider>:<dataset>.
        self._breaker_factory = lambda provider, dataset: SharedCircuitBreaker(
            f"gw:{provider}:{dataset}", failure_threshold, recovery_seconds
        )

    def _breaker(self, provider: str, dataset: str):
        key = (provider, dataset)
        if key not in self._breakers:
            self._breakers[key] = self._breaker_factory(provider, dataset)
        return self._breakers[key]

    @staticmethod
    def _upstream_open(p: DataProvider) -> bool:
        """Providers may declare ``upstream_breaker_keys`` (e.g. the transport's
        ``tushare:promax:rt_k``); any of them open in the shared store => skip without calling."""
        keys = getattr(p, "upstream_breaker_keys", ()) or ()
        if not keys:
            return False
        from app.core.shared_breaker import is_open

        try:
            return any(is_open(k) for k in keys)
        except Exception:  # noqa: BLE001
            return False

    def _candidates(self, dataset: str, query: Mapping[str, Any]) -> list[DataProvider]:
        out = []
        for p in self._providers:
            if not p.supports(dataset, query):
                continue
            if not self._enabled(p):
                _metrics.inc("dataset_requests", dataset=dataset, provider=p.name, outcome=ErrorKind.DISABLED.value)
                continue
            out.append(p)
        return out

    def _fetch_once(self, p: DataProvider, dataset: str, query: Mapping[str, Any], budget: _Budget) -> Any:
        remaining = budget.remaining()
        if remaining <= 0:
            raise DataSourceError(ErrorKind.TIMEOUT, "request deadline exhausted", provider=p.name, dataset=dataset)
        rows = _call_with_timeout(lambda: p.fetch(dataset, query), remaining)
        seq = rows.values() if isinstance(rows, Mapping) else (rows or [])
        validate_rows(dataset, seq, p.name)
        if not rows and dataset in EXPECT_NONEMPTY_IN_SESSION and in_trading_session():
            raise DataSourceError(ErrorKind.BAD_DATA, "empty during trading session", provider=p.name, dataset=dataset)
        return rows

    def _fetch_with_retry(self, p: DataProvider, dataset: str, query: Mapping[str, Any], budget: _Budget) -> Any:
        """tenacity retry for transient kinds, bounded by the shared per-request retry budget and deadline."""
        import tenacity

        def _retryable(exc: BaseException) -> bool:
            if classify_exception(exc) not in RETRYABLE_KINDS or budget.retries_left <= 0 or budget.remaining() < 0.3:
                return False
            budget.retries_left -= 1
            return True

        retrying = tenacity.Retrying(
            stop=tenacity.stop_after_attempt(3),
            retry=tenacity.retry_if_exception(_retryable),
            wait=tenacity.wait_exponential(multiplier=0.1, max=1.0),
            reraise=True,
        )
        return retrying(lambda: self._fetch_once(p, dataset, query, budget))

    def _attempt(
        self, p: DataProvider, dataset: str, query: Mapping[str, Any], attempts: list, budget: _Budget | None = None
    ) -> Any:
        budget = budget or _Budget(dataset, None)
        if budget.remaining() <= 0:
            attempts.append({"provider": p.name, "outcome": "deadline"})
            _metrics.inc("dataset_requests", dataset=dataset, provider=p.name, outcome="deadline")
            return None
        breaker = self._breaker(p.name, dataset)
        if self._upstream_open(p) or not breaker.allow_request():
            attempts.append({"provider": p.name, "outcome": ErrorKind.CIRCUIT_OPEN.value})
            _metrics.inc("dataset_requests", dataset=dataset, provider=p.name, outcome="circuit_open")
            return None
        t0 = time.perf_counter()
        try:
            rows = self._fetch_with_retry(p, dataset, query, budget)
        except Exception as exc:  # noqa: BLE001 - classified, recorded, next provider tried
            kind = classify_exception(exc)
            if kind not in NON_BREAKER_KINDS:
                breaker.record_failure(
                    kind=BREAKER_KIND.get(kind), recovery_seconds=getattr(exc, "retry_after_seconds", None)
                )
            attempts.append({"provider": p.name, "outcome": kind.value, "error": str(exc)[:160]})
            _metrics.inc("dataset_requests", dataset=dataset, provider=p.name, outcome=kind.value)
            return None
        if not rows:  # valid-empty: not a success (breaker untouched), not a failure
            attempts.append({"provider": p.name, "outcome": "empty", "rows": 0})
            _metrics.inc("dataset_requests", dataset=dataset, provider=p.name, outcome="empty")
            return rows
        breaker.record_success()
        outcome = "success"
        attempts.append({"provider": p.name, "outcome": outcome, "rows": len(rows or ())})
        _metrics.inc("dataset_requests", dataset=dataset, provider=p.name, outcome=outcome)
        _metrics.observe("dataset_latency", time.perf_counter() - t0, dataset=dataset, provider=p.name)
        return rows

    def fetch_first(
        self, dataset: str, query: Mapping[str, Any], *, deadline_s: float | None = None,
        allow_stale: bool | None = None,
    ) -> GatewayResult:
        """Cached / single-flight / stale-fallback wrapper over ``_fetch_first_live``."""
        from app.services.datasource import cache as _c

        ck = canonical(query)
        hit = _c.fresh_get(dataset, ck)
        if hit is not None:
            return GatewayResult(hit[0], hit[1], [{"provider": hit[1], "outcome": "cache_hit"}])
        res = _c.single_flight(
            f"first|{dataset}|{ck}", lambda: self._fetch_first_live(dataset, query, deadline_s=deadline_s)
        )
        if res.provider is not None:
            _c.fresh_put(dataset, ck, (res.rows, res.provider))
            _c.stale_put(dataset, ck, res.rows, res.provider)
            return res
        if _c.stale_allowed(dataset, allow_stale):
            st = _c.stale_get(dataset, ck)
            if st is not None:
                _metrics.inc("dataset_requests", dataset=dataset, provider=str(st.get("provider")), outcome="stale")
                rows = st["value"]
                if isinstance(rows, Mapping):
                    rows = {k: _c.mark_stale_row(v, st["data_time"]) for k, v in rows.items()}
                return GatewayResult(rows, st.get("provider"), res.attempts + [{"provider": st.get("provider"),
                                     "outcome": "stale"}], stale=True, data_time=st["data_time"])
        return res

    def _fetch_first_live(
        self, dataset: str, query: Mapping[str, Any], *, deadline_s: float | None = None
    ) -> GatewayResult:
        """First provider returning a non-empty answer wins (whole call bounded by deadline_s)."""
        attempts: list[dict[str, Any]] = []
        budget = _Budget(dataset, deadline_s)
        for p in self._candidates(dataset, query):
            rows = self._attempt(p, dataset, query, attempts, budget)
            if rows:
                return GatewayResult(rows, p.name, attempts)
        return GatewayResult([], None, attempts)

    def fetch_fill(
        self, dataset: str, query: Mapping[str, Any], keys: Sequence[str], *, deadline_s: float | None = None,
        allow_stale: bool | None = None,
    ) -> GatewayResult:
        """Per-key cache, single-flight on the missing set, per-key stale fallback."""
        from app.services.datasource import cache as _c

        qk = canonical(query)
        merged: dict[str, Any] = {}
        hit_provs: list[str] = []
        for k in keys:
            hit = _c.fresh_get(dataset, f"{qk}|{k}")
            if hit is not None:
                merged[k] = hit[0]
                hit_provs.append(hit[1])
        missing = [k for k in keys if k not in merged]
        attempts: list[dict[str, Any]] = [{"provider": "cache", "outcome": "cache_hit", "rows": len(merged)}] if merged else []
        providers = sorted(set(hit_provs))
        if missing:
            live = _c.single_flight(
                f"fill|{dataset}|{qk}|{','.join(sorted(missing))}",
                lambda: self._fetch_fill_live(dataset, query, missing, deadline_s=deadline_s),
            )
            attempts += live.attempts
            for k, row in live.rows.items():
                merged[k] = row
                prov = str((row or {}).get("source") or live.provider) if isinstance(row, Mapping) else live.provider
                _c.fresh_put(dataset, f"{qk}|{k}", (row, prov))
                _c.stale_put(dataset, f"{qk}|{k}", row, prov)
            if live.provider:
                providers += [x for x in live.provider.split(",") if x not in providers]
        still = [k for k in keys if k not in merged]
        stale_keys: list[str] = []
        data_times: list[str] = []
        if still and _c.stale_allowed(dataset, allow_stale):
            for k in still:
                st = _c.stale_get(dataset, f"{qk}|{k}")
                if st is not None:
                    merged[k] = _c.mark_stale_row(st["value"], st["data_time"])
                    stale_keys.append(k)
                    data_times.append(st["data_time"])
            if stale_keys:
                attempts.append({"provider": "stale_store", "outcome": "stale", "rows": len(stale_keys)})
                _metrics.inc("dataset_requests", dataset=dataset, provider="stale_store", outcome="stale")
        return GatewayResult(
            merged, ",".join(providers) or None, attempts, stale=bool(stale_keys),
            data_time=min(data_times) if data_times else None, stale_keys=tuple(stale_keys),
        )

    def _fetch_fill_live(
        self, dataset: str, query: Mapping[str, Any], keys: Sequence[str], *, deadline_s: float | None = None
    ) -> GatewayResult:
        """Keyed merge: each provider is asked only for keys still missing (bounded by deadline_s)."""
        attempts: list[dict[str, Any]] = []
        budget = _Budget(dataset, deadline_s)
        merged: dict[str, Any] = {}
        used: list[str] = []
        for p in self._candidates(dataset, query):
            missing = [k for k in keys if k not in merged]
            if not missing:
                break
            rows = self._attempt(p, dataset, {**query, "keys": missing}, attempts, budget) or {}
            added = False
            for k in missing:
                if k in rows:
                    merged[k] = rows[k]
                    added = True
            if added:
                used.append(p.name)
        return GatewayResult(merged, ",".join(used) or None, attempts)


import weakref  # noqa: E402

_ALL_GATEWAYS = weakref.WeakSet()


# ── cutover / shadow ───────────────────────────────────────────────────────
def _flag_set(attr: str) -> set[str]:
    from app.core.config import get_settings

    raw = str(getattr(get_settings(), attr, "") or "")
    return {x.strip() for x in raw.split(",") if x.strip()}


# Every routed dataset goes through the gateway (owner, 2026-10-21): switch + breaker + metrics
# apply everywhere.  ``ASHARE_DATASOURCE_CUTOVER`` now only carries the opt-in flags that ADD a
# backup provider to a chain (quote.realtime.tencent, bar.minute.sina/tencent, ann.list.xiaoshi,
# aux.eastmoney, eastmoney.alt); those change which upstreams get called, so they stay explicit.
ALWAYS_GATEWAY: frozenset[str] = frozenset({
    "ann.search.cninfo", "bar.minute", "quote.snapshot", "quote.daily", "bar.daily",
    "aux.limit_up", "aux.concept_sector", "aux.capital_flow", "aux.dragon_tiger", "aux.news",
    "ann.list", "xiaoshi.alt", "fina_indicator", "margin.detail", "float.unlock",
})


def is_cutover(dataset: str) -> bool:
    return dataset in ALWAYS_GATEWAY or dataset in _flag_set("datasource_cutover")


def is_shadow(dataset: str) -> bool:
    if dataset not in _flag_set("datasource_shadow"):
        return False
    from app.core.config import get_settings

    return random.random() < float(getattr(get_settings(), "datasource_shadow_sample_rate", 0.1))


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


SHADOW_MAX_BYTES = 2_000_000  # memory cap: larger payloads are compared by hash only


def shadow_compare(dataset: str, legacy: Any, gateway_fn: Callable[[], Any]) -> None:
    """Run the gateway path beside legacy; never raises, never changes the returned value."""
    try:
        new = gateway_fn()
        a, b = canonical(legacy), canonical(new)
        same = a == b
        _metrics.inc("dataset_shadow", dataset=dataset, outcome="match" if same else "mismatch")
        if not same:
            import hashlib

            logger.warning(
                "datasource shadow mismatch dataset=%s legacy_len=%d gateway_len=%d legacy_sha=%s gateway_sha=%s sample=%s",
                dataset, len(a), len(b), hashlib.sha256(a.encode()).hexdigest()[:12],
                hashlib.sha256(b.encode()).hexdigest()[:12],
                "" if max(len(a), len(b)) > SHADOW_MAX_BYTES else f"{a[:200]} || {b[:200]}",
            )
        del a, b, new
    except Exception:  # noqa: BLE001 - shadow must never affect production
        _metrics.inc("dataset_shadow", dataset=dataset, outcome="error")
        logger.debug("datasource shadow failed dataset=%s", dataset, exc_info=True)


# ── Tushare compatibility: dataset "tushare.<api>" → existing router ───────
class TushareApiProvider:
    """Expose any Tushare pro api as dataset ``tushare.<api>`` (routing/LB/limiter unchanged)."""

    name = "tushare_router"
    config_keys: tuple[tuple[str, str], ...] = ()

    def __init__(self, router_factory: Callable[[], Any] | None = None) -> None:
        self._router_factory = router_factory

    def _router(self):
        if self._router_factory is not None:
            return self._router_factory()
        from app.services.tushare_client import get_pro

        return get_pro()._router

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset.startswith("tushare.")

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> Mapping[str, Any]:
        params = dict(query.get("params") or {})
        return self._router().request(dataset.split(".", 1)[1], params=params, fields=str(query.get("fields") or ""))
