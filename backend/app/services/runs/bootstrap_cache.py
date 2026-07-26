"""驾驶舱首页 / 初始化状态的 stale-while-revalidate 缓存原语。

任务 7.4（需求 6.1）落地：把 `workbench_service.py` 里散落的 `_bootstrap_cache` /
`_initial_state_cache` 两组缓存字典 + 锁 + `invalidate_bootstrap_cache` 收口到本
模块，作为纯缓存原语（无业务域依赖），供 `workbench_service.bootstrap_workbench_state`
/ `get_initial_workbench_state`（编排）与 `registry/*`（skill/preset CRUD，写操作后
需要失效缓存）共用，避免 workbench_service <-> registry 之间因 `invalidate_bootstrap_cache`
产生循环 import。
"""
from __future__ import annotations

import copy
import threading
import time
from typing import Any

CacheKey = tuple[str, ...]

_bootstrap_cache_lock = threading.Lock()
_bootstrap_cache: dict[CacheKey, dict[str, Any]] = {}
_initial_state_cache_lock = threading.Lock()
_initial_state_cache: dict[CacheKey, dict[str, Any]] = {}


def get_bootstrap_cache(cache_key: CacheKey, ttl_seconds: float) -> dict[str, Any] | None:
    """命中且未过期则返回深拷贝，避免调用方修改缓存内部状态。"""
    now = time.monotonic()
    with _bootstrap_cache_lock:
        cached = _bootstrap_cache.get(cache_key)
        if cached and (now - float(cached["ts"])) < ttl_seconds:
            return copy.deepcopy(cached["data"])
    return None


def set_bootstrap_cache(cache_key: CacheKey, data: dict[str, Any]) -> None:
    with _bootstrap_cache_lock:
        _bootstrap_cache[cache_key] = {"ts": time.monotonic(), "data": copy.deepcopy(data)}


def get_initial_state_cache(cache_key: CacheKey, ttl_seconds: float) -> dict[str, Any] | None:
    now = time.monotonic()
    with _initial_state_cache_lock:
        cached = _initial_state_cache.get(cache_key)
        if cached and (now - float(cached["ts"])) < ttl_seconds:
            return copy.deepcopy(cached["data"])
    return None


def set_initial_state_cache(cache_key: CacheKey, data: dict[str, Any]) -> None:
    with _initial_state_cache_lock:
        _initial_state_cache[cache_key] = {"ts": time.monotonic(), "data": copy.deepcopy(data)}


def invalidate_bootstrap_cache() -> None:
    with _bootstrap_cache_lock:
        _bootstrap_cache.clear()
    with _initial_state_cache_lock:
        _initial_state_cache.clear()
