"""
Tushare 稳定数据源客户端。

使用方式：
    from backend.app.services.tushare_client import get_pro
    pro = get_pro()
    df = pro.daily(ts_code="000001.SZ", limit=5)

上游 provider：promax / quantdata（见 _build_provider_router 与 datasource/routing_table.json）。
兼容 Tushare pro_api HTTP 协议（POST JSON: api_name/token/params/fields）。
"""

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
from app.services.tushare_transport import (
    AuthMode,
    FailureCategory,
    HttpMethod,
    ProviderConfig,
    ProviderFailure,
    TushareHttpProvider,
    TushareProviderError,
    TushareProviderRouter,
)
from app.services.tushare_client_pure import (  # noqa: F401,E402  re-export (split)
    TushareRawEnvelopeError,
    _BAOSTOCK_FREQ_MAP,
    _BAOSTOCK_MAX_RETRIES,
    _BAOSTOCK_MIN_INTERVAL_S,
    _ENABLED_OVERRIDE_CACHE,
    _ENABLED_OVERRIDE_TTL_SECONDS,
    _REALTIME_MINUTE_FREQS,
    _STK_MINS_MAX_ROWS,
    _STK_MINS_WINDOW_DAYS,
    _attach_pre_close,
    _baostock_lock,
    _baostock_to_bs_code,
    _compact_trade_date,
    _f,
    _fmt_date,
    _is_tushare_permission_error,
    _normalize_realtime_minute_freq,
    _persist_baostock_trace,
    _provider_api_roles,
    _record_minute_source_trace,
    _safe_float,
    _trace_correlation,
    _trace_correlation_id,
    _trace_session_factory,
    set_trace_correlation_id,
)

logger = logging.getLogger(__name__)


def _promax_minute_contract_issue(envelope: Mapping[str, Any]) -> str | None:
    """Return why a Promax ``stk_mins`` response is not canonical minute K."""
    data = envelope.get("data")
    if not isinstance(data, Mapping):
        return "malformed_envelope"
    fields = data.get("fields")
    items = data.get("items")
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes, bytearray)):
        return "malformed_envelope"
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return "malformed_envelope"
    if not items:
        return None
    try:
        trade_time_index = list(fields).index("trade_time")
    except ValueError:
        return "missing_trade_time"

    seen: set[str] = set()
    for item in items:
        if (
            not isinstance(item, Sequence)
            or isinstance(item, (str, bytes, bytearray))
            or trade_time_index >= len(item)
        ):
            return "malformed_envelope"
        raw_trade_time = str(item[trade_time_index] or "").strip()
        try:
            parsed = datetime.fromisoformat(raw_trade_time)
        except ValueError:
            return "invalid_trade_time"
        if parsed.second != 0 or parsed.microsecond != 0:
            return "non_minute_timestamp"
        session_time = parsed.strftime("%H:%M")
        if not (
            "09:30" <= session_time <= "11:30"
            or "13:00" <= session_time <= "15:00"
        ):
            return "outside_continuous_session"
        minute_key = parsed.strftime("%Y-%m-%d %H:%M")
        if minute_key in seen:
            return "duplicate_minute"
        seen.add(minute_key)
    return None


class _PromaxHttpProvider(TushareHttpProvider):
    """Promax transport with a strict gate for its asynchronously built minute data."""

    def _request_once(
        self,
        api_name: str,
        *,
        params: Mapping[str, Any],
        fields: str,
    ) -> Mapping[str, Any]:
        envelope = super()._request_once(api_name, params=params, fields=fields)
        if api_name != "stk_mins":
            return envelope
        issue = _promax_minute_contract_issue(envelope)
        if issue is None:
            return envelope
        logger.warning("promax stk_mins response rejected by minute contract: %s", issue)
        raise ProviderFailure(
            provider=self.name,
            api_name=api_name,
            category=FailureCategory.MALFORMED_RESPONSE,
            retryable=False,
            fallback_allowed=True,
        )


# ═════════════════════════════════════════════════════════════════════════════
# Canonical client contract. Provider authentication, limiting, retry, and
# circuit breaking live in ``tushare_transport``; the local MySQL pool remains
# the recommendation query plane.
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class TushareRawEnvelope:
    """Lossless successful Tushare envelope projected to schema + item mappings.

    Request credentials and response message/detail text are intentionally excluded.  The
    complete response field order and every item value are retained before any DataFrame
    construction or legacy normalization.  Tushare-compatible successful empty replies
    may omit ``data.fields`` (or provide an empty field sequence); non-empty replies
    still require an explicit, complete field list for lossless row capture.
    """

    api_name: str
    requested_fields: str
    envelope_keys: tuple[str, ...]
    data_keys: tuple[str, ...]
    fields: tuple[str, ...]
    items: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        if not self.api_name.strip():
            raise TushareRawEnvelopeError("tushare_raw_invalid_api", "raw API name must not be blank")
        if any(not key.strip() for key in (*self.envelope_keys, *self.data_keys, *self.fields)):
            raise TushareRawEnvelopeError(
                "tushare_raw_invalid_envelope", "raw envelope keys and fields must not be blank"
            )
        if len(self.fields) != len(set(self.fields)):
            raise TushareRawEnvelopeError(
                "tushare_raw_invalid_envelope", "raw response fields must be unique"
            )
        expected_fields = set(self.fields)
        if any(set(item) != expected_fields for item in self.items):
            raise TushareRawEnvelopeError(
                "tushare_raw_invalid_envelope", "raw response items must match the full field set"
            )

    @property
    def field_fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_envelope(
        cls,
        *,
        api_name: str,
        requested_fields: str,
        envelope: Mapping[str, Any],
    ) -> "TushareRawEnvelope":
        if not isinstance(envelope, Mapping):
            raise TushareRawEnvelopeError(
                "tushare_raw_invalid_envelope", "raw provider envelope must be a mapping"
            )
        data = envelope.get("data")
        if not isinstance(data, Mapping):
            raise TushareRawEnvelopeError(
                "tushare_raw_invalid_envelope", "raw provider envelope must contain data mapping"
            )
        raw_fields = data.get("fields")
        raw_items = data.get("items")
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes, bytearray)):
            raise TushareRawEnvelopeError(
                "tushare_raw_invalid_envelope", "raw provider data.items must be an item sequence"
            )

        # Tushare returns successful empty result sets in both forms observed in
        # compatible transports: ``items=[]`` with no ``fields`` key, and
        # ``items=[]`` with ``fields=[]``.  An omitted/empty schema is safe only
        # when there are no rows: otherwise values cannot be captured losslessly.
        if raw_fields is None:
            if raw_items:
                raise TushareRawEnvelopeError(
                    "tushare_raw_invalid_envelope",
                    "non-empty raw provider items require an explicit field sequence",
                )
            fields: tuple[str, ...] = ()
        else:
            if (
                not isinstance(raw_fields, Sequence)
                or isinstance(raw_fields, (str, bytes, bytearray))
                or not all(isinstance(field, str) and field.strip() for field in raw_fields)
            ):
                raise TushareRawEnvelopeError(
                    "tushare_raw_invalid_envelope", "raw provider data.fields must be a field sequence"
                )
            fields = tuple(raw_fields)
            if not fields and raw_items:
                raise TushareRawEnvelopeError(
                    "tushare_raw_invalid_envelope",
                    "non-empty raw provider items require at least one declared field",
                )
        if len(fields) != len(set(fields)):
            raise TushareRawEnvelopeError(
                "tushare_raw_invalid_envelope", "raw provider data.fields must be unique"
            )
        items: list[Mapping[str, Any]] = []
        for raw_item in raw_items:
            if (
                not isinstance(raw_item, Sequence)
                or isinstance(raw_item, (str, bytes, bytearray))
                or len(raw_item) != len(fields)
            ):
                raise TushareRawEnvelopeError(
                    "tushare_raw_invalid_envelope",
                    "raw provider item does not match the declared field list",
                )
            items.append(MappingProxyType(dict(zip(fields, raw_item, strict=True))))
        return cls(
            api_name=api_name,
            requested_fields=requested_fields,
            envelope_keys=tuple(str(key) for key in envelope),
            data_keys=tuple(str(key) for key in data),
            fields=fields,
            items=tuple(items),
        )


class _TushareSession:
    """Tushare ``pro_api`` compatible facade over the provider router."""

    def __init__(self, router: TushareProviderRouter, *, http_url: str = "") -> None:
        self._router = router
        # Tushare ``pro_bar`` inspects this private SDK attribute.  The actual
        # transport remains routed and does not read it.
        self._DataApi__http_url = http_url

    def close(self) -> None:
        self._router.close()

    def provider_health_snapshot(self) -> list[dict[str, Any]]:
        return self._router.health_snapshot()

    def fetch_raw(
        self,
        api_name: str,
        *,
        params: Mapping[str, Any] | None = None,
        fields: str = "",
    ) -> TushareRawEnvelope:
        """Fetch one successful raw envelope without altering legacy callers."""
        if not api_name.strip() or api_name.startswith("_"):
            raise TushareRawEnvelopeError("tushare_raw_invalid_api", "raw API name is invalid")
        if not isinstance(fields, str):
            raise TushareRawEnvelopeError(
                "tushare_raw_invalid_request", "raw fields request must be a string"
            )
        envelope = self._router.request(
            api_name,
            params=dict(params or {}),
            fields=fields,
        )
        return TushareRawEnvelope.from_envelope(
            api_name=api_name,
            requested_fields=fields,
            envelope=envelope,
        )

    def _call(self, api_name: str, *, fields: str = "", params: Mapping[str, Any]) -> pd.DataFrame:
        envelope = self._router.request(api_name, params=params, fields=fields)
        data = envelope.get("data")
        if not data:
            return pd.DataFrame()
        raw = TushareRawEnvelope.from_envelope(
            api_name=api_name,
            requested_fields=fields,
            envelope=envelope,
        )
        if not raw.items:
            return pd.DataFrame(columns=list(raw.fields))
        return pd.DataFrame.from_records(raw.items, columns=list(raw.fields))

    def query(self, api_name: str, fields: str = "", **params: Any) -> pd.DataFrame:
        """Compatibility with ``tushare.pro_bar(api=...)`` query calls."""
        if not api_name or api_name.startswith("_"):
            raise AttributeError(api_name)
        return self._call(api_name, fields=fields or "", params=params)

    def __getattr__(self, api_name: str):
        if api_name.startswith("_"):
            raise AttributeError(api_name)

        def caller(**params: Any) -> pd.DataFrame:
            request_params = dict(params)
            fields = request_params.pop("fields", "")
            if not isinstance(fields, str):
                raise TushareRawEnvelopeError(
                    "tushare_raw_invalid_request", "fields request must be a string"
                )
            return self._call(api_name, fields=fields, params=request_params)

        return caller


def _build_provider_router() -> tuple[TushareProviderRouter, str]:
    settings = get_settings()
    timeout = (
        max(0.1, float(settings.tushare_connect_timeout_seconds)),
        max(0.1, float(settings.tushare_read_timeout_seconds)),
    )
    common = {
        "timeout": timeout,
        "max_attempts": max(1, int(settings.tushare_provider_max_attempts)),
        "failure_threshold": max(1, int(settings.tushare_provider_failure_threshold)),
        "recovery_seconds": max(0.1, float(settings.tushare_provider_recovery_seconds)),
    }
    providers: list[TushareHttpProvider] = []

    promax_api_key = settings.tushare_promax_api_key.strip()
    if promax_api_key:
        providers.append(
            _PromaxHttpProvider(
                ProviderConfig(
                    name="promax",
                    base_url=settings.tushare_promax_url,
                    credential=promax_api_key,
                    auth_mode=AuthMode.API_KEY_HEADER,
                    http_method=HttpMethod.GET,
                    rate_limit_per_minute=max(
                        0, int(settings.tushare_promax_rate_limit_per_minute)
                    ),
                    max_concurrency=max(0, int(settings.tushare_promax_max_concurrency)),
                    append_api_path=True,
                    # 各 API 可用性/顺序由 datasource/routing_table.json 决定（实测能力矩阵生成）。
                    source="promax",
                    priority=4,
                    **common,
                )
            )
        )

    quantdata_token = settings.tushare_quantdata_token.strip()
    if quantdata_token:
        providers.append(
            TushareHttpProvider(
                ProviderConfig(
                    name="quantdata",
                    base_url=settings.tushare_quantdata_url,
                    credential=quantdata_token,
                    auth_mode=AuthMode.JSON_TOKEN,
                    rate_limit_per_minute=max(
                        0, int(settings.tushare_quantdata_rate_limit_per_minute)
                    ),
                    append_api_path=False,
                    priority=1,
                    **common,
                )
            )
        )

    if not providers:
        raise RuntimeError(
            "Tushare 上游未配置：请至少设置一个 Provider 凭据。"
        )
    providers, api_routes = _apply_routing_table(providers)
    return (
        TushareProviderRouter(
            providers,
            api_routes=api_routes,
            api_tiers=_routing_tiers(),
            enabled_override=_provider_enabled_override,
            priority_override=_provider_priority_override,
        ),
        settings.tushare_promax_url,
    )


def _routing_table():
    from app.services.datasource.capability import load_routing_table

    return load_routing_table()


def _routing_tiers() -> dict[str, tuple[tuple[str, ...], ...]]:
    from app.services.datasource.capability import resolve_tiers

    return resolve_tiers(_routing_table(), enabled=bool(get_settings().tushare_load_balancing))


def _apply_routing_table(
    providers: list[TushareHttpProvider],
) -> tuple[list[TushareHttpProvider], dict[str, tuple[str, ...]]]:
    """Apply the data-driven routing table (capability matrix -> per-API order).

    The table's ``unsupported`` entries become each provider's excluded_apis; its
    ``order`` becomes the per-API chain on top of DEFAULT_API_ROUTES.  Providers not
    in the table (new backup sources) still participate, appended by priority.
    """
    from dataclasses import replace

    from app.services.datasource.capability import load_routing_table, resolve_routes

    routes, excluded = resolve_routes(_routing_table(), TushareProviderRouter.DEFAULT_API_ROUTES)
    out: list[TushareHttpProvider] = []
    for provider in providers:
        extra = excluded.get(provider.name)
        if extra:
            provider.config = replace(
                provider.config, excluded_apis=provider.config.excluded_apis | extra
            )
        out.append(provider)
    return out, routes


def _provider_enabled_override() -> dict[str, bool]:
    """从 data_source_config 读 provider 运行时启停覆盖（管理面 Tab 写入）。

    返回 {provider_name: enabled}；查不到/出错返回 {}（视为全部启用）。
    独立 Session 查询，避免污染调用方事务；进程内短缓存减少每次选路都查库。
    """
    cached = _read_provider_override_cache()
    return dict(cached["enabled"])


def _provider_priority_override() -> dict[str, int]:
    """从 data_source_config 读 provider 路由优先级覆盖（管理面 Tab 拖拽写入）。

    返回 {provider_name: priority}；查不到/出错返回 {}（用代码默认 priority）。
    与 enabled 同一份 DB 行、同一份进程内缓存。
    """
    cached = _read_provider_override_cache()
    return dict(cached["priority"])


def _read_provider_override_cache() -> dict[str, dict]:
    """读取 provider 运行时覆盖（enabled + priority），进程内短缓存。

    缓存结构 {"enabled": {name: bool}, "priority": {name: int}}，5s TTL。
    """
    now = time.monotonic()
    cached = _ENABLED_OVERRIDE_CACHE
    if cached["expires_at"] > now:
        return cached["value"]
    value: dict[str, dict] = {"enabled": {}, "priority": {}}
    try:
        from app.db.session import SessionLocal
        from app.models.tables import DataSourceConfig

        db = SessionLocal()
        try:
            rows = db.scalars(
                select(DataSourceConfig).where(
                    DataSourceConfig.source_type == "tushare_provider"
                )
            ).all()
            value["enabled"] = {str(r.source): bool(r.enabled) for r in rows}
            value["priority"] = {str(r.source): int(r.priority) for r in rows}
        finally:
            db.close()
    except Exception:
        logger.debug("provider override query failed", exc_info=True)
        value = {"enabled": {}, "priority": {}}
    _ENABLED_OVERRIDE_CACHE["value"] = value
    _ENABLED_OVERRIDE_CACHE["expires_at"] = now + _ENABLED_OVERRIDE_TTL_SECONDS
    return value


def invalidate_provider_enabled_override() -> None:
    """启停/调序操作后调用，立即清空覆盖缓存，下一次选路即生效。"""
    _ENABLED_OVERRIDE_CACHE["expires_at"] = 0.0


@functools.lru_cache(maxsize=1)
def get_pro() -> _TushareSession:
    """Return the cached ingestion/realtime provider facade.

    注入统一 trace sink（R-SOURCE-3）：router 选路循环内单点持久化，
    业务调用方不重复埋点。sink 惰性构造 session，持久化失败不影响取数。
    """
    from app.services.data_source_trace_service import MySQLTraceSink

    router, http_url = _build_provider_router()
    sink = MySQLTraceSink(_trace_session_factory)
    router._trace_sink = sink
    router._correlation_id_factory = _trace_correlation_id
    return _TushareSession(router, http_url=http_url)


def clear_pro_cache() -> None:
    """Close provider sessions and clear cached routing/breaker state."""
    if get_pro.cache_info().currsize:
        try:
            get_pro().close()
        except Exception:
            logger.debug("closing cached Tushare providers failed", exc_info=True)
    get_pro.cache_clear()


def get_tushare_provider_health() -> list[dict[str, Any]]:
    """Project configured Providers and process-local telemetry to the health API."""
    settings = get_settings()
    # configured = config 有 token（已接入）；enabled = 运行时实际生效（configured AND DB 启停覆盖）。
    # 管理面 Tab 的「禁用」只改 DB override，不动 config，实现不重启启停。
    overrides = _provider_enabled_override()
    priority_overrides = _provider_priority_override()
    specs = (
        {
            "name": "promax",
            "source": "promax",
            "configured": bool(settings.tushare_promax_api_key.strip()),
            "priority": 4,
            "note": "Promax GET 聚合源；实时行情与历史分钟主源；历史分钟仅接受标准分钟响应，202/异常快照自动 fallback",
            "auth_mode": AuthMode.API_KEY_HEADER.value,
            "rate_limit_per_minute": max(
                0, int(settings.tushare_promax_rate_limit_per_minute)
            ),
            "api_rate_limits": {},
            "max_concurrency": max(0, int(settings.tushare_promax_max_concurrency)),
        },
        {
            "name": "quantdata",
            "source": "quantdata",
            "configured": bool(settings.tushare_quantdata_token.strip()),
            "priority": 1,
            "note": "第三方 pro 协议源；日线/基本面/筹码/资金流全量高吞吐（anns_d/rt_k/stk_mins 无权限走链 fallback）",
            "auth_mode": AuthMode.JSON_TOKEN.value,
            "rate_limit_per_minute": max(
                0, int(settings.tushare_quantdata_rate_limit_per_minute)
            ),
            "api_rate_limits": {},
            "max_concurrency": 0,
        },
    )

    snapshots: dict[str, dict[str, Any]] = {}
    initialization_failed = False
    if any(bool(spec["configured"]) for spec in specs):
        try:
            snapshots = {
                str(item.get("name")): item
                for item in get_pro().provider_health_snapshot()
            }
        except Exception:
            initialization_failed = True
            logger.warning("tushare provider health initialization failed", exc_info=True)

    rows: list[dict[str, Any]] = []
    for spec in specs:
        configured = bool(spec["configured"])
        if not configured:
            continue
        enabled = configured and bool(overrides.get(spec["name"], True))
        # 面板 priority 显示实际生效值（DB 覆盖优先于代码默认），与路由一致。
        effective_priority = int(priority_overrides.get(spec["name"], spec["priority"]))
        snapshot = snapshots.get(str(spec["name"]), {})
        status = str(snapshot.get("status") or ("untested" if configured else "disabled"))
        failure_count = int(snapshot.get("failure_count") or 0)
        last_error = snapshot.get("last_error")
        if not enabled and configured:
            status = "disabled"
        elif configured and initialization_failed and not snapshot:
            status = "degraded"
            failure_count = max(1, failure_count)
            last_error = "provider initialization failed (configuration)"
        rows.append(
            {
                "source": spec["source"],
                "provider_name": spec["name"],
                "source_type": "tushare_provider",
                "layer": "provider",
                "enabled": enabled,
                "configured": configured,
                "priority": effective_priority,
                "note": spec["note"],
                "apis": _provider_api_roles(spec["name"]),
                "success_count": int(snapshot.get("success_count") or 0),
                "failure_count": failure_count,
                "empty_count": int(snapshot.get("empty_count") or 0),
                "success_rate": snapshot.get("success_rate"),
                "latency_ms": snapshot.get("latency_ms"),
                "last_success_at": snapshot.get("last_success_at"),
                "last_failure_at": snapshot.get("last_failure_at"),
                "last_error": last_error,
                "last_api": snapshot.get("last_api"),
                "status": status if enabled else "disabled",
                "updated_at": snapshot.get("updated_at"),
                "auth_mode": snapshot.get("auth_mode") or spec["auth_mode"],
                "rate_limit_per_minute": snapshot.get("rate_limit_per_minute")
                if snapshot
                else spec["rate_limit_per_minute"],
                "api_rate_limits": snapshot.get("api_rate_limits")
                if snapshot
                else spec["api_rate_limits"],
                "max_concurrency": snapshot.get("max_concurrency")
                if snapshot
                else spec["max_concurrency"],
                "breaker_state": snapshot.get("breaker_state")
                or ("closed" if enabled else "disabled"),
                "open_circuits": snapshot.get("open_circuits") or [],
                "health_scope": "process_runtime",
            }
        )
    return rows


def ts_code(code: str) -> str:
    """纯数字代码 → Tushare ts_code 格式。"""
    if "." in code:
        return code
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


# ═════════════════════════════════════════════════════════════════════════════
# K线 + 技术指标 + 筹码（一次拉取，合并返回）
# ═════════════════════════════════════════════════════════════════════════════


def fetch_daily_kbars(
    code: str,
    days: int = 90,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """
    获取一只股票的完整日K数据（OHLCV + 技术指标 + 筹码分布）。

    合并 daily + daily_basic + stk_factor + cyq_perf 四个接口。
    """
    pro = get_pro()
    tsc = ts_code(code)
    sd = start_date or (date.today() - timedelta(days=days * 3)).strftime("%Y%m%d")
    ed = end_date or date.today().strftime("%Y%m%d")

    # 1. daily — OHLCV
    daily = pro.daily(ts_code=tsc, start_date=sd, end_date=ed)
    if daily is None or daily.empty:
        return []
    validate_market_frame("daily", daily)
    daily = daily.sort_values("trade_date")
    if start_date is None:
        daily = daily.tail(days)
    daily_rows = {str(r["trade_date"]): r for _, r in daily.iterrows()}

    # 2. stk_factor — MACD/KDJ/RSI/BOLL/CCI + adj_factor
    factor_map: dict[str, dict] = {}
    try:
        factor = pro.stk_factor(ts_code=tsc, start_date=sd, end_date=ed)
        if factor is not None and not factor.empty:
            validate_market_frame("stk_factor", factor)
            for _, r in factor.iterrows():
                factor_map[str(r["trade_date"])] = dict(r)
    except MarketFrameContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
        logger.warning("fetch_daily_kbars: ignored non-fatal error: %s", exc)

    # 3. daily_basic — turnover_rate, pe_ttm, total_mv
    basic_map: dict[str, dict] = {}
    try:
        basic = pro.daily_basic(ts_code=tsc, start_date=sd, end_date=ed)
        if basic is not None and not basic.empty:
            validate_market_frame("daily_basic", basic)
            for _, r in basic.iterrows():
                basic_map[str(r["trade_date"])] = dict(r)
    except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
        logger.warning("fetch_daily_kbars: ignored non-fatal error: %s", exc)

    # 4. cyq_perf — 筹码分布
    chip_map: dict[str, dict] = {}
    try:
        chips = pro.cyq_perf(ts_code=tsc, start_date=sd, end_date=ed)
        if chips is not None and not chips.empty:
            validate_market_frame("cyq_perf", chips)
            for _, r in chips.iterrows():
                chip_map[str(r["trade_date"])] = dict(r)
    except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
        logger.warning("fetch_daily_kbars: ignored non-fatal error: %s", exc)

    # 5. stk_factor_pro — 扩展因子（ASI/ATR/BBI/DPO/OBV/PSY/VR/WR 等）
    factor_pro_map: dict[str, dict] = {}
    try:
        fp = pro.stk_factor_pro(ts_code=tsc, start_date=sd, end_date=ed)
        if fp is not None and not fp.empty:
            validate_market_frame("stk_factor_pro", fp)
            for _, r in fp.iterrows():
                factor_pro_map[str(r["trade_date"])] = dict(r)
    except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
        logger.warning("fetch_daily_kbars: ignored non-fatal error: %s", exc)

    bars: list[dict[str, Any]] = []
    for td, row in daily_rows.items():
        fac = factor_map.get(td, {})
        bas = basic_map.get(td, {})
        chp = chip_map.get(td, {})
        fpr = factor_pro_map.get(td, {})

        bar: dict[str, Any] = {
            "code": code,
            "trade_date": _fmt_date(str(row.get("trade_date", td))),
            "open": _f(row.get("open")),
            "high": _f(row.get("high")),
            "low": _f(row.get("low")),
            "close": _f(row.get("close")),
            "volume": _f(row.get("vol")),
            "amount": _f(row.get("amount")) * 1000,  # 千元 → 元
            "pct_chg": _f(row.get("pct_chg")),
            "turnover_rate": _f(bas.get("turnover_rate")) or _f(fac.get("turnover_rate")),
            "pe_ttm": _f(bas.get("pe_ttm")),
            "total_mv_yi": _f(bas.get("total_mv")) / 1e8 if bas.get("total_mv") is not None else 0.0,
            # 技术指标 (from stk_factor)
            "macd_dif": _f(fac.get("macd_dif")),
            "macd_dea": _f(fac.get("macd_dea")),
            "macd": _f(fac.get("macd")),
            "kdj_k": _f(fac.get("kdj_k")),
            "kdj_d": _f(fac.get("kdj_d")),
            "kdj_j": _f(fac.get("kdj_j")),
            "rsi_6": _f(fac.get("rsi_6")),
            "rsi_12": _f(fac.get("rsi_12")),
            "rsi_24": _f(fac.get("rsi_24")),
            "boll_upper": _f(fac.get("boll_upper")),
            "boll_mid": _f(fac.get("boll_mid")),
            "boll_lower": _f(fac.get("boll_lower")),
            "cci": _f(fac.get("cci")),
            # 扩展因子 (from stk_factor_pro)
            "adj_factor": _f(fpr.get("adj_factor")),
            "asi": _f(fpr.get("asi_bfq")),
            "asit": _f(fpr.get("asit_bfq")),
            "atr": _f(fpr.get("atr_bfq")),
            "bbi": _f(fpr.get("bbi_bfq")),
            "brar_ar": _f(fpr.get("brar_ar_bfq")),
            "brar_br": _f(fpr.get("brar_br_bfq")),
            "dpo": _f(fpr.get("dpo_bfq")),
            "ema_12": _f(fpr.get("ema_12_bfq")),
            "ema_50": _f(fpr.get("ema_50_bfq")),
            "ma_volume_5": _f(fpr.get("ma_volume_5_bfq")),
            "ma_volume_10": _f(fpr.get("ma_volume_10_bfq")),
            "mtm": _f(fpr.get("mtm_bfq")),
            "obv": _f(fpr.get("obv_bfq")),
            "psy": _f(fpr.get("psy_bfq")),
            "trix": _f(fpr.get("trix_bfq")),
            "vr": _f(fpr.get("vr_bfq")),
            "wr_6": _f(fpr.get("wr_6_bfq")),
            "wr_10": _f(fpr.get("wr_10_bfq")),
            # 筹码分布 (from cyq_perf)
            "chip_cost_15": _f(chp.get("cost_15pct")),
            "chip_cost_50": _f(chp.get("cost_50pct")),
            "chip_cost_85": _f(chp.get("cost_85pct")),
            "chip_weight_avg": _f(chp.get("weight_avg")),
            "chip_winner_rate": _f(chp.get("winner_rate")),
            # 来源标记
            "source": "tushare",
        }
        bars.append(bar)
    return bars


def fetch_basic_daily_kbars(
    code: str,
    days: int = 90,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """获取一只股票的基础日K，用于交互式快速补齐本地池历史缺口。"""
    pro = get_pro()
    tsc = ts_code(code)
    sd = start_date or (date.today() - timedelta(days=days * 3)).strftime("%Y%m%d")
    ed = end_date or date.today().strftime("%Y%m%d")

    daily = pro.daily(ts_code=tsc, start_date=sd, end_date=ed)
    if daily is None or daily.empty:
        return []
    daily = daily.sort_values("trade_date")
    if start_date is None:
        daily = daily.tail(days)

    bars: list[dict[str, Any]] = []
    for _, row in daily.iterrows():
        bars.append(
            {
                "code": code,
                "trade_date": _fmt_date(str(row.get("trade_date") or "")),
                "open": _f(row.get("open")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "close": _f(row.get("close")),
                "volume": _f(row.get("vol")),
                "amount": _f(row.get("amount")) * 1000,
                "pct_chg": _f(row.get("pct_chg")),
                "turnover_rate": 0.0,
                "source": "tushare",
            }
        )
    return bars


def fetch_daily_kbars_for_trade_date(trade_date: str) -> list[dict[str, Any]]:
    """按单个交易日批量获取全市场日K，用于数据缺口修复。"""
    target = _compact_trade_date(trade_date)
    if not target:
        return []

    pro = get_pro()
    daily = pro.daily(trade_date=target)
    if daily is None or daily.empty:
        return []
    validate_market_frame("daily", daily)

    basic_map: dict[str, dict[str, Any]] = {}
    try:
        basic = pro.daily_basic(
            trade_date=target,
            fields="ts_code,trade_date,turnover_rate,pe_ttm,total_mv",
        )
        if basic is not None and not basic.empty:
            validate_market_frame("daily_basic", basic)
            for _, row in basic.iterrows():
                basic_map[str(row.get("ts_code") or "")] = dict(row)
    except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
        logger.warning("fetch_daily_kbars_for_trade_date: ignored non-fatal error: %s", exc)

    # 2026-10-19：17:35 盘后写入时 stk_factor 常未发布 → factor_map 为空 → adj_factor 被 _f 写成 0.0
    # （214 个交易日全市场 adj_factor=0）。adj_factor 改从专用 adj_factor 接口补（盘前即发布），
    # 两者取不到时写 NULL 而非 0。
    adj_map: dict[str, float] = {}
    try:
        adj = pro.adj_factor(trade_date=target)
        if adj is not None and not adj.empty:
            for _, row in adj.iterrows():
                value = _f(row.get("adj_factor"))
                if value > 0:
                    adj_map[str(row.get("ts_code") or "")] = value
    except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
        logger.warning("fetch_daily_kbars_for_trade_date: adj_factor fetch failed: %s", exc)

    factor_map: dict[str, dict[str, Any]] = {}
    try:
        factor = pro.stk_factor(trade_date=target)
        if factor is not None and not factor.empty:
            validate_market_frame("stk_factor", factor)
            for _, row in factor.iterrows():
                factor_map[str(row.get("ts_code") or "")] = dict(row)
    except MarketFrameContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
        logger.warning("fetch_daily_kbars_for_trade_date: ignored non-fatal error: %s", exc)

    # 筹码分布（cyq_perf）——按 trade_date 批量一次拉全市场。此前批量补数只拉
    # daily/daily_basic/stk_factor，导致盘后写入的 DailyKbar 行 chip_cost_50/15/85
    # 永远为 0，数据健康页把「筹码峰」域误判为长期 stale。逐股票路径
    # (fetch_daily_kbars) 含 cyq_perf，但盘后批量路径遗漏，这里补齐。
    chip_map: dict[str, dict[str, Any]] = {}
    try:
        chips = pro.cyq_perf(
            trade_date=target,
            fields="ts_code,trade_date,cost_15pct,cost_50pct,cost_85pct,weight_avg,winner_rate",
        )
        if chips is not None and not chips.empty:
            validate_market_frame("cyq_perf", chips)
            for _, row in chips.iterrows():
                chip_map[str(row.get("ts_code") or "")] = dict(row)
    except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
        logger.warning("fetch_daily_kbars_for_trade_date: ignored non-fatal error: %s", exc)

    bars: list[dict[str, Any]] = []
    for _, row in daily.iterrows():
        raw_ts_code = str(row.get("ts_code") or "")
        code = raw_ts_code.split(".")[0].strip()
        if not code:
            continue
        fac = factor_map.get(raw_ts_code, {})
        bas = basic_map.get(raw_ts_code, {})
        chp = chip_map.get(raw_ts_code, {})
        bars.append(
            {
                "code": code,
                "trade_date": _fmt_date(str(row.get("trade_date") or target)),
                "open": _f(row.get("open")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "close": _f(row.get("close")),
                "volume": _f(row.get("vol")),
                "amount": _f(row.get("amount")) * 1000,
                "pct_chg": _f(row.get("pct_chg")),
                "turnover_rate": _f(bas.get("turnover_rate")) or _f(fac.get("turnover_rate")),
                "pe_ttm": _f(bas.get("pe_ttm")),
                "total_mv_yi": _f(bas.get("total_mv")) / 1e8 if bas.get("total_mv") is not None else 0.0,
                "ma5": _f(fac.get("ma5")),
                "ma10": _f(fac.get("ma10")),
                "ma20": _f(fac.get("ma20")),
                "ma60": _f(fac.get("ma60")),
                "ma250": _f(fac.get("ma250")),
                "macd_dif": _f(fac.get("macd_dif")),
                "macd_dea": _f(fac.get("macd_dea")),
                "macd": _f(fac.get("macd")),
                "kdj_k": _f(fac.get("kdj_k")),
                "kdj_d": _f(fac.get("kdj_d")),
                "kdj_j": _f(fac.get("kdj_j")),
                "rsi_6": _f(fac.get("rsi_6")),
                "rsi_12": _f(fac.get("rsi_12")),
                "rsi_24": _f(fac.get("rsi_24")),
                "boll_upper": _f(fac.get("boll_upper")),
                "boll_mid": _f(fac.get("boll_mid")),
                "boll_lower": _f(fac.get("boll_lower")),
                "cci": _f(fac.get("cci")),
                "adj_factor": (_f(fac.get("adj_factor")) or adj_map.get(raw_ts_code)) or None,
                "chip_cost_15": _f(chp.get("cost_15pct")),
                "chip_cost_50": _f(chp.get("cost_50pct")),
                "chip_cost_85": _f(chp.get("cost_85pct")),
                "chip_weight_avg": _f(chp.get("weight_avg")),
                "chip_winner_rate": _f(chp.get("winner_rate")),
                "source": "tushare",
            }
        )
    return bars


# ═════════════════════════════════════════════════════════════════════════════
# 股票基础信息
# ═════════════════════════════════════════════════════════════════════════════


def fetch_stock_list() -> list[dict[str, Any]]:
    """获取全 A 股列表（含行业、地区、上市日期）。"""
    pro = get_pro()
    df = pro.stock_basic(exchange="", list_status="L", fields="symbol,name,industry,area,market,list_date")
    if df is None or df.empty:
        return []
    stocks = []
    for _, row in df.iterrows():
        stocks.append({
            "code": str(row.get("symbol", "")).strip(),
            "name": str(row.get("name", "")).strip(),
            "industry": str(row.get("industry", "")).strip(),
            "area": str(row.get("area", "")).strip(),
            "market": str(row.get("market", "")).strip(),
            "list_date": _fmt_date(str(row.get("list_date", ""))),
        })
    return stocks


def fetch_stock_industry_map() -> dict[str, str]:
    """获取 code → industry 映射。"""
    pro = get_pro()
    df = pro.stock_basic(exchange="", list_status="L", fields="symbol,industry")
    if df is None or df.empty:
        return {}
    result: dict[str, str] = {}
    for _, row in df.iterrows():
        code = str(row.get("symbol", "")).strip()
        ind = str(row.get("industry", "")).strip()
        if code and ind:
            result[code] = ind
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 资金流向
# ═════════════════════════════════════════════════════════════════════════════


def fetch_moneyflow(code: str, days: int = 10, trade_date: str | None = None) -> dict[str, Any] | None:
    """获取个股资金流向（含累计数据）。"""
    pro = get_pro()
    tsc = ts_code(code)
    target = _compact_trade_date(trade_date)
    if target:
        start = (date.fromisoformat(_normalize_trade_date(target)) - timedelta(days=max(days * 3, 30))).strftime("%Y%m%d")
        try:
            df = pro.moneyflow(ts_code=tsc, start_date=start, end_date=target)
        except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
            logger.debug("fetch_moneyflow: best-effort fallback after %r", _ble_exc)
            df = pro.moneyflow(ts_code=tsc, trade_date=target)
    else:
        df = pro.moneyflow(ts_code=tsc, limit=days)
    if df is None or df.empty:
        return None
    validate_market_frame("moneyflow", df)

    df = df.sort_values("trade_date", ascending=False)
    if target:
        df = df[df["trade_date"].astype(str) <= target]
        if df.empty or str(df.iloc[0].get("trade_date", "")) != target:
            return None
    latest = df.iloc[0]

    cum_3d = cum_5d = cum_10d = 0.0
    for i in range(min(len(df), 10)):
        row = df.iloc[i]
        net = float(row.get("net_mf_amount") or 0)
        if i < 3:
            cum_3d += net
        if i < 5:
            cum_5d += net
        cum_10d += net

    return {
        "code": code,
        "trade_date": _normalize_trade_date(latest.get("trade_date", target)),
        "main_net_inflow_yi": round(float(latest.get("net_mf_amount") or 0) / 1e8, 4),
        "super_large_net_inflow_yi": round(
            float(latest.get("buy_elg_amount") or 0) / 1e8
            - float(latest.get("sell_elg_amount") or 0) / 1e8,
            4,
        ),
        "large_net_inflow_yi": round(
            float(latest.get("buy_lg_amount") or 0) / 1e8
            - float(latest.get("sell_lg_amount") or 0) / 1e8,
            4,
        ),
        "medium_net_inflow_yi": round(
            float(latest.get("buy_md_amount") or 0) / 1e8
            - float(latest.get("sell_md_amount") or 0) / 1e8,
            4,
        ),
        "small_net_inflow_yi": round(
            float(latest.get("buy_sm_amount") or 0) / 1e8
            - float(latest.get("sell_sm_amount") or 0) / 1e8,
            4,
        ),
        "main_net_inflow_ratio": round(float(latest.get("net_mf_vol") or 0), 2),
        "cumulative_inflow_3d_yi": round(cum_3d / 1e8, 4),
        "cumulative_inflow_5d_yi": round(cum_5d / 1e8, 4),
        "cumulative_inflow_10d_yi": round(cum_10d / 1e8, 4),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 涨停板
# ═════════════════════════════════════════════════════════════════════════════


def fetch_limit_up_board(trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取涨停板榜单。trade_date 格式 YYYYMMDD，默认最新交易日。

    limit_type: U=涨停 D=跌停 Z=炸板（曾涨停后打开）
    炸板数据用于短线情绪温度计（炸板率=炸板数/(涨停数+炸板数)）。
    """
    pro = get_pro()
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    result: list[dict[str, Any]] = []
    errors: list[str] = []
    for limit_type in ("U", "D", "Z"):
        try:
            df = pro.limit_list_d(trade_date=td, limit_type=limit_type)
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                code = str(row.get("ts_code", "")).split(".")[0]
                if len(code) != 6:
                    continue
                result.append({
                    "code": code,
                    "name": str(row.get("name", "")),
                    "trade_date": _fmt_date(str(row.get("trade_date", td))),
                    "pct_chg": _f(row.get("pct_chg")),
                    "limit_times": int(row.get("limit_times") or 0),
                    "limit_type": limit_type,  # 循环已按 U/D 调 API，直接用循环变量（row.get("limit") 字段名不可靠）
                    "first_time": str(row.get("first_time", "")),
                    "last_time": str(row.get("last_time", "")),
                    "open_times": int(row.get("open_times") or 0),
                    "up_stat": str(row.get("up_stat", "")),
                    "limit_amount_yi": _f(row.get("limit_amount")) / 1e8 if row.get("limit_amount") is not None else 0.0,
                    "turnover_ratio": _f(row.get("turnover_ratio")),
                    "fd_amount": _f(row.get("fd_amount")) / 1e8 if row.get("fd_amount") is not None else 0.0,
                    "industry": str(row.get("industry", "")),
                })
        except Exception as exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
            errors.append(f"{limit_type}: {type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError(f"limit_list_d({td}) failed: {'; '.join(errors)}")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 龙虎榜
# ═════════════════════════════════════════════════════════════════════════════


def fetch_dragon_tiger(trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取龙虎榜数据。"""
    pro = get_pro()
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    try:
        df = pro.top_list(trade_date=td)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_dragon_tiger: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        code = str(row.get("ts_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        result.append({
            "code": code,
            "name": str(row.get("name", "")),
            "trade_date": _fmt_date(str(row.get("trade_date", td))),
            "reason": str(row.get("reason", "")),
            "buy_amount_yi": round(_f(row.get("l_buy")) / 1e8, 4) if row.get("l_buy") is not None else None,
            "sell_amount_yi": round(_f(row.get("l_sell")) / 1e8, 4) if row.get("l_sell") is not None else None,
            "net_amount_yi": round(_f(row.get("net_amount")) / 1e8, 4) if row.get("net_amount") is not None else None,
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 概念板块
# ═════════════════════════════════════════════════════════════════════════════


def fetch_concept_sectors() -> list[dict[str, Any]]:
    """获取同花顺概念板块列表（含成分股数量）。"""
    pro = get_pro()
    try:
        df = pro.ths_index()
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_concept_sectors: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "sector_code": str(row.get("ts_code", "")),
            "sector_name": str(row.get("name", "")),
            "member_count": int(_f(row.get("count"))),
            "exchange": str(row.get("exchange", "")),
            "list_date": _fmt_date(str(row.get("list_date", ""))),
            "type": str(row.get("type", "")),
        })
    return result


def fetch_concept_members(sector_code: str, trade_date: str = "") -> list[dict[str, Any]]:
    """获取指定概念板块的成分股列表。"""
    pro = get_pro()
    try:
        df = pro.ths_member(ts_code=sector_code)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_concept_members: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    if not trade_date:
        from datetime import date as _date, timedelta
        td = _date.today()
        if td.weekday() == 5:
            td = td - timedelta(days=1)
        elif td.weekday() == 6:
            td = td - timedelta(days=2)
        trade_date = td.isoformat()
    result = []
    for _, row in df.iterrows():
        code = str(row.get("con_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        result.append({
            "sector_code": sector_code,
            "code": code,
            "name": str(row.get("con_name", "")),
            "trade_date": trade_date,
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 新增能力：融资融券、港股通、股东人数、涨跌停价、停复牌
# ═════════════════════════════════════════════════════════════════════════════


def fetch_margin(code: str, days: int = 5, trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取个股融资融券余额。"""
    pro = get_pro()
    tsc = ts_code(code)
    target = _compact_trade_date(trade_date)
    try:
        if target:
            start = (date.fromisoformat(_normalize_trade_date(target)) - timedelta(days=max(days * 3, 30))).strftime("%Y%m%d")
            df = pro.margin(ts_code=tsc, start_date=start, end_date=target)
        else:
            df = pro.margin(ts_code=tsc, limit=days)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_margin: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    if target:
        df = df[df["trade_date"].astype(str) <= target]
        if df.empty:
            return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "trade_date": _normalize_trade_date(row.get("trade_date", target)),
            "rzye": _f(row.get("rzye")) / 1e8,  # 融资余额(亿)
            "rqye": _f(row.get("rqye")) / 1e8,  # 融券余额(亿)
            "rzmre": _f(row.get("rzmre")) / 1e8,  # 融资买入额(亿)
            "rzche": _f(row.get("rzche")) / 1e8,  # 融资偿还额(亿)
            "rzrqye": _f(row.get("rzrqye")) / 1e8,  # 融资融券余额(亿)
        })
    return result


def fetch_hk_hold(code: str, trade_date: str | None = None) -> dict[str, Any] | None:
    """获取港股通持股（最新一条）。"""
    pro = get_pro()
    tsc = ts_code(code)
    target = _compact_trade_date(trade_date)
    try:
        if target:
            try:
                df = pro.hk_hold(ts_code=tsc, trade_date=target)
            except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
                logger.debug("fetch_hk_hold: best-effort fallback after %r", _ble_exc)
                df = pro.hk_hold(ts_code=tsc, limit=1)
        else:
            df = pro.hk_hold(ts_code=tsc, limit=1)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_hk_hold: best-effort fallback after %r", _ble_exc)
        return None
    if df is None or df.empty:
        return None
    if target:
        df = df[df["trade_date"].astype(str) <= target]
        if df.empty:
            return None
        df = df.sort_values("trade_date", ascending=False)
    row = df.iloc[0]
    return {
        "code": code,
        "trade_date": _normalize_trade_date(row.get("trade_date", target)),
        "vol": _f(row.get("vol")),  # 持股数量(股)
        "ratio": _f(row.get("ratio")),  # 持股比例(%)
    }


def fetch_holder_number(code: str, limit: int = 5, trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取股东人数变化。"""
    pro = get_pro()
    tsc = ts_code(code)
    target = _compact_trade_date(trade_date)
    try:
        if target:
            df = pro.stk_holdernumber(ts_code=tsc, end_date=target, limit=limit)
        else:
            df = pro.stk_holdernumber(ts_code=tsc, limit=limit)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_holder_number: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    if target:
        df = df[df["end_date"].astype(str) <= target]
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "end_date": _fmt_date(str(row.get("end_date", ""))),
            "holder_num": int(row.get("holder_num") or 0),
        })
    return result


def fetch_stk_limit(code: str, days: int = 5, trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取每日涨跌停价格。"""
    pro = get_pro()
    tsc = ts_code(code)
    target = _compact_trade_date(trade_date)
    try:
        if target:
            start = (date.fromisoformat(_normalize_trade_date(target)) - timedelta(days=max(days * 3, 30))).strftime("%Y%m%d")
            df = pro.stk_limit(ts_code=tsc, start_date=start, end_date=target)
        else:
            df = pro.stk_limit(ts_code=tsc, limit=days)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_stk_limit: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    validate_market_frame("stk_limit", df)
    if target:
        df = df[df["trade_date"].astype(str) <= target]
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "trade_date": _normalize_trade_date(row.get("trade_date", target)),
            "up_limit": _f(row.get("up_limit")),
            "down_limit": _f(row.get("down_limit")),
        })
    return result


def fetch_suspend(code: str) -> dict[str, Any] | None:
    """获取最新停复牌信息。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.suspend(ts_code=tsc, limit=1)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_suspend: best-effort fallback after %r", _ble_exc)
        return None
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    return {
        "code": code,
        "suspend_date": _fmt_date(str(row.get("suspend_date", ""))),
        "resume_date": _fmt_date(str(row.get("resume_date", ""))),
        "suspend_reason": str(row.get("suspend_reason", "")),
    }


def fetch_top_inst(trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取龙虎榜机构席位交易明细（top_inst 接口）。"""
    pro = get_pro()
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    try:
        df = pro.top_inst(trade_date=td)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_top_inst: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        code = str(row.get("ts_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        result.append({
            "code": code,
            "trade_date": _fmt_date(str(row.get("trade_date", td))),
            "institution": str(row.get("exalter", "")),
            "side": str(row.get("side", "")),
            "buy_amount_yi": round(_f(row.get("buy")) / 1e8, 4) if row.get("buy") is not None else 0.0,
            "buy_rate": _f(row.get("buy_rate")),
            "sell_amount_yi": round(_f(row.get("sell")) / 1e8, 4) if row.get("sell") is not None else 0.0,
            "sell_rate": _f(row.get("sell_rate")),
            "net_buy_yi": round(_f(row.get("net_buy")) / 1e8, 4) if row.get("net_buy") is not None else 0.0,
            "reason": str(row.get("reason", "")),
        })
    return result


def fetch_latest_trade_date() -> str:
    """获取最新交易日（本地交易日历；拿不到时抛 TradeCalendarUnavailable，绝不猜成今天）。"""
    from app.services.trade_calendar import latest_trade_date

    return latest_trade_date()


def fetch_recent_trade_dates(lookback_days: int = 30) -> list[str]:
    """最近 lookback_days 个自然日内的交易日列表（compact YYYYMMDD，升序）。

    只返回交易所日历里 is_open=1 的日期。调用方要判定「最新已收盘交易日」时必须用
    这份清单，而不能用工作日算术——休市的工作日（如 2026-09-25）会被算成交易日，
    使下游把所有表都判成缺那一天（L-147）。读本地交易日历；拿不到时抛
    TradeCalendarUnavailable，而不是返回空列表。
    """
    from app.services.trade_calendar import open_dates_between

    today = date.today()
    return open_dates_between(today - timedelta(days=max(1, int(lookback_days))), today)


def is_open_trade_date(day: date) -> bool:
    """交易所日历判定 day 是否开市（SSE is_open=1）。

    定时作业的 cron 只能表达「周一到周五」，国庆、春节等工作日休市只能问交易所日历。
    日历拿不到时直接抛错：把休市日当交易日会让盘后作业误报失败，把交易日当休市日
    会漏跑整天推荐，两种猜测都不可接受。
    """
    from app.services.trade_calendar import TradeCalendarUnavailable, is_open

    try:
        return is_open(day)
    except TradeCalendarUnavailable as exc:
        raise RuntimeError(f"Tushare 交易日历未返回 {day.strftime('%Y%m%d')}，无法判定是否交易日: {exc}") from exc


# ═════════════════════════════════════════════════════════════════════════════
# 指数日线（用于回测基准对比）
# ═════════════════════════════════════════════════════════════════════════════


def fetch_index_daily(
    ts_code: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """获取指数日线行情。

    Parameters
    ----------
    ts_code : str, optional
        Tushare 指数代码（如 "000300.SH"）。默认沪深300。
    start_date, end_date : str, optional
        YYYYMMDD 格式。未指定则查最近 60 个自然日。

    Returns
    -------
    list[dict] 每行含 ts_code, trade_date, open, high, low, close, pct_chg, volume
    """
    pro = get_pro()
    target = ts_code or "000300.SH"
    sd = start_date or (date.today() - timedelta(days=90)).strftime("%Y%m%d")
    ed = end_date or date.today().strftime("%Y%m%d")
    try:
        df = pro.index_daily(ts_code=target, start_date=sd, end_date=ed)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_index_daily: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    validate_market_frame("index_daily", df)
    result: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        trade_date_raw = str(row.get("trade_date") or "")
        if len(trade_date_raw) == 8:
            trade_date = f"{trade_date_raw[:4]}-{trade_date_raw[4:6]}-{trade_date_raw[6:]}"
        else:
            trade_date = trade_date_raw
        result.append({
            "ts_code": str(row.get("ts_code") or target),
            "trade_date": trade_date,
            "close": _f(row.get("close")),
            "pct_chg": _f(row.get("pct_chg")),
            "open": _f(row.get("open")),
            "high": _f(row.get("high")),
            "low": _f(row.get("low")),
            "volume": _f(row.get("volume")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 集合竞价
# ═════════════════════════════════════════════════════════════════════════════


def fetch_auction(code: str, trade_date: str | None = None) -> dict[str, Any] | None:
    """获取个股集合竞价数据。"""
    pro = get_pro()
    tsc = ts_code(code)
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    try:
        df = pro.stk_auction(ts_code=tsc, trade_date=td)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_auction: best-effort fallback after %r", _ble_exc)
        return None
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    return {
        "code": code,
        "trade_date": _fmt_date(str(row.get("trade_date", td))),
        "vol": _f(row.get("vol")),
        "price": _f(row.get("price")),
        "amount": _f(row.get("amount")),
        "pre_close": _f(row.get("pre_close")),
        "turnover_rate": _f(row.get("turnover_rate")),
        "volume_ratio": _f(row.get("volume_ratio")),
        "float_share": _f(row.get("float_share")),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 量化因子
# ═════════════════════════════════════════════════════════════════════════════


def fetch_factor_pro(code: str, days: int = 90, trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取个股量化因子（stk_factor_pro）。"""
    pro = get_pro()
    tsc = ts_code(code)
    target = _normalize_trade_date(trade_date)
    end_day = date.fromisoformat(target) if target else date.today()
    sd = (end_day - timedelta(days=days * 3)).strftime("%Y%m%d")
    ed = end_day.strftime("%Y%m%d")
    try:
        df = pro.stk_factor_pro(ts_code=tsc, start_date=sd, end_date=ed)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_factor_pro: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    validate_market_frame("stk_factor_pro", df)
    df = df.sort_values("trade_date")
    if days and len(df) > days:
        df = df.tail(days)
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "trade_date": _fmt_date(str(row.get("trade_date", ""))),
            "adj_factor": _f(row.get("adj_factor")),
            "asi": _f(row.get("asi_bfq")),
            "asit": _f(row.get("asit_bfq")),
            "atr": _f(row.get("atr_bfq")),
            "bbi": _f(row.get("bbi_bfq")),
            "bias_6": _f(row.get("bias_6_bfq")),
            "bias_12": _f(row.get("bias_12_bfq")),
            "bias_24": _f(row.get("bias_24_bfq")),
            "brar_ar": _f(row.get("brar_ar_bfq")),
            "brar_br": _f(row.get("brar_br_bfq")),
            "dpo": _f(row.get("dpo_bfq")),
            "ema_12": _f(row.get("ema_12_bfq")),
            "ema_50": _f(row.get("ema_50_bfq")),
            "kdj_k": _f(row.get("kdj_k_bfq")),
            "kdj_d": _f(row.get("kdj_d_bfq")),
            "kdj_j": _f(row.get("kdj_j_bfq")),
            "ma_volume_5": _f(row.get("ma_volume_5_bfq")),
            "ma_volume_10": _f(row.get("ma_volume_10_bfq")),
            "macd_dif": _f(row.get("macd_dif_bfq")),
            "macd_dea": _f(row.get("macd_dea_bfq")),
            "macd": _f(row.get("macd_bfq")),
            "mtm": _f(row.get("mtm_bfq")),
            "obv": _f(row.get("obv_bfq")),
            "psy": _f(row.get("psy_bfq")),
            "rsi_6": _f(row.get("rsi_6_bfq")),
            "rsi_12": _f(row.get("rsi_12_bfq")),
            "rsi_24": _f(row.get("rsi_24_bfq")),
            "boll_upper": _f(row.get("boll_upper_bfq")),
            "boll_mid": _f(row.get("boll_mid_bfq")),
            "boll_lower": _f(row.get("boll_lower_bfq")),
            "trix": _f(row.get("trix_bfq")),
            "vr": _f(row.get("vr_bfq")),
            "wr_6": _f(row.get("wr_6_bfq")),
            "wr_10": _f(row.get("wr_10_bfq")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 增减持
# ═════════════════════════════════════════════════════════════════════════════


def fetch_holder_trade(code: str) -> list[dict[str, Any]]:
    """获取股东增减持数据。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.stk_holdertrade(ts_code=tsc)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_holder_trade: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "ann_date": _fmt_date(str(row.get("ann_date", ""))),
            "holder_name": str(row.get("holder_name", "")),
            "holder_type": str(row.get("holder_type", "")),
            "in_de": str(row.get("in_de", "")),
            "change_vol": _f(row.get("change_vol")),
            "change_ratio": _f(row.get("change_ratio")),
            "after_ratio": _f(row.get("after_ratio")),
            "avg_price": _f(row.get("avg_price")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 回购
# ═════════════════════════════════════════════════════════════════════════════


def fetch_repurchase(code: str) -> list[dict[str, Any]]:
    """获取股票回购数据。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.repurchase(ts_code=tsc)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_repurchase: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "ann_date": _fmt_date(str(row.get("ann_date", ""))),
            "end_date": _fmt_date(str(row.get("end_date", ""))),
            "vol": _f(row.get("vol")),
            "amount": _f(row.get("amount")),
            "high_limit": _f(row.get("high_limit")),
            "low_limit": _f(row.get("low_limit")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 质押统计
# ═════════════════════════════════════════════════════════════════════════════


def fetch_pledge_stat(code: str) -> dict[str, Any] | None:
    """获取股权质押统计。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.pledge_stat(ts_code=tsc)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_pledge_stat: best-effort fallback after %r", _ble_exc)
        return None
    if df is None or df.empty:
        return None
    latest = df.sort_values("end_date").iloc[-1]
    return {
        "code": code,
        "end_date": _fmt_date(str(latest.get("end_date", ""))),
        "pledge_count": int(_f(latest.get("pledge_count"))),
        "unrest_pledge": _f(latest.get("unrest_pledge")),
        "rest_pledge": _f(latest.get("rest_pledge")),
        "pledge_ratio": _f(latest.get("pledge_ratio")),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 限售解禁
# ═════════════════════════════════════════════════════════════════════════════


def fetch_share_float(code: str) -> list[dict[str, Any]]:
    """获取限售股解禁数据。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.share_float(ts_code=tsc)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_share_float: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "ann_date": _fmt_date(str(row.get("ann_date", ""))),
            "float_date": _fmt_date(str(row.get("float_date", ""))),
            "float_share": _f(row.get("float_share")),
            "float_ratio": _f(row.get("float_ratio")),
            "holder_name": str(row.get("holder_name", "")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 券商金股
# ═════════════════════════════════════════════════════════════════════════════


def fetch_broker_recommend(month: str | None = None) -> list[dict[str, Any]]:
    """获取券商金股推荐。"""
    pro = get_pro()
    m = month or date.today().strftime("%Y%m")
    try:
        df = pro.broker_recommend(month=m)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_broker_recommend: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        code = str(row.get("ts_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        result.append({
            "code": code,
            "name": str(row.get("name", "")),
            "month": str(row.get("month", m)),
            "broker": str(row.get("broker", "")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 分钟K线备用源：Baostock（5/15/30/60min）
# 全部 tushare 源 stk_mins 无权限时，用 baostock 补齐 5m+ 分钟K。
# 防封纪律（用户硬性要求，实测连续多次查询会触发限速挂起）：
#   - 单进程全局锁串行，进程内复用一次 login；
#   - 每次查询间隔 >= _BAOSTOCK_MIN_INTERVAL_S（默认 3.5s）；
#   - 查询异常/空结果重 login 后最多重试 2 次；
#   - baostock 不支持 1min（frequency='1' 恒返回空）。
# ═════════════════════════════════════════════════════════════════════════════

_baostock_logged_in = False
_baostock_last_query = 0.0


def _baostock_ensure_login() -> Any:
    global _baostock_logged_in
    import baostock as bs

    if not _baostock_logged_in:
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock login failed: {lg.error_msg}")
        _baostock_logged_in = True
    return bs


def _baostock_relogin() -> Any:
    global _baostock_logged_in
    import baostock as bs

    try:
        bs.logout()
    except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
        logger.debug("_baostock_relogin: ignored non-fatal error: %s", exc)
    _baostock_logged_in = False
    return _baostock_ensure_login()


def _fetch_baostock_minute_kbars(
    code: str,
    freq: str,
    start_date: str,
    end_date: str,
    *,
    is_index: bool = False,
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """用 baostock 抓 5/15/30/60min 历史分钟K，返回与 tushare 分钟行同构的 dict 列表。

    严格串行 + 限速 + 失败重 login 重试。不支持的 freq（如 1min）直接返回 []。
    """
    global _baostock_last_query

    _bs_started = time.perf_counter()
    bs_freq = _BAOSTOCK_FREQ_MAP.get(str(freq or "").lower())
    if bs_freq is None:
        _record_minute_source_trace(
            trace, source="baostock", code=code, status="unsupported_freq", row_count=0,
            reason=f"baostock 不支持 freq={freq}（仅 5/15/30/60min）",
        )
        return []

    start_day = _normalize_trade_date(start_date)
    end_day = _normalize_trade_date(end_date)
    if not start_day or not end_day:
        return []

    bs_code = _baostock_to_bs_code(code, is_index=is_index)
    fields = "date,time,open,high,low,close,volume,amount"

    with _baostock_lock:
        rs = None
        last_err: str | None = None
        for attempt in range(_BAOSTOCK_MAX_RETRIES + 1):
            try:
                # 限速：距上次查询至少 _BAOSTOCK_MIN_INTERVAL_S
                wait = _BAOSTOCK_MIN_INTERVAL_S - (time.monotonic() - _baostock_last_query)
                if wait > 0:
                    time.sleep(wait)
                bs = _baostock_ensure_login()
                rs = bs.query_history_k_data_plus(
                    bs_code, fields,
                    start_date=start_day, end_date=end_day,
                    frequency=bs_freq, adjustflag="3",
                )
                _baostock_last_query = time.monotonic()
                if rs.error_code == "0":
                    break
                last_err = f"error_code={rs.error_code} {rs.error_msg}"
                logger.warning("baostock minute query failed code=%s freq=%s attempt=%d: %s", code, freq, attempt + 1, last_err)
            except Exception as exc:  # 网络/COM 异常 → 重 login 重试  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {str(exc)[:120]}"
                logger.warning("baostock minute query exception code=%s freq=%s attempt=%d: %s", code, freq, attempt + 1, last_err)
            if attempt < _BAOSTOCK_MAX_RETRIES:
                try:
                    _baostock_relogin()
                except Exception as exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
                    logger.debug("_fetch_baostock_minute_kbars: best-effort fallback after %r", exc)
                    last_err = f"relogin failed: {exc}"
        if rs is None or getattr(rs, "error_code", "1") != "0":
            _record_minute_source_trace(
                trace, source="baostock", code=code, status="error", row_count=0,
                reason=last_err or "unknown",
            )
            _persist_baostock_trace(
                code, freq, status="error",
                duration_ms=int((time.perf_counter() - _bs_started) * 1000),
                failure_category="provider_error",
                fallback_from="tushare_stk_mins",
            )
            return []

        rows: list[dict[str, Any]] = []
        while rs.next():
            data = rs.get_row_data()
            if len(data) < 8:
                continue
            raw_date, raw_time = str(data[0]).strip(), str(data[1]).strip()
            trade_date = _normalize_trade_date(raw_date or raw_time)
            # baostock time 为 'YYYYMMDDHHMMSSmmm'（14位+毫秒），截到 'YYYY-MM-DD HH:MM:SS'
            trade_time = ""
            if len(raw_time) >= 14:
                trade_time = (
                    f"{raw_time[0:4]}-{raw_time[4:6]}-{raw_time[6:8]} "
                    f"{raw_time[8:10]}:{raw_time[10:12]}:{raw_time[12:14]}"
                )
            if not trade_date or not trade_time:
                continue
            open_price = _f(data[2])
            high_price = _f(data[3])
            low_price = _f(data[4])
            close_price = _f(data[5])
            rows.append({
                "code": code,
                "trade_date": trade_date,
                "trade_time": trade_time,
                "open": round(open_price, 3),
                "high": round(max(high_price, open_price, close_price), 3),
                "low": round(min(v for v in (low_price, open_price, close_price) if v > 0) if any(v > 0 for v in (low_price, open_price, close_price)) else close_price, 3),
                "close": round(close_price, 3),
                "volume": _f(data[6]),
                "amount": _f(data[7]),
                "source": "baostock",
            })

    _record_minute_source_trace(
        trace, source="baostock", code=code,
        status="success" if rows else "empty", row_count=len(rows),
    )
    _persist_baostock_trace(
        code, freq,
        status="success" if rows else "empty",
        duration_ms=int((time.perf_counter() - _bs_started) * 1000),
        fallback_from="tushare_stk_mins",
    )
    return rows


# ═════════════════════════════════════════════════════════════════════════════
# 分钟K线（Tushare stk_mins）
# ═════════════════════════════════════════════════════════════════════════════


def fetch_minute_kbars(
    code: str,
    trade_date: str | None = None,
    trace: list[dict[str, Any]] | None = None,
    freq: str = "1min",
) -> list[dict[str, Any]]:
    """获取个股分钟 K 线。

    freq="1min"（默认）：仅使用 Tushare stk_mins。
    freq="5min"/"15min"/"30min"/"60min"：Tushare 源均无权限，改用 Baostock 备用源
      （严格限速，见 _fetch_baostock_minute_kbars）。须配合 trade_date 使用；
      未传 trade_date 时回落到「最近一个有数据的交易日」。
    """
    from app.services.datasource import gateway as _gw

    if _gw.is_cutover("bar.minute"):
        return _fetch_minute_kbars_gateway(code, trade_date, trace, freq)
    rows = _legacy_fetch_minute_kbars(code, trade_date, trace, freq)
    if _gw.is_shadow("bar.minute"):
        _gw.shadow_compare("bar.minute", rows, lambda: _fetch_minute_kbars_gateway(code, trade_date, None, freq))
    return rows


def _fetch_minute_kbars_gateway(
    code: str,
    trade_date: str | None,
    trace: list[dict[str, Any]] | None,
    freq: str,
) -> list[dict[str, Any]]:
    """Gateway path for ``bar.minute``; post-processing per provider mirrors the legacy branches."""
    from app.services.datasource import bar_minute

    target_trade_date = _normalize_trade_date(trade_date)
    normalized_freq = str(freq or "1min").lower()
    if target_trade_date:
        res = bar_minute.fetch(code, target_trade_date, target_trade_date, normalized_freq, trace)
        if res.provider == "xiaoshi_min1":
            return _attach_pre_close(res.rows)
        if res.provider == "tushare_stk_mins":
            return [row for row in res.rows if row["trade_date"] == target_trade_date]
        return list(res.rows or [])
    if normalized_freq != "1min":
        end_day = date.today()
        start_day = end_day - timedelta(days=10)
        res = bar_minute.fetch(code, start_day.isoformat(), end_day.isoformat(), normalized_freq, trace)
        rows = list(res.rows or [])
        latest = max((row["trade_date"] for row in rows), default="")
        picked = [row for row in rows if row["trade_date"] == latest]
        return _attach_pre_close(picked) if res.provider == "xiaoshi_min1" else picked
    rows = _recent_minute_kbars_gateway(code, 5, trace)
    latest_trade_date = max((row["trade_date"] for row in rows), default="")
    return [row for row in rows if row["trade_date"] == latest_trade_date]


def _recent_minute_kbars_gateway(code: str, ndays: int, trace: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    from app.services.datasource import bar_minute

    end_day = date.today()
    start_day = end_day - timedelta(days=max(10, int(ndays or 5) * 3))
    res = bar_minute.fetch(code, start_day.isoformat(), end_day.isoformat(), "1min", trace)
    if not res.rows:
        return []
    if res.provider == "xiaoshi_min1":
        return _trim_recent_trade_dates(_attach_pre_close(res.rows), ndays)
    return _trim_recent_trade_dates(res.rows, ndays)


def _legacy_fetch_minute_kbars(
    code: str,
    trade_date: str | None,
    trace: list[dict[str, Any]] | None,
    freq: str,
) -> list[dict[str, Any]]:
    target_trade_date = _normalize_trade_date(trade_date)
    normalized_freq = str(freq or "1min").lower()

    if target_trade_date:
        xiaoshi_rows = _fetch_xiaoshi_minute_kbars(
            code,
            start_date=target_trade_date,
            end_date=target_trade_date,
            freq=normalized_freq,
            trace=trace,
        )
        if xiaoshi_rows:
            return _attach_pre_close(xiaoshi_rows)

    if normalized_freq != "1min":
        if target_trade_date:
            return _fetch_baostock_minute_kbars(
                code, normalized_freq, target_trade_date, target_trade_date, trace=trace,
            )
        # 未指定日期：抓近 10 天窗口，取最近一个有数据的交易日
        end_day = date.today()
        start_day = end_day - timedelta(days=10)
        xiaoshi_rows = _fetch_xiaoshi_minute_kbars(
            code,
            start_date=start_day.isoformat(),
            end_date=end_day.isoformat(),
            freq=normalized_freq,
            trace=trace,
        )
        if xiaoshi_rows:
            latest = max((row["trade_date"] for row in xiaoshi_rows), default="")
            return _attach_pre_close([row for row in xiaoshi_rows if row["trade_date"] == latest])
        rows = _fetch_baostock_minute_kbars(
            code, normalized_freq, start_day.isoformat(), end_day.isoformat(), trace=trace,
        )
        latest = max((row["trade_date"] for row in rows), default="")
        return [row for row in rows if row["trade_date"] == latest]

    if target_trade_date:
        rows = _fetch_tushare_minute_kbars(
            code,
            start_time=f"{target_trade_date} 09:00:00",
            end_time=f"{target_trade_date} 15:30:00",
            trace=trace,
        )
        return [row for row in rows if row["trade_date"] == target_trade_date]

    rows = fetch_recent_minute_kbars(code, trace=trace)
    latest_trade_date = max((row["trade_date"] for row in rows), default="")
    return [row for row in rows if row["trade_date"] == latest_trade_date]


def fetch_recent_minute_kbars(
    code: str,
    ndays: int = 5,
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """获取个股近 N 个交易日的 1 分钟数据（xiaoshi → Tushare stk_mins）。"""
    from app.services.datasource import gateway as _gw

    if _gw.is_cutover("bar.minute"):
        return _recent_minute_kbars_gateway(code, ndays, trace)
    end_day = date.today()
    start_day = end_day - timedelta(days=max(10, int(ndays or 5) * 3))
    xiaoshi_rows = _fetch_xiaoshi_minute_kbars(
        code,
        start_date=start_day.isoformat(),
        end_date=end_day.isoformat(),
        freq="1min",
        trace=trace,
    )
    if xiaoshi_rows:
        return _trim_recent_trade_dates(_attach_pre_close(xiaoshi_rows), ndays)
    tushare_rows = _fetch_tushare_minute_kbars(
        code,
        start_time=f"{start_day.isoformat()} 09:00:00",
        end_time=f"{end_day.isoformat()} 15:30:00",
        trace=trace,
    )
    if tushare_rows:
        return _trim_recent_trade_dates(tushare_rows, ndays)
    return []


def fetch_recent_index_minute_kbars(
    index_code: str,
    ndays: int = 5,
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """获取指数近 N 个交易日的 1 分钟数据。仅使用 Tushare stk_mins。"""
    end_day = date.today()
    start_day = end_day - timedelta(days=max(10, int(ndays or 5) * 3))
    tushare_rows = _fetch_tushare_minute_kbars(
        index_code,
        start_time=f"{start_day.isoformat()} 09:00:00",
        end_time=f"{end_day.isoformat()} 15:30:00",
        trace=trace,
        is_index=True,
    )
    if tushare_rows:
        return _trim_recent_trade_dates(tushare_rows, ndays)
    return []


def fetch_index_minute_kbars(
    index_code: str,
    start_date: str,
    end_date: str,
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """按日期区间获取指数 1 分钟 K 线。仅使用 Tushare stk_mins。"""
    start_day = _normalize_trade_date(start_date)
    end_day = _normalize_trade_date(end_date)
    if not start_day or not end_day:
        return []
    rows = _fetch_tushare_minute_kbars(
        index_code,
        start_time=f"{start_day} 09:00:00",
        end_time=f"{end_day} 15:30:00",
        trace=trace,
        is_index=True,
    )
    return [
        row for row in rows
        if start_day <= row.get("trade_date", "") <= end_day
    ]


def _minute_query_windows(start_time: str, end_time: str) -> list[tuple[str, str]]:
    """Slice long 1-minute ranges well below the provider's 8,000-row cap."""
    try:
        start_value = datetime.fromisoformat(start_time)
        end_value = datetime.fromisoformat(end_time)
    except ValueError:
        return [(start_time, end_time)]
    if start_value > end_value:
        return []

    windows: list[tuple[str, str]] = []
    current_day = start_value.date()
    end_day = end_value.date()
    while current_day <= end_day:
        window_end_day = min(
            current_day + timedelta(days=_STK_MINS_WINDOW_DAYS - 1),
            end_day,
        )
        window_start = start_time if current_day == start_value.date() else f"{current_day.isoformat()} 09:00:00"
        window_end = end_time if window_end_day == end_day else f"{window_end_day.isoformat()} 15:30:00"
        windows.append((window_start, window_end))
        current_day = window_end_day + timedelta(days=1)
    return windows


def _fetch_xiaoshi_minute_kbars(
    code: str,
    *,
    start_date: str,
    end_date: str,
    freq: str,
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Read completed A-share minute history from Xiaoshi's cached R2 files."""
    try:
        from app.services.xiaoshi_client import get_xiaoshi_client
        from app.services.xiaoshi_minute_source import (
            XiaoshiMinuteHistorySource,
            default_xiaoshi_minute_cache_dir,
        )

        settings = get_settings()
        with XiaoshiMinuteHistorySource(
            client=get_xiaoshi_client(),
            cache_dir=default_xiaoshi_minute_cache_dir(settings),
        ) as source:
            rows = source.fetch(
                code,
                start_date=start_date,
                end_date=end_date,
                freq=freq,
            )
        _record_minute_source_trace(
            trace,
            source="xiaoshi_min1",
            code=code,
            status="success" if rows else "empty",
            row_count=len(rows),
        )
        return rows
    except Exception as exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
        _record_minute_source_trace(
            trace,
            source="xiaoshi_min1",
            code=code,
            status="error",
            row_count=0,
            reason=type(exc).__name__,
        )
        return []


def _fetch_tushare_minute_kbars(
    code: str,
    *,
    start_time: str,
    end_time: str,
    trace: list[dict[str, Any]] | None = None,
    is_index: bool = False,
) -> list[dict[str, Any]]:
    windows = _minute_query_windows(start_time, end_time)
    if not windows:
        _record_minute_source_trace(
            trace,
            source="tushare_stk_mins",
            code=code,
            status="error",
            row_count=0,
            reason="minute range start is after end",
        )
        return []

    frames: list[pd.DataFrame] = []
    try:
        pro = get_pro()
        for window_start, window_end in windows:
            frame = pro.stk_mins(
                ts_code=_minute_ts_code(code, is_index=is_index),
                freq="1min",
                start_date=window_start,
                end_date=window_end,
            )
            if frame is None or frame.empty:
                continue
            if len(frame) >= _STK_MINS_MAX_ROWS:
                _record_minute_source_trace(
                    trace,
                    source="tushare_stk_mins",
                    code=code,
                    status="truncated",
                    row_count=len(frame),
                    reason="stk_mins response reached the 8000-row safety limit",
                )
                return []
            frames.append(frame)
    except Exception as exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
        reason = str(exc)
        status = "permission_denied" if _is_tushare_permission_error(exc) else "error"
        _record_minute_source_trace(
            trace,
            source="tushare_stk_mins",
            code=code,
            status=status,
            row_count=0,
            reason=reason,
        )
        return []
    if not frames:
        _record_minute_source_trace(
            trace,
            source="tushare_stk_mins",
            code=code,
            status="empty",
            row_count=0,
        )
        return []

    combined = pd.concat(frames, ignore_index=True)
    validate_market_frame("minute_kbar", combined)
    rows_by_time: dict[str, dict[str, Any]] = {}
    for _, row in combined.iterrows():
        trade_time = str(row.get("trade_time") or row.get("datetime") or row.get("date") or "").strip()
        trade_date = _normalize_trade_date(trade_time)
        if not trade_time or not trade_date:
            continue
        normalized_time = trade_time if len(trade_time) >= 19 else f"{trade_time}:00"
        close_price = _f(row.get("close"))
        open_price = _f(row.get("open"), close_price)
        high_price = _f(row.get("high"), max(open_price, close_price))
        low_price = _f(row.get("low"), min(open_price, close_price))
        rows_by_time[normalized_time] = {
            "code": code,
            "trade_date": trade_date,
            "trade_time": normalized_time,
            "open": round(open_price, 3),
            "close": round(close_price, 3),
            "high": round(max(high_price, open_price, close_price), 3),
            "low": round(min(v for v in (low_price, open_price, close_price) if v > 0) if any(v > 0 for v in (low_price, open_price, close_price)) else close_price, 3),
            "volume": _f(row.get("vol") if "vol" in row else row.get("volume")),
            "amount": _f(row.get("amount")),
            "source": "tushare_stk_mins",
        }
    rows = _attach_pre_close([rows_by_time[key] for key in sorted(rows_by_time)])
    _record_minute_source_trace(
        trace,
        source="tushare_stk_mins",
        code=code,
        status="success" if rows else "empty",
        row_count=len(rows),
    )
    return rows


def fetch_realtime_minute_kbar(code: str, freq: str | int = "1MIN") -> dict[str, Any] | None:
    """Fetch one current intraday minute bar for a final candidate.

    Historical bars remain sourced from local ``MinuteKbar``. This helper is an
    intraday overlay and must not be used to scan the full A-share universe.
    """
    normalized_freq = _normalize_realtime_minute_freq(freq)
    df = get_pro().rt_min(ts_code=ts_code(code), freq=normalized_freq)
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    bar_time = str(row.get("time") or row.get("trade_time") or row.get("period_end") or "").strip()
    trade_date = _normalize_trade_date(row.get("trade_date") or bar_time)
    return {
        "code": code,
        "ts_code": str(row.get("ts_code") or ts_code(code)),
        "freq": str(row.get("freq") or normalized_freq).upper(),
        "trade_date": trade_date,
        "trade_time": bar_time,
        "period_start": str(row.get("period_start") or ""),
        "period_end": str(row.get("period_end") or ""),
        "open": _f(row.get("open")),
        "high": _f(row.get("high")),
        "low": _f(row.get("low")),
        "close": _f(row.get("close")),
        "volume": _f(row.get("vol") if "vol" in row else row.get("volume")),
        "amount": _f(row.get("amount")),
        "is_complete": str(row.get("is_complete") or "").strip().lower() in {"1", "true", "yes"},
        "source": "tushare_rt_min",
    }


def fetch_realtime_k_snapshot(code: str) -> dict[str, Any] | None:
    """Fetch an intraday aggregate snapshot; this is not a minute-bar series."""
    df = get_pro().rt_k(ts_code=ts_code(code))
    if df is None or df.empty:
        return None
    row = dict(df.iloc[0])
    return {
        **row,
        "code": code,
        "ts_code": str(row.get("ts_code") or ts_code(code)),
        "trade_date": _normalize_trade_date(
            row.get("trade_date") or row.get("date") or row.get("time")
        ),
        "source": "tushare_rt_k",
    }


def _minute_ts_code(code: str, *, is_index: bool = False) -> str:
    if "." in code:
        return code
    if is_index:
        return f"{code}.SZ" if code.startswith("399") else f"{code}.SH"
    return ts_code(code)


def _trade_dates(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(row.get("trade_date") or "") for row in rows if row.get("trade_date")})


def _trim_recent_trade_dates(rows: list[dict[str, Any]], ndays: int) -> list[dict[str, Any]]:
    if not rows:
        return []
    recent_dates = _trade_dates(rows)[-max(1, int(ndays or 1)) :]
    recent_set = set(recent_dates)
    return [row for row in rows if str(row.get("trade_date") or "") in recent_set]


# ═════════════════════════════════════════════════════════════════════════════
# 融资融券明细（全市场批量）
# ═════════════════════════════════════════════════════════════════════════════


def fetch_margin_detail_batch(trade_date: str | None = None) -> list[dict[str, Any]]:
    """按交易日批量获取全市场融资融券明细（margin_detail 接口）。

    比逐股 margin 更高效，一次拉 4000+ 条，适合日常同步。
    """
    pro = get_pro()
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    try:
        df = pro.margin_detail(trade_date=td)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_margin_detail_batch: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        code = str(row.get("ts_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        result.append({
            "code": code,
            "trade_date": _fmt_date(str(row.get("trade_date", td))),
            "rzye": _f(row.get("rzye")) / 1e8,         # 融资余额(亿)
            "rqye": _f(row.get("rqye")) / 1e8,         # 融券余额(亿)
            "rzmre": _f(row.get("rzmre")) / 1e8,        # 融资买入额(亿)
            "rzche": _f(row.get("rzche")) / 1e8,        # 融资偿还额(亿)
            "rzrqye": _f(row.get("rzrqye")) / 1e8,      # 融资融券余额(亿)
            "rqyl": _f(row.get("rqyl")),                 # 融券余量(股)
            "rqmcl": _f(row.get("rqmcl")),               # 融券卖出量(股)
            "rzrqye_md": _f(row.get("rzrqye")) / 1e8,   # 融资融券余额(亿,同字段)
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 大宗交易
# ═════════════════════════════════════════════════════════════════════════════


def fetch_block_trade(trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取大宗交易数据。折价率=机构态度，大宗交易量=大资金动向。"""
    pro = get_pro()
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    try:
        df = pro.block_trade(trade_date=td)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_block_trade: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        code = str(row.get("ts_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        price = _f(row.get("price"))
        vol = _f(row.get("vol"))
        amount = _f(row.get("amount"))
        result.append({
            "code": code,
            "trade_date": _fmt_date(str(row.get("trade_date", td))),
            "price": price,
            "vol": vol,                     # 成交量(万股)
            "amount": amount,               # 成交额(万元)
            "amount_yi": amount / 1e4,       # 成交额(亿)
            "buyer": str(row.get("buyer", "")),
            "seller": str(row.get("seller", "")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 北向资金
# ═════════════════════════════════════════════════════════════════════════════


def fetch_hsgt_flow(trade_date: str | None = None) -> dict[str, Any] | None:
    """获取沪深港通资金流向（全市场汇总，1条/天）。"""
    pro = get_pro()
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    try:
        df = pro.moneyflow_hsgt(trade_date=td)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_hsgt_flow: best-effort fallback after %r", _ble_exc)
        return None
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    return {
        "trade_date": _fmt_date(str(row.get("trade_date", td))),
        "north_money": _f(row.get("north_money")) / 1e4,  # 北向资金(亿)
        "south_money": _f(row.get("south_money")) / 1e4,  # 南向资金(亿)
        "hgt": _f(row.get("hgt")),                        # 沪股通(万元)
        "sgt": _f(row.get("sgt")),                        # 深股通(万元)
        "ggt_ss": _f(row.get("ggt_ss")),                  # 港股通(沪)(万元)
        "ggt_sz": _f(row.get("ggt_sz")),                  # 港股通(深)(万元)
    }


def fetch_hsgt_top10(trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取北向持股 TOP10。"""
    pro = get_pro()
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    try:
        df = pro.hsgt_top10(trade_date=td)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_hsgt_top10: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        code = str(row.get("ts_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        result.append({
            "code": code,
            "trade_date": _fmt_date(str(row.get("trade_date", td))),
            "name": str(row.get("name", "")),
            "close": _f(row.get("close")),
            "change": _f(row.get("change")),
            "rank": int(_f(row.get("rank"))),
            "market_type": str(row.get("market_type", "")),
            "amount": _f(row.get("amount")),           # 持股数量(股)
            "net_amount": _f(row.get("net_amount")),   # 持股变化(股)
            "buy": _f(row.get("buy")),                 # 买入(股)
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 财务指标（季报）
# ═════════════════════════════════════════════════════════════════════════════


def fetch_fina_indicator(
    code: str,
    limit: int = 4,
    trade_date: str | None = None,
) -> list[dict[str, Any]]:
    """获取个股财务指标（fina_indicator 接口）。

    缺失字段保留为 None（不伪装成 0），由消费端区分"无数据"和"真实 0"。

    Parameters
    ----------
    code : str
        6 位股票代码。
    limit : int
        返回最近几期报告，默认 4（即最近一年）。
    trade_date : str, optional
        YYYY-MM-DD 或 YYYYMMDD。指定时只返回该日之前（含）的报告期。
    """
    pro = get_pro()
    tsc = ts_code(code)
    target = _compact_trade_date(trade_date)
    try:
        df = pro.fina_indicator(ts_code=tsc, limit=limit)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_fina_indicator: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    if target:
        if "ann_date" in df.columns:
            df = df[df["ann_date"].astype(str) <= target]
        elif "end_date" in df.columns:
            df = df[df["end_date"].astype(str) <= target]
    result: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        end_raw = str(row.get("end_date", ""))
        ann_raw = str(row.get("ann_date", ""))
        result.append({
            "code": code,
            "end_date": end_raw,
            "ann_date": ann_raw,
            "roe": _safe_float(row.get("roe")),
            "grossprofit_margin": _safe_float(row.get("grossprofit_margin")),
            "netprofit_margin": _safe_float(row.get("netprofit_margin")),
            "netprofit_yoy": _safe_float(row.get("netprofit_yoy")),
            "or_yoy": _safe_float(row.get("or_yoy")),
            "eps": _safe_float(row.get("eps")),
            "bps": _safe_float(row.get("bps")),
        })
    return result


def fetch_fina_indicator_batch(
    trade_date: str | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """按公告日批量获取全市场财务指标。

    适合 backfill 场景：一次拉取某日之前公告的所有财报数据，返回
    最近 limit 期的指标。缺失字段保留为 None。
    """
    pro = get_pro()
    target = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    try:
        df = pro.fina_indicator(ann_date=target)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("fetch_fina_indicator_batch: best-effort fallback after %r", _ble_exc)
        return []
    if df is None or df.empty:
        return []
    result: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        ts = str(row.get("ts_code", ""))
        code = ts.split(".")[0]
        if len(code) != 6:
            continue
        end_raw = str(row.get("end_date", ""))
        ann_raw = str(row.get("ann_date", ""))
        result.append({
            "code": code,
            "end_date": end_raw,
            "ann_date": ann_raw,
            "roe": _safe_float(row.get("roe")),
            "grossprofit_margin": _safe_float(row.get("grossprofit_margin")),
            "netprofit_margin": _safe_float(row.get("netprofit_margin")),
            "netprofit_yoy": _safe_float(row.get("netprofit_yoy")),
            "or_yoy": _safe_float(row.get("or_yoy")),
            "eps": _safe_float(row.get("eps")),
            "bps": _safe_float(row.get("bps")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# helpers
# ═════════════════════════════════════════════════════════════════════════════


