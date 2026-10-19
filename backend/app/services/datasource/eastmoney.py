"""Eastmoney adapter (push2 / push2ex / push2his / datacenter-web), no token.

Backups for datasets that had none: limit_up, concept_sector, capital_flow,
dragon_tiger (aux dispatch, flag ``aux.eastmoney``) and float.unlock future calendar,
margin.detail (daily sync, flag ``eastmoney.alt``).  Announcements/news already come
from ``eastmoney_news`` via ``ann.list``.  All requests share the ``eastmoney``
gateway bucket (2 req/s, cross-process) and send a browser UA + Referer; HTTP 567
(anti-crawl) opens the breaker immediately.  Output dicts match the Tushare fetchers'
shapes so callers/tables are unchanged.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
_UT = "7eea3edcaed734bea9cbfc24409ed989"
FLOAT_PREFIX = "eastmoney:"


class EastmoneyBlockedError(RuntimeError):
    pass


def _get(url: str, params: dict[str, Any], referer: str = "https://quote.eastmoney.com/") -> dict[str, Any]:
    import httpx

    from app.services.datasource.wrap import PROVIDERS, _limiter

    _limiter("eastmoney", PROVIDERS["eastmoney"].rpm).acquire()
    try:
        r = httpx.get(url, params=params, headers={"User-Agent": UA, "Referer": referer}, timeout=15.0)
    except httpx.RemoteProtocolError as exc:  # push2 drops the connection when it bans an IP
        raise EastmoneyBlockedError(f"eastmoney disconnected (anti-crawl): {exc}") from exc
    if r.status_code == 567:
        raise EastmoneyBlockedError("eastmoney HTTP 567 (anti-crawl)")
    r.raise_for_status()
    return r.json()


def _call(dataset: str, fn, *a, **kw):
    from app.services.datasource.wrap import gw_call

    return gw_call(dataset, "eastmoney", fn, *a, **kw)


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _hms(v: Any) -> str:
    s = str(int(v)) if v not in (None, "") else ""
    return f"{s.zfill(6)[:2]}:{s.zfill(6)[2:4]}:{s.zfill(6)[4:]}" if s else ""


def _dash(td: str) -> str:
    td = td.replace("-", "")
    return f"{td[:4]}-{td[4:6]}-{td[6:8]}"


def _datacenter(report: str, flt: str, columns: str = "ALL", sort: str = "", page_size: int = 500, max_pages: int = 40) -> list[dict]:
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        p = {"reportName": report, "columns": columns, "filter": flt, "pageNumber": page,
             "pageSize": page_size, "source": "WEB", "client": "WEB"}
        if sort:
            p.update(sortColumns=sort, sortTypes=1)
        j = _get("https://datacenter-web.eastmoney.com/api/data/v1/get", p, "https://data.eastmoney.com/")
        res = j.get("result") or {}
        out.extend(res.get("data") or [])
        if page >= int(res.get("pages") or 0):
            break
    return out


# ── limit_up (U/D/Z pools) ─────────────────────────────────────────────
def _pool(kind: str, td: str) -> list[dict]:
    path, sort = {"U": ("getTopicZTPool", "fbt:asc"), "D": ("getTopicDTPool", "fund:asc"), "Z": ("getTopicZBPool", "fbt:asc")}[kind]
    j = _get(f"https://push2ex.eastmoney.com/{path}",
             {"ut": _UT, "dpt": "wz.ztzt", "Pageindex": 0, "pagesize": 10000, "sort": sort, "date": td})
    return ((j.get("data") or {}).get("pool")) or []


def fetch_limit_up_board(trade_date: str) -> list[dict[str, Any]]:
    td = trade_date.replace("-", "")
    out: list[dict[str, Any]] = []
    for kind in ("U", "D", "Z"):
        for r in _pool(kind, td):
            code = str(r.get("c") or "")
            if len(code) != 6:
                continue
            zt = r.get("zttj") or {}
            out.append({
                "code": code, "name": str(r.get("n") or ""), "trade_date": _dash(td),
                "pct_chg": round(_f(r.get("zdp")), 4),
                "limit_times": int(r.get("lbc") or r.get("days") or 0), "limit_type": kind,
                "first_time": _hms(r.get("fbt")), "last_time": _hms(r.get("lbt")),
                "open_times": int(r.get("zbc") or r.get("oc") or 0),
                "up_stat": f"{zt['days']}/{zt['ct']}" if zt.get("days") else "",
                "limit_amount_yi": _f(r.get("amount")) / 1e8, "turnover_ratio": round(_f(r.get("hs")), 4),
                "fd_amount": _f(r.get("fund")) / 1e8, "industry": str(r.get("hybk") or ""),
            })
    return out


# ── concept sectors ────────────────────────────────────────────────────
def fetch_concept_sectors() -> list[dict[str, Any]]:
    j = _get("https://push2.eastmoney.com/api/qt/clist/get",
             {"pn": 1, "pz": 2000, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:90+t:3",
              "fields": "f12,f14,f104,f105,f106"})
    return [{
        "sector_code": str(r.get("f12") or ""), "sector_name": str(r.get("f14") or ""),
        "member_count": int(_f(r.get("f104")) + _f(r.get("f105")) + _f(r.get("f106"))),
        "exchange": "EM", "list_date": "", "type": "N",
    } for r in ((j.get("data") or {}).get("diff") or [])]


# ── capital flow (per code, latest day <= trade_date) ──────────────────
def _secid(code: str) -> str:
    return ("1." if code.startswith(("5", "6", "9")) else "0.") + code


def fetch_capital_flow(code: str, trade_date: str | None = None) -> dict[str, Any] | None:
    j = _get("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
             {"lmt": 10, "klt": 101, "secid": _secid(code), "fields1": "f1,f2,f3,f7",
              "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"})
    lines = (j.get("data") or {}).get("klines") or []
    want = _dash(trade_date) if trade_date else None
    for line in reversed(lines):
        p = line.split(",")
        if want and p[0] > want:
            continue
        return {
            "code": code, "trade_date": p[0],
            "main_net_inflow_yi": round(_f(p[1]) / 1e8, 4), "super_large_net_inflow_yi": round(_f(p[5]) / 1e8, 4),
            "large_net_inflow_yi": round(_f(p[4]) / 1e8, 4), "medium_net_inflow_yi": round(_f(p[3]) / 1e8, 4),
            "small_net_inflow_yi": round(_f(p[2]) / 1e8, 4), "main_net_inflow_ratio": round(_f(p[6]), 2),
        }
    return None


# ── dragon tiger ───────────────────────────────────────────────────────
def fetch_dragon_tiger(trade_date: str) -> list[dict[str, Any]]:
    d = _dash(trade_date)
    rows = _datacenter("RPT_DAILYBILLBOARD_DETAILSNEW", f"(TRADE_DATE='{d}')",
                       "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,EXPLANATION,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,BILLBOARD_NET_AMT")
    y = lambda v: None if v is None else round(_f(v) / 1e8, 4)  # noqa: E731
    return [{
        "code": str(r["SECURITY_CODE"]), "name": str(r.get("SECURITY_NAME_ABBR") or ""), "trade_date": d,
        "reason": str(r.get("EXPLANATION") or ""), "buy_amount_yi": y(r.get("BILLBOARD_BUY_AMT")),
        "sell_amount_yi": y(r.get("BILLBOARD_SELL_AMT")), "net_amount_yi": y(r.get("BILLBOARD_NET_AMT")),
    } for r in rows if len(str(r.get("SECURITY_CODE") or "")) == 6]


# ── float.unlock future calendar / margin.detail ──────────────────────
def fetch_float_future(from_date: str) -> list[dict[str, Any]]:
    rows = _datacenter("RPT_LIFT_STAGE", f"(FREE_DATE>='{_dash(from_date)}')",
                       "SECURITY_CODE,FREE_DATE,FREE_SHARES_TYPE,CURRENT_FREE_SHARES,TOTAL_RATIO", "FREE_DATE")
    return [{
        "code": str(r["SECURITY_CODE"]), "ann_date": None, "float_date": str(r["FREE_DATE"])[:10].replace("-", ""),
        "float_share": round(_f(r.get("CURRENT_FREE_SHARES")) * 1e4, 0),
        "float_ratio": None if r.get("TOTAL_RATIO") is None else round(_f(r["TOTAL_RATIO"]) * 100.0, 4),
        "holder_name": (FLOAT_PREFIX + str(r.get("FREE_SHARES_TYPE") or ""))[:120],
    } for r in rows if len(str(r.get("SECURITY_CODE") or "")) == 6]


def fetch_margin_detail(trade_date: str) -> list[dict[str, Any]]:
    d = _dash(trade_date)
    rows = _datacenter("RPTA_WEB_RZRQ_GGMX", f"(DATE='{d}')", "SCODE,DATE,RZYE,RQYE,RZMRE,RZCHE,RZRQYE,RQYL", page_size=500, max_pages=10)
    yi = lambda v: None if v is None else round(_f(v) / 1e8, 6)  # noqa: E731
    return [{
        "code": str(r["SCODE"]), "trade_date": d, "rzye": yi(r.get("RZYE")), "rqye": yi(r.get("RQYE")),
        "rzmre": yi(r.get("RZMRE")), "rzche": yi(r.get("RZCHE")), "rzrqye": yi(r.get("RZRQYE")),
        "rqyl": None if r.get("RQYL") is None else _f(r["RQYL"]),
    } for r in rows if len(str(r.get("SCODE") or "")) == 6]


# ── gateway entry points ───────────────────────────────────────────────
def aux_enabled() -> bool:
    from app.services.datasource.gateway import is_cutover

    return is_cutover("aux.eastmoney")


def limit_up(td: str) -> list[dict[str, Any]]:
    return _call("aux.limit_up", fetch_limit_up_board, td)


def concept_sectors() -> list[dict[str, Any]]:
    return _call("aux.concept_sector", fetch_concept_sectors)


def capital_flow(code: str, td: str | None) -> dict[str, Any] | None:
    return _call("aux.capital_flow", fetch_capital_flow, code, td)


def dragon_tiger(td: str) -> list[dict[str, Any]]:
    return _call("aux.dragon_tiger", fetch_dragon_tiger, td)


def sync_eastmoney_alt(db, trade_date: str) -> dict[str, Any]:
    """Daily (flag ``eastmoney.alt``): fill margin_detail gaps + refresh future unlock calendar."""
    from sqlalchemy import select, text

    from app.models.tables import MarginDetail, ShareFloat
    from app.services.datasource.gateway import is_cutover

    result: dict[str, Any] = {"margin_inserted": 0, "float_inserted": 0, "errors": []}
    if not is_cutover("eastmoney.alt"):
        result["skipped"] = "flag eastmoney.alt off"
        return result
    try:
        margin = _call("margin.detail", fetch_margin_detail, trade_date)
        have = {c for (c,) in db.execute(select(MarginDetail.code).where(MarginDetail.trade_date == _dash(trade_date)))}
        new = [r for r in margin if r["code"] not in have]
        if new:
            db.bulk_insert_mappings(MarginDetail, new)
            db.commit()
        result.update(margin_inserted=len(new), margin_rows=len(margin))
    except Exception as exc:  # noqa: BLE001 - non-blocking backup
        db.rollback()
        result["errors"].append(f"margin: {str(exc)[:120]}")
    try:
        floats = _call("float.unlock", fetch_float_future, trade_date)
        have_f = {tuple(r) for r in db.execute(text(
            "SELECT code, float_date, holder_name FROM share_floats WHERE holder_name LIKE :p"), {"p": FLOAT_PREFIX + "%"})}
        new_f, seen = [], set()
        for r in floats:
            k = (r["code"], r["float_date"], r["holder_name"])
            if k in have_f or k in seen:
                continue
            seen.add(k)
            new_f.append(r)
        if new_f:
            db.bulk_insert_mappings(ShareFloat, new_f)
            db.commit()
        result.update(float_inserted=len(new_f), float_rows=len(floats))
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        result["errors"].append(f"float: {str(exc)[:120]}")
    return result
