"""Dataset ``bar.minute``: xiaoshi 1-min history (resampled) -> Baostock (5m+) | Tushare stk_mins (1m).

Adapters wrap the existing, unchanged fetchers in ``tushare_client`` so normalized rows
are byte-identical; the gateway adds DB enable switches, a cross-process shared limiter
per adapter, circuit breakers and ``dataset_requests`` metrics.  Per-provider
post-processing (pre_close attach, latest-day trim) stays with the caller exactly as before.
"""
from __future__ import annotations

from typing import Any, Mapping

from app.services.datasource.gateway import DatasetGateway, GatewayResult

DATASET = "bar.minute"
BAOSTOCK_RPM = 17  # <= 60 / 3.5s min interval; the per-query interval inside the fetcher still applies


def _limiter(key: str, rpm: int):
    from app.services.tushare_transport import _make_limiter

    return _make_limiter(f"gateway:{key}", rpm)


class XiaoshiMinuteProvider:
    name = "xiaoshi_min1"
    config_keys = (("xiaoshi", "kline"),)

    def __init__(self) -> None:
        from app.core.config import get_settings

        self._lim = _limiter(self.name, int(getattr(get_settings(), "xiaoshi_max_qps", 30)) * 60)

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset == DATASET and not query.get("is_index")

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> list[dict[str, Any]]:
        from app.services import tushare_client as tc

        self._lim.acquire()
        return tc._fetch_xiaoshi_minute_kbars(
            query["code"], start_date=query["start_date"], end_date=query["end_date"],
            freq=query["freq"], trace=query.get("trace"),
        )


class BaostockMinuteProvider:
    name = "baostock"
    config_keys = (("baostock", "kline"),)

    def __init__(self) -> None:
        self._lim = _limiter(self.name, BAOSTOCK_RPM)

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset == DATASET and query.get("freq") != "1min"

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> list[dict[str, Any]]:
        from app.services import tushare_client as tc

        self._lim.acquire()
        return tc._fetch_baostock_minute_kbars(
            query["code"], query["freq"], query["start_date"], query["end_date"], trace=query.get("trace"),
        )


class TushareStkMinsProvider:
    """stk_mins through the Tushare router (its own limiter/LB/breakers apply)."""

    name = "tushare_stk_mins"
    config_keys: tuple[tuple[str, str], ...] = ()

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset == DATASET and query.get("freq") == "1min"

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> list[dict[str, Any]]:
        from app.services import tushare_client as tc

        return tc._fetch_tushare_minute_kbars(
            query["code"], start_time=f"{query['start_date']} 09:00:00",
            end_time=f"{query['end_date']} 15:30:00", trace=query.get("trace"),
            **({"is_index": True} if query.get("is_index") else {}),
        )


def _web_backups() -> list:
    from app.services.datasource.web_backups import SinaKlineProvider, TencentMinuteProvider

    return [SinaKlineProvider(), TencentMinuteProvider()]


_GATEWAY: DatasetGateway | None = None


def gateway() -> DatasetGateway:
    global _GATEWAY
    if _GATEWAY is None:
        from app.services.datasource import retired as _retired

        providers = [
            XiaoshiMinuteProvider(), BaostockMinuteProvider(), TushareStkMinsProvider(),
            *_web_backups(),  # flags bar.minute.sina / bar.minute.tencent (default off)
        ]
        # 退役源不进链：小石端点不可达时它是链首，每次空耗 6~16 秒，8 线程并发补数会撞
        # 网关 120s deadline 成批放弃（L-200）。类保留，恢复订阅时删掉这行过滤即可复活。
        _GATEWAY = DatasetGateway([p for p in providers if not _retired.provider_retired(p.name)])
    return _GATEWAY


def fetch(
    code: str, start_date: str, end_date: str, freq: str, trace: Any = None, *, use_cache: bool = True
) -> GatewayResult:
    """ ``bar.minute`` 唯一入口。

    ``use_cache=True``（默认）时先走本地按月 parquet 缓存（优化项③）：命中直接返回、缺失才
    向上游补并落盘——回测/复盘重复取同一段历史不会再打上游。内部 ``minute_cache`` 补数时
    传 ``use_cache=False`` 走纯网关，避免自己调自己。
    """
    if use_cache:
        from app.services.datasource import minute_cache

        cached, meta, upstream = minute_cache.serve(code, start_date, end_date, freq, trace)
        if cached:
            return GatewayResult(
                cached, "minute_cache",
                [{"provider": "minute_cache", "outcome": "success", "rows": len(cached),
                  "months_cached": len(meta.get("months_cached") or []),
                  "months_fetched": len(meta.get("months_fetched") or []),
                  "empty_months": meta.get("empty_months") or []}],
            )
        if upstream is not None:
            return upstream   # 上游确实没数据：直接透传它的 attempts，避免再打一次
    return gateway().fetch_first(DATASET, {
        "code": code, "start_date": start_date, "end_date": end_date, "freq": freq, "trace": trace,
    })
