"""Tushare 实时报价爬虫客户端（realtime_quote）。

走 ``realtime.stockai888.top``，独立于 ai-tool/fastapic 的 pro HTTP 协议
（``TushareHttpProvider`` 那套 JSON POST 不适用这里——realtime_quote 是
tushare SDK 内置的多源爬虫函数）。多代码批量、含现价/最高/最低/昨收/五档盘口，
用于未结算推荐的盘中实时浮盈。

使用方式：
    from app.services.realtime_quote_client import fetch_realtime_quotes
    quotes = fetch_realtime_quotes(["000011", "603893"])
    # -> {"000011": {"price": 8.11, "high": 9.09, ...}, ...}

token 复用 fastapic token（同一 Tushare 账号）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import pandas as pd

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 进程内短缓存：realtime_quote 每次调用都会做一次 token 校验 + 网络往返，
# 盘中高频刷新时按 TTL 复用结果，避免打爆上游与重复鉴权。
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"fetched_at": 0.0, "rows": {}}


def _configure_realtime_session() -> None:
    """把 tushare SDK 的 token 与爬虫端点指向 realtime.stockai888.top。

    realtime_quote 内部会先 ``verify_token``（打 ``ct.verify_token_url``）再爬数据，
    两处都要指向 realtime 代理，否则鉴权/数据都落到 tushare 官方（无权限/超时）。
    """
    import tushare as ts
    from tushare.stock import cons as ct

    settings = get_settings()
    token = settings.tushare_fastapic_token.strip()
    if not token:
        raise RuntimeError("实时报价需要 tushare_fastapic_token（复用同一 Tushare 账号）")
    ts.set_token(token)
    ct.verify_token_url = settings.tushare_realtime_quote_url


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_rows(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """把 realtime_quote 的 DataFrame 归一化成 {code6: quote}。

    返回 key 用 6 位纯代码（与 Recommendation.code / DailyKbar.code 对齐），
    值里保留 ts_code 便于回溯。数值列统一转 float，缺失补 0。
    """
    rows: dict[str, dict[str, Any]] = {}
    if df is None or df.empty:
        return rows
    for _, r in df.iterrows():
        ts_code_str = str(r.get("TS_CODE") or "").strip()
        if not ts_code_str:
            continue
        code6 = ts_code_str.split(".")[0]
        quote_time = str(r.get("TIME") or "").strip()
        trade_date_raw = str(r.get("DATE") or "").strip()
        # DATE 形如 20260727；归一化成 2026-07-27
        trade_date = (
            f"{trade_date_raw[0:4]}-{trade_date_raw[4:6]}-{trade_date_raw[6:8]}"
            if len(trade_date_raw) == 8 and trade_date_raw.isdigit()
            else trade_date_raw
        )
        rows[code6] = {
            "code": code6,
            "ts_code": ts_code_str,
            "name": str(r.get("NAME") or "").strip(),
            "trade_date": trade_date,
            "quote_time": quote_time,
            "price": _f(r.get("PRICE")),
            "open": _f(r.get("OPEN")),
            "pre_close": _f(r.get("PRE_CLOSE")),
            "high": _f(r.get("HIGH")),
            "low": _f(r.get("LOW")),
            "volume": _f(r.get("VOLUME")),
            "amount": _f(r.get("AMOUNT")),
            "source": "realtime_quote",
        }
    return rows


def fetch_realtime_quotes(codes: list[str], *, use_cache: bool = True) -> dict[str, dict[str, Any]]:
    """批量拉实时报价，返回 {code6: quote}。

    - 多代码一次调用（realtime_quote 支持逗号分隔 ts_code 列表）。
    - 超过 batch_size 自动分片，逐片调用后合并。
    - TTL 缓存：``use_cache=True`` 且距上次成功拉取 < ttl 时直接复用全量结果
      （缓存以「整批」为粒度，因为 realtime_quote 本身就是批量接口）。
    - 上游失败/超时 → 返回 {}，不抛异常（调用方据此降级显示）。
    """
    import tushare as ts

    settings = get_settings()
    ttl = max(0.0, float(settings.tushare_realtime_quote_ttl_seconds))
    batch_size = max(1, int(settings.tushare_realtime_quote_batch_size))

    if use_cache and ttl > 0:
        with _cache_lock:
            age = time.time() - _cache["fetched_at"]
            if _cache["rows"] and age < ttl:
                wanted = {c for c in codes}
                return {c: q for c, q in _cache["rows"].items() if c in wanted}

    # 归一化成 ts_code 列表（去重、保序）
    from app.services.tushare_client import ts_code as _to_ts_code

    seen: set[str] = set()
    ts_codes: list[str] = []
    for c in codes:
        code6 = str(c).split(".")[0].strip()
        if len(code6) != 6 or not code6.isdigit() or code6 in seen:
            continue
        seen.add(code6)
        ts_codes.append(_to_ts_code(code6))
    if not ts_codes:
        return {}

    try:
        _configure_realtime_session()
    except RuntimeError as exc:
        logger.warning("realtime_quote 配置失败: %s", exc)
        return {}

    frames: list[pd.DataFrame] = []
    for i in range(0, len(ts_codes), batch_size):
        chunk = ts_codes[i : i + batch_size]
        try:
            df = ts.realtime_quote(ts_code=",".join(chunk))
            if df is not None and not df.empty:
                frames.append(df)
        except Exception as exc:  # 上游 504/超时/权限等——降级为空，单片失败不影响其他片
            logger.warning("realtime_quote 拉取失败（%d 只）: %s", len(chunk), str(exc)[:160])
            continue

    merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    rows = _normalize_rows(merged)

    if rows:
        with _cache_lock:
            _cache["fetched_at"] = time.time()
            _cache["rows"] = rows
    return rows


def get_realtime_quote_health() -> dict[str, Any]:
    """向数据源健康面板汇报 realtime_quote 的进程内状态。"""
    settings = get_settings()
    enabled = bool(settings.tushare_fastapic_token.strip())
    with _cache_lock:
        fetched_at = _cache["fetched_at"]
        row_count = len(_cache["rows"])
    last_success_at = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(fetched_at)) if fetched_at else None
    )
    return {
        "source": "realtime-quote",
        "provider_name": "realtime_quote",
        "source_type": "tushare_provider",
        "layer": "provider",
        "enabled": enabled,
        "configured": enabled,
        "priority": 2,
        "note": "实时报价爬虫（realtime.stockai888.top）；未结算推荐盘中浮盈",
        "success_count": 0,
        "failure_count": 0,
        "empty_count": 0,
        "success_rate": None,
        "latency_ms": None,
        "last_success_at": last_success_at,
        "last_failure_at": None,
        "last_error": None,
        "last_api": "realtime_quote",
        "status": ("healthy" if (enabled and row_count) else ("untested" if enabled else "disabled")),
        "updated_at": last_success_at,
        "auth_mode": "JSON_TOKEN",
        "rate_limit_per_minute": 0,
        "api_rate_limits": {},
        "max_concurrency": 0,
        "breaker_state": "closed" if enabled else "disabled",
        "open_circuits": [],
        "health_scope": "process_runtime",
    }
