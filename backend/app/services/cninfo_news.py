"""巨潮资讯（cninfo）个股公告 —— 东方财富被封时的第二回填/补数源。

免费公开接口，需先 topSearch 拿 orgId，再 hisAnnouncement/query。
全进程限速，避免打爆。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SEARCH_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
}
RISK_KEYWORDS = ("风险", "警示", "退市", "ST", "处罚", "监管", "立案", "亏损", "减持", "问询", "通报")

_MIN_INTERVAL = 0.5
_last = 0.0
_lock = threading.Lock()
_org_cache: dict[str, str | None] = {}


def _wait() -> None:
    global _last
    with _lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()


def reset_rate_limiter_for_tests() -> None:
    global _last
    with _lock:
        _last = 0.0
    _org_cache.clear()


def _column_for(code: str) -> str:
    return "szse" if code.startswith(("0", "1", "2", "3")) else "sse"


def resolve_org_id(code: str, client: httpx.Client | None = None) -> str | None:
    if code in _org_cache:
        return _org_cache[code]
    owns = client is None
    if owns:
        client = httpx.Client(headers=HEADERS, timeout=15.0)
    assert client is not None
    try:
        _wait()
        r = client.post(SEARCH_URL, data={"keyWord": code, "maxNum": 5})
        r.raise_for_status()
        org: str | None = None
        for item in r.json() or []:
            if str(item.get("code")) == code and item.get("category") == "A股":
                org = str(item.get("orgId") or "") or None
                break
        _org_cache[code] = org
        return org
    except Exception as exc:
        logger.debug("cninfo orgId resolve failed code=%s: %s", code, exc)
        _org_cache[code] = None
        return None
    finally:
        if owns:
            client.close()


def _ms_to_date(ms: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0).strftime("%Y-%m-%d")
    except Exception:
        return ""


def parse_announcement(row: dict[str, Any], code: str) -> dict[str, Any] | None:
    aid = str(row.get("announcementId") or "").strip()
    title = str(row.get("announcementTitle") or "").strip()
    if not aid or not title:
        return None
    announce_date = _ms_to_date(row.get("announcementTime"))
    if len(announce_date) < 10:
        return None
    is_risk = any(k in title for k in RISK_KEYWORDS)
    return {
        "source": "cninfo",
        "article_id": aid[:80],
        "code": code,
        "announce_date": announce_date,
        "title": title[:500],
        "summary": None,
        "category": "announcement",
        "sentiment": "negative" if is_risk else "neutral",
        "is_risk_warning": is_risk,
    }


def fetch_announcements_for_code(
    code: str,
    *,
    start_date: str,
    end_date: str,
    page_size: int = 30,
    max_pages: int = 3,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    owns = client is None
    if owns:
        client = httpx.Client(headers=HEADERS, timeout=15.0)
    assert client is not None
    out: list[dict[str, Any]] = []
    try:
        org = resolve_org_id(code, client=client)
        if not org:
            return []
        se_date = f"{start_date}~{end_date}"
        for page in range(1, max_pages + 1):
            _wait()
            r = client.post(
                QUERY_URL,
                data={
                    "pageNum": page,
                    "pageSize": page_size,
                    "column": _column_for(code),
                    "tabName": "fulltext",
                    "plate": "",
                    "stock": f"{code},{org}",
                    "searchkey": "",
                    "secid": "",
                    "category": "",
                    "trade": "",
                    "seDate": se_date,
                    "sortName": "",
                    "sortType": "",
                    "isHLtitle": "true",
                },
            )
            r.raise_for_status()
            data = r.json() or {}
            rows = data.get("announcements") or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                parsed = parse_announcement(row, code)
                if parsed is None:
                    continue
                if parsed["announce_date"] < start_date or parsed["announce_date"] > end_date:
                    continue
                out.append(parsed)
            total = int(data.get("totalAnnouncement") or 0)
            if page * page_size >= total or not rows:
                break
    finally:
        if owns:
            client.close()
    return out
