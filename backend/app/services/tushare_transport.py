"""Resilient upstream transports for Tushare-compatible providers.

This module belongs to the ingestion/realtime edge.  It does not replace the
local MySQL market pool used by recommendations and backtests.
"""
from __future__ import annotations

import contextvars
import logging
import random
import os
import threading
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

import requests
import tenacity

from app.core import metrics as _metrics
from app.core import provider_quota as _provider_quota

from app.services.observability import log_observation

from app.core import promax_window as _promax_window  # noqa: E402

logger = logging.getLogger(__name__)

_RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_EXCEPTIONS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ProxyError,
    requests.exceptions.ChunkedEncodingError,
)

# Tushare 业务错误码。40203 是账号没有该接口的访问权限（需要积分），
# 上游原文为「抱歉，您没有接口(xxx)访问权限」；换 token 或充积分才能解决，
# 等待重试没有意义。42901 是 token 当日配额耗尽，等到配额重置即可恢复。
_PERMISSION_BUSINESS_CODES = frozenset({"40203"})
_RATE_LIMIT_BUSINESS_CODES = frozenset({"42901"})
# HTTP 200 但业务层报「上游容量忙」的措辞（teajoin: "query capacity busy"）。
_BUSINESS_CAPACITY_TERMS = ("capacity busy", "capacity_busy", "pool_exhausted", "pool exhausted", "server busy", "系统繁忙", "服务繁忙")
# 批量/补数进程遇到「容量忙」：就地重试 BUSY_BATCH_ATTEMPTS 次，间隔 BUSY_BATCH_WAIT 秒 × 次数（带 ±25% 抖动），不开熔断。
BUSY_BATCH_ATTEMPTS = 5
BUSY_BATCH_WAIT = (2.0, 5.0)
_PERMISSION_MESSAGE_TERMS = ("权限", "未开通", "积分", "permission", "unauthorized")
_RATE_LIMIT_MESSAGE_TERMS = ("频率", "频次", "rate limit", "请求速度", "limit exceeded")

# 代理型数据源在自身容量耗尽时会返回 5xx 并在响应体里给出标识，例如
# `{"ok":false,"error":"upstream_pool_exhausted"}`。这类失败的正确反应是立刻换源并让
# 该源冷却一段时间，而不是按瞬时服务器错误做几百毫秒的退避重试——短退避会在容量尚未
# 恢复时继续施压，把「等一会儿就好」变成「这个源彻底不可用」。
_CAPACITY_EXHAUSTED_TERMS = (
    "pool_exhausted",
    "pool exhausted",
    "capacity",
    "overloaded",
    "too many",
    "busy",
    "quota",
)
# 5xx 响应体里承载错误标识的常见字段名，按优先级取第一个非空值。
_UPSTREAM_ERROR_FIELDS = ("error", "msg", "message", "detail", "reason")
_UPSTREAM_DETAIL_MAX_CHARS = 120


def _extract_upstream_detail(response: Any) -> str | None:
    """从失败响应里取出上游自己给的短错误标识，供归类与日志使用。

    响应体可能不是 JSON，也可能根本读不出来；任何一种都不应该让失败处理本身出错，
    因为那会把一个可以换源的失败变成不可恢复的异常。
    """
    try:
        payload = response.json()
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("_extract_upstream_detail: best-effort fallback after %r", _ble_exc)
        try:
            text = (getattr(response, "text", "") or "").strip()
        except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
            logger.debug("_extract_upstream_detail: best-effort fallback after %r", _ble_exc)
            return None
        return text[:_UPSTREAM_DETAIL_MAX_CHARS] or None

    if isinstance(payload, Mapping):
        for field in _UPSTREAM_ERROR_FIELDS:
            value = payload.get(field)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text[:_UPSTREAM_DETAIL_MAX_CHARS]
        return None
    text = str(payload).strip()
    return text[:_UPSTREAM_DETAIL_MAX_CHARS] or None




def _is_daily_limit_text(text: str) -> bool:
    """42901（Tushare 协议：token 当日配额耗尽），或同时出现「当日/每日」与「上限/限额/次数」字样。"""
    text = (text or "").lower()
    if "42901" in text or "daily limit" in text:
        return True
    daily = any(t in text for t in ("daily", "每日", "今日", "每天", "当日"))
    limit = any(t in text for t in ("limit", "上限", "限额", "额度", "次数"))
    return daily and limit


def _is_daily_limit(failure: "ProviderFailure") -> bool:
    if getattr(failure, "category", None) is FailureCategory.QUOTA_EXHAUSTED:
        return True
    return _is_daily_limit_text(f"{getattr(failure, 'upstream_detail', '') or ''} {failure}")


# 当日配额耗尽标记（provider_quota，跨进程 SQLite）读一次缓存这么久，避免每个请求都开库。
_EXHAUSTED_CHECK_TTL = 30.0
_exhausted_cache: dict[str, tuple[float, str, bool]] = {}
QUOTA_SKIP_DETAIL = "skipped: daily quota exhausted today"


def provider_exhausted_today(provider: str) -> bool:
    """provider 今天（Asia/Shanghai）已报告过日配额耗尽 → True。任何读取错误都视为未耗尽。"""
    if os.environ.get("ASHARE_UNDER_PYTEST") and not os.environ.get("ASHARE_QUOTA_SKIP_IN_TESTS"):
        return False  # 进程内耗尽标记会跨用例残留；只有专门的用例打开这个开关
    now = time.monotonic()
    day = _provider_quota._today()
    hit = _exhausted_cache.get(provider)
    if hit and hit[0] > now and hit[1] == day:
        return hit[2]
    try:
        exhausted = bool(_provider_quota.usage(provider)[1])
    except Exception:  # noqa: BLE001 - telemetry must never block fetching
        exhausted = False
    _exhausted_cache[provider] = (now + _EXHAUSTED_CHECK_TTL, day, exhausted)
    return exhausted


class AuthMode(StrEnum):
    JSON_TOKEN = "json_token"
    API_KEY_HEADER = "api_key_header"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"


class FailureCategory(StrEnum):
    CONFIGURATION = "configuration"
    CIRCUIT_OPEN = "circuit_open"
    NETWORK = "network"
    TIMEOUT = "timeout"
    HTTP_RATE_LIMIT = "http_rate_limit"
    HTTP_SERVER = "http_server"
    HTTP_CLIENT = "http_client"
    DATA_PENDING = "data_pending"
    NON_JSON = "non_json"
    BUSINESS_RATE_LIMIT = "business_rate_limit"
    # 上游 token 当日配额耗尽（quantdata 42901 "token daily limit exceeded"）。与按分钟限流不同：
    # 等几分钟不会恢复，本 provider 当天（Asia/Shanghai 日历日）其余请求直接跳过、换源。
    QUOTA_EXHAUSTED = "quota_exhausted"
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
    recovery_seconds: float = 300.0
    api_rate_limits: Mapping[str, int] = field(default_factory=dict)
    proxy: str = field(default="", repr=False)
    http_method: HttpMethod = HttpMethod.POST
    excluded_apis: frozenset[str] = frozenset()
    # Allowlist: when set, the provider only serves these APIs (and never enters
    # routing for any other API). None = no allowlist.
    included_apis: frozenset[str] | None = None
    # Business-facing source identity.  This is intentionally separate from
    # the provider name so legacy DB overrides remain compatible while two
    # credentials can be exposed as two independent data sources.
    source: str = ""
    # 路由优先级：数值越小越优先。per-API 链见 DEFAULT_API_ROUTES / routing_table.json，
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
        upstream_detail: str | None = None,
        capacity_busy: bool = False,
    ) -> None:
        self.provider = provider
        self.api_name = api_name
        self.category = category
        self.retryable = retryable
        self.fallback_allowed = fallback_allowed
        self.retry_after_seconds = retry_after_seconds
        # 上游自己给出的失败标识（例如代理的 `upstream_pool_exhausted`）。只保留短标识，
        # 不保留完整响应体，避免把 token 或业务数据带进日志。没有这个字段时，5xx 在日志里
        # 只剩 http_server 这个粗标签，无法区分「代理宕机」与「代理容量耗尽」。
        self.upstream_detail = upstream_detail
        # 上游「容量忙」（teajoin: query capacity busy）：瞬时拥塞，不是故障。批量/补数进程就地短退避重试且不开熔断；
        # live 进程保持保守（30s 冷却 + 立即降级到备源）。
        self.capacity_busy = bool(capacity_busy)
        suffix = f", upstream={upstream_detail}" if upstream_detail else ""
        super().__init__(f"{provider} {api_name} failed ({category.value}{suffix})")


class TushareProviderError(RuntimeError):
    """All eligible providers failed, containing only sanitized evidence."""

    def __init__(self, api_name: str, failures: list[ProviderFailure]) -> None:
        self.api_name = api_name
        self.failures = tuple(failures)
        # 汇总里带上每个 provider 的上游标识：作业结果与 data-health 里看到的就是这条消息，
        # 只有 category 时无法区分「代理容量耗尽」「账号无权限」「端点不存在」。
        summary = ", ".join(
            f"{item.provider}:{item.category.value}"
            + (f"({item.upstream_detail})" if item.upstream_detail else "")
            for item in failures
        )
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
    """Thread-safe sliding-window limiter with no lock held while sleeping.

    Shared by Tushare (60s window, +10ms pad, 10ms floor) and Xiaoshi
    (1s window, no pad, 1ms floor); parameters reproduce both originals.
    """

    def __init__(
        self,
        requests_per_minute: int,
        *,
        window_seconds: float = 60.0,
        pad_seconds: float = 0.01,
        min_wait_seconds: float = 0.01,
    ) -> None:
        self._limit = max(0, int(requests_per_minute))
        self._window = float(window_seconds)
        self._pad = float(pad_seconds)
        self._min_wait = float(min_wait_seconds)
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def headroom(self) -> float:
        """Fraction of the window still free; observational, never blocks."""
        if self._limit <= 0:
            return 1.0
        with self._lock:
            cutoff = time.monotonic() - self._window
            used = sum(1 for t in self._timestamps if t > cutoff)
        return max(0.0, 1.0 - used / self._limit)

    def acquire(self) -> None:
        if self._limit <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                while self._timestamps and self._timestamps[0] <= now - self._window:
                    self._timestamps.popleft()
                if len(self._timestamps) < self._limit:
                    self._timestamps.append(now)
                    return
                wait_seconds = max(self._min_wait, self._timestamps[0] + self._window - now + self._pad)
            time.sleep(wait_seconds)


def _make_limiter(key: str, requests_per_minute: int):
    """In-process limiter, or a cross-process one when TUSHARE_SHARED_LIMITER_DB is set."""
    local = SlidingWindowLimiter(requests_per_minute)
    db_path = os.environ.get("TUSHARE_SHARED_LIMITER_DB", "").strip()
    if not db_path:
        return local
    from app.services.shared_rate_limiter import SharedSlidingWindowLimiter
    return SharedSlidingWindowLimiter(key, requests_per_minute, db_path, fallback=local)


# Promax quota reservation (owner, 2026-10-19): during the trading session (Mon-Fri
# 09:15-15:05 Asia/Shanghai) processes in the research: breaker namespace are throttled on
# promax so live: realtime quotes (rt_k) keep their headroom of the ~200 RPM per-IP budget.
RESEARCH_INTRADAY_WINDOW = ((9, 15), (15, 5))
_RESEARCH_LIMITER: list[Any] = []
_RESEARCH_LIMITER_LOCK = threading.Lock()


def _is_research_namespace() -> bool:
    from app.core.shared_breaker import RESEARCH_NAMESPACE, breaker_namespace

    return breaker_namespace() == RESEARCH_NAMESPACE


def _in_research_window(now: datetime | None = None) -> bool:
    from zoneinfo import ZoneInfo

    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if now.weekday() >= 5:
        return False
    hm = (now.hour, now.minute)
    return RESEARCH_INTRADAY_WINDOW[0] <= hm < RESEARCH_INTRADAY_WINDOW[1]


def research_intraday_throttled(now: datetime | None = None) -> bool:
    from app.core.shared_breaker import RESEARCH_NAMESPACE, breaker_namespace

    return breaker_namespace() == RESEARCH_NAMESPACE and _in_research_window(now)


def _research_promax_limiter():
    with _RESEARCH_LIMITER_LOCK:
        if not _RESEARCH_LIMITER:
            rpm = 30
            try:
                from app.core.config import get_settings

                rpm = int(get_settings().tushare_promax_research_intraday_rpm)
            except Exception:  # noqa: BLE001
                pass
            _RESEARCH_LIMITER.append(_make_limiter("promax:research_intraday", max(1, rpm)))
        return _RESEARCH_LIMITER[0]


_RESEARCH_OTHER_LIMITERS: dict[str, Any] = {}


def _research_provider_limiter(provider: str):
    """非 promax 源（quantdata/teajoin…）在 research 命名空间盘中的限速器；rpm<=0 表示不限（保持旧行为）。

    与 promax 同一思路：交易时段（工作日 09:15–15:05）批量回补/研究脚本让路给 live: 实时与选股链路。
    rpm 来自 Settings.tushare_<provider>_research_intraday_rpm（teajoin 默认 30，quantdata 默认 0=不限）。"""
    with _RESEARCH_LIMITER_LOCK:
        if provider not in _RESEARCH_OTHER_LIMITERS:
            rpm = 0
            try:
                from app.core.config import get_settings

                rpm = int(getattr(get_settings(), f"tushare_{provider}_research_intraday_rpm", 0) or 0)
            except Exception:  # noqa: BLE001
                pass
            _RESEARCH_OTHER_LIMITERS[provider] = _make_limiter(f"{provider}:research_intraday", rpm) if rpm > 0 else None
        return _RESEARCH_OTHER_LIMITERS[provider]


# ── 「主源」模式（teajoin 等）：主源失败/超时要快速降级到原链路，不能因它慢拖住实盘 ──
# 路由器在把某源放到链首且后面还有备源时，用 _lead_scope 给这一次调用套上更短的读超时和 1 次尝试；
# 超时/容量忙/熔断/额度耗尽都会立刻落到下一家（原有 promax/quantdata 链），不在主源上做退避重试。
_LEAD_CALL: contextvars.ContextVar[tuple[float, int] | None] = contextvars.ContextVar("tushare_lead_call", default=None)


class _lead_scope:
    def __init__(self, read_timeout: float, attempts: int = 1) -> None:
        self._value = (float(read_timeout), max(1, int(attempts)))
        self._token: Any = None

    def __enter__(self) -> "_lead_scope":
        self._token = _LEAD_CALL.set(self._value)
        return self

    def __exit__(self, *exc: Any) -> None:
        _LEAD_CALL.reset(self._token)


@dataclass(frozen=True, slots=True)
class LeaderSpec:
    """某个 provider 在其支持的接口上做主源（排链首）的规则。

    * apis                 做主源的接口名单（其余接口保持原链序，该源若在 included_apis 内仍为末位兜底）。
    * intraday_sensitive   盘中（工作日 09:15–15:05）live 进程里对延迟敏感的接口：此时不做主源（排末位兜底），
                           盘后/研究回补进程（research 命名空间，本身已限速）仍做主源，把 promax/quantdata 额度留给实盘 rt_k。
    * heavy_apis/fast_apis  大表/慢接口读超时放宽到 heavy_timeout，小表收紧到 fast_timeout，其余 read_timeout。
    * read_timeout         做主源且后面还有备源时的读超时（秒）；超时即换源。
    * min_health           近期健康度低于此值（lb_health）时不做主源，直接走原链路。
    """

    provider: str
    apis: frozenset[str]
    intraday_sensitive: frozenset[str] = frozenset()
    heavy_apis: frozenset[str] = frozenset()
    fast_apis: frozenset[str] = frozenset()
    read_timeout: float = 40.0
    heavy_timeout: float = 90.0
    fast_timeout: float = 15.0
    min_health: float = 0.25
    intraday_read_timeout: float = 15.0  # 盘中 live 进程里做主源：读超时收紧到这个值（超时立即降级，不拖住实盘）

    def timeout_for(self, api_name: str, *, live_intraday: bool = False) -> float:
        if api_name in self.heavy_apis:
            base = self.heavy_timeout
        elif api_name in self.fast_apis:
            base = self.fast_timeout
        else:
            base = self.read_timeout
        if live_intraday:
            return min(base, self.intraday_read_timeout) if api_name not in self.heavy_apis else min(base, self.intraday_read_timeout * 2)
        return base

    def leads(self, api_name: str, *, research: bool, intraday: bool) -> bool:
        if api_name not in self.apis:
            return False
        if intraday and not research and api_name in self.intraday_sensitive:
            return False
        return True


def _breaker_kind(category: "FailureCategory") -> str | None:
    """FailureCategory -> SharedCircuitBreaker failure kind (per-kind thresholds)."""
    if category in (
        FailureCategory.HTTP_RATE_LIMIT, FailureCategory.BUSINESS_RATE_LIMIT, FailureCategory.QUOTA_EXHAUSTED
    ):
        return "rate_limit"
    if category is FailureCategory.TIMEOUT:
        return "timeout"
    if category in (FailureCategory.MALFORMED_RESPONSE, FailureCategory.NON_JSON):
        return "bad_data"
    return None


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

    def record_failure(
        self, *, force_open: bool = False, recovery_seconds: float | None = None, kind: str | None = None
    ) -> None:
        if kind == "rate_limit":
            force_open = True
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
        self._provider_limiter = _make_limiter(config.name, config.rate_limit_per_minute)
        self._api_limiters = {
            api_name: _make_limiter(f"{config.name}:{api_name}", limit)
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
        # Load-balancing health: per-API time-decayed failure score (half-life LB_HALF_LIFE_S).
        self._lb_errors: dict[str, tuple[float, float]] = {}
        self._lb_lock = threading.Lock()

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
            "source": self.config.source or self.name,
            **self._health.snapshot(),
            "auth_mode": self.config.auth_mode.value,
            "http_method": self.config.http_method.value,
            "rate_limit_per_minute": self.config.rate_limit_per_minute,
            "api_rate_limits": dict(self.config.api_rate_limits),
            "max_concurrency": self.config.max_concurrency,
            "proxy_configured": bool(self.config.proxy.strip()),
            "breaker_state": breaker_state,
            "open_circuits": open_circuits,
        }

    LB_HALF_LIFE_S = 120.0

    def _lb_record(self, api_name: str, failed: bool, *, weight: float = 1.0) -> None:
        now = time.monotonic()
        with self._lb_lock:
            score, ts = self._lb_errors.get(api_name, (0.0, now))
            score *= 0.5 ** ((now - ts) / self.LB_HALF_LIFE_S)
            self._lb_errors[api_name] = (score + (weight if failed else 0.0), now)

    def lb_health(self, api_name: str) -> float:
        """1.0 = healthy; decays toward 0 with recent errors/429s; 0 when breaker open."""
        with self._breaker_lock:
            breaker = self._breakers.get(api_name)
        if breaker is not None and breaker.state == "open":
            return 0.0
        now = time.monotonic()
        with self._lb_lock:
            score, ts = self._lb_errors.get(api_name, (0.0, now))
        score *= 0.5 ** ((now - ts) / self.LB_HALF_LIFE_S)
        return 1.0 / (1.0 + score)

    def lb_headroom(self, api_name: str) -> float:
        def h(limiter: Any) -> float:
            fn = getattr(limiter, "headroom", None)
            return float(fn()) if fn else 1.0

        value = h(self._provider_limiter)
        api_limiter = self._api_limiters.get(api_name)
        if api_limiter is not None:
            value = min(value, h(api_limiter))
        return value

    def _breaker(self, api_name: str) -> CircuitBreaker:
        with self._breaker_lock:
            breaker = self._breakers.get(api_name)
            if breaker is None:
                from app.core.shared_breaker import SharedCircuitBreaker

                # shared across backend / Prefect worker / scripts (runtime SQLite store)
                breaker = SharedCircuitBreaker(
                    f"tushare:{self.config.name}:{api_name}",
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

    def supports(self, api_name: str) -> bool:
        included = self.config.included_apis
        if included is not None and api_name not in included:
            return False
        return api_name not in self.config.excluded_apis

    def _call_timeout(self) -> tuple[float, float]:
        """本次调用的 (连接, 读取) 超时；处于 _lead_scope（主源且有备源）时读超时收紧。"""
        connect, read = self.config.timeout
        lead = _LEAD_CALL.get()
        if lead is not None:
            read = min(read, lead[0])
        return (connect, read)

    def _wait_for_capacity(self, api_name: str) -> None:
        t0 = time.perf_counter()
        if self.config.name == "promax" and research_intraday_throttled():
            _research_promax_limiter().acquire()  # leave promax headroom for live: realtime
        if self.config.name != "promax" and research_intraday_throttled():
            _lim = _research_provider_limiter(self.config.name)
            if _lim is not None:
                _lim.acquire()  # 盘中研究/回补类请求限速，给 live: 链路留额度
        if self.config.name == "promax" and _is_research_namespace():
            # 按窗口令牌桶（5min/60min）；盘中先让路给 live rt_k
            _promax_window.research_acquire(api_name, intraday=_in_research_window())
        self._provider_limiter.acquire()
        api_limiter = self._api_limiters.get(api_name)
        if api_limiter is not None:
            api_limiter.acquire()
        _metrics.observe("provider_ratelimit_wait", time.perf_counter() - t0, provider=self.name)

    def request(
        self,
        api_name: str,
        *,
        params: Mapping[str, Any] | None = None,
        fields: str = "",
    ) -> Mapping[str, Any]:
        started = time.perf_counter()
        if provider_exhausted_today(self.name):
            # 当日配额已耗尽：不再打上游（不计请求数、不占限速令牌），直接让路由换下一家。
            _metrics.inc("provider_requests", provider=self.name, outcome="quota_skip")
            raise ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.QUOTA_EXHAUSTED,
                retryable=False,
                fallback_allowed=True,
                upstream_detail=QUOTA_SKIP_DETAIL,
            )
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
        _provider_quota.record_request(self.name)
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
                _metrics.inc("provider_requests", provider=self.name, outcome=str(failure.category.value))
                rate_limited = failure.category in (
                    FailureCategory.HTTP_RATE_LIMIT, FailureCategory.BUSINESS_RATE_LIMIT,
                    FailureCategory.QUOTA_EXHAUSTED,
                )
                if failure.category in (
                    FailureCategory.BUSINESS_RATE_LIMIT, FailureCategory.QUOTA_EXHAUSTED
                ) and _is_daily_limit(failure):
                    _provider_quota.mark_exhausted(self.name)
                    _exhausted_cache.pop(self.name, None)
                    logger.warning(
                        "tushare provider daily quota exhausted provider=%s api=%s; skipping it for the rest of today",
                        self.name, api_name,
                    )
                self._lb_record(api_name, True, weight=0.5 if failure.capacity_busy else (2.0 if rate_limited else 1.0))
                raise
            data = envelope.get("data")
            items = data.get("items") if isinstance(data, Mapping) else None
            self._health.record_success(
                api_name,
                latency_ms=(time.perf_counter() - started) * 1000,
                empty=not bool(items),
            )
            _metrics.inc("provider_requests", provider=self.name, outcome="success")
            _metrics.inc("provider_api_requests", provider=self.name, api=api_name, outcome="success")
            self._lb_record(api_name, False)
            _metrics.observe("provider_latency", time.perf_counter() - started, provider=self.name)
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
        attempt_box = [0]

        def _once() -> Mapping[str, Any]:
            attempt = attempt_box[0]
            attempt_box[0] += 1
            started = time.perf_counter()
            try:
                self._wait_for_capacity(api_name)
                envelope = self._request_once(api_name, params=params, fields=fields)
            except ProviderFailure as failure:
                logger.warning(
                    "tushare provider failure provider=%s api=%s attempt=%d category=%s "
                    "duration_ms=%.1f upstream=%s",
                    self.name,
                    api_name,
                    attempt + 1,
                    failure.category.value,
                    (time.perf_counter() - started) * 1000,
                    failure.upstream_detail or "-",
                )
                raise
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

        base_attempts = min(self.config.max_attempts, (_LEAD_CALL.get() or (0.0, self.config.max_attempts))[1])

        def _last_busy(state: Any) -> bool:
            exc = state.outcome.exception() if state.outcome is not None else None
            return bool(isinstance(exc, ProviderFailure) and exc.capacity_busy and exc.retryable)

        def _stop(state: Any) -> bool:
            return state.attempt_number >= (BUSY_BATCH_ATTEMPTS if _last_busy(state) else base_attempts)

        def _wait(state: Any) -> float:
            if _last_busy(state):
                lo, hi = BUSY_BATCH_WAIT
                delay = random.uniform(lo, hi) * min(state.attempt_number, 3)
                _metrics.inc("provider_capacity_busy_retry", provider=self.name, api=api_name)
                return delay
            return min(5.0, 0.5 * (2 ** (state.attempt_number - 1))) + random.uniform(0.0, 0.2)

        retrying = tenacity.Retrying(
            stop=_stop,
            retry=tenacity.retry_if_exception(
                lambda exc: isinstance(exc, ProviderFailure) and bool(exc.retryable)
            ),
            wait=_wait,
            sleep=lambda seconds: time.sleep(seconds),
            reraise=True,
        )
        try:
            return retrying(_once)
        except ProviderFailure as failure:
            force_open = failure.category in {
                FailureCategory.BUSINESS_RATE_LIMIT,
                FailureCategory.QUOTA_EXHAUSTED,
                FailureCategory.HTTP_RATE_LIMIT,
                FailureCategory.PERMISSION,
            }
            if failure.capacity_busy and _is_research_namespace():
                # 批量/补数遇到上游容量忙：已就地重试过；不计熔断失败（否则 3 次忙 = 5 分钟起的停摆），
                # 由路由器换下一家；只留指标，监控可见。
                _metrics.inc("provider_capacity_busy", provider=self.name, api=api_name, mode="batch")
                raise
            if failure.capacity_busy:
                _metrics.inc("provider_capacity_busy", provider=self.name, api=api_name, mode="live")
            if failure.fallback_allowed and failure.category not in {
                FailureCategory.INVALID_REQUEST,
                FailureCategory.HTTP_CLIENT,
                FailureCategory.DATA_PENDING,
            }:
                breaker.record_failure(
                    force_open=force_open and failure.category is FailureCategory.PERMISSION,
                    recovery_seconds=failure.retry_after_seconds,
                    kind=_breaker_kind(failure.category),
                )
            raise

    def _request_once(
        self,
        api_name: str,
        *,
        params: Mapping[str, Any],
        fields: str,
    ) -> Mapping[str, Any]:
        response: requests.Response | None = None
        try:
            if self.config.http_method is HttpMethod.GET:
                query = dict(params)
                if fields:
                    query["fields"] = fields
                response = self._session.get(
                    self._endpoint(api_name),
                    params=query,
                    timeout=self._call_timeout(),
                )
            else:
                response = self._session.post(
                    self._endpoint(api_name),
                    json=self._payload(api_name, params, fields),
                    timeout=self._call_timeout(),
                )
            status = int(response.status_code)
            if status != 200:
                if status == 202:
                    raise ProviderFailure(
                        provider=self.name,
                        api_name=api_name,
                        category=FailureCategory.DATA_PENDING,
                        retryable=False,
                        fallback_allowed=True,
                    )
                if status == 429:
                    upstream_wait = _promax_window.upstream_wait_seconds(response)
                    if self.config.name == "promax":
                        retry_after = _promax_window.cooldown_seconds(api_name, upstream_wait)
                    else:
                        retry_after = upstream_wait or 60.0
                    busy_detail = (_extract_upstream_detail(response) or "") if self.config.name != "promax" else ""
                    if busy_detail and any(term in busy_detail.lower() for term in _BUSINESS_CAPACITY_TERMS):
                        # teajoin：HTTP 429 + {"msg":"query capacity busy"} + Retry-After:2 —— 上游瞬时拥塞，不是额度/故障。
                        # 批量/补数进程：就地短退避重试（见 BUSY_BATCH_*），不开熔断；live 进程：不重试，30s 冷却并立即降级。
                        batch = _is_research_namespace()
                        raise ProviderFailure(
                            provider=self.name,
                            api_name=api_name,
                            category=FailureCategory.HTTP_RATE_LIMIT,
                            retryable=batch,
                            fallback_allowed=True,
                            retry_after_seconds=30.0,
                            upstream_detail=busy_detail,
                            capacity_busy=True,
                        )
                    raise ProviderFailure(
                        provider=self.name,
                        api_name=api_name,
                        category=FailureCategory.HTTP_RATE_LIMIT,
                        retryable=False,
                        fallback_allowed=True,
                        retry_after_seconds=retry_after,
                    )
                if status >= 500:
                    upstream_detail = _extract_upstream_detail(response)
                    haystack = (upstream_detail or "").lower()
                    if any(term in haystack for term in _CAPACITY_EXHAUSTED_TERMS):
                        # 容量耗尽：让本源冷却并立刻切换到下一个 provider。上游给了等待时间就用上游的；
                        # promax 否则按实测窗口（app/core/promax_window.py），其他源沿用 recovery_seconds。
                        upstream_wait = _promax_window.upstream_wait_seconds(response)
                        if self.config.name == "promax":
                            cooldown = _promax_window.cooldown_seconds(api_name, upstream_wait)
                        else:
                            cooldown = upstream_wait or float(self.config.recovery_seconds)
                        raise ProviderFailure(
                            provider=self.name,
                            api_name=api_name,
                            category=FailureCategory.HTTP_RATE_LIMIT,
                            retryable=False,
                            fallback_allowed=True,
                            retry_after_seconds=cooldown,
                            upstream_detail=upstream_detail,
                        )
                    raise ProviderFailure(
                        provider=self.name,
                        api_name=api_name,
                        category=FailureCategory.HTTP_SERVER,
                        retryable=status in _RETRYABLE_HTTP_STATUS,
                        fallback_allowed=True,
                        upstream_detail=upstream_detail,
                    )
                client_detail = _extract_upstream_detail(response)
                if status == 403 and self.config.name == "teajoin" and "invalid api key" in (client_detail or "").lower():
                    # 2026-10-26 21:00 实测：teajoin 在负载高峰会偶发整段 403 "invalid api key"（同一把 key，
                    # 几十秒后恢复，补数日志里约 10% 的请求命中）。当瞬时拥塞处理：批量/补数就地短退避重试，
                    # live 不重试、30s 冷却并立即降级；都不开熔断。key 真被吊销时重试 5 次后仍会降级，并由
                    # 「provider 全失败」告警暴露。
                    batch = _is_research_namespace()
                    raise ProviderFailure(
                        provider=self.name,
                        api_name=api_name,
                        category=FailureCategory.HTTP_RATE_LIMIT,
                        retryable=batch,
                        fallback_allowed=True,
                        retry_after_seconds=30.0,
                        upstream_detail=client_detail,
                        capacity_busy=True,
                    )
                raise ProviderFailure(
                    provider=self.name,
                    api_name=api_name,
                    category=FailureCategory.HTTP_CLIENT,
                    retryable=False,
                    fallback_allowed=status in {401, 403},
                    upstream_detail=client_detail,
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
        raw_message = str(envelope.get("msg") or "").strip()
        message = raw_message.lower()
        # 上游业务错误码与原文一起保留，例如「40203: 抱歉，您没有接口(cyq_perf)访问权限」。
        upstream_detail = (f"{code}: {raw_message}" if code else raw_message)[:_UPSTREAM_DETAIL_MAX_CHARS] or None
        # Tushare 把「访问频率超限（如 1次/小时）」也放在 40203 下，原文为「抱歉，您访问接口(x)频率超限(1次/小时)」；
        # 只有「没有接口…访问权限」才是真无权限。否则限流会被判成 permission → 探测矩阵标 no_permission →
        # 路由表把该 provider 永久排除（等一小时就能恢复的源被误判）。
        _rate_text = any(term in message for term in ("频率超限", "频次超限", "访问接口") ) and (
            "频率" in message or "频次" in message
        )
        _really_no_permission = "没有接口" in message or "访问权限" in message or "未开通" in message
        if _rate_text and not _really_no_permission and not _is_daily_limit_text(f"{code} {raw_message}"):
            _per_hour = "小时" in message or "/hour" in message
            return ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.BUSINESS_RATE_LIMIT,
                retryable=False,
                fallback_allowed=True,
                retry_after_seconds=3600.0 if _per_hour else 60.0,
                upstream_detail=upstream_detail,
            )
        if code in _PERMISSION_BUSINESS_CODES or any(
            term in message for term in _PERMISSION_MESSAGE_TERMS
        ):
            return ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.PERMISSION,
                retryable=False,
                fallback_allowed=True,
                upstream_detail=upstream_detail,
            )
        if _is_daily_limit_text(f"{code} {raw_message}"):
            return ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.QUOTA_EXHAUSTED,
                retryable=False,
                fallback_allowed=True,
                upstream_detail=upstream_detail,
            )
        if code in _RATE_LIMIT_BUSINESS_CODES or any(
            term in message for term in _RATE_LIMIT_MESSAGE_TERMS
        ):
            return ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.BUSINESS_RATE_LIMIT,
                retryable=False,
                fallback_allowed=True,
                retry_after_seconds=60.0,
                upstream_detail=upstream_detail,
            )
        if any(term in message for term in _BUSINESS_CAPACITY_TERMS):
            # 2026-10-26 teajoin：HTTP 200 + 业务码非 0 + "query capacity busy"（重查询时上游并发满）。
            # 不是参数/权限错误，也不是日额度：短冷却后换源/重试，别把它当 provider_error 反复打。
            batch = _is_research_namespace()
            return ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.HTTP_RATE_LIMIT,
                retryable=batch,  # 批量/补数：就地短退避重试；live：不重试，立即换源
                fallback_allowed=True,
                retry_after_seconds=30.0,
                upstream_detail=upstream_detail,
                capacity_busy=True,
            )
        if any(term in message for term in ("参数", "parameter", "invalid argument")):
            return ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.INVALID_REQUEST,
                retryable=False,
                fallback_allowed=False,
                upstream_detail=upstream_detail,
            )
        return ProviderFailure(
            provider=self.name,
            api_name=api_name,
            category=FailureCategory.PROVIDER_ERROR,
            retryable=False,
            fallback_allowed=True,
            upstream_detail=upstream_detail,
        )


def _alert_all_providers_failed(api_name: str, failures: Sequence[Any]) -> None:
    from app.core.alerts import emit_alert

    detail = ", ".join(
        f"{getattr(f, 'provider', None) or (f.get('provider') if isinstance(f, dict) else '?')}:"
        f"{getattr(getattr(f, 'category', None), 'value', None) or (f.get('category') if isinstance(f, dict) else '?')}"
        for f in failures
    ) or "无可用 provider"
    emit_alert("datasource_all_failed", api_name, f"Tushare 全部上游失败 api={api_name} [{detail}]")


class TushareProviderRouter:
    """API-aware routing over independently protected providers."""

    # 默认 per-API 选路链：{api_name: [provider 名按尝试顺序]}。
    # 未列出的 API 回落到按 provider priority 升序。
    DEFAULT_API_ROUTES: Mapping[str, tuple[str, ...]] = {
        "rt_k": ("promax", "quantdata"),
        "stk_mins": ("promax", "quantdata"),
        "rt_min": ("promax", "quantdata"),
        "anns_d": ("quantdata",),
        # Promax 支持标准辅助状态适配器所用的批量契约，排第一；quantdata 兜底。
        "fina_indicator": ("promax", "quantdata"),
        "ths_member": ("promax", "quantdata"),
        # promax 常被限速：quantdata 作为 trade_cal 兜底（2026-10 复查）。
        "trade_cal": ("promax", "quantdata"),
    }

    def __init__(
        self,
        providers: list[TushareHttpProvider],
        *,
        api_routes: Mapping[str, tuple[str, ...]] | None = None,
        api_tiers: Mapping[str, Sequence[Sequence[str]]] | None = None,
        balance_rng: random.Random | None = None,
        balance_deterministic: bool = False,
        enabled_override: Any = None,
        priority_override: Any = None,
        leaders: Sequence[LeaderSpec] | None = None,
        trace_sink: Any = None,
        correlation_id_factory: Any = None,
    ) -> None:
        self._providers = {provider.name: provider for provider in providers}
        # per-API 选路链：api_name -> [provider 名按尝试顺序]。默认 DEFAULT_API_ROUTES，
        # 可由 api_routes 覆盖（数据源管理/未来新源在此调链序，与业务解耦）。
        self._api_routes = dict(api_routes) if api_routes is not None else dict(self.DEFAULT_API_ROUTES)
        # Load balancing: api -> equal-quality tiers. Absent = no balancing (pure chain order),
        # which is the default for directly-constructed routers (equivalence tests).
        self._api_tiers = {k: tuple(tuple(t) for t in v) for k, v in (api_tiers or {}).items()}
        self._balance_rng = balance_rng or random.Random()
        self._balance_deterministic = bool(balance_deterministic)
        # enabled_override: 可选可调用 () -> Mapping[str, bool]，提供 provider 运行时启停
        # （DB 覆盖 config）。为 None 时全部启用。每次选路时读取，使启停即时生效。
        self._enabled_override = enabled_override
        # priority_override: 可选可调用 () -> Mapping[str, int]，提供 provider 运行时优先级
        # 覆盖（管理面拖拽调序写 DB）。为 None 时用代码默认 priority。每次选路时读取。
        self._priority_override = priority_override
        # leaders: 在其名单接口上做主源（排链首）的 provider 规则；None/空=保持原链序。
        self._leaders = {spec.provider: spec for spec in (leaders or ())}
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

    def request_pinned(
        self, provider_name: str, api_name: str, *, params: Mapping[str, Any] | None = None, fields: str = ""
    ) -> Mapping[str, Any]:
        """只走指定 provider（仍受其熔断/限流/额度保护）。用于「主源返回了可疑的截断结果，改问另一家核实」。
        provider 未配置/被停用/不支持该接口时抛 TushareProviderError，调用方应保留原结果。"""
        p = self._providers.get(provider_name)
        if p is None or not self._is_enabled(p.name) or not p.supports(api_name):
            raise TushareProviderError(api_name, [])
        return p.request(api_name, params=params, fields=fields)

    def _ordered_providers(self, api_name: str) -> list[TushareHttpProvider]:
        enabled = [
            p
            for p in self._providers.values()
            if self._is_enabled(p.name) and p.supports(api_name)
        ]
        route = self._api_routes.get(api_name)
        if route:
            # 显式链序：按 route 顺序挑选已启用且已注册的 provider
            by_name = {p.name: p for p in enabled}
            ordered = [by_name[name] for name in route if name in by_name]
            # 链里没列出但已启用的 provider 追加在末尾（按 priority），避免漏掉 fallback
            rest = sorted((p for p in enabled if p.name not in route), key=self._effective_priority)
            return self._apply_leaders(api_name, self._balance(api_name, ordered + rest))
        return self._apply_leaders(api_name, self._balance(api_name, sorted(enabled, key=self._effective_priority)))

    # promax 额度专留实时行情：这些接口保持原链路（含 promax）
    PROMAX_RESERVED_FOR = frozenset({"rt_k", "rt_min"})

    def _apply_promax_reserve(self, api_name: str, ordered: list[TushareHttpProvider]) -> list[TushareHttpProvider]:
        """2026-10-26：promax 降为少额度用户，额度只留给 rt_k/rt_min。
        * research 命名空间（补数/研究脚本）：不动用 promax（含唯一源时直接失败，宁可晚补也不抢实盘额度）。
        * live：其余接口链路里还有别的上游时，把 promax 拿掉；它是唯一源（如 anns_d）时保留。
        两个开关见 Settings.tushare_promax_research_enabled / tushare_promax_rtk_reserved（默认都启用保护）。"""
        if api_name in self.PROMAX_RESERVED_FOR or not any(p.name == "promax" for p in ordered):
            return ordered
        try:
            from app.core.config import get_settings

            st = get_settings()
            research_ok = bool(st.tushare_promax_research_enabled)
            reserved = bool(st.tushare_promax_rtk_reserved)
        except Exception:  # noqa: BLE001
            return ordered
        others = [p for p in ordered if p.name != "promax"]
        if _is_research_namespace() and not research_ok:
            _metrics.inc("provider_promax_reserved", api=api_name, namespace="research")
            return others
        if reserved and others:
            _metrics.inc("provider_promax_reserved", api=api_name, namespace="live")
            return others
        return ordered

    def _lead_spec_for(self, api_name: str, ordered: list[TushareHttpProvider]) -> tuple[LeaderSpec, TushareHttpProvider] | None:
        """当前时刻应当做该接口主源的 (规则, provider)；没有则 None。"""
        if not self._leaders:
            return None
        research = _is_research_namespace()
        intraday = _in_research_window()
        for p in ordered:
            spec = self._leaders.get(p.name)
            if spec is None or not spec.leads(api_name, research=research, intraday=intraday):
                continue
            health = p.lb_health(api_name) if hasattr(p, "lb_health") else 1.0
            if health < spec.min_health:
                _metrics.inc("provider_lead_skipped", api=api_name, provider=p.name, reason="unhealthy")
                continue
            return spec, p
        return None

    def _apply_leaders(self, api_name: str, ordered: list[TushareHttpProvider]) -> list[TushareHttpProvider]:
        if not self._leaders or len(ordered) < 2:
            return ordered
        found = self._lead_spec_for(api_name, ordered)
        if found is None:
            # 盘中延迟敏感接口：主源规则暂不生效 → 该源（若在链内）排到最后做兜底，原链路优先
            demoted = [p for p in ordered if p.name in self._leaders and not self._leaders[p.name].leads(
                api_name, research=_is_research_namespace(), intraday=_in_research_window())
                and api_name in self._leaders[p.name].apis]
            if demoted:
                ordered = [p for p in ordered if p not in demoted] + demoted
            return ordered
        _spec, lead = found
        if ordered[0] is not lead:
            ordered = [lead] + [p for p in ordered if p is not lead]
        _metrics.inc("provider_lead_choice", api=api_name, provider=lead.name)
        return ordered

    def balance_weights(self, api_name: str, providers: Sequence[TushareHttpProvider]) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in providers:
            health = p.lb_health(api_name) if hasattr(p, "lb_health") else 1.0
            headroom = p.lb_headroom(api_name) if hasattr(p, "lb_headroom") else 1.0
            rpm = max(1, int(getattr(getattr(p, "config", None), "rate_limit_per_minute", 0) or 60))
            # remaining absolute capacity × live health; floor keeps an idle-but-full provider eligible
            out[p.name] = health * max(0.02, headroom) * rpm
        return out

    def _balance(self, api_name: str, ordered: list[TushareHttpProvider]) -> list[TushareHttpProvider]:
        """Reorder within equal-quality tiers by headroom×health; tiers & fallback order kept."""
        tiers = self._api_tiers.get(api_name)
        if not tiers or len(ordered) < 2:
            return ordered
        tier_of = {name: i for i, tier in enumerate(tiers) for name in tier}
        n = len(tiers)
        # stable: tiered providers first by tier index, then untiered in chain order
        indexed = sorted(enumerate(ordered), key=lambda t: (tier_of.get(t[1].name, n), t[0]))
        result: list[TushareHttpProvider] = []
        i = 0
        while i < len(indexed):
            tidx = tier_of.get(indexed[i][1].name, n)
            group = []
            while i < len(indexed) and tier_of.get(indexed[i][1].name, n) == tidx:
                group.append(indexed[i][1])
                i += 1
            if tidx < n and len(group) > 1:
                group = self._weighted_order(api_name, group)
            result.extend(group)
        _metrics.inc("provider_lb_choice", api=api_name, provider=result[0].name)
        return result

    def _weighted_order(self, api_name: str, group: list[TushareHttpProvider]) -> list[TushareHttpProvider]:
        weights = self.balance_weights(api_name, group)
        for name, w in weights.items():
            _metrics.set_gauge("provider_lb_weight", w, api=api_name, provider=name)
        live = [p for p in group if weights[p.name] > 0]
        dead = [p for p in group if weights[p.name] <= 0]  # breaker open: last, still a fallback
        if self._balance_deterministic:
            live.sort(key=lambda p: -weights[p.name])  # stable on ties
            return live + dead
        out: list[TushareHttpProvider] = []
        pool = list(live)
        while pool:
            total = sum(weights[p.name] for p in pool)
            pick = self._balance_rng.random() * total
            acc = 0.0
            for idx, p in enumerate(pool):
                acc += weights[p.name]
                if pick < acc or idx == len(pool) - 1:
                    out.append(pool.pop(idx))
                    break
        return out + dead

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
        providers = self._apply_promax_reserve(api_name, self._ordered_providers(api_name))
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
                lead_spec = self._leaders.get(provider.name) if (index == 0 and len(providers) > 1) else None
                if lead_spec is not None and lead_spec.leads(
                    api_name, research=_is_research_namespace(), intraday=_in_research_window()
                ):
                    _live_intraday = _in_research_window() and not _is_research_namespace()
                    with _lead_scope(lead_spec.timeout_for(api_name, live_intraday=_live_intraday), 2 if _is_research_namespace() else 1):
                        envelope = provider.request(api_name, params=params, fields=fields)
                else:
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
                    _metrics.inc("provider_fallback", api=api_name, provider=provider.name, fallback_from=fallback_from)
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
                is_breaker_skip = failure.category is FailureCategory.CIRCUIT_OPEN or (
                    failure.category is FailureCategory.QUOTA_EXHAUSTED
                    and failure.upstream_detail == QUOTA_SKIP_DETAIL
                )
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
                        "tushare provider fallback api=%s from=%s category=%s upstream=%s",
                        api_name,
                        provider.name,
                        failure.category.value,
                        failure.upstream_detail or "-",
                    )
        self._persist_trace(api_name, params, attempts, selected_provider, final_outcome)
        _alert_all_providers_failed(api_name, failures)
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
