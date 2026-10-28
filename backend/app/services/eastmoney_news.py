"""东方财富个股公告抓取（日更主源）。

Tushare anns_d 对本账号返回 40203 无权限（2026-10 核实），日常新闻补数改走
东方财富。接口字段 2026-09 起 notice_id→art_code；HTTP 567 表示反爬封禁，
必须退避并中止，不能继续刷。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx
import tenacity

from app.services.market_auxiliary import _eastmoney_article_id
from app.services.text_events.keywords import legacy_is_risk

logger = logging.getLogger(__name__)

ANN_URL = (
    "https://np-anotice-stock.eastmoney.com/api/security/ann"
    "?page_size={page_size}&page_index={page_index}&ann_type=A"
    "&stock_list={code}&column=NOTICE_COLUMN"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}
# 全进程共享：日更补数与一次性回填共用同一限速，避免并发打爆。
_MIN_INTERVAL_SECONDS = 0.6
_last_request_at = 0.0
_rate_lock = threading.Lock()

# 567 / 反爬连续触发后的退避；累计到上限就让调用方整体中止。
BLOCKED_STATUS_CODES = frozenset({567, 403, 429})


class _SoftBlock(Exception):
    """Non-fatal block (below MAX_CONSECUTIVE_BLOCKS); retried, backoff already slept."""
MAX_CONSECUTIVE_BLOCKS = 3
BLOCK_BACKOFF_SECONDS = (5.0, 15.0, 30.0)


class EastmoneyBlockedError(RuntimeError):
    """东方财富返回反爬/封禁状态码。"""


def _wait_rate_limit() -> None:
    global _last_request_at
    with _rate_lock:
        wait = _MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def reset_rate_limiter_for_tests() -> None:
    global _last_request_at
    with _rate_lock:
        _last_request_at = 0.0


def parse_announcement_row(row: dict[str, Any], code: str) -> dict[str, Any] | None:
    """把东方财富单行公告转成 news_announcement 写入形态；空 article_id 丢弃。"""
    article_id = _eastmoney_article_id(row)
    if not article_id:
        return None
    title = str(row.get("title_ch") or row.get("title") or "").strip()
    if not title:
        return None
    announce_date = str(row.get("notice_date") or "")[:10]
    if len(announce_date) < 10:
        return None
    is_risk = legacy_is_risk(title)
    return {
        "source": "eastmoney",
        "article_id": article_id[:80],
        "code": code,
        "announce_date": announce_date,
        "title": title[:500],
        "summary": (str(row.get("summary") or "")[:500] or None),
        "category": "announcement",
        "sentiment": "negative" if is_risk else "neutral",
        "is_risk_warning": is_risk,
    }


def fetch_announcements_for_code(
    code: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    page_size: int = 50,
    max_pages: int = 2,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """拉取一只股票的公告；遇到 567/403/429 抛 EastmoneyBlockedError。"""
    owns_client = client is None
    if owns_client:
        client = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15.0)
    assert client is not None
    out: list[dict[str, Any]] = []
    consecutive_blocks = 0
    try:
        for page_index in range(1, max_pages + 1):
            url = ANN_URL.format(page_size=page_size, page_index=page_index, code=code)
            attempt_box = [0]

            def _fetch_page() -> dict[str, Any]:
                nonlocal consecutive_blocks
                attempt = attempt_box[0]
                attempt_box[0] += 1
                _wait_rate_limit()
                try:
                    response = client.get(url)
                    if response.status_code in BLOCKED_STATUS_CODES:
                        consecutive_blocks += 1
                        backoff = BLOCK_BACKOFF_SECONDS[
                            min(consecutive_blocks - 1, len(BLOCK_BACKOFF_SECONDS) - 1)
                        ]
                        logger.warning(
                            "eastmoney news blocked status=%s code=%s attempt=%s backoff=%.0fs",
                            response.status_code, code, attempt + 1, backoff,
                        )
                        time.sleep(backoff)
                        if consecutive_blocks >= MAX_CONSECUTIVE_BLOCKS:
                            raise EastmoneyBlockedError(
                                f"eastmoney HTTP {response.status_code} x{consecutive_blocks}"
                            )
                        raise _SoftBlock(f"eastmoney HTTP {response.status_code}")
                    response.raise_for_status()
                    payload = response.json()
                    consecutive_blocks = 0
                    return payload
                except (EastmoneyBlockedError, _SoftBlock):
                    raise
                except Exception:
                    time.sleep(1.0 + attempt)
                    raise

            try:
                data = tenacity.Retrying(
                    stop=tenacity.stop_after_attempt(3),
                    retry=tenacity.retry_if_not_exception_type(EastmoneyBlockedError),
                    wait=tenacity.wait_none(),
                    sleep=lambda _seconds: None,  # backoff already slept inside _fetch_page
                    reraise=True,
                )(_fetch_page)
            except EastmoneyBlockedError:
                raise
            except _SoftBlock as exc:
                raise EastmoneyBlockedError(str(exc)) from None
            except Exception as exc:
                raise RuntimeError(str(exc)) from exc
            rows = (data.get("data") or {}).get("list") or []
            oldest: str | None = None
            for row in rows:
                if not isinstance(row, dict):
                    continue
                parsed = parse_announcement_row(row, code)
                if parsed is None:
                    continue
                d = parsed["announce_date"]
                oldest = d if oldest is None or d < oldest else oldest
                if start_date and d < start_date:
                    continue
                if end_date and d > end_date:
                    continue
                out.append(parsed)
            if len(rows) < page_size:
                break
            if start_date and oldest and oldest < start_date:
                break
    finally:
        if owns_client:
            client.close()
    return out
