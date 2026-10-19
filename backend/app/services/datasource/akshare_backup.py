"""akshare (free, tokenless) backup provider behind Tushare for a few event/aux APIs.

Exposed as gateway datasets ``tushare.<api>`` so rows come back in the Tushare field
shape and existing ``get_pro().<api>()`` callers are unchanged.  Routing order is
always Tushare first (``TushareProviderRouter``), akshare only after Tushare failed or
returned empty (see ``tushare_client._TushareSession._call``).

APIs and upstreams (all verified on akshare==1.19.1):
* ``margin_detail``  <- stock_margin_detail_sse + stock_margin_detail_szse (1 req / exchange / day;
  both must succeed, never a half-market answer)
* ``stk_surv``       <- stock_jgdy_tj_em (东财机构调研统计; window <= 62 days)
* ``anns_d``         <- stock_notice_report("全部", day) (whole market per day, <= 7 days)
* ``trade_cal``      <- tool_trade_date_hist_sina (all SSE open days; days in range up to the
  last listed date get is_open 0/1); then baostock query_trade_dates (baostock_backup)
* ``pledge_stat``    <- stock_gpzy_pledge_ratio_em (中登 weekly, whole market per date;
  history back to 2018 available)

Breaker / timeout / retry come from ``DatasetGateway``; rate limit is a shared limiter
(``gateway:akshare``); enabled switch is ``data_source_config(akshare, tushare_provider)``.
Every row carries ``source`` (akshare_sse / akshare_szse / akshare_jgdy_em / ...).
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Mapping

from app.services.datasource.gateway import DataSourceError, DatasetGateway, ErrorKind

PROVIDER_NAME = "akshare"
CONFIG_KEY = (PROVIDER_NAME, "tushare_provider")
AKSHARE_BACKUP_APIS: frozenset[str] = frozenset({"margin_detail", "stk_surv", "anns_d", "pledge_stat", "trade_cal"})
AKSHARE_RPM = 30
_CACHE_TTL = 600.0
MAX_SURV_WINDOW_DAYS = 62
MAX_ANNS_DAYS = 7

_cache: dict[tuple, tuple[float, list[dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


def _ak():
    import akshare  # lazy: heavy import, optional dependency

    return akshare


def _limit() -> None:
    from app.services.datasource.web_backups import _lim

    _lim("akshare", AKSHARE_RPM).acquire()


def suffix(code6: str) -> str:
    if code6.startswith(("4", "8", "92")):
        return "BJ"
    return "SH" if code6.startswith(("5", "6", "9")) else "SZ"


def ts_code(code6: str) -> str:
    code6 = str(code6).strip().zfill(6)
    return f"{code6}.{suffix(code6)}"


def _compact(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        s = str(value).replace(",", "").strip()
        if s in ("", "-", "nan", "None", "NaN"):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    v = _num(value)
    return None if v is None else int(v)


def _str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return None if s in ("", "nan", "None", "NaT") else s


def _day(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def _records(df: Any) -> list[dict[str, Any]]:
    if df is None or getattr(df, "empty", True):
        return []
    return df.to_dict("records")


def _call(fn_name: str, **kwargs: Any) -> list[dict[str, Any]]:
    """One rate-limited akshare call; native schema drift -> CONTRACT (network errors propagate)."""
    key = (fn_name, tuple(sorted(kwargs.items())))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    _limit()
    fn: Callable[..., Any] = getattr(_ak(), fn_name)
    try:
        rows = _records(fn(**kwargs))
    except (KeyError, IndexError, AttributeError) as exc:
        raise DataSourceError(ErrorKind.CONTRACT, f"{fn_name}: {exc!r}", provider=PROVIDER_NAME) from exc
    except TypeError as exc:  # EM endpoints answer result=null for empty days
        if "NoneType" in str(exc):
            rows = []
        else:
            raise DataSourceError(ErrorKind.CONTRACT, f"{fn_name}: {exc!r}", provider=PROVIDER_NAME) from exc
    with _cache_lock:
        _cache[key] = (now + _CACHE_TTL, rows)
    return rows


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _range(params: Mapping[str, Any], single: str) -> tuple[str, str]:
    one = _compact(params.get(single))
    start = one or _compact(params.get("start_date"))
    end = one or _compact(params.get("end_date")) or start
    if not start:
        raise DataSourceError(ErrorKind.INVALID_REQUEST, f"akshare backup needs {single} or start_date",
                              provider=PROVIDER_NAME)
    return start, end


def _filter_code(rows: list[dict[str, Any]], params: Mapping[str, Any]) -> list[dict[str, Any]]:
    want = {c.strip().upper() for c in str(params.get("ts_code") or "").split(",") if c.strip()}
    return [r for r in rows if r["ts_code"] in want] if want else rows


# ── per-API mappers (output = Tushare field names) ──────────────────────────
def map_margin_sse(rows: list[dict[str, Any]], td: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        code = str(r.get("标的证券代码") or "").strip()
        if len(code) != 6:
            continue
        out.append({
            "trade_date": _compact(r.get("信用交易日期")) or td, "ts_code": ts_code(code),
            "name": _str(r.get("标的证券简称")),
            "rzye": _num(r.get("融资余额")), "rqye": None, "rzmre": _num(r.get("融资买入额")),
            "rqyl": _num(r.get("融券余量")), "rzche": _num(r.get("融资偿还额")), "rqchl": _num(r.get("融券偿还量")),
            "rqmcl": _num(r.get("融券卖出量")), "rzrqye": None, "source": "akshare_sse",
        })
    return out


def map_margin_szse(rows: list[dict[str, Any]], td: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        code = str(r.get("证券代码") or "").strip().zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        out.append({
            "trade_date": td, "ts_code": ts_code(code), "name": _str(r.get("证券简称")),
            "rzye": _num(r.get("融资余额")), "rqye": _num(r.get("融券余额")), "rzmre": _num(r.get("融资买入额")),
            "rqyl": _num(r.get("融券余量")), "rzche": None, "rqchl": None,
            "rqmcl": _num(r.get("融券卖出量")), "rzrqye": _num(r.get("融资融券余额")), "source": "akshare_szse",
        })
    return out


def map_surv(rows: list[dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        code = str(r.get("代码") or "").strip()
        sd = _compact(r.get("接待日期"))
        if len(code) != 6 or not sd or not (start <= sd <= end):
            continue
        out.append({
            "ts_code": ts_code(code), "name": _str(r.get("名称")), "surv_date": sd,
            "fund_visitors": _str(r.get("接待人员")), "rece_place": _str(r.get("接待地点")),
            "rece_mode": _str(r.get("接待方式")), "rece_org": None, "org_type": None, "comp_rece": None,
            "org_count": _int(r.get("接待机构数量")), "ann_date": _compact(r.get("公告日期")) or None,
            "source": "akshare_jgdy_em",
        })
    return out


def map_anns(rows: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        code = str(r.get("代码") or "").strip()
        title = _str(r.get("公告标题"))
        if len(code) != 6 or not title:
            continue
        out.append({
            "ts_code": ts_code(code), "symbol": code, "name": _str(r.get("名称")), "title": title,
            "ann_type": _str(r.get("公告类型")), "ann_date": _compact(r.get("公告日期")) or day,
            "ann_time": None, "url": _str(r.get("网址")), "source": "akshare_notice_em",
        })
    return out


def map_pledge(rows: list[dict[str, Any]], end_date: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        code = str(r.get("股票代码") or "").strip()
        if len(code) != 6:
            continue
        out.append({
            "ts_code": ts_code(code), "end_date": _compact(r.get("交易日期")) or end_date,
            "pledge_count": _int(r.get("质押笔数")),
            "unrest_pledge": _num(r.get("无限售股质押数")),  # 万股, same unit as Tushare
            "rest_pledge": _num(r.get("限售股质押数")),
            "total_share": None, "rest_total": None,
            "pledge_ratio": _num(r.get("质押比例")),  # %
            "source": "akshare_gpzy_em",
        })
    return out


def calendar_rows(opens: set[str], start: str, end: str, params: Mapping[str, Any],
                  source: str) -> list[dict[str, Any]]:
    """Tushare trade_cal shape for every calendar day in [start, end]; honours is_open filter."""
    out: list[dict[str, Any]] = []
    d, last = _day(start), _day(end)
    want = str(params.get("is_open") or "").strip()
    while d <= last:
        day = d.strftime("%Y%m%d")
        is_open = 1 if day in opens else 0
        if not want or str(is_open) == want:
            out.append({"exchange": str(params.get("exchange") or "SSE"), "cal_date": day,
                        "is_open": is_open, "source": source})
        d += timedelta(days=1)
    return out


# ── provider ────────────────────────────────────────────────────────────────
class AkshareProvider:
    name = PROVIDER_NAME
    config_keys = (CONFIG_KEY,)

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset.startswith("tushare.") and dataset.split(".", 1)[1] in AKSHARE_BACKUP_APIS

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> list[dict[str, Any]]:
        api = dataset.split(".", 1)[1]
        params = dict(query.get("params") or {})
        return _filter_code(getattr(self, f"_{api}")(params), params)

    def _margin_detail(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        td = _compact(params.get("trade_date"))
        if not td:
            raise DataSourceError(ErrorKind.INVALID_REQUEST, "margin_detail backup needs trade_date",
                                  provider=PROVIDER_NAME)
        sse = map_margin_sse(_call("stock_margin_detail_sse", date=td), td)
        szse = map_margin_szse(_call("stock_margin_detail_szse", date=td), td)
        if not sse or not szse:  # never hand back half the market
            return []
        return sse + szse

    def _stk_surv(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        start, end = _range(params, "trade_date")
        if (date.today() - _day(start)).days > MAX_SURV_WINDOW_DAYS:
            raise DataSourceError(ErrorKind.INVALID_REQUEST, "stk_surv backup window too old",
                                  provider=PROVIDER_NAME)
        since = (_day(start) - timedelta(days=1)).strftime("%Y%m%d")
        return map_surv(_call("stock_jgdy_tj_em", date=since), start, end)

    def _anns_d(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        start, end = _range(params, "ann_date")
        d0, d1 = _day(start), _day(end)
        if (d1 - d0).days >= MAX_ANNS_DAYS:
            raise DataSourceError(ErrorKind.INVALID_REQUEST, "anns_d backup range > 7 days", provider=PROVIDER_NAME)
        out: list[dict[str, Any]] = []
        d = d0
        while d <= d1:
            day = d.strftime("%Y%m%d")
            out += map_anns(_call("stock_notice_report", symbol="全部", date=day), day)
            d += timedelta(days=1)
        return out

    def _trade_cal(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        start, end = _range(params, "cal_date")
        opens = {_compact(r.get("trade_date")) for r in _call("tool_trade_date_hist_sina")}
        opens.discard("")
        if not opens:
            return []
        return calendar_rows(opens, start, min(end, max(opens)), params, "akshare_sina")

    def _pledge_stat(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        # 中登 data is weekly (Fridays).  end_date given: that date, else the last 3 Fridays <= it;
        # no end_date (per-stock "latest"): last 3 Fridays from today.  Whole market cached per date.
        end = _compact(params.get("end_date"))
        if end:
            rows = map_pledge(_call("stock_gpzy_pledge_ratio_em", date=end), end)
            if rows:
                return rows
        d = _day(end) if end else date.today()
        d -= timedelta(days=(d.weekday() - 4) % 7)
        if end and d.strftime("%Y%m%d") == end:
            d -= timedelta(days=7)
        for _ in range(3):
            day = d.strftime("%Y%m%d")
            rows = map_pledge(_call("stock_gpzy_pledge_ratio_em", date=day), day)
            if rows:
                return rows
            d -= timedelta(days=7)
        return []


_GATEWAY: DatasetGateway | None = None


def backup_gateway() -> DatasetGateway:
    global _GATEWAY
    if _GATEWAY is None:
        from app.services.datasource.baostock_backup import BaostockTradeCalProvider

        _GATEWAY = DatasetGateway([AkshareProvider(), BaostockTradeCalProvider()])
    return _GATEWAY


def backup_enabled() -> bool:
    from app.services.datasource.gateway import provider_enabled

    return provider_enabled(AkshareProvider())
