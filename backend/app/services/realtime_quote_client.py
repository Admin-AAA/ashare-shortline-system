"""批量实时报价（dataset ``quote.realtime``）。

统一走数据网关：promax rt_k → quantdata rt_min → xiaoshi 快照（遵守管理面启停开关）。
（旧的 token 爬虫报价客户端已删除。）

    from app.services.realtime_quote_client import fetch_realtime_quotes
    quotes = fetch_realtime_quotes(["000011", "603893"])
    # -> {"000011": {"price": 8.11, "high": 9.09, ..., "source": "tushare_rt_k"}, ...}
"""

from __future__ import annotations

import threading
import time
from typing import Any

# 进程内短缓存：盘中高频刷新时按 TTL 复用整批结果，避免打爆上游。
CACHE_TTL_SECONDS = 20.0
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"fetched_at": 0.0, "rows": {}}


def _codes6(codes: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for c in codes:
        code6 = str(c).split(".")[0].strip()
        if len(code6) != 6 or not code6.isdigit() or code6 in seen:
            continue
        seen.add(code6)
        out.append(code6)
    return out


def fetch_realtime_quotes(codes: list[str], *, use_cache: bool = True) -> dict[str, dict[str, Any]]:
    """返回 {code6: quote}（schema 见 gateway.SCHEMAS['quote.realtime']）；全部失败返回 {}。"""
    from app.services.datasource import quote_realtime

    wanted = _codes6(codes)
    if not wanted:
        return {}
    if use_cache:
        with _cache_lock:
            age = time.time() - _cache["fetched_at"]
            if _cache["rows"] and age < CACHE_TTL_SECONDS and set(wanted) <= set(_cache["rows"]):
                return {c: _cache["rows"][c] for c in wanted}
    rows = quote_realtime.fetch(wanted)
    if rows:
        with _cache_lock:
            _cache["fetched_at"] = time.time()
            _cache["rows"] = rows
    return rows
