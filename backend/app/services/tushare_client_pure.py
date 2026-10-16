"""Pure helpers extracted from app.services.tushare_client (no monkeypatched names; re-exported there)."""
from __future__ import annotations
import functools
import hashlib
import json
import logging
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Any
import pandas as pd
from sqlalchemy import select
from app.adapters.market_source.dataframe_contracts import MarketFrameContractError, validate_market_frame
from app.core.config import get_settings
from app.engine.trading_calendar import normalize_trade_date as _normalize_trade_date
from app.services.tushare_transport import AuthMode, FailureCategory, HttpMethod, ProviderConfig, ProviderFailure, TushareHttpProvider, TushareProviderError, TushareProviderRouter
import logging

logger = logging.getLogger('app.services.tushare_client')


class TushareRawEnvelopeError(RuntimeError):
    """A successful HTTP response did not satisfy the raw Tushare envelope contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_ENABLED_OVERRIDE_TTL_SECONDS = 5.0


_ENABLED_OVERRIDE_CACHE: dict[str, Any] = {"value": {"enabled": {}, "priority": {}}, "expires_at": 0.0}


def _trace_session_factory():
    from app.db.session import SessionLocal

    return SessionLocal()


_trace_correlation: dict[str, Any] = {"run_id": None}


def set_trace_correlation_id(correlation_id: str | None) -> None:
    """由 run/任务上下文注入 correlation id，使同一批数据源请求可关联。"""
    _trace_correlation["run_id"] = correlation_id


def _trace_correlation_id() -> str:
    import uuid

    return _trace_correlation["run_id"] or f"ts-{uuid.uuid4().hex[:16]}"


def _provider_api_roles(provider_name: str) -> list[str]:
    """该 provider 在各 API 路由链中的角色，用于面板展示「这个源承接哪些数据」。

    返回该源参与的 API（primary 在前，其余按字母序），格式 "api:角色"。
    通用同步 API（daily/daily_basic/stk_factor/cyq_perf 等未显式列链的）按 priority
    兜底，所有 pro 源都参与，故对每个 pro 源都标注 "通用同步"。
    """
    routes = TushareProviderRouter.DEFAULT_API_ROUTES
    roles: list[str] = []
    for api_name, chain in routes.items():
        if provider_name not in chain:
            continue
        position = chain.index(provider_name)
        if position == 0:
            roles.append(f"{api_name}:主源")
        else:
            roles.append(f"{api_name}:备用{position}")
    # 通用同步（未显式列链的 pro API，按 priority 兜底）：所有 pro 源都参与
    roles.insert(0, "通用同步(daily/stk_factor/cyq_perf等)")
    return roles


def _compact_trade_date(value: Any) -> str:
    return _normalize_trade_date(value).replace("-", "")


_BAOSTOCK_MIN_INTERVAL_S = 3.5


_BAOSTOCK_MAX_RETRIES = 2


_baostock_lock = threading.Lock()


# 本模块 freq 字符串 → baostock frequency 参数
_BAOSTOCK_FREQ_MAP = {"5min": "5", "15min": "15", "30min": "30", "60min": "60"}


def _baostock_to_bs_code(code: str, *, is_index: bool = False) -> str:
    """6位代码 → baostock 格式（sh.600000 / sz.000001）。指数 399→sz 其余→sh。"""
    bare = str(code or "").split(".")[0]
    if is_index:
        return f"sz.{bare}" if bare.startswith("399") else f"sh.{bare}"
    return f"sh.{bare}" if bare.startswith(("6", "9")) else f"sz.{bare}"


_REALTIME_MINUTE_FREQS = frozenset({"1MIN", "5MIN", "15MIN", "30MIN", "60MIN"})


_STK_MINS_MAX_ROWS = 8000


_STK_MINS_WINDOW_DAYS = 20


def _normalize_realtime_minute_freq(freq: str | int) -> str:
    normalized = str(freq).strip().upper()
    if normalized.isdigit():
        normalized = f"{normalized}MIN"
    if normalized not in _REALTIME_MINUTE_FREQS:
        supported = ", ".join(sorted(_REALTIME_MINUTE_FREQS))
        raise ValueError(f"实时分钟频率仅支持: {supported}")
    return normalized


def _persist_baostock_trace(
    code: str,
    freq: str,
    *,
    status: str,
    duration_ms: int,
    failure_category: str | None = None,
    fallback_from: str | None = None,
) -> None:
    """把 baostock 分钟 K 主备尝试投影到统一 trace（R-SOURCE-2/3）。

    baostock 不经 Tushare router，这里在其 transport 边界构造同一契约。
    持久化失败不影响取数（telemetry 降级）。
    """
    try:
        import hashlib
        import time as _time

        from app.services.data_source_trace import (
            TRACE_CONTRACT_ID,
            TRACE_CONTRACT_VERSION,
            DataSourceAttempt,
            DataSourceAttemptOutcome,
            DataSourceRequestTrace,
            FinalOutcome,
            request_identity_hash,
        )
        from app.services.data_source_trace_service import MySQLTraceSink

        outcome_map = {
            "success": (DataSourceAttemptOutcome.SUCCESS, FinalOutcome.SUCCESS),
            "empty": (DataSourceAttemptOutcome.VALID_EMPTY, FinalOutcome.VALID_EMPTY),
        }
        attempt_outcome, final_outcome = outcome_map.get(
            status, (DataSourceAttemptOutcome.FAILURE, FinalOutcome.FAILURE)
        )
        correlation_id = _trace_correlation_id()
        req_hash = request_identity_hash(
            "minute_kbar", code=code, params={"freq": freq, "source": "baostock"}
        )
        trace_id = "dstr_" + hashlib.sha256(
            f"{correlation_id}|{req_hash}|bs|{_time.monotonic_ns()}".encode("utf-8")
        ).hexdigest()[:48]
        attempt = DataSourceAttempt(
            ordinal=1,
            provider="baostock",
            outcome=attempt_outcome,
            duration_ms=max(0, int(duration_ms)),
            failure_category=failure_category,
            retryable=False if attempt_outcome is DataSourceAttemptOutcome.FAILURE else None,
            fallback_allowed=None,
            fallback_from=fallback_from,
        )
        trace = DataSourceRequestTrace(
            trace_id=trace_id,
            correlation_id=correlation_id,
            operation_name="minute_kbar",
            request_identity_hash=req_hash,
            contract_id=TRACE_CONTRACT_ID,
            contract_version=TRACE_CONTRACT_VERSION,
            attempts=(attempt,),
            selected_provider="baostock" if final_outcome is not FinalOutcome.FAILURE else None,
            final_outcome=final_outcome,
        )
        MySQLTraceSink(_trace_session_factory).persist(trace)
    except Exception:
        logger.debug("baostock minute trace persist failed code=%s", code, exc_info=True)


def _record_minute_source_trace(
    trace: list[dict[str, Any]] | None,
    *,
    source: str,
    code: str,
    status: str,
    row_count: int,
    reason: str | None = None,
) -> None:
    if trace is None:
        return
    trace.append(
        {
            "source": source,
            "code": code,
            "status": status,
            "row_count": int(row_count or 0),
            "reason": (reason or "")[:160],
        }
    )


def _is_tushare_permission_error(exc: BaseException) -> bool:
    """判断异常是否为 Tushare 权限拒绝。

    优先用结构化证据：Router 聚合的 ``TushareProviderError`` 会在
    ``failures[*].category`` 里携带 ``FailureCategory.PERMISSION``。直接
    读 category 而非对 ``str(exc)`` 做子串匹配，避免异常文本格式变化时
    （provider 名/category 值改名）判断静默失效。
    字符串匹配仅作兜底，兼容旧式中文 ``RuntimeError`` 假对象/调用方。
    """
    if isinstance(exc, TushareProviderError):
        return any(
            failure.category is FailureCategory.PERMISSION
            for failure in exc.failures
        )
    reason = str(exc)
    normalized = reason.lower()
    return (
        "没有该接口权限" in reason
        or "接口权限" in reason
        or "permission" in normalized
    )


def _attach_pre_close(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    ordered = sorted(rows, key=lambda item: str(item["trade_time"]))
    grouped_dates = sorted({str(row["trade_date"]) for row in ordered})
    prev_close_map: dict[str, float] = {}
    for index, trade_date in enumerate(grouped_dates):
        if index == 0:
            first_row = next((row for row in ordered if row["trade_date"] == trade_date), None)
            prev_close_map[trade_date] = _f((first_row or {}).get("close"))
            continue
        previous_date = grouped_dates[index - 1]
        last_close = next(
            (row["close"] for row in reversed(ordered) if row["trade_date"] == previous_date),
            0.0,
        )
        prev_close_map[trade_date] = _f(last_close)
    return [
        {
            **row,
            "pre_close": round(prev_close_map.get(str(row["trade_date"]), _f(row.get("close"))), 3),
        }
        for row in ordered
    ]


def _safe_float(val: Any) -> float | None:
    """安全转 float，NaN/空值保留为 None。"""
    import math
    if val is None or val == "":
        return None
    try:
        v = float(val)
        return None if math.isnan(v) else v
    except (ValueError, TypeError):
        return None


def _f(val: Any, default: float = 0.0) -> float:
    """安全转 float，NaN → 0.0。"""
    import math
    if val is None:
        return default
    try:
        v = float(val)
        return default if math.isnan(v) else v
    except (ValueError, TypeError):
        return default


def _fmt_date(val: str) -> str:
    """统一日期格式 → YYYY-MM-DD。"""
    v = val.strip().replace("-", "")
    if len(v) == 8:
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    return val
