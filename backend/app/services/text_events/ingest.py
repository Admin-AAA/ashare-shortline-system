"""经统一数据网关把文本事件写入 text_event。

调用 ``get_pro()``，因此限速、重试、熔断、超时换源都在现有网关上。
teajoin 忙或超时后，网关会落到该接口链上的下一家；这里只记录失败并继续下一天，方便续跑。
"""
from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any, Iterable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.engine.trading_calendar import normalize_trade_date
from app.models.market import NewsAnnouncement, TextEvent
from app.services.text_events.sources import (
    CODE_SOURCES,
    DAILY_SOURCES,
    SNAPSHOT_SOURCES,
    TextSource,
    announcement_to_event,
    materialize,
    params_for_day,
    source_by_name,
)
from app.services.text_events.timealign import weekday_sessions

logger = logging.getLogger(__name__)


def load_sessions(db: Session, start: str, end: str) -> tuple[list[str], str]:
    """优先用本地 trade_calendar。覆盖不够时退回工作日，并在摘要里标明。"""
    start_d = date.fromisoformat(start[:10]) - timedelta(days=10)
    end_d = date.fromisoformat(end[:10]) + timedelta(days=45)
    try:
        rows = db.execute(
            text(
                "SELECT cal_date FROM trade_calendar WHERE exchange='SSE' AND is_open=1 "
                "AND cal_date BETWEEN :a AND :b ORDER BY cal_date"
            ),
            {"a": start_d.strftime("%Y%m%d"), "b": end_d.strftime("%Y%m%d")},
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - 日历缺失不应挡住文本入库
        db.rollback()
        logger.info("trade_calendar unreadable, weekday fallback: %s", exc)
        rows = []
    days = [normalize_trade_date(row[0]) for row in rows]
    days = [item for item in days if len(item) == 10]
    if len(days) >= 5:
        return days, "exchange"
    return weekday_sessions(start_d, end_d), "weekday"


def upsert_events(db: Session, rows: Iterable[dict[str, Any]], *, provider: str) -> int:
    written = 0
    for row in rows:
        existing = db.scalar(
            select(TextEvent).where(TextEvent.source == row["source"], TextEvent.event_id == row["event_id"])
        )
        if existing is None:
            db.add(TextEvent(provider=provider, **row))
            written += 1
            continue
        changed = False
        for key, value in row.items():
            if getattr(existing, key) != value:
                setattr(existing, key, value)
                changed = True
        if existing.provider != provider:
            existing.provider = provider
            changed = True
        if changed:
            written += 1
    db.commit()
    return written


def fetch_records(pro: Any, spec: TextSource, params: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """分页直到短页。上游忽略 offset 时停在第一页，避免把同一页写很多遍。"""
    collected: list[dict[str, Any]] = []
    previous = None
    offset = 0
    for page in range(spec.max_pages):
        call = dict(params)
        call["limit"] = spec.page_limit
        if offset:
            call["offset"] = offset
        try:
            frame = getattr(pro, spec.api)(**call)
        except Exception as exc:
            message = str(exc)
            if page == 0 and _rejects_paging(message):
                frame = getattr(pro, spec.api)(**params)
                return _as_records(frame), "single_page"
            if page > 0 and _rejects_paging(message):
                return collected, "single_page"
            raise
        records = _as_records(frame)
        if not records:
            break
        signature = repr(records[0])
        if signature == previous:
            return collected, "offset_ignored"
        previous = signature
        collected.extend(records)
        if len(records) < spec.page_limit:
            break
        offset += spec.page_limit
    else:
        return collected, "page_cap"
    return collected, "ok"


def ingest_day(db: Session, pro: Any, spec: TextSource, day: str, sessions: list[str]) -> dict[str, Any]:
    try:
        records, page_status = fetch_records(pro, spec, params_for_day(spec, day))
    except Exception as exc:  # noqa: BLE001 - 单日失败留给续跑
        db.rollback()
        logger.warning("text event %s %s failed: %s", spec.api, day, exc)
        return {"api": spec.api, "day": day, "status": "error", "error": _safe_error(exc), "rows": 0}
    events = [item for item in (materialize(spec, row, sessions) for row in records) if item]
    written = upsert_events(db, events, provider="gateway") if events else 0
    return {
        "api": spec.api, "day": day, "status": "ok", "page_status": page_status,
        "fetched": len(records), "kept": len(events), "written": written,
    }


def ingest_snapshot(db: Session, pro: Any, spec: TextSource, sessions: list[str]) -> dict[str, Any]:
    try:
        records, page_status = fetch_records(pro, spec, {})
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return {"api": spec.api, "status": "error", "error": _safe_error(exc), "rows": 0}
    events = [item for item in (materialize(spec, row, sessions) for row in records) if item]
    written = upsert_events(db, events, provider="gateway") if events else 0
    return {"api": spec.api, "status": "ok", "page_status": page_status, "fetched": len(records), "kept": len(events), "written": written}


def ingest_code(db: Session, pro: Any, spec: TextSource, ts_code: str, sessions: list[str]) -> dict[str, Any]:
    try:
        records, page_status = fetch_records(pro, spec, {"ts_code": ts_code})
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return {"api": spec.api, "ts_code": ts_code, "status": "error", "error": _safe_error(exc), "rows": 0}
    events = [item for item in (materialize(spec, row, sessions) for row in records) if item]
    written = upsert_events(db, events, provider="gateway") if events else 0
    return {
        "api": spec.api, "ts_code": ts_code, "status": "ok", "page_status": page_status,
        "fetched": len(records), "kept": len(events), "written": written,
    }


def ingest_range(
    db: Session,
    *,
    start: str,
    end: str,
    sources: list[str] | None = None,
    ts_code: str | None = None,
    pro: Any = None,
) -> dict[str, Any]:
    """按自然日拉取。不调用 LLM。``sources`` 为空时跑全部日频接口，不含按股票接口。"""
    if pro is None:
        from app.services.tushare_client import get_pro

        pro = get_pro()
    sessions, calendar_mode = load_sessions(db, start, end)
    selected = _select(sources)
    summary: dict[str, Any] = {
        "start": start[:10], "end": end[:10], "calendar": calendar_mode,
        "results": [], "skipped": [],
    }
    for spec in selected:
        if spec.mode == "snapshot":
            summary["results"].append(ingest_snapshot(db, pro, spec, sessions))
            continue
        if spec.mode == "code":
            if not ts_code:
                summary["skipped"].append({"api": spec.api, "reason": spec.note or "需要 ts_code"})
                continue
            summary["results"].append(ingest_code(db, pro, spec, ts_code, sessions))
            continue
        cursor = date.fromisoformat(start[:10])
        last = date.fromisoformat(end[:10])
        while cursor <= last:
            summary["results"].append(ingest_day(db, pro, spec, cursor.isoformat(), sessions))
            cursor += timedelta(days=1)
    return summary


def mirror_announcements(db: Session, *, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """把 news_announcement 的标题和摘要复制到文本层。不改原表。"""
    start = start or "1990-01-01"
    end = end or "2100-01-01"
    sessions, calendar_mode = load_sessions(db, start if start != "1990-01-01" else end, end)
    query = select(NewsAnnouncement)
    if start != "1990-01-01":
        query = query.where(NewsAnnouncement.announce_date >= start[:10])
    if end != "2100-01-01":
        query = query.where(NewsAnnouncement.announce_date <= end[:10])
    # 先读完再写。边 yield_per 边 commit 会关掉服务端游标，后半段公告会被丢掉。
    source_rows = [
        {
            "source": row.source,
            "article_id": row.article_id,
            "code": row.code,
            "announce_date": row.announce_date,
            "title": row.title,
            "summary": row.summary,
            "category": row.category,
            "sentiment": row.sentiment,
        }
        for row in db.scalars(query).all()
    ]
    batch: list[dict[str, Any]] = []
    seen = 0
    kept = 0
    written = 0
    for row in source_rows:
        seen += 1
        event = announcement_to_event(row, sessions)
        if event is None:
            continue
        kept += 1
        batch.append(event)
        if len(batch) >= 400:
            written += upsert_events(db, batch, provider="news_announcement")
            batch = []
    if batch:
        written += upsert_events(db, batch, provider="news_announcement")
    return {"seen": seen, "kept": kept, "written": written, "calendar": calendar_mode}


def _select(names: list[str] | None) -> list[TextSource]:
    if not names:
        return list(DAILY_SOURCES) + list(SNAPSHOT_SOURCES)
    chosen: list[TextSource] = []
    for name in names:
        spec = source_by_name(name.strip())
        if spec is None:
            raise KeyError(f"未知文本接口 {name}")
        chosen.append(spec)
    return chosen


def _as_records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "empty") and bool(frame.empty):
        return []
    if hasattr(frame, "to_dict"):
        return list(frame.to_dict("records"))
    if isinstance(frame, list):
        return [row for row in frame if isinstance(row, dict)]
    return []


def _rejects_paging(message: str) -> bool:
    text_lower = message.lower()
    return "limit" in text_lower or "offset" in text_lower


def _safe_error(exc: Exception) -> str:
    text_value = str(exc).replace("\n", " ")
    return text_value[:240]
