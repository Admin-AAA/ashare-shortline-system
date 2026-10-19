"""baostock (free, anonymous login) backup for Tushare ``trade_cal`` only.

Second backup after akshare in ``akshare_backup.backup_gateway()``; breaker / timeout /
retry come from DatasetGateway, enabled switch is data_source_config(baostock, tushare_provider).
"""
from __future__ import annotations

import threading
from typing import Any, Mapping

from app.services.datasource.gateway import DataSourceError, ErrorKind

PROVIDER_NAME = "baostock"
CONFIG_KEY = (PROVIDER_NAME, "tushare_provider")
_lock = threading.Lock()  # baostock keeps one global socket session


def _bs():
    import baostock  # lazy: optional dependency

    return baostock


def _iso(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def query_open_days(start: str, end: str) -> tuple[set[str], str]:
    """Returns (open days YYYYMMDD, last covered day YYYYMMDD) for [start, end]."""
    bs = _bs()
    with _lock:
        lg = bs.login()
        if str(getattr(lg, "error_code", "0")) != "0":
            raise DataSourceError(ErrorKind.NETWORK, f"baostock login: {lg.error_msg}", provider=PROVIDER_NAME)
        try:
            rs = bs.query_trade_dates(start_date=_iso(start), end_date=_iso(end))
            if str(rs.error_code) != "0":
                raise DataSourceError(ErrorKind.NETWORK, f"baostock query_trade_dates: {rs.error_msg}",
                                      provider=PROVIDER_NAME)
            opens: set[str] = set()
            last = ""
            while rs.next():
                row = dict(zip(rs.fields, rs.get_row_data()))
                day = str(row.get("calendar_date", "")).replace("-", "")
                if len(day) != 8:
                    continue
                last = max(last, day)
                if str(row.get("is_trading_day")) == "1":
                    opens.add(day)
            return opens, last
        finally:
            try:
                bs.logout()
            except Exception:  # noqa: BLE001
                pass


class BaostockTradeCalProvider:
    name = PROVIDER_NAME
    config_keys = (CONFIG_KEY,)

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset == "tushare.trade_cal"

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> list[dict[str, Any]]:
        from app.services.datasource.akshare_backup import _range, calendar_rows

        params = dict(query.get("params") or {})
        start, end = _range(params, "cal_date")
        opens, last = query_open_days(start, end)
        if not last:
            return []
        return calendar_rows(opens, start, min(end, last), params, "baostock")
