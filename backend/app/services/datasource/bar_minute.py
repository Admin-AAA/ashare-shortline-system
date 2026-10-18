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


_GATEWAY: DatasetGateway | None = None


def gateway() -> DatasetGateway:
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = DatasetGateway([XiaoshiMinuteProvider(), BaostockMinuteProvider(), TushareStkMinsProvider()])
    return _GATEWAY


def fetch(code: str, start_date: str, end_date: str, freq: str, trace: Any = None) -> GatewayResult:
    return gateway().fetch_first(DATASET, {
        "code": code, "start_date": start_date, "end_date": end_date, "freq": freq, "trace": trace,
    })
