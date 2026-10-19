"""Tokenless web backups: Tencent qt.gtimg / ifzq and Sina getKLineData.

* ``quote.realtime``: TencentQuoteProvider (after sina, before rt_min), flag
  ``quote.realtime.tencent`` (default off -> provider unsupported, chain unchanged).
* ``bar.minute``: SinaKlineProvider then TencentMinuteProvider appended after the
  existing chain, flags ``bar.minute.sina`` / ``bar.minute.tencent`` (default off).
  Rows match the Baostock/xiaoshi minute shape; Tencent minute bars carry no
  turnover so ``amount`` is 0.0 (that is why Sina is ahead of it).
Shared limiters: tencent 300 rpm, sina_kline 120 rpm.  No-cache, GBK decode for qt.
"""
from __future__ import annotations

from typing import Any, Mapping

import httpx

from app.services.datasource.gateway import DataSourceError, ErrorKind, is_cutover

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"
_FREQ = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60}
_lims: dict[str, Any] = {}


def _lim(key: str, rpm: int):
    if key not in _lims:
        from app.services.tushare_transport import _make_limiter

        _lims[key] = _make_limiter(f"gateway:{key}", rpm)
    return _lims[key]


def _sym(code6: str) -> str:
    return ("sh" if code6.startswith(("5", "6", "9")) else "bj" if code6.startswith(("4", "8")) else "sz") + code6


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _get(key: str, rpm: int, url: str, referer: str = "") -> httpx.Response:
    _lim(key, rpm).acquire()
    r = httpx.get(url, headers={"User-Agent": UA, **({"Referer": referer} if referer else {})}, timeout=10.0)
    if r.status_code in (403, 429, 456):
        raise DataSourceError(ErrorKind.RATE_LIMIT, f"HTTP {r.status_code}", provider=key)
    r.raise_for_status()
    return r


# ── Tencent realtime ───────────────────────────────────────────────────
def parse_qt(text: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line in text.split(";"):
        if '="' not in line:
            continue
        p = line.split('="', 1)[1].rstrip('"\n ').split("~")
        if len(p) < 38 or _f(p[3]) <= 0:
            continue
        code6, ts = p[2], p[30]
        sfx = {"sh": "SH", "sz": "SZ", "bj": "BJ"}[_sym(code6)[:2]]
        out[code6] = {
            "code": code6, "ts_code": f"{code6}.{sfx}", "name": p[1].strip(),
            "trade_date": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else "",
            "quote_time": f"{ts[8:10]}:{ts[10:12]}:{ts[12:14]}" if len(ts) >= 14 else "",
            "price": _f(p[3]), "open": _f(p[5]), "pre_close": _f(p[4]), "high": _f(p[33]), "low": _f(p[34]),
            "volume": _f(p[36]) * 100.0, "amount": _f(p[37]) * 1e4, "source": "tencent_qt",
        }
    return out


class TencentQuoteProvider:
    name = "tencent_qt"
    config_keys = (("tencent", "quote"),)

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset == "quote.realtime" and is_cutover("quote.realtime.tencent")

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        keys = list(query["keys"])
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(keys), 60):
            r = _get("tencent", 300, "https://qt.gtimg.cn/q=" + ",".join(_sym(c) for c in keys[i:i + 60]))
            out.update(parse_qt(r.content.decode("gbk", errors="replace")))
        return out


# ── minute bars ────────────────────────────────────────────────────────
def _bar(code: str, day_time: str, o: Any, h: Any, lo: Any, c: Any, vol: float, amt: float, src: str) -> dict[str, Any]:
    o, h, lo, c = _f(o), _f(h), _f(lo), _f(c)
    return {
        "code": code, "trade_date": day_time[:10], "trade_time": day_time[:19],
        "open": round(o, 3), "high": round(max(h, o, c), 3), "low": round(min(lo, o, c), 3), "close": round(c, 3),
        "volume": vol, "amount": amt, "source": src,
    }


def _in_range(rows: list[dict], start: str, end: str) -> list[dict]:
    return [r for r in rows if start <= r["trade_date"] <= end]


def parse_sina_kline(code: str, items: list[dict]) -> list[dict[str, Any]]:
    return [_bar(code, str(it["day"]), it["open"], it["high"], it["low"], it["close"],
                 _f(it.get("volume")), _f(it.get("amount")), "sina_kline") for it in items or []]


def parse_tencent_mkline(code: str, items: list[list]) -> list[dict[str, Any]]:
    out = []
    for it in items or []:
        t = str(it[0])
        if len(t) < 12:
            continue
        out.append(_bar(code, f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}:00", it[1], it[3], it[4], it[2],
                        _f(it[5]) * 100.0, 0.0, "tencent_mkline"))
    return out


class SinaKlineProvider:
    name = "sina_kline"
    config_keys = (("sina", "kline"),)

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return (dataset == "bar.minute" and query.get("freq") in ("5min", "15min", "30min", "60min")
                and not query.get("is_index") and is_cutover("bar.minute.sina"))

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> list[dict[str, Any]]:
        code = query["code"]
        url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
               f"?symbol={_sym(code)}&scale={_FREQ[query['freq']]}&ma=no&datalen=1023")
        items = _get("sina_kline", 120, url, "https://finance.sina.com.cn").json()
        return _in_range(parse_sina_kline(code, items), query["start_date"], query["end_date"])


class TencentMinuteProvider:
    name = "tencent_mkline"
    config_keys = (("tencent", "kline"),)

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return (dataset == "bar.minute" and query.get("freq") in _FREQ and not query.get("is_index")
                and is_cutover("bar.minute.tencent"))

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> list[dict[str, Any]]:
        code, k = query["code"], f"m{_FREQ[query['freq']]}"
        j = _get("tencent", 300, f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={_sym(code)},{k},,800").json()
        items = ((j.get("data") or {}).get(_sym(code)) or {}).get(k) or []
        return _in_range(parse_tencent_mkline(code, items), query["start_date"], query["end_date"])
