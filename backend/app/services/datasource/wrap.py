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
    # 巨潮全文检索（ann.search.cninfo）：独立低速桶，礼貌访问；HTTP 403/429/5xx 触发熔断计数
    "cninfo_search": ProviderSpec((("cninfo", "news"),), 20, ("CninfoBlockedError",)),
    "xiaoshi_quote": ProviderSpec((("xiaoshi", "quote"),), 0),
    "xiaoshi_kline": ProviderSpec((("xiaoshi", "kline"),), 0),
    "xiaoshi_alt": ProviderSpec((("xiaoshi", "quote"),), 0),
    "tushare_quote": ProviderSpec((("tushare", "quote"),), 0),
    "tushare_kline": ProviderSpec((("tushare", "kline"),), 0),
    "tushare_limit_up": ProviderSpec((("tushare", "limit_up"),), 0),
    "tushare_concept_sector": ProviderSpec((("tushare", "concept_sector"),), 0),
    "tushare_capital_flow": ProviderSpec((("tushare", "capital_flow"),), 0),
    "tushare_dragon_tiger": ProviderSpec((("tushare", "dragon_tiger"),), 0),
    "tushare_news": ProviderSpec((("tushare", "news"),), 0),
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
        from app.core.shared_breaker import SharedCircuitBreaker

        # shared across backend / Prefect worker / scripts (runtime SQLite store)
        _breakers[key] = SharedCircuitBreaker(f"gw:{name}:{dataset}", 5, 300.0)
    return _breakers[key]


def gw_call(dataset: str, provider: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    if not _gw.is_cutover(dataset):
        return fn(*args, **kwargs)
    spec = PROVIDERS.get(provider, ProviderSpec((), 0))
    if not _gw.provider_enabled(_P(provider, spec)):
        _metrics.inc("dataset_requests", dataset=dataset, provider=provider, outcome="disabled")
        raise _gw.DataSourceError(_gw.ErrorKind.DISABLED, "disabled in data_source_config", provider=provider, dataset=dataset)
    if not _gw.item_enabled(dataset, provider):
        # 数据项级开关：只在这一数据类型里跳过该源；调用方现有的 except 回退会接着试下一个源。
        _metrics.inc("dataset_requests", dataset=dataset, provider=provider, outcome="disabled")
        raise _gw.DataSourceError(_gw.ErrorKind.DISABLED, "disabled for this dataset", provider=provider, dataset=dataset)
    breaker = _breaker(provider, dataset)
    if not breaker.allow_request():
        _metrics.inc("dataset_requests", dataset=dataset, provider=provider, outcome="circuit_open")
        raise _gw.DataSourceError(_gw.ErrorKind.CIRCUIT_OPEN, provider=provider, dataset=dataset)
    if spec.rpm > 0:
        _limiter(provider, spec.rpm).acquire()
    t0 = time.perf_counter()
    try:
        out = fn(*args, **kwargs)
    except Exception as exc:
        kind = _gw.classify_exception(exc)
        # 与 DatasetGateway 同一套口径：请求本身无效 / 数据未发布不是源故障，不计熔断；
        # 限流按上游 retry-after 立即熔断，超时 / 坏数据走各自阈值。
        if type(exc).__name__ in spec.force_open_on:
            breaker.record_failure(force_open=True)
        elif kind not in _gw.NON_BREAKER_KINDS:
            breaker.record_failure(kind=_gw.BREAKER_KIND.get(kind), recovery_seconds=getattr(exc, "retry_after_seconds", None))
        _metrics.inc("dataset_requests", dataset=dataset, provider=provider, outcome=kind.value)
        raise
    breaker.record_success()
    _metrics.inc("dataset_requests", dataset=dataset, provider=provider, outcome="empty" if _is_empty(out) else "success")
    _metrics.observe("dataset_latency", time.perf_counter() - t0, dataset=dataset, provider=provider)
    return out


def _is_empty(out: Any) -> bool:
    """DataFrame 的真值判断会抛 ValueError，按 .empty 判空；其余按真值。"""
    empty = getattr(out, "empty", None)
    return bool(empty) if isinstance(empty, bool) else not out
