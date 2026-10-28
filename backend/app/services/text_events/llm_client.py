"""文本标签的模型调用。

复用 model_route_config 的路由、进程内熔断和 api_call_logs 成本记录。
温度固定为 0，不读路由上的 temperature。默认开关关闭时，调用方不应进入这里。
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Any, Callable

from app.agents.circuit_breaker import check_circuit, record_result
from app.services.model_router_service import OPENAI_CLIENT_HEADERS, get_active_model_routes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Completion:
    content: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: float


class QpmLimiter:
    """按固定间隔放行，间隔 = 60/qpm。多线程共用一把锁。"""

    def __init__(self, qpm: int) -> None:
        self.interval = 60.0 / max(1, int(qpm))
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now if now < self._next else 0.0
            self._next = (self._next if now < self._next else now) + self.interval
        if delay > 0:
            time.sleep(delay)


class GatewayCompleter:
    def __init__(self, settings: Any, cost_recorder: Callable[..., None] | None = None) -> None:
        self.settings = settings
        self.cost_recorder = cost_recorder or _record_cost
        self.limiter = QpmLimiter(int(getattr(settings, "text_event_llm_qpm", 20) or 20))

    def complete(self, *, system: str, user: str) -> Completion:
        routes = [route for route in get_active_model_routes() if route.valid]
        if not routes:
            raise RuntimeError("没有可用的模型路由（model_route_config 缺少 key、base_url 或 model）")
        errors: list[str] = []
        retries = max(0, int(getattr(self.settings, "text_event_llm_max_retries", 2) or 0))
        for route in routes:
            try:
                check_circuit(route.circuit_key)
            except RuntimeError as exc:
                errors.append(str(exc)[:180])
                continue
            attempt = 0
            while True:
                attempt += 1
                self.limiter.wait()
                started = time.perf_counter()
                try:
                    completion = _chat(route, system, user, self.settings)
                except Exception as exc:  # noqa: BLE001 - 换路由或重试
                    record_result(route.circuit_key, False, _error_type(exc))
                    self._cost(route, system + user, "", started, "error", str(exc)[:200], None, None)
                    errors.append(f"{route.model}: {exc}"[:180])
                    if attempt > retries or _switch_route(exc):
                        break
                    continue
                record_result(route.circuit_key, True)
                self._cost(
                    route, system + user, completion.content, started, "success", None,
                    completion.prompt_tokens, completion.completion_tokens,
                )
                return completion
        raise RuntimeError("文本标签模型调用失败：" + " | ".join(errors)[:500])

    def _cost(self, route: Any, system: str, content: str, started: float, status: str,
              error: str | None, prompt_tokens: int | None, completion_tokens: int | None) -> None:
        try:
            self.cost_recorder(
                model=route.model,
                endpoint=f"{str(route.base_url).rstrip('/')} [{route.route_id}]",
                prompt_chars=len(system),
                completion_chars=len(content or ""),
                latency_ms=(time.perf_counter() - started) * 1000,
                status=status,
                error_message=error,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except Exception as exc:  # noqa: BLE001 - 记账失败不挡住标签
            logger.warning("text event cost log failed: %s", exc)


def _chat(route: Any, system: str, user: str, settings: Any) -> Completion:
    from openai import OpenAI

    timeout = float(route.timeout_seconds or getattr(settings, "text_event_llm_timeout_seconds", 60.0) or 60.0)
    timeout = max(5.0, min(timeout, 120.0))
    client = OpenAI(
        api_key=route.api_key,
        base_url=route.base_url,
        timeout=timeout,
        default_headers=OPENAI_CLIENT_HEADERS,
    )
    started = time.perf_counter()
    response = _create(client, route.model, system, user, with_json=True)
    usage = getattr(response, "usage", None)
    content = str(response.choices[0].message.content or "")
    return Completion(
        content=content,
        model=route.model,
        prompt_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
        completion_tokens=getattr(usage, "completion_tokens", None) if usage else None,
        latency_ms=(time.perf_counter() - started) * 1000,
    )


def _create(client: Any, model: str, system: str, user: str, *, with_json: bool) -> Any:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 800,
    }
    if with_json:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        return client.chat.completions.create(**kwargs)
    except Exception as exc:
        if with_json and "response_format" in str(exc).lower():
            kwargs.pop("response_format", None)
            return client.chat.completions.create(**kwargs)
        raise


def _error_type(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status == 402 or "insufficient balance" in text or "quota exceeded" in text:
        return "insufficient_balance"
    if status in {401, 403} or "invalid api key" in text or "authentication" in text:
        return "auth_error"
    if status == 404 or "model not found" in text:
        return "model_unavailable"
    if status == 429 or "rate limit" in text:
        return "rate_limited"
    return "error"


def _switch_route(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in {401, 402, 403, 404, 429, 502, 503, 504}:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in ("invalid api key", "insufficient balance", "model not found", "断路器"))


def _record_cost(**kwargs: Any) -> None:
    from app.db.session import SessionLocal
    from app.services.cost_service import record_api_call

    with SessionLocal() as db:
        record_api_call(
            db,
            skill_id="text_event_label",
            model=kwargs.get("model") or "unknown",
            endpoint=kwargs.get("endpoint") or "",
            prompt_chars=int(kwargs.get("prompt_chars") or 0),
            completion_chars=int(kwargs.get("completion_chars") or 0),
            latency_ms=float(kwargs.get("latency_ms") or 0),
            status=kwargs.get("status") or "success",
            error_message=kwargs.get("error_message"),
            prompt_tokens=kwargs.get("prompt_tokens"),
            completion_tokens=kwargs.get("completion_tokens"),
        )
