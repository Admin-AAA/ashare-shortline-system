"""Dataset ``quote.realtime``: promax rt_k -> ths market_data_cn -> sina realtime_quote -> tdx hq (pytdx) -> tencent (flag) -> akshare snapshot -> quantdata rt_min -> xiaoshi snapshot.

Fill mode: each provider is asked only for codes still missing.  Every adapter
returns ``{code6: row}`` normalized to ``SCHEMAS["quote.realtime"]``.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutTimeout
from datetime import date
from typing import Any, Mapping

from app.services.datasource.gateway import DatasetGateway, DataSourceError, ErrorKind

DATASET = "quote.realtime"
RT_MIN_MAX_CODES = 60  # rt_min is per-code; cap the fan-out of this last-resort tier
# Fast-fail (owner, 2026-10-19): a rate-limited promax used to stall every quote ~9s
# (limiter wait + retries) before Sina was tried. rt_k now gets a hard deadline and is
# skipped outright while its shared breaker is open.
RTK_DEADLINE_SECONDS = 1.5
_RTK_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rtk-fast")


def _f(v: Any) -> float:
    """归一化委托到 :mod:`datasource.adapter`（架构优化项①：同一逻辑不再各写一份）。"""
    from app.services.datasource.adapter import to_float

    return to_float(v)


def _ts(code6: str) -> str:
    from app.services.datasource.adapter import to_ts_code

    return to_ts_code(code6)


class RtkProvider:
    """promax rt_k (batch, comma-separated ts_code) via the Tushare router."""

    name = "tushare_rt_k"
    # generic gateway pre-check: skipped without a call while the transport breaker is open
    upstream_breaker_keys = ("tushare:promax:rt_k",)
    config_keys = (("promax", "tushare_provider"),)

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset == DATASET

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        from app.engine.trading_calendar import latest_allowed_quote_date
        from app.services.tushare_client import get_pro

        from app.services.datasource.cache import submit_ctx

        fut = submit_ctx(_RTK_POOL, get_pro().rt_k, ts_code=",".join(_ts(c) for c in query["keys"]))
        try:
            df = fut.result(timeout=RTK_DEADLINE_SECONDS)
        except _FutTimeout:
            fut.cancel()
            raise DataSourceError(
                ErrorKind.TIMEOUT, f"rt_k exceeded {RTK_DEADLINE_SECONDS}s", provider=self.name, dataset=dataset
            ) from None
        out: dict[str, dict[str, Any]] = {}
        if df is None or df.empty:
            return out
        quote_date = latest_allowed_quote_date(date.today())  # rt_k carries no date (weekend-safe)
        for _, r in df.iterrows():
            code6 = str(r.get("ts_code") or "").split(".")[0]
            if not code6:
                continue
            out[code6] = {
                "code": code6, "ts_code": str(r.get("ts_code") or ""), "name": str(r.get("name") or "").strip(),
                "trade_date": quote_date, "quote_time": "", "price": _f(r.get("close")),
                "open": _f(r.get("open")), "pre_close": _f(r.get("pre_close")), "high": _f(r.get("high")),
                "low": _f(r.get("low")), "volume": _f(r.get("vol")), "amount": _f(r.get("amount")),
                "source": self.name,
            }
        return out


class ThsRealtimeProvider:
    """同花顺 ``market_data_cn`` 基础数据：rt_k 之后的实时行情冗余（P0，2026-11-15 接入）。

    定位是**修 rt_k 单点**，不是替换主源：rt_k 仍然排第一，本条只在 rt_k 失败 / 熔断 /
    额度耗尽时补位，所以它在链上紧跟 RtkProvider。

    * ``market_data_cn`` 一次只能查同一市场的代码（混沪深直接报「一次性查询多支股票必须市场
      代码相同」），按 USHA/USZA/USTM 分组，最多 3 次调用。
    * 返回不带交易日，用 ``latest_allowed_quote_date`` 补齐（与 rt_k 同口径，周末安全）。
    * 限频与重连由 ``ths_client`` 的全局节流负责；单次失败按 DataSourceError 交给网关熔断。
    """

    name = "ths_realtime"
    config_keys = (("ths", "quote"),)

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset == DATASET

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        from app.engine.trading_calendar import latest_allowed_quote_date
        from app.services.datasource import ths_client

        groups: dict[str, list[str]] = {}
        for code6 in query["keys"]:
            venue = ths_client.venue_for(code6)
            if venue:
                groups.setdefault(venue, []).append(str(code6))
        if not groups:
            return {}
        quote_date = latest_allowed_quote_date(date.today())
        out: dict[str, dict[str, Any]] = {}
        for venue, codes in groups.items():
            rows = ths_client.call(
                "market_data_cn", [f"{venue}{c}" for c in codes], "基础数据", dataset=dataset
            )
            for raw in rows:
                code6 = str(raw.get("代码") or "")[len(venue):]
                price = _f(raw.get("价格"))
                if not code6 or price <= 0:
                    continue
                out[code6] = {
                    "code": code6, "ts_code": _ts(code6), "name": str(raw.get("名称") or "").strip(),
                    "trade_date": quote_date, "quote_time": "", "price": price,
                    "open": _f(raw.get("开盘价")), "pre_close": _f(raw.get("昨收价")),
                    "high": _f(raw.get("最高价")), "low": _f(raw.get("最低价")),
                    "volume": _f(raw.get("成交量")), "amount": _f(raw.get("总金额")),
                    "source": self.name,
                }
        if not out:
            raise DataSourceError(ErrorKind.BAD_DATA, "ths 实时行情为空", provider=self.name, dataset=dataset)
        return out


class RtMinProvider:
    """quantdata rt_min (per code, last 1-minute bar). Only price is day-meaningful;
    day open/high/low are unknown from one bar and reported as 0.0."""

    name = "tushare_rt_min"
    config_keys = (("quantdata", "tushare_provider"),)

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset == DATASET

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        from app.services.tushare_client import fetch_realtime_minute_kbar

        out: dict[str, dict[str, Any]] = {}
        for code6 in list(query["keys"])[:RT_MIN_MAX_CODES]:
            bar = fetch_realtime_minute_kbar(code6)
            if not bar or _f(bar.get("close")) <= 0:
                continue
            out[code6] = {
                "code": code6, "ts_code": bar.get("ts_code") or _ts(code6), "name": "",
                "trade_date": bar.get("trade_date") or "", "quote_time": bar.get("trade_time") or "",
                "price": _f(bar.get("close")), "open": 0.0, "pre_close": 0.0, "high": 0.0, "low": 0.0,
                "volume": 0.0, "amount": 0.0, "source": self.name,
            }
        return out


class XiaoshiSnapshotProvider:
    name = "xiaoshi_snapshot"
    config_keys = (("xiaoshi", "quote"),)

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset == DATASET

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        from app.services.market_source.port import QuoteRequest
        from app.services.xiaoshi_market_source import XiaoshiMarketSourceAdapter

        wanted = set(query["keys"])
        batch = XiaoshiMarketSourceAdapter().fetch_quotes(QuoteRequest(max_quote_count=6000))
        if batch.errors:
            raise DataSourceError(ErrorKind.UPSTREAM, batch.errors[0].message, provider=self.name, dataset=dataset)
        out: dict[str, dict[str, Any]] = {}
        for r in batch.as_legacy_rows():
            code6 = str(r.get("code") or "").split(".")[0]
            if code6 not in wanted or _f(r.get("price")) <= 0:
                continue
            td = str(r.get("trade_date") or r.get("date") or "")
            out[code6] = {
                "code": code6, "ts_code": _ts(code6), "name": str(r.get("name") or ""),
                "trade_date": f"{td[:4]}-{td[4:6]}-{td[6:8]}" if len(td) == 8 and td.isdigit() else td,
                "quote_time": str(r.get("quote_time") or r.get("time") or ""), "price": _f(r.get("price")),
                "open": _f(r.get("open")), "pre_close": _f(r.get("pre_close")), "high": _f(r.get("high")),
                "low": _f(r.get("low")), "volume": _f(r.get("volume")), "amount": _f(r.get("amount")),
                "source": self.name,
            }
        return out


def _ths():
    """同花顺 thsdk 实时行情（P0 冗余，ASHARE_THS_ENABLED=false 关闭；见 datasource/ths_client.py）。"""
    from app.services.datasource import ths_client

    if not ths_client.is_enabled():
        return None
    return ThsRealtimeProvider()


def _sina():
    from app.services.datasource.sina_realtime import SinaProvider

    return SinaProvider()


def _tdx():
    from app.services.datasource.tdx_quote import TdxQuoteProvider

    return TdxQuoteProvider()  # 公开 HQ，无密钥；ASHARE_TDX_QUOTE_ENABLED=false 关闭


def _tencent():
    from app.services.datasource.web_backups import TencentQuoteProvider

    return TencentQuoteProvider()  # flag quote.realtime.tencent (default off => skipped)


def _akshare():
    from app.services.datasource.akshare_quote import AkshareSnapshotProvider

    return AkshareSnapshotProvider()  # 全市场快照（新浪翻页），ASHARE_QUOTE_AKSHARE_ENABLED=false 关闭


_GATEWAY: DatasetGateway | None = None


def gateway() -> DatasetGateway:
    global _GATEWAY
    if _GATEWAY is None:
        from app.services.datasource import retired as _retired

        providers = [RtkProvider(), _ths(), _sina(), _tdx(), _tencent(), _akshare(), RtMinProvider(), XiaoshiSnapshotProvider()]
        # 退役源（小石端点不可达）不进链；类保留，恢复订阅时去掉过滤即可复活。
        _GATEWAY = DatasetGateway([p for p in providers if p is not None and not _retired.provider_retired(p.name)])
    return _GATEWAY


def fetch(codes: list[str]) -> dict[str, dict[str, Any]]:
    return gateway().fetch_fill(DATASET, {}, codes).rows
