"""Route an existing source-specific fetch function through the gateway, unchanged.

``gw_call(dataset, provider, fn, *args, **kw)``:
* cutover off (default) -> ``fn(*args, **kw)`` exactly as before;
* cutover on -> same call, plus DB enabled switch (raises ``DataSourceError(DISABLED)``,
  which callers' existing ``except Exception`` fallbacks already handle), the
  provider's cross-process shared limiter, per-(provider,dataset) circuit breaker and
  ``dataset_requests``/``dataset_latency`` metrics.  The function's own return value
  and exceptions pass through untouched, so outputs are byte-identical and existing
  orchestration (fallback order, abort-on-block, counters) is preserved.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core import metrics as _metrics
from app.services.datasource import gateway as _gw


@dataclass(frozen=True)
class ProviderSpec:
    config_keys: tuple[tuple[str, str], ...]
    rpm: int  # shared cross-process limit; 0 = none (fetcher keeps its own pacing)
    force_open_on: tuple[str, ...] = ()  # exception class names that open the breaker immediately


PROVIDERS: dict[str, ProviderSpec] = {
    "eastmoney": ProviderSpec((("eastmoney", "news"),), 120, ("EastmoneyBlockedError",)),
    "cninfo": ProviderSpec((("cninfo", "news"),), 60),
    "xiaoshi_quote": ProviderSpec((("xiaoshi", "quote"),), 0),
    "xiaoshi_kline": ProviderSpec((("xiaoshi", "kline"),), 0),
    "xiaoshi_alt": ProviderSpec((("xiaoshi", "quote"),), 0),
    "tushare_quote": ProviderSpec((("tushare", "quote"),), 0),
    "tushare_kline": ProviderSpec((("tushare", "kline"),), 0),
    "tushare_limit_up": ProviderSpec((("tushare", "limit_up"),), 0),
    "tushare_concept_sector": ProviderSpec((("tushare", "concept_sector"),), 0),
    "tushare_capital_flow": ProviderSpec((("tushare", "capital_flow"),), 0),
    "tushare_dragon_tiger": ProviderSpec((("tushare", "dragon_tiger"),), 0),
    "tushare": ProviderSpec((), 0),  # Tushare calls already go through the router limiter/LB
    "local": ProviderSpec((), 0),
}

_limiters: dict[str, Any] = {}
_breakers: dict[tuple[str, str], Any] = {}


class _P:
    def __init__(self, name: str, spec: ProviderSpec) -> None:
        self.name, self.config_keys = name, spec.config_keys


def _limiter(name: str, rpm: int):
    if name not in _limiters:
        from app.services.tushare_transport import _make_limiter

        _limiters[name] = _make_limiter(f"gateway:{name}", rpm)
    return _limiters[name]


def _breaker(name: str, dataset: str):
    key = (name, dataset)
    if key not in _breakers:
        from app.services.tushare_transport import CircuitBreaker

        _breakers[key] = CircuitBreaker(5, 300.0)
    return _breakers[key]


def gw_call(dataset: str, provider: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    if not _gw.is_cutover(dataset):
        return fn(*args, **kwargs)
    spec = PROVIDERS.get(provider, ProviderSpec((), 0))
    if not _gw.provider_enabled(_P(provider, spec)):
        _metrics.inc("dataset_requests", dataset=dataset, provider=provider, outcome="disabled")
        raise _gw.DataSourceError(_gw.ErrorKind.DISABLED, "disabled in data_source_config", provider=provider, dataset=dataset)
    breaker = _breaker(provider, dataset)
    if not breaker.allow_request():
        _metrics.inc("dataset_requests", dataset=dataset, provider=provider, outcome="circuit_open")
        raise _gw.DataSourceError(_gw.ErrorKind.CIRCUIT_OPEN, provider=provider, dataset=dataset)
    if spec.rpm > 0:
        _limiter(provider, spec.rpm).acquire()
    t0 = time.perf_counter()
    try:
        out = fn(*args, **kwargs)
    except BaseException as exc:
        breaker.record_failure(force_open=type(exc).__name__ in spec.force_open_on)
        _metrics.inc("dataset_requests", dataset=dataset, provider=provider, outcome=_gw.classify_exception(exc).value)
        raise
    breaker.record_success()
    _metrics.inc("dataset_requests", dataset=dataset, provider=provider, outcome="success" if out else "empty")
    _metrics.observe("dataset_latency", time.perf_counter() - t0, dataset=dataset, provider=provider)
    return out
