"""promax 限流窗口：熔断冷却时长 + research 补数的按窗口令牌桶（2026-10-19 实测）。

实测依据（data_source_attempts × data_source_request_traces，10-12~10-19，642 次 promax http_rate_limit，
上游标识全部是 5xx `upstream_pool_exhausted`，没有任何一次带 Retry-After / 等待时间）：
- 不是按窗口计数的配额：本机负载越高报错率越低（每分钟 ≤10 次时 8.5%，40–90 次 0.3%，>90 次 0.09%）；
  同一时段别的接口照常成功（多数限流片段内部就有成功请求）；rt_k 三次报错前 5 分钟本机 promax 请求为 0。
  滚动 60 分钟最高 4028 次、5 分钟最高 579 次也没有触发成片失败。=> 是代理侧共享连接池瞬时耗尽。
- 恢复时间：报错后任一接口下一次成功中位 ~20s(P25)/88s(P50，受本机 60s 冷却影响)；
  同接口在 60s 冷却后下一次请求成功率 stk_surv 86% / dividend 86% / forecast 100% / income 100%，
  fina_indicator 27%、share_float 0%（这两个是 invalid_params / 无权限类伴随失败）。
所以冷却默认 60s（rt_k 15s），上游给了等待时间就以上游为准；连续失败按 60→120→300s 退避。
令牌桶只给 research（脚本/补数）用，作为保险上限，取实测安全量的约 60%。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---- 冷却 ----
DEFAULT_COOLDOWN_SECONDS = 60.0
API_COOLDOWN_SECONDS: dict[str, float] = {"rt_k": 15.0}
BACKOFF_STEPS = (1.0, 2.0, 5.0)  # 连续限流片段：60 -> 120 -> 300
BACKOFF_RESET_SECONDS = 600.0
MIN_COOLDOWN, MAX_COOLDOWN = 1.0, 1800.0
_WAIT_FIELDS = ("retry_after", "retry_after_seconds", "retryAfter", "wait", "wait_seconds", "reset_in", "reset_after")

_streak: dict[str, tuple[int, float]] = {}
_streak_lock = threading.Lock()


def upstream_wait_seconds(response: Any) -> float | None:
    """上游给的等待时间：Retry-After 头（秒）优先，其次 JSON 体里的 retry_after/wait 等字段。"""
    try:
        headers = getattr(response, "headers", None) or {}
        raw = headers.get("Retry-After") if hasattr(headers, "get") else None
        if raw not in (None, ""):
            return min(MAX_COOLDOWN, max(MIN_COOLDOWN, float(raw)))
    except (TypeError, ValueError):
        pass
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        try:
            payload = json.loads(getattr(response, "text", "") or "")
        except Exception:  # noqa: BLE001
            return None
    if isinstance(payload, dict):
        for key in _WAIT_FIELDS:
            val = payload.get(key)
            try:
                if val is not None and float(val) > 0:
                    return min(MAX_COOLDOWN, max(MIN_COOLDOWN, float(val)))
            except (TypeError, ValueError):
                continue
    return None


def cooldown_seconds(api_name: str, upstream_wait: float | None = None, *, now: float | None = None) -> float:
    """promax 限流后的冷却：上游等待时间优先；否则按实测窗口 + 连续片段退避。"""
    now = time.monotonic() if now is None else now
    with _streak_lock:
        n, last = _streak.get(api_name, (0, 0.0))
        n = n + 1 if last and now - last < BACKOFF_RESET_SECONDS else 1
        _streak[api_name] = (n, now)
    if upstream_wait and upstream_wait > 0:
        return min(MAX_COOLDOWN, max(MIN_COOLDOWN, float(upstream_wait)))
    base = API_COOLDOWN_SECONDS.get(api_name, DEFAULT_COOLDOWN_SECONDS)
    return min(MAX_COOLDOWN, base * BACKOFF_STEPS[min(n, len(BACKOFF_STEPS)) - 1])


def reset_streak(api_name: str) -> None:
    with _streak_lock:
        _streak.pop(api_name, None)


# ---- research 令牌桶（按窗口）----
# (窗口秒, 盘外上限, 盘中上限)。实测 5 分钟 579 / 60 分钟 4028 次无成片失败。
DEFAULT_WINDOWS: tuple[tuple[float, int, int], ...] = ((60.0, 160, 160), (3600.0, 9600, 9600))
# 2026-11-15 实测重标定（旧值 ((300,300,100),(3600,2400,900))，盘中仅 20/min，比上游低约 10 倍）：
# - 上游真实硬窗由响应头给出且只有 IP 维度：`X-Ratelimit-Ip-Limit: 200`（每分钟），
#   12 线程并发在第 4.36s、累计 200 次成功时被拒，返回
#   `{"ok":false,"error":"ip_rate_limited","message":"当前IP请求频率超过限制，请在 56 秒后重试"}`。
# - 串行 133/min 跑 130 次全绿、IP-Remaining 从 190 递减到 126，证明 200/min 窗口是硬顶且按分钟回补。
# => 生效值改为 60s 窗 160 次（对 200 硬顶留 20% 余量）；1h 窗放到 9600（=160/min，只作上位保险，
#    不让它成为比分钟窗更紧的约束）。旧「5min 100 次」结论来自代理侧连接池耗尽（upstream_pool_exhausted），
#    与 IP 计数限频是两件事，不再用连接池结论压缩额度。
# 向后兼容旧引用；**实际生效值走 window_table()**（可用 env 覆盖，见下）。
WINDOWS: tuple[tuple[float, int, int], ...] = DEFAULT_WINDOWS
RT_K_LIVE_KEY = "live:tushare:promax:rt_k"
RT_K_MAX_YIELD_SECONDS = 120.0
_limiters: dict[tuple[float, bool], Any] = {}
_lim_lock = threading.Lock()
_table_cache: list[Any] = []


def window_table() -> tuple[tuple[float, int, int], ...]:
    """research 补数的真实硬窗口 ``((window_s, 非盘中限额, 盘中限额), ...)``。

    2026-11-15：从常量改为可覆盖（``ASHARE_PROMAX_WINDOW_TABLE`` = JSON，如
    ``[[300,300,100],[3600,2400,900]]``）。原因：config 里的
    ``tushare_promax_rate_limit_per_minute=180`` 是"每分钟"口径，而生效的是
    "5 分钟 / 1 小时"两个滚动窗口（盘中 100 次/5min = 20/min、900 次/1h = 15/min），
    两者差约 9 倍，读配置的人会高估可用额度。默认值不变（沿用实测结论），
    只是让"想对齐上游新口径"时不必改代码。
    """
    if _table_cache:
        return _table_cache[0]
    table = DEFAULT_WINDOWS
    try:
        from app.core.runtime_env import env_value

        raw = env_value("ASHARE_PROMAX_WINDOW_TABLE", "TUSHARE_PROMAX_WINDOW_TABLE")
        if raw:
            parsed = json.loads(raw)
            rows = []
            for item in parsed:
                window, off_limit, in_limit = (float(item[0]), int(item[1]), int(item[2]))
                if window > 0 and off_limit > 0 and in_limit > 0:
                    rows.append((window, off_limit, in_limit))
            if rows:
                table = tuple(rows)
    except Exception:  # noqa: BLE001 - 配错就用默认，限流本身不能因此失效
        table = DEFAULT_WINDOWS
    _table_cache.append(table)
    return table


def reset_window_table_cache() -> None:
    _table_cache.clear()


def _limiter(window: float, limit: int, intraday: bool):
    key = (window, intraday)
    with _lim_lock:
        lim = _limiters.get(key)
        if lim is None:
            from app.core import limiter_registry
            from app.services.tushare_transport import SlidingWindowLimiter

            local = SlidingWindowLimiter(limit, window_seconds=window)
            lim = local
            path = _shared_path()
            registry_key = f"promax:research:{'intraday' if intraday else 'offhours'}:{int(window)}"
            if path:
                from app.services.shared_rate_limiter import SharedSlidingWindowLimiter

                lim = SharedSlidingWindowLimiter(
                    registry_key, limit, path, window_seconds=window, fallback=local,
                )
            limiter_registry.register(registry_key, lim, limit, window_seconds=window, kind="promax_window")
            _limiters[key] = lim
        return lim


def _shared_path() -> str | None:
    # 与网关限流器共用同一份 shared_limiter_db_path 解析（相对路径按 backend 根目录归一），
    # 否则 promax 窗口会在 worker 的 CWD 下另建一个库，"共享"变成两份独立配额。
    try:
        from app.services.tushare_transport import shared_limiter_db_path

        raw = shared_limiter_db_path()
        if raw:
            return raw
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.core import metrics

        if metrics._process_role() == "pytest":
            return None
        p = metrics._store_path()
        return str(p.parent / "promax_window.sqlite") if p else None
    except Exception:  # noqa: BLE001
        return None


def _rt_k_live_open_until() -> float:
    try:
        from app.core.shared_breaker import shared_open_until

        return float(shared_open_until(RT_K_LIVE_KEY) or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def research_acquire(api_name: str, *, intraday: bool, sleep=time.sleep) -> None:
    """research 进程每次打 promax 前调用：盘中先让路给 live rt_k，再按窗口取令牌。"""
    if intraday and api_name != "rt_k":
        until = _rt_k_live_open_until()
        wait = min(RT_K_MAX_YIELD_SECONDS, until - time.time())
        if wait > 0:
            logger.info("promax research yields to live rt_k for %.0fs", wait)
            sleep(wait)
    for window, off_limit, in_limit in window_table():
        _limiter(window, in_limit if intraday else off_limit, intraday).acquire()


def in_trading_session(now: datetime | None = None) -> bool:
    from app.services.tushare_transport import _in_research_window

    return _in_research_window(now)
