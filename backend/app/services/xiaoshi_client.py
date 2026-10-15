"""Authenticated Xiaoshi API client with a process-local QPS guard.

The client deliberately owns only the HTTP boundary.  Dataset-specific
normalization belongs to ``xiaoshi_market_source`` so the rest of the system
does not depend on Xiaoshi response details.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
import tenacity

from app.services.tushare_transport import SlidingWindowLimiter


class XiaoshiError(RuntimeError):
    """A sanitized Xiaoshi request or response failure."""

    def __init__(self, message: str, *, status_code: int | None = None, path: str = "") -> None:
        self.status_code = status_code
        self.path = path
        super().__init__(message)


class XiaoshiConfigurationError(XiaoshiError):
    """The Xiaoshi client has no usable API key or endpoint configuration."""


class _RetryableHTTPStatus(XiaoshiError):
    """429/5xx response; retried with Retry-After (clamped) or linear backoff."""

    def __init__(self, message: str, *, status_code: int, path: str, retry_after: str) -> None:
        super().__init__(message, status_code=status_code, path=path)
        self.retry_after = retry_after


class _FinalXiaoshiError(XiaoshiError):
    """Non-retryable 4xx response."""


def _xiaoshi_wait(state: tenacity.RetryCallState) -> float:
    linear = 0.2 * state.attempt_number
    exc = state.outcome.exception() if state.outcome else None
    if isinstance(exc, _RetryableHTTPStatus):
        try:
            return max(0.2, min(5.0, float(exc.retry_after)))
        except (TypeError, ValueError):
            return linear
    return linear


@dataclass(frozen=True)
class XiaoshiSkillArtifact:
    """Pinned, integrity-checked copy of the upstream quant Skill."""

    version: str
    sha256: str
    content: str


class _QPSLimiter(SlidingWindowLimiter):
    """Per-second sliding window; every retry also consumes capacity."""

    def __init__(self, max_qps: int) -> None:
        if int(max_qps) < 1:
            raise ValueError("max_qps must be positive")
        super().__init__(int(max_qps), window_seconds=1.0, pad_seconds=0.0, min_wait_seconds=0.001)


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

        url = f"{self.base_url}{normalized_path}"

        def _attempt() -> httpx.Response:
            self._limiter.acquire()
            try:
                response = self._client.request(
                    method,
                    url,
                    params=dict(params or {}),
                    json=dict(json or {}) if json is not None else None,
                    headers=headers,
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                raise XiaoshiError(
                    f"Xiaoshi 请求超时: {normalized_path}", path=normalized_path
                ) from exc
            except httpx.HTTPError as exc:
                raise XiaoshiError(
                    f"Xiaoshi 网络请求失败: {normalized_path}", path=normalized_path
                ) from exc
            status = int(response.status_code)
            if status == 429 or status >= 500:
                raise _RetryableHTTPStatus(
                    f"Xiaoshi HTTP {status}: {normalized_path}",
                    status_code=status,
                    path=normalized_path,
                    retry_after=response.headers.get("Retry-After", ""),
                )
            if status >= 400:
                raise _FinalXiaoshiError(
                    f"Xiaoshi HTTP {status}: {normalized_path}",
                    status_code=status,
                    path=normalized_path,
                )
            return response

        retrying = tenacity.Retrying(
            stop=tenacity.stop_after_attempt(self.max_attempts),
            retry=tenacity.retry_if_exception(
                lambda exc: isinstance(exc, XiaoshiError) and not isinstance(exc, _FinalXiaoshiError)
            ),
            wait=_xiaoshi_wait,
            sleep=lambda seconds: time.sleep(seconds),
            reraise=True,
        )
        try:
            return retrying(_attempt)
        except _RetryableHTTPStatus as exc:
            raise XiaoshiError(str(exc), status_code=exc.status_code, path=exc.path) from None
        except _FinalXiaoshiError as exc:
            raise XiaoshiError(str(exc), status_code=exc.status_code, path=exc.path) from None

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        cache_control: str | None = None,
    ) -> dict[str, Any]:
        response = self._request_response(
            method,
            path,
            params=params,
            json=json,
            cache_control=cache_control,
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

    def request_text(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        cache_control: str | None = None,
    ) -> str:
        response = self._request_response(
            "GET",
            path,
            params=params,
            cache_control=cache_control,
            accept="text/markdown, text/plain",
        )
        return response.text

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

    def get_financials(
        self, code: str, *, statement_type: str = "all", periods: int = 8
    ) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/api/v3/data/financials/{code}",
            params={"type": statement_type, "periods": max(1, int(periods))},
        )

    def get_quant_catalog(self) -> dict[str, Any]:
        return self.request("GET", "/api/v3/quant-data/catalog")

    def get_quant_data(self, dataset: str, **params: Any) -> dict[str, Any]:
        if not dataset or not all(char.isalnum() or char in "-_" for char in dataset):
            raise ValueError("dataset must be a simple quant-data identifier")
        return self.request("GET", f"/api/v3/quant-data/{dataset}", params=params)

    def get_history_manifest(self, **params: Any) -> dict[str, Any]:
        """历史交付契约元数据：datasets 声明、coverage、schemas、download_contracts。

        不含具体文件条目（那在 `get_history_catalog`）。小石 v3 的日线/分钟历史只按
        清单 + R2 下载契约批量交付：平台在 `/api/v3/meta/data-contract` 声明
        `history.manifest` / `history.download`，并声明
        `stability.legacy_aliases = false`——旧的 `/api/v3/data/kline/{code}`
        逐股票端点已随 `/api/v3/data/*` 整族退役且不保留别名（L-149）。
        """
        return self.request("GET", "/api/v3/history/manifest", params=params or None)

    def get_history_catalog(self, **params: Any) -> dict[str, Any]:
        """历史文件条目清单（items + total），可按 dataset/product/market/adjust 过滤。"""
        return self.request("GET", "/api/v3/history/catalog", params=params or None)

    def get_history_download_session(self, **params: Any) -> dict[str, Any]:
        return self.request("GET", "/api/v3/history/download-session", params=params)

    def get_quant_skill(
        self,
        *,
        version: str,
        expected_sha256: str,
    ) -> XiaoshiSkillArtifact:
        content = self.request_text(
            "/api/v3/skills/xiaoshi-quant-expert/SKILL.md",
            params={"v": version},
            cache_control="no-store, no-cache",
        )
        actual_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual_sha256 != expected_sha256.strip().lower():
            raise XiaoshiError(
                "Xiaoshi 量化 Skill 完整性校验失败",
                path="/api/v3/skills/xiaoshi-quant-expert/SKILL.md",
            )
        return XiaoshiSkillArtifact(
            version=version,
            sha256=actual_sha256,
            content=content,
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
