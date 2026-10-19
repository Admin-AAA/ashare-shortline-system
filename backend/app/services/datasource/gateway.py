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
    if "connect" in name or "network" in name:
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


# ── gateway ────────────────────────────────────────────────────────────────
@dataclass
class GatewayResult:
    rows: Any
    provider: str | None
    attempts: list[dict[str, Any]]


class DatasetGateway:
    def __init__(
        self,
        providers: Sequence[DataProvider],
        *,
        enabled: Callable[[DataProvider], bool] = provider_enabled,
        failure_threshold: int = 3,
        recovery_seconds: float = 60.0,
    ) -> None:
        from app.services.tushare_transport import CircuitBreaker

        self._providers = list(providers)
        self._enabled = enabled
        self._breakers: dict[tuple[str, str], Any] = {}
        self._breaker_factory = lambda: CircuitBreaker(failure_threshold, recovery_seconds)

    def _breaker(self, provider: str, dataset: str):
        key = (provider, dataset)
        if key not in self._breakers:
            self._breakers[key] = self._breaker_factory()
        return self._breakers[key]

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

    def _attempt(self, p: DataProvider, dataset: str, query: Mapping[str, Any], attempts: list) -> Any:
        breaker = self._breaker(p.name, dataset)
        if not breaker.allow_request():
            attempts.append({"provider": p.name, "outcome": ErrorKind.CIRCUIT_OPEN.value})
            _metrics.inc("dataset_requests", dataset=dataset, provider=p.name, outcome="circuit_open")
            return None
        t0 = time.perf_counter()
        try:
            rows = p.fetch(dataset, query)
            seq = rows.values() if isinstance(rows, Mapping) else (rows or [])
            validate_rows(dataset, seq, p.name)
        except Exception as exc:  # noqa: BLE001 - classified, recorded, next provider tried
            kind = classify_exception(exc)
            breaker.record_failure()
            attempts.append({"provider": p.name, "outcome": kind.value, "error": str(exc)[:160]})
            _metrics.inc("dataset_requests", dataset=dataset, provider=p.name, outcome=kind.value)
            return None
        breaker.record_success()
        outcome = "success" if rows else "empty"
        attempts.append({"provider": p.name, "outcome": outcome, "rows": len(rows or ())})
        _metrics.inc("dataset_requests", dataset=dataset, provider=p.name, outcome=outcome)
        _metrics.observe("dataset_latency", time.perf_counter() - t0, dataset=dataset, provider=p.name)
        return rows

    def fetch_first(self, dataset: str, query: Mapping[str, Any]) -> GatewayResult:
        """First provider returning a non-empty answer wins."""
        attempts: list[dict[str, Any]] = []
        for p in self._candidates(dataset, query):
            rows = self._attempt(p, dataset, query, attempts)
            if rows:
                return GatewayResult(rows, p.name, attempts)
        return GatewayResult([], None, attempts)

    def fetch_fill(self, dataset: str, query: Mapping[str, Any], keys: Sequence[str]) -> GatewayResult:
        """Keyed merge: each provider is asked only for keys still missing."""
        attempts: list[dict[str, Any]] = []
        merged: dict[str, Any] = {}
        used: list[str] = []
        for p in self._candidates(dataset, query):
            missing = [k for k in keys if k not in merged]
            if not missing:
                break
            rows = self._attempt(p, dataset, {**query, "keys": missing}, attempts) or {}
            added = False
            for k in missing:
                if k in rows:
                    merged[k] = rows[k]
                    added = True
            if added:
                used.append(p.name)
        return GatewayResult(merged, ",".join(used) or None, attempts)


# ── cutover / shadow ───────────────────────────────────────────────────────
def _flag_set(attr: str) -> set[str]:
    from app.core.config import get_settings

    raw = str(getattr(get_settings(), attr, "") or "")
    return {x.strip() for x in raw.split(",") if x.strip()}


# New datasets with no legacy path: always routed through the gateway (no cutover flag needed).
ALWAYS_GATEWAY: frozenset[str] = frozenset({"ann.search.cninfo"})


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
