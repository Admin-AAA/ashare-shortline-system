"""Local trading calendar: sync from Tushare trade_cal, read locally, never guess.

Readers raise TradeCalendarUnavailable when the calendar cannot be established
(no local coverage and remote sync failed) instead of returning "today" or [].
A missing/ stale local window triggers at most one remote sync per process per
SYNC_COOLDOWN_SECONDS, so readers stay cheap and the daily sync is implicit.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow_naive
from app.db.session import SessionLocal
from app.models.market import TradeCalendar

logger = logging.getLogger(__name__)

EXCHANGE = "SSE"
SYNC_BACK_DAYS = 400
SYNC_AHEAD_DAYS = 60
SYNC_COOLDOWN_SECONDS = 600.0
_last_sync_attempt = 0.0
_sync_lock = threading.Lock()


class TradeCalendarUnavailable(RuntimeError):
    """Trading calendar could not be determined locally or remotely."""


def _compact(d: date) -> str:
    return d.strftime("%Y%m%d")


def sync_trade_calendar(db: Session, *, start: date | None = None, end: date | None = None,
                        pro_factory: Callable | None = None) -> int:
    """Fetch trade_cal for [start, end] and upsert into trade_calendar. Returns rows written."""
    today = date.today()
    start = start or today - timedelta(days=SYNC_BACK_DAYS)
    end = end or today + timedelta(days=SYNC_AHEAD_DAYS)
    if pro_factory is None:
        from app.services.tushare_client import get_pro as pro_factory
    cal = pro_factory().trade_cal(exchange=EXCHANGE, start_date=_compact(start), end_date=_compact(end))
    if cal is None or cal.empty:
        raise TradeCalendarUnavailable(f"trade_cal 未返回 {_compact(start)}..{_compact(end)}")
    now = utcnow_naive()
    existing = {
        r.cal_date: r
        for r in db.execute(select(TradeCalendar).where(
            TradeCalendar.exchange == EXCHANGE,
            TradeCalendar.cal_date >= _compact(start),
            TradeCalendar.cal_date <= _compact(end),
        )).scalars()
    }
    n = 0
    for row in cal.to_dict("records"):
        d = str(row["cal_date"])
        is_open = int(row["is_open"]) == 1
        rec = existing.get(d)
        if rec is None:
            db.add(TradeCalendar(exchange=EXCHANGE, cal_date=d, is_open=is_open, synced_at=now))
        else:
            rec.is_open = is_open
            rec.synced_at = now
        n += 1
    db.commit()
    return n


def _covered(db: Session, day: date) -> bool:
    return db.get(TradeCalendar, (EXCHANGE, _compact(day))) is not None


def _ensure(db: Session, day: date, pro_factory: Callable | None) -> None:
    """Make sure local calendar covers `day`; sync remotely (rate-limited) if not."""
    global _last_sync_attempt
    if _covered(db, day):
        return
    with _sync_lock:
        if _covered(db, day):
            return
        if pro_factory is None and time.monotonic() - _last_sync_attempt < SYNC_COOLDOWN_SECONDS:
            raise TradeCalendarUnavailable(f"本地交易日历未覆盖 {_compact(day)}，且最近一次同步失败")
        _last_sync_attempt = time.monotonic()
        try:
            sync_trade_calendar(db, pro_factory=pro_factory)
        except TradeCalendarUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised as domain error
            db.rollback()
            raise TradeCalendarUnavailable(f"交易日历同步失败: {exc}") from exc
    if not _covered(db, day):
        raise TradeCalendarUnavailable(f"交易日历同步后仍未覆盖 {_compact(day)}")


def _with_db(fn, db: Session | None):
    if db is not None:
        return fn(db)
    s = SessionLocal()
    try:
        return fn(s)
    finally:
        s.close()


REFRESH_AFTER_SECONDS = 86400.0


def _maybe_refresh(db: Session, pro_factory: Callable | None) -> None:
    """Daily refresh (holiday changes); failure is logged, local data stays authoritative."""
    global _last_sync_attempt
    from sqlalchemy import func

    newest = db.execute(select(func.max(TradeCalendar.synced_at))).scalar()
    if newest is None or (utcnow_naive() - newest).total_seconds() < REFRESH_AFTER_SECONDS:
        return
    if pro_factory is None and time.monotonic() - _last_sync_attempt < SYNC_COOLDOWN_SECONDS:
        return
    with _sync_lock:
        _last_sync_attempt = time.monotonic()
        try:
            sync_trade_calendar(db, pro_factory=pro_factory)
        except Exception as exc:  # noqa: BLE001 - logged; stale-but-present calendar is still valid
            db.rollback()
            logger.warning("trade calendar daily refresh failed (using local copy): %s", exc)


def open_dates_between(start: date, end: date, *, db: Session | None = None,
                       pro_factory: Callable | None = None) -> list[str]:
    def run(s: Session) -> list[str]:
        _maybe_refresh(s, pro_factory)
        _ensure(s, start, pro_factory)
        _ensure(s, end, pro_factory)
        rows = s.execute(select(TradeCalendar.cal_date).where(
            TradeCalendar.exchange == EXCHANGE, TradeCalendar.is_open.is_(True),
            TradeCalendar.cal_date >= _compact(start), TradeCalendar.cal_date <= _compact(end),
        ).order_by(TradeCalendar.cal_date)).scalars().all()
        return [str(r) for r in rows]
    return _with_db(run, db)


def latest_trade_date(as_of: date | None = None, *, db: Session | None = None,
                      pro_factory: Callable | None = None) -> str:
    as_of = as_of or date.today()
    dates = open_dates_between(as_of - timedelta(days=14), as_of, db=db, pro_factory=pro_factory)
    if not dates:
        raise TradeCalendarUnavailable(f"{_compact(as_of)} 前 14 天无开市日")
    return dates[-1]


def is_open(day: date, *, db: Session | None = None, pro_factory: Callable | None = None) -> bool:
    def run(s: Session) -> bool:
        _ensure(s, day, pro_factory)
        return bool(s.get(TradeCalendar, (EXCHANGE, _compact(day))).is_open)
    return _with_db(run, db)
