"""限流器注册表：把进程内已创建的限流器登记下来，供 ``/api/data-sources/limits`` 只读展示。

问题背景：限流器是"用到才建、建了就藏在 provider 实例里"的懒对象（``adapter.ProviderBase``、
``promax_window._limiter``、``ths_client``），配额视图无法反查"当前实际生效的窗口用量"。
这里登记 ``key → limiter`` 的弱引用，视图侧只读调用 ``usage()`` / ``headroom()``，
**绝不 acquire、绝不阻塞、绝不抛出**（观测代码不允许影响控制流）。

注意：注册表只覆盖**当前进程**已创建的限流器。跨进程真实用量以共享 SQLite 的 ``hits`` 表为准，
视图会把两者合并（见 ``services/datasource/quota_view``）。
"""
from __future__ import annotations

import threading
import weakref
from typing import Any

_lock = threading.Lock()
# key -> {"ref": weakref, "requests_per_minute": int, "window_seconds": float, "kind": str}
_registry: dict[str, dict[str, Any]] = {}


def register(key: str, limiter: Any, requests_per_minute: int, *, window_seconds: float = 60.0,
             kind: str = "gateway") -> None:
    """登记一个限流器（幂等：同 key 重复注册以最后一次为准）。"""
    try:
        with _lock:
            _registry[str(key)] = {
                "ref": weakref.ref(limiter) if _weakrefable(limiter) else (lambda _o=limiter: _o),
                "requests_per_minute": int(requests_per_minute),
                "window_seconds": float(window_seconds),
                "kind": kind,
            }
    except Exception:  # noqa: BLE001 - 观测登记绝不能影响限流本身
        return


def _weakrefable(obj: Any) -> bool:
    try:
        weakref.ref(obj)
        return True
    except TypeError:
        return False


def registered_keys() -> list[str]:
    with _lock:
        return sorted(_registry)


def describe() -> list[dict[str, Any]]:
    """已登记限流器的声明信息（不含用量，调用方无需 limiter 存活）。"""
    now = []
    with _lock:
        items = list(_registry.items())
    for key, meta in items:
        now.append({"key": key, "requests_per_minute": meta["requests_per_minute"],
                    "window_seconds": meta["window_seconds"], "kind": meta["kind"],
                    "limit": meta["requests_per_minute"]})
    return now


def usage() -> list[dict[str, Any]]:
    """已登记限流器的**实时**用量（只读）。limiter 已被回收则 used 为 None。"""
    out: list[dict[str, Any]] = []
    with _lock:
        items = list(_registry.items())
    for key, meta in items:
        limiter = meta["ref"]()
        used = limit = limit_rpm = window = None
        try:
            if limiter is not None and hasattr(limiter, "usage"):
                used, limit, window = limiter.usage()
            elif limiter is not None and hasattr(limiter, "headroom"):
                hr = limiter.headroom()
                if hr is not None:
                    limit = meta["requests_per_minute"]
                    used = int(round((1.0 - float(hr)) * limit)) if limit else None
                    window = meta["window_seconds"]
        except Exception:  # noqa: BLE001
            used = None
        limit_rpm = meta["requests_per_minute"]
        out.append({
            "key": key,
            "kind": meta["kind"],
            "requests_per_minute": limit_rpm,
            "window_seconds": window if window is not None else meta["window_seconds"],
            "used": used,
            "limit": limit if limit is not None else limit_rpm,
            "alive": limiter is not None,
        })
    out.sort(key=lambda i: i["key"])
    return out


def reset() -> None:
    """测试用。"""
    with _lock:
        _registry.clear()
