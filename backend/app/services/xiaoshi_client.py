"""Authenticated Xiaoshi API client with a process-local QPS guard.

The client owns only the HTTP boundary: 只覆盖当前公开契约里的行情、量化数据集
与历史文件地址，不封装已经下线的旧查询接口。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

import httpx


_DOWNLOAD_SESSION_RETRY_CAP_SECONDS = 2.0


class XiaoshiError(RuntimeError):
    """A sanitized Xiaoshi request or response failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        path: str = "",
        retry_after_seconds: float | None = None,
    ) -> None:
        self.status_code = status_code
        self.path = path
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


class XiaoshiConfigurationError(XiaoshiError):
    """The Xiaoshi client has no usable API key or endpoint configuration."""


class _QPSLimiter:
    """Thread-safe sliding-window limiter; every retry also consumes capacity."""

    def __init__(self, max_qps: int) -> None:
        if int(max_qps) < 1:
            raise ValueError("max_qps must be positive")
        self._limit = int(max_qps)
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._timestamps and self._timestamps[0] <= now - 1.0:
                    self._timestamps.popleft()
                if len(self._timestamps) < self._limit:
                    self._timestamps.append(now)
                    return
                wait_seconds = max(0.001, self._timestamps[0] + 1.0 - now)
            time.sleep(wait_seconds)


class XiaoshiClient:
    """Small authenticated client for the stable Xiaoshi v3 contracts."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        max_qps: int = 30,
        timeout_seconds: float = 30.0,
        max_attempts: int = 2,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.max_attempts = max(1, int(max_attempts))
        self._timeout = max(0.1, float(timeout_seconds))
        self._limiter = _QPSLimiter(max_qps)
        self._client = http_client or httpx.Client(follow_redirects=True)
        self._owns_client = http_client is None

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "XiaoshiClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request_response(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        cache_control: str | None = None,
        accept: str = "application/json",
        retry_after_cap_seconds: float = 5.0,
    ) -> httpx.Response:
        if not self.configured:
            raise XiaoshiConfigurationError("Xiaoshi API Key 或 API 地址未配置")
        normalized_path = "/" + path.lstrip("/")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": accept,
        }
        if cache_control:
            headers["Cache-Control"] = cache_control

        last_error: XiaoshiError | None = None
        for attempt in range(self.max_attempts):
            self._limiter.acquire()
            try:
                response = self._client.request(
                    method,
                    f"{self.base_url}{normalized_path}",
                    params=dict(params or {}),
                    json=dict(json or {}) if json is not None else None,
                    headers=headers,
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                last_error = XiaoshiError(
                    f"Xiaoshi 请求超时: {normalized_path}", path=normalized_path
                )
                if attempt + 1 < self.max_attempts:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = XiaoshiError(
                    f"Xiaoshi 网络请求失败: {normalized_path}", path=normalized_path
                )
                if attempt + 1 < self.max_attempts:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                raise last_error from exc

            status = int(response.status_code)
            if status == 429 or status >= 500:
                retry_after_seconds: float | None = None
                raw_retry_after = response.headers.get("Retry-After", "")
                try:
                    retry_after_seconds = float(raw_retry_after)
                except (TypeError, ValueError):
                    retry_after_seconds = None
                last_error = XiaoshiError(
                    f"Xiaoshi HTTP {status}: {normalized_path}",
                    status_code=status,
                    path=normalized_path,
                    retry_after_seconds=retry_after_seconds,
                )
                if attempt + 1 < self.max_attempts:
                    if retry_after_seconds is None:
                        delay = 0.2 * (attempt + 1)
                    else:
                        delay = max(0.2, min(retry_after_cap_seconds, retry_after_seconds))
                    time.sleep(delay)
                    continue
                raise last_error
            if status >= 400:
                raise XiaoshiError(
                    f"Xiaoshi HTTP {status}: {normalized_path}",
                    status_code=status,
                    path=normalized_path,
                )
            return response

        assert last_error is not None
        raise last_error

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        cache_control: str | None = None,
        retry_after_cap_seconds: float = 5.0,
    ) -> dict[str, Any]:
        response = self._request_response(
            method,
            path,
            params=params,
            json=json,
            cache_control=cache_control,
            retry_after_cap_seconds=retry_after_cap_seconds,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise XiaoshiError(
                f"Xiaoshi 返回非 JSON: {response.request.url.path}",
                status_code=response.status_code,
                path=response.request.url.path,
            ) from exc
        if not isinstance(payload, Mapping):
            raise XiaoshiError(
                f"Xiaoshi 返回格式无效: {response.request.url.path}",
                status_code=response.status_code,
                path=response.request.url.path,
            )
        return dict(payload)

    def get_manifest(self) -> dict[str, Any]:
        return self.request("GET", "/api/v3/manifest", cache_control="no-store, no-cache")

    def get_capabilities(self) -> dict[str, Any]:
        return self.request("GET", "/api/v3/market/capabilities")

    def get_quote(
        self, code: str, *, market: str = "CN", instrument: str = "stock"
    ) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/api/v3/market/quote/{code}",
            params={"market": market, "instrument": instrument},
        )

    def get_quant_catalog(self) -> dict[str, Any]:
        return self.request("GET", "/api/v3/quant-data/catalog")

    def get_quant_data(self, dataset: str, **params: Any) -> dict[str, Any]:
        if not dataset or not all(char.isalnum() or char in "-_" for char in dataset):
            raise ValueError("dataset must be a simple quant-data identifier")
        return self.request("GET", f"/api/v3/quant-data/{dataset}", params=params)

    def get_history_catalog(self, **params: Any) -> dict[str, Any]:
        return self.request("GET", "/api/v3/history/catalog", params=params)

    def get_history_manifest(self) -> dict[str, Any]:
        return self.request("GET", "/api/v3/history/manifest")

    def get_history_download_session(self, **params: Any) -> dict[str, Any]:
        # 签名接口有独立的配额窗口。这里不做长等待：调用方按 Retry-After 记录冷却并
        # 立刻返回，本轮不再逐个试探，等窗口过去由下一轮同步补数。
        return self.request(
            "GET",
            "/api/v3/history/download-session",
            params=params,
            retry_after_cap_seconds=_DOWNLOAD_SESSION_RETRY_CAP_SECONDS,
        )


@lru_cache(maxsize=1)
def get_xiaoshi_client() -> XiaoshiClient:
    """Return the process-wide client so the QPS budget is shared by all callers."""
    from app.core.config import get_settings

    settings = get_settings()
    return XiaoshiClient(
        base_url=settings.xiaoshi_api_base_url,
        api_key=settings.xiaoshi_api_key,
        max_qps=settings.xiaoshi_max_qps,
        timeout_seconds=settings.xiaoshi_timeout_seconds,
        max_attempts=settings.xiaoshi_max_attempts,
    )


def clear_xiaoshi_client() -> None:
    """Close and clear the cached client after configuration changes or tests."""
    if get_xiaoshi_client.cache_info().currsize:
        get_xiaoshi_client().close()
    get_xiaoshi_client.cache_clear()
