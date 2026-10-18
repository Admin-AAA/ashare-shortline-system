"""Generic CNINFO (巨潮资讯) full-text announcement search — gateway dataset ``ann.search.cninfo``.

POST http://www.cninfo.com.cn/new/hisAnnouncement/query (form data), paginated (pageSize 30).
Reusable by any announcement strategy (股权激励草案、回购、业绩预告 …): pass ``searchkey`` and/or
``category`` (e.g. ``category_gqjl_szsh``) plus a date range.

Gateway: provider ``cninfo_search`` (shared cross-process limiter 20 req/min, circuit breaker,
``dataset_requests``/``dataset_latency`` metrics, DB enable switch ``cninfo/news``). Always on
(``gateway.ALWAYS_GATEWAY``) — there is no legacy path.

Public entry point::

    search_announcements(searchkey="", category="", start="YYYY-MM-DD", end="YYYY-MM-DD",
                         column="szse", plate="", max_pages=50, page_size=30) -> list[dict]

Each row: secCode, secName, announcementTitle (HTML <em> stripped), announcementTime (Beijing
datetime, tz-aware), announcementId, adjunctUrl, pdf_url, raw (the original item dict).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

DATASET = "ann.search.cninfo"
PROVIDER = "cninfo_search"
URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
REFERER = "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"
PDF_BASE = "http://static.cninfo.com.cn/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
BEIJING = timezone(timedelta(hours=8))
_TAG = re.compile(r"<[^>]+>")


class CninfoBlockedError(RuntimeError):
    """403/429 or HTML anti-crawl page — opens the breaker immediately."""


def _post_page(form: dict[str, Any], *, timeout: float = 15.0) -> dict[str, Any]:
    import httpx

    from app.services.datasource.gateway import DataSourceError, ErrorKind

    headers = {"User-Agent": UA, "Referer": REFERER, "X-Requested-With": "XMLHttpRequest",
               "Accept": "application/json, text/javascript, */*; q=0.01"}
    r = httpx.post(URL, data=form, headers=headers, timeout=timeout)
    if r.status_code in (403, 429):
        raise CninfoBlockedError(f"cninfo HTTP {r.status_code}")
    if r.status_code >= 500:
        raise DataSourceError(ErrorKind.UPSTREAM, f"HTTP {r.status_code}", provider=PROVIDER, dataset=DATASET)
    if r.status_code >= 400:
        raise DataSourceError(ErrorKind.INVALID_REQUEST, f"HTTP {r.status_code}", provider=PROVIDER, dataset=DATASET)
    try:
        payload = r.json()
    except ValueError as exc:
        raise CninfoBlockedError("cninfo returned non-JSON (anti-crawl page?)") from exc
    if not isinstance(payload, dict):
        raise DataSourceError(ErrorKind.CONTRACT, "payload not an object", provider=PROVIDER, dataset=DATASET)
    return payload


def normalize(item: dict[str, Any]) -> dict[str, Any]:
    ts = item.get("announcementTime")
    when = None
    if isinstance(ts, (int, float)) and ts > 0:
        when = datetime.fromtimestamp(ts / 1000.0, tz=BEIJING)
    adj = str(item.get("adjunctUrl") or "")
    return {
        "secCode": str(item.get("secCode") or ""),
        "secName": str(item.get("secName") or ""),
        "announcementTitle": _TAG.sub("", str(item.get("announcementTitle") or "")).strip(),
        "announcementTime": when,
        "announcementId": str(item.get("announcementId") or ""),
        "adjunctUrl": adj,
        "pdf_url": (PDF_BASE + adj) if adj else "",
        "raw": dict(item),
    }


def _fetch_all(form_base: dict[str, Any], max_pages: int) -> list[dict[str, Any]]:
    from app.services.datasource.gateway import validate_rows

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    from app.services.datasource.wrap import PROVIDERS, _limiter

    for page in range(1, max(1, int(max_pages)) + 1):
        if page > 1:  # page 1 is paced by gw_call; every further page takes its own token
            _limiter(PROVIDER, PROVIDERS[PROVIDER].rpm).acquire()
        payload = _post_page({**form_base, "pageNum": page})
        items = payload.get("announcements") or []
        for it in items:
            row = normalize(it)
            key = row["announcementId"] or f"{row['secCode']}:{row['announcementTitle']}:{row['raw'].get('announcementTime')}"
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        if not items or not payload.get("hasMore"):
            break
    validate_rows(DATASET, out, provider=PROVIDER)
    return out


def search_announcements(
    searchkey: str = "",
    category: str = "",
    start: str | date | None = None,
    end: str | date | None = None,
    *,
    column: str = "szse",
    plate: str = "",
    stock: str = "",
    max_pages: int = 50,
    page_size: int = 30,
) -> list[dict[str, Any]]:
    """Search CNINFO announcements through the gateway (limiter + breaker + metrics). Default range: last 30 days."""
    from app.services.datasource.wrap import gw_call

    end_d = date.fromisoformat(str(end)[:10]) if end else datetime.now(BEIJING).date()
    start_d = date.fromisoformat(str(start)[:10]) if start else end_d - timedelta(days=30)
    form = {
        "column": column, "tabName": "fulltext", "plate": plate, "stock": stock,
        "searchkey": searchkey, "secid": "", "category": category, "trade": "",
        "seDate": f"{start_d.isoformat()}~{end_d.isoformat()}",
        "pageSize": int(page_size), "sortName": "", "sortType": "", "isHLtitle": "true",
    }
    return gw_call(DATASET, PROVIDER, _fetch_all, form, max_pages)
