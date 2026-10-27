"""Dataset ``quote.realtime``: promax rt_k -> sina realtime_quote -> tencent (flag) -> akshare snapshot -> quantdata rt_min -> xiaoshi snapshot.

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
    try:
        return float(v) if v is not None and v == v else 0.0
    except (TypeError, ValueError):
        return 0.0


def _ts(code6: str) -> str:
    from app.services.tushare_client import ts_code

    return ts_code(code6)


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


def _sina():
    from app.services.datasource.sina_realtime import SinaProvider

    return SinaProvider()


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
        _GATEWAY = DatasetGateway([RtkProvider(), _sina(), _tencent(), _akshare(), RtMinProvider(), XiaoshiSnapshotProvider()])
    return _GATEWAY


def fetch(codes: list[str]) -> dict[str, dict[str, Any]]:
    return gateway().fetch_fill(DATASET, {}, codes).rows
