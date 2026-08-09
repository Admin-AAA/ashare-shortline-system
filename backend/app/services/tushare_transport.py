"""Resilient upstream transports for Tushare-compatible providers.

This module belongs to the ingestion/realtime edge.  It does not replace the
local MySQL market pool used by recommendations and backtests.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

import requests

from app.services.observability import log_observation

logger = logging.getLogger(__name__)

_RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_EXCEPTIONS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ProxyError,
    requests.exceptions.ChunkedEncodingError,
)


class AuthMode(StrEnum):
    JSON_TOKEN = "json_token"
    API_KEY_HEADER = "api_key_header"


class FailureCategory(StrEnum):
    CONFIGURATION = "configuration"
    CIRCUIT_OPEN = "circuit_open"
    NETWORK = "network"
    TIMEOUT = "timeout"
    HTTP_RATE_LIMIT = "http_rate_limit"
    HTTP_SERVER = "http_server"
    HTTP_CLIENT = "http_client"
    NON_JSON = "non_json"
    BUSINESS_RATE_LIMIT = "business_rate_limit"
    PERMISSION = "permission"
    INVALID_REQUEST = "invalid_request"
    PROVIDER_ERROR = "provider_error"
    MALFORMED_RESPONSE = "malformed_response"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    base_url: str
    credential: str = field(repr=False)
    auth_mode: AuthMode
    rate_limit_per_minute: int
    max_concurrency: int = 0
    timeout: tuple[float, float] = (5.0, 120.0)
    max_attempts: int = 2
    append_api_path: bool = False
    failure_threshold: int = 3
    recovery_seconds: float = 60.0
    api_rate_limits: Mapping[str, int] = field(default_factory=dict)
    proxy: str = field(default="", repr=False)
    # 路由优先级：数值越小越优先。ai_tool_primary_apis 命中的 API 会整体偏向 ai_tool，
    # 其余按 priority 升序尝试；同优先级按注册顺序。enabled 覆盖见 TushareProviderRouter。
    priority: int = 100

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("provider name must not be blank")
        if not self.base_url.strip():
            raise ValueError(f"{self.name} base URL must not be blank")
        if not self.credential.strip():
            raise ValueError(f"{self.name} credential must not be blank")
        if self.rate_limit_per_minute < 0:
            raise ValueError("rate limit must not be negative")
        if self.max_attempts < 1:
            raise ValueError("max attempts must be positive")


class ProviderFailure(RuntimeError):
    """A sanitized provider failure safe to propagate across the router."""

    def __init__(
        self,
        *,
        provider: str,
        api_name: str,
        category: FailureCategory,
        retryable: bool,
        fallback_allowed: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.provider = provider
        self.api_name = api_name
        self.category = category
        self.retryable = retryable
        self.fallback_allowed = fallback_allowed
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"{provider} {api_name} failed ({category.value})")


class TushareProviderError(RuntimeError):
    """All eligible providers failed, containing only sanitized evidence."""

    def __init__(self, api_name: str, failures: list[ProviderFailure]) -> None:
        self.api_name = api_name
        self.failures = tuple(failures)
        summary = ", ".join(f"{item.provider}:{item.category.value}" for item in failures)
        super().__init__(f"Tushare {api_name} unavailable; providers={summary or 'none'}")


class ProviderRuntimeHealth:
    """Thread-safe, process-local Provider telemetry with sanitized errors only."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._success_count = 0
        self._failure_count = 0
        self._empty_count = 0
        self._latency_ms: float | None = None
        self._last_success_at: str | None = None
        self._last_failure_at: str | None = None
        self._last_error: str | None = None
        self._last_api: str | None = None
        self._updated_at: str | None = None

    def record_success(self, api_name: str, *, latency_ms: float, empty: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if empty:
                self._empty_count += 1
            else:
                self._success_count += 1
            self._latency_ms = round(float(latency_ms), 2)
            self._last_success_at = now
            self._last_error = None
            self._last_api = api_name
            self._updated_at = now

    def record_failure(
        self,
        api_name: str,
        *,
        category: FailureCategory,
        latency_ms: float,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._failure_count += 1
            self._latency_ms = round(float(latency_ms), 2)
            self._last_failure_at = now
            self._last_error = f"{api_name}: {category.value}"
            self._last_api = api_name
            self._updated_at = now

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            effective_total = self._success_count + self._failure_count
            if self._last_success_at and (
                not self._last_failure_at or self._last_success_at >= self._last_failure_at
            ):
                status = "healthy"
            elif self._failure_count:
                status = "degraded"
            else:
                status = "untested"
            return {
                "success_count": self._success_count,
                "failure_count": self._failure_count,
                "empty_count": self._empty_count,
                "success_rate": (
                    round(self._success_count / effective_total, 3)
                    if effective_total
                    else None
                ),
                "latency_ms": self._latency_ms,
                "last_success_at": self._last_success_at,
                "last_failure_at": self._last_failure_at,
                "last_error": self._last_error,
                "last_api": self._last_api,
                "status": status,
                "updated_at": self._updated_at,
            }


class SlidingWindowLimiter:
    """Thread-safe fixed-window limiter with no lock held while sleeping."""

    def __init__(self, requests_per_minute: int) -> None:
        self._limit = max(0, int(requests_per_minute))
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self._limit <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                while self._timestamps and self._timestamps[0] <= now - 60.0:
                    self._timestamps.popleft()
                if len(self._timestamps) < self._limit:
                    self._timestamps.append(now)
                    return
                wait_seconds = max(0.01, self._timestamps[0] + 60.0 - now + 0.01)
            time.sleep(wait_seconds)


class CircuitBreaker:
    """Small per-API circuit breaker with a single half-open probe."""

    def __init__(self, failure_threshold: int, recovery_seconds: float) -> None:
        self._threshold = max(1, int(failure_threshold))
        self._recovery_seconds = max(0.1, float(recovery_seconds))
        self._failure_count = 0
        self._open_until = 0.0
        self._half_open_probe = False
        self._lock = threading.Lock()

    def allow_request(self) -> bool:
        with self._lock:
            now = time.monotonic()
            if self._open_until <= 0:
                return True
            if now < self._open_until:
                return False
            if self._half_open_probe:
                return False
            self._half_open_probe = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._open_until = 0.0
            self._half_open_probe = False

    def record_failure(self, *, force_open: bool = False, recovery_seconds: float | None = None) -> None:
        with self._lock:
            self._failure_count += 1
            if force_open or self._half_open_probe or self._failure_count >= self._threshold:
                delay = max(self._recovery_seconds, float(recovery_seconds or 0.0))
                self._open_until = time.monotonic() + delay
                self._half_open_probe = False

    @property
    def state(self) -> str:
        with self._lock:
            if self._open_until <= 0:
                return "closed"
            if time.monotonic() < self._open_until:
                return "open"
            return "half_open"


class TushareHttpProvider:
    """One authenticated Tushare-compatible HTTP provider."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._session = requests.Session()
        self._session.trust_env = False
        proxy = config.proxy.strip()
        self._session.proxies = {"http": proxy, "https": proxy} if proxy else {"http": "", "https": ""}
        self._session.headers.update({"Accept-Encoding": "gzip"})
        if config.auth_mode is AuthMode.API_KEY_HEADER:
            self._session.headers.update({"X-API-Key": config.credential})
        self._provider_limiter = SlidingWindowLimiter(config.rate_limit_per_minute)
        self._api_limiters = {
            api_name: SlidingWindowLimiter(limit)
            for api_name, limit in config.api_rate_limits.items()
            if int(limit) > 0
        }
        self._concurrency = (
            threading.BoundedSemaphore(config.max_concurrency)
            if config.max_concurrency > 0
            else None
        )
        self._breakers: dict[str, CircuitBreaker] = {}
        self._breaker_lock = threading.Lock()
        self._health = ProviderRuntimeHealth()

    @property
    def name(self) -> str:
        return self.config.name

    def close(self) -> None:
        self._session.close()

    def health_snapshot(self) -> dict[str, Any]:
        with self._breaker_lock:
            circuit_states = {
                api_name: breaker.state
                for api_name, breaker in self._breakers.items()
            }
        open_circuits = sorted(
            api_name for api_name, state in circuit_states.items() if state != "closed"
        )
        breaker_state = (
            "open"
            if any(state == "open" for state in circuit_states.values())
            else "half_open"
            if any(state == "half_open" for state in circuit_states.values())
            else "closed"
        )
        return {
            "name": self.name,
            **self._health.snapshot(),
            "auth_mode": self.config.auth_mode.value,
            "rate_limit_per_minute": self.config.rate_limit_per_minute,
            "api_rate_limits": dict(self.config.api_rate_limits),
            "max_concurrency": self.config.max_concurrency,
            "proxy_configured": bool(self.config.proxy.strip()),
            "breaker_state": breaker_state,
            "open_circuits": open_circuits,
        }

    def _breaker(self, api_name: str) -> CircuitBreaker:
        with self._breaker_lock:
            breaker = self._breakers.get(api_name)
            if breaker is None:
                breaker = CircuitBreaker(
                    self.config.failure_threshold,
                    self.config.recovery_seconds,
                )
                self._breakers[api_name] = breaker
            return breaker

    def _endpoint(self, api_name: str) -> str:
        base_url = self.config.base_url.rstrip("/")
        return f"{base_url}/{api_name}" if self.config.append_api_path else base_url

    def _payload(self, api_name: str, params: Mapping[str, Any], fields: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "api_name": api_name,
            "params": dict(params),
            "fields": fields,
        }
        if self.config.auth_mode is AuthMode.JSON_TOKEN:
            payload["token"] = self.config.credential
        return payload

    def _wait_for_capacity(self, api_name: str) -> None:
        self._provider_limiter.acquire()
        api_limiter = self._api_limiters.get(api_name)
        if api_limiter is not None:
            api_limiter.acquire()

    def request(
        self,
        api_name: str,
        *,
        params: Mapping[str, Any] | None = None,
        fields: str = "",
    ) -> Mapping[str, Any]:
        started = time.perf_counter()
        breaker = self._breaker(api_name)
        if not breaker.allow_request():
            failure = ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.CIRCUIT_OPEN,
                retryable=False,
                fallback_allowed=True,
            )
            self._health.record_failure(
                api_name,
                category=failure.category,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            raise failure

        semaphore = self._concurrency
        if semaphore is not None:
            semaphore.acquire()
        try:
            try:
                envelope = self._request_with_retries(
                    api_name,
                    params=dict(params or {}),
                    fields=fields,
                    breaker=breaker,
                )
            except ProviderFailure as failure:
                self._health.record_failure(
                    api_name,
                    category=failure.category,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
                raise
            data = envelope.get("data")
            items = data.get("items") if isinstance(data, Mapping) else None
            self._health.record_success(
                api_name,
                latency_ms=(time.perf_counter() - started) * 1000,
                empty=not bool(items),
            )
            return envelope
        finally:
            if semaphore is not None:
                semaphore.release()

    def _request_with_retries(
        self,
        api_name: str,
        *,
        params: Mapping[str, Any],
        fields: str,
        breaker: CircuitBreaker,
    ) -> Mapping[str, Any]:
        last_failure: ProviderFailure | None = None
        for attempt in range(self.config.max_attempts):
            started = time.perf_counter()
            try:
                self._wait_for_capacity(api_name)
                envelope = self._request_once(api_name, params=params, fields=fields)
                breaker.record_success()
                logger.info(
                    "tushare provider success provider=%s api=%s attempt=%d duration_ms=%.1f breaker=%s",
                    self.name,
                    api_name,
                    attempt + 1,
                    (time.perf_counter() - started) * 1000,
                    breaker.state,
                )
                return envelope
            except ProviderFailure as failure:
                last_failure = failure
                duration_ms = (time.perf_counter() - started) * 1000
                logger.warning(
                    "tushare provider failure provider=%s api=%s attempt=%d category=%s duration_ms=%.1f",
                    self.name,
                    api_name,
                    attempt + 1,
                    failure.category.value,
                    duration_ms,
                )
                if failure.retryable and attempt < self.config.max_attempts - 1:
                    delay = min(5.0, 0.5 * (2**attempt)) + random.uniform(0.0, 0.2)
                    time.sleep(delay)
                    continue
                force_open = failure.category in {
                    FailureCategory.BUSINESS_RATE_LIMIT,
                    FailureCategory.HTTP_RATE_LIMIT,
                    FailureCategory.PERMISSION,
                }
                if failure.fallback_allowed and failure.category not in {
                    FailureCategory.INVALID_REQUEST,
                    FailureCategory.HTTP_CLIENT,
                }:
                    breaker.record_failure(
                        force_open=force_open,
                        recovery_seconds=failure.retry_after_seconds,
                    )
                raise

        assert last_failure is not None
        raise last_failure

    def _request_once(
        self,
        api_name: str,
        *,
        params: Mapping[str, Any],
        fields: str,
    ) -> Mapping[str, Any]:
        response: requests.Response | None = None
        try:
            response = self._session.post(
                self._endpoint(api_name),
                json=self._payload(api_name, params, fields),
                timeout=self.config.timeout,
            )
            status = int(response.status_code)
            if status != 200:
                if status == 429:
                    retry_after = 60.0
                    try:
                        retry_after = max(
                            1.0,
                            float((getattr(response, "headers", {}) or {}).get("Retry-After", 60)),
                        )
                    except (TypeError, ValueError):
                        pass
                    raise ProviderFailure(
                        provider=self.name,
                        api_name=api_name,
                        category=FailureCategory.HTTP_RATE_LIMIT,
                        retryable=False,
                        fallback_allowed=True,
                        retry_after_seconds=retry_after,
                    )
                if status >= 500:
                    raise ProviderFailure(
                        provider=self.name,
                        api_name=api_name,
                        category=FailureCategory.HTTP_SERVER,
                        retryable=status in _RETRYABLE_HTTP_STATUS,
                        fallback_allowed=True,
                    )
                raise ProviderFailure(
                    provider=self.name,
                    api_name=api_name,
                    category=FailureCategory.HTTP_CLIENT,
                    retryable=False,
                    fallback_allowed=status in {401, 403},
                )
            try:
                envelope = response.json()
            except ValueError as exc:
                raise ProviderFailure(
                    provider=self.name,
                    api_name=api_name,
                    category=FailureCategory.NON_JSON,
                    retryable=True,
                    fallback_allowed=True,
                ) from exc
        except ProviderFailure:
            raise
        except requests.exceptions.Timeout as exc:
            raise ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.TIMEOUT,
                retryable=True,
                fallback_allowed=True,
            ) from exc
        except _RETRYABLE_EXCEPTIONS as exc:
            raise ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.NETWORK,
                retryable=True,
                fallback_allowed=True,
            ) from exc
        finally:
            if response is not None:
                response.close()

        if not isinstance(envelope, Mapping):
            raise ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.MALFORMED_RESPONSE,
                retryable=True,
                fallback_allowed=True,
            )
        if envelope.get("code") != 0:
            raise self._business_failure(api_name, envelope)
        self._validate_success_envelope(api_name, envelope)
        return envelope

    def _malformed_failure(self, api_name: str) -> ProviderFailure:
        return ProviderFailure(
            provider=self.name,
            api_name=api_name,
            category=FailureCategory.MALFORMED_RESPONSE,
            retryable=True,
            fallback_allowed=True,
        )

    def _validate_success_envelope(
        self,
        api_name: str,
        envelope: Mapping[str, Any],
    ) -> None:
        """Reject malformed successes before breaker success/fallback decisions."""
        data = envelope.get("data")
        if not isinstance(data, Mapping):
            raise self._malformed_failure(api_name)
        raw_items = data.get("items")
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes, bytearray)):
            raise self._malformed_failure(api_name)
        raw_fields = data.get("fields")
        if raw_fields is None:
            if raw_items:
                raise self._malformed_failure(api_name)
            return
        if (
            not isinstance(raw_fields, Sequence)
            or isinstance(raw_fields, (str, bytes, bytearray))
            or not all(isinstance(field, str) and field.strip() for field in raw_fields)
            or len(raw_fields) != len(set(raw_fields))
        ):
            raise self._malformed_failure(api_name)
        expected_size = len(raw_fields)
        for raw_item in raw_items:
            if (
                not isinstance(raw_item, Sequence)
                or isinstance(raw_item, (str, bytes, bytearray))
                or len(raw_item) != expected_size
            ):
                raise self._malformed_failure(api_name)

    def _business_failure(self, api_name: str, envelope: Mapping[str, Any]) -> ProviderFailure:
        code = str(envelope.get("code") or "")
        message = str(envelope.get("msg") or "").lower()
        if code == "40203" or any(term in message for term in ("频率", "频次", "rate limit", "请求速度")):
            return ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.BUSINESS_RATE_LIMIT,
                retryable=False,
                fallback_allowed=True,
                retry_after_seconds=60.0,
            )
        if any(term in message for term in ("权限", "未开通", "积分", "permission", "unauthorized")):
            return ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.PERMISSION,
                retryable=False,
                fallback_allowed=True,
            )
        if any(term in message for term in ("参数", "parameter", "invalid argument")):
            return ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.INVALID_REQUEST,
                retryable=False,
                fallback_allowed=False,
            )
        return ProviderFailure(
            provider=self.name,
            api_name=api_name,
            category=FailureCategory.PROVIDER_ERROR,
            retryable=False,
            fallback_allowed=True,
        )


class TushareProviderRouter:
    """API-aware routing over independently protected providers."""

    # 默认 per-API 选路链：{api_name: [provider 名按尝试顺序]}。
    # 未列出的 API 回落到按 provider priority 升序；分钟/公告相关 API 优先走双 Key。
    DEFAULT_API_ROUTES: Mapping[str, tuple[str, ...]] = {
        "rt_k": ("ai_tool_key1", "ai_tool_key2", "ai_tool", "xiaodefa", "fastapic", "quantdata"),
        "stk_mins": ("ai_tool_key1", "ai_tool_key2", "ai_tool", "fastapic", "quantdata", "xiaodefa"),
        "rt_min": ("ai_tool_key1", "ai_tool_key2", "ai_tool", "fastapic", "quantdata", "xiaodefa"),
        "anns_d": ("ai_tool_key1", "ai_tool_key2", "ai_tool", "fastapic", "quantdata", "xiaodefa"),
    }

    def __init__(
        self,
        providers: list[TushareHttpProvider],
        *,
        api_routes: Mapping[str, tuple[str, ...]] | None = None,
        enabled_override: Any = None,
        priority_override: Any = None,
        trace_sink: Any = None,
        correlation_id_factory: Any = None,
    ) -> None:
        self._providers = {provider.name: provider for provider in providers}
        # per-API 选路链：api_name -> [provider 名按尝试顺序]。默认 DEFAULT_API_ROUTES，
        # 可由 api_routes 覆盖（数据源管理/未来新源在此调链序，与业务解耦）。
        self._api_routes = dict(api_routes) if api_routes is not None else dict(self.DEFAULT_API_ROUTES)
        # enabled_override: 可选可调用 () -> Mapping[str, bool]，提供 provider 运行时启停
        # （DB 覆盖 config）。为 None 时全部启用。每次选路时读取，使启停即时生效。
        self._enabled_override = enabled_override
        # priority_override: 可选可调用 () -> Mapping[str, int]，提供 provider 运行时优先级
        # 覆盖（管理面拖拽调序写 DB）。为 None 时用代码默认 priority。每次选路时读取。
        self._priority_override = priority_override
        # trace_sink: 可选 DataSourceTraceSink；为 None 时不持久化 trace（默认行为不变）。
        # correlation_id_factory: 可选 () -> str，用于关联一次逻辑请求的多次 attempt。
        self._trace_sink = trace_sink
        self._correlation_id_factory = correlation_id_factory

    def close(self) -> None:
        for provider in self._providers.values():
            provider.close()

    def health_snapshot(self) -> list[dict[str, Any]]:
        """Return non-sensitive runtime telemetry for every configured Provider."""
        return [provider.health_snapshot() for provider in self._providers.values()]

    def _is_enabled(self, name: str) -> bool:
        if self._enabled_override is None:
            return True
        try:
            overrides = self._enabled_override() or {}
        except Exception:
            # 覆盖读取失败（如 DB 暂不可用）→ 保守视为全部启用，不阻断同步链路
            logger.debug("provider enabled_override read failed", exc_info=True)
            return True
        return bool(overrides.get(name, True))

    def _effective_priority(self, provider: TushareHttpProvider) -> int:
        if self._priority_override is not None:
            try:
                overrides = self._priority_override() or {}
            except Exception:
                # 覆盖读取失败 → 回退代码默认 priority，不阻断同步链路
                logger.debug("provider priority_override read failed", exc_info=True)
                overrides = {}
            if provider.name in overrides:
                return int(overrides[provider.name])
        return provider.config.priority

    def _ordered_providers(self, api_name: str) -> list[TushareHttpProvider]:
        enabled = [p for p in self._providers.values() if self._is_enabled(p.name)]
        route = self._api_routes.get(api_name)
        if route:
            # 显式链序：按 route 顺序挑选已启用且已注册的 provider
            by_name = {p.name: p for p in enabled}
            ordered = [by_name[name] for name in route if name in by_name]
            # 链里没列出但已启用的 provider 追加在末尾（按 priority），避免漏掉 fallback
            rest = sorted((p for p in enabled if p.name not in route), key=self._effective_priority)
            return ordered + rest
        return sorted(enabled, key=self._effective_priority)

    def request(
        self,
        api_name: str,
        *,
        params: Mapping[str, Any] | None = None,
        fields: str = "",
    ) -> Mapping[str, Any]:
        log_observation(
            logger,
            stage="provider",
            reason_code="PROVIDER_REQUEST",
            identity=api_name,
            level=logging.DEBUG,
        )
        providers = self._ordered_providers(api_name)
        if not providers:
            raise TushareProviderError(api_name, [])

        failures: list[ProviderFailure] = []
        attempts: list[dict[str, Any]] = []
        selected_provider: str | None = None
        final_outcome = "failure"
        for index, provider in enumerate(providers):
            ordinal = index + 1
            fallback_from = providers[index - 1].name if index else None
            started = time.perf_counter()
            try:
                envelope = provider.request(api_name, params=params, fields=fields)
                duration_ms = int((time.perf_counter() - started) * 1000)
                data = envelope.get("data")
                items = data.get("items") if isinstance(data, Mapping) else None
                empty = not bool(items)
                attempts.append({
                    "ordinal": ordinal,
                    "provider": provider.name,
                    "outcome": "valid_empty" if empty else "success",
                    "duration_ms": duration_ms,
                    "fallback_from": fallback_from,
                })
                selected_provider = provider.name
                final_outcome = "valid_empty" if empty else "success"
                if index:
                    logger.warning(
                        "tushare provider fallback success api=%s provider=%s",
                        api_name,
                        provider.name,
                    )
                self._persist_trace(api_name, params, attempts, selected_provider, final_outcome)
                return envelope
            except ProviderFailure as failure:
                duration_ms = int((time.perf_counter() - started) * 1000)
                failures.append(failure)
                is_breaker_skip = failure.category is FailureCategory.CIRCUIT_OPEN
                attempts.append({
                    "ordinal": ordinal,
                    "provider": provider.name,
                    "outcome": "skipped_breaker" if is_breaker_skip else "failure",
                    "duration_ms": duration_ms,
                    "failure_category": failure.category.value,
                    "retryable": failure.retryable,
                    "fallback_allowed": failure.fallback_allowed,
                    "fallback_from": fallback_from,
                    "fallback_to": (
                        providers[index + 1].name
                        if (failure.fallback_allowed and index < len(providers) - 1)
                        else None
                    ),
                })
                if not failure.fallback_allowed:
                    break
                if index < len(providers) - 1:
                    logger.warning(
                        "tushare provider fallback api=%s from=%s category=%s",
                        api_name,
                        provider.name,
                        failure.category.value,
                    )
        self._persist_trace(api_name, params, attempts, selected_provider, final_outcome)
        raise TushareProviderError(api_name, failures)

    def _persist_trace(
        self,
        api_name: str,
        params: Mapping[str, Any] | None,
        attempts: list[dict[str, Any]],
        selected_provider: str | None,
        final_outcome: str,
    ) -> None:
        """构造并持久化统一 trace。持久化失败不影响已取到的数据（telemetry 降级）。"""
        if self._trace_sink is None or not attempts:
            return
        try:
            from app.services.data_source_trace import (
                TRACE_CONTRACT_ID,
                TRACE_CONTRACT_VERSION,
                DataSourceAttempt,
                DataSourceAttemptOutcome,
                DataSourceRequestTrace,
                FinalOutcome,
                request_identity_hash,
            )
            import hashlib
            import uuid

            # 默认 correlation 含唯一成分：每次逻辑请求生成独立 trace（append-only）。
            # 业务侧可通过 correlation_id_factory 注入 run_id/任务 id 关联同一批请求。
            correlation_id = (
                self._correlation_id_factory()
                if self._correlation_id_factory is not None
                else f"tushare-{api_name}-{uuid.uuid4().hex[:16]}"
            )
            req_hash = request_identity_hash(
                api_name, params={k: v for k, v in (params or {}).items()}
            )
            attempt_contracts = tuple(
                DataSourceAttempt(
                    ordinal=a["ordinal"],
                    provider=a["provider"],
                    outcome=DataSourceAttemptOutcome(a["outcome"]),
                    duration_ms=a["duration_ms"],
                    failure_category=a.get("failure_category"),
                    retryable=a.get("retryable"),
                    fallback_allowed=a.get("fallback_allowed"),
                    fallback_from=a.get("fallback_from"),
                    fallback_to=a.get("fallback_to"),
                )
                for a in attempts
            )
            trace_id = "dstr_" + hashlib.sha256(
                f"{correlation_id}|{req_hash}".encode("utf-8")
            ).hexdigest()[:48]
            trace = DataSourceRequestTrace(
                trace_id=trace_id,
                correlation_id=correlation_id,
                operation_name=api_name,
                request_identity_hash=req_hash,
                contract_id=TRACE_CONTRACT_ID,
                contract_version=TRACE_CONTRACT_VERSION,
                attempts=attempt_contracts,
                selected_provider=selected_provider,
                final_outcome=FinalOutcome(final_outcome),
            )
            self._trace_sink.persist(trace)
        except Exception:
            logger.debug("data source trace persist skipped/failed api=%s", api_name, exc_info=True)
