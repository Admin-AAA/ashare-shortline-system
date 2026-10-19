"""Local trading calendar: sync from Tushare trade_cal (via gateway), read locally.

Readers (open_dates_between / latest_trade_date / is_open) read the MySQL
``trade_calendar`` table first and only go upstream when the local table does not
cover the requested day.  Upstream = ``get_pro().trade_cal`` -> TushareProviderRouter
(promax -> quantdata) -> gateway backups (akshare tool_trade_date_hist_sina, baostock
query_trade_dates).  If all of them fail (or we are inside the trading session, where
readers must never block on the network), uncovered days are decided by the
weekday rule and an ``[ALERT:trade_calendar]`` is emitted; ``strict=True`` callers get
TradeCalendarUnavailable instead.  The table is refreshed once a day after the close
by ``refresh_trade_calendar`` (postclose pipeline), never on the intraday read path.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
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


CST = timezone(timedelta(hours=8))
SESSION_START = dtime(9, 0)
SESSION_END = dtime(15, 5)


class TradeCalendarUnavailable(RuntimeError):
    """Trading calendar could not be determined locally or remotely."""


def in_trading_session(now: datetime | None = None) -> bool:
    """Weekday 09:00-15:05 CST: readers must not make trade_cal network calls."""
    now = (now or datetime.now(CST)).astimezone(CST)
    return now.weekday() < 5 and SESSION_START <= now.time() <= SESSION_END


def _alert(key: str, message: str) -> None:
    logger.warning("trade calendar: %s", message)
    try:
        from app.core.alerts import emit_alert

        emit_alert("trade_calendar", key, message)
    except Exception:  # noqa: BLE001 - alerting must never break the reader
        pass


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
    """Make sure local calendar covers `day`; sync remotely (rate-limited) if not.

    Never goes to the network inside the trading session (unless a pro_factory is injected).
    """
    global _last_sync_attempt
    if _covered(db, day):
        return
    if pro_factory is None and in_trading_session():
        raise TradeCalendarUnavailable(f"本地交易日历未覆盖 {_compact(day)}，盘中不在线拉取 trade_cal")
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
    """Stale-table safety net (holiday changes) -- outside the trading session only.

    The regular refresh is ``refresh_trade_calendar`` after the close; failure is
    logged, local data stays authoritative.
    """
    global _last_sync_attempt
    from sqlalchemy import func

    if pro_factory is None and in_trading_session():
        return
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


def refresh_trade_calendar(*, db: Session | None = None, pro_factory: Callable | None = None) -> dict:
    """Post-close daily refresh through the gateway (tushare -> akshare -> baostock).

    Failure keeps the local table and alerts; never raises.
    """
    global _last_sync_attempt

    def run(s: Session) -> dict:
        global _last_sync_attempt
        with _sync_lock:
            _last_sync_attempt = time.monotonic()
            try:
                n = sync_trade_calendar(s, pro_factory=pro_factory)
                return {"status": "ok", "rows": n}
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                _alert("refresh", f"盘后交易日历刷新失败（沿用本地表）: {exc}")
                return {"status": "failed", "error": str(exc)[:300]}
    return _with_db(run, db)


def _weekday_dates(start: date, end: date) -> list[str]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(_compact(d))
        d += timedelta(days=1)
    return out


def open_dates_between(start: date, end: date, *, db: Session | None = None,
                       pro_factory: Callable | None = None, strict: bool = False) -> list[str]:
    def run(s: Session) -> list[str]:
        _maybe_refresh(s, pro_factory)
        missing: list[str] = []
        for d in (start, end):
            try:
                _ensure(s, d, pro_factory)
            except TradeCalendarUnavailable as exc:
                if strict:
                    raise
                missing.append(f"{_compact(d)}: {exc}")
        rows = s.execute(select(TradeCalendar.cal_date, TradeCalendar.is_open).where(
            TradeCalendar.exchange == EXCHANGE,
            TradeCalendar.cal_date >= _compact(start), TradeCalendar.cal_date <= _compact(end),
        )).all()
        known = {str(r[0]): bool(r[1]) for r in rows}
        if missing:
            _alert(f"weekday:{_compact(start)}-{_compact(end)}",
                   "交易日历本地未覆盖且上游/备源均不可用，按本地表+工作日规则判定: " + "; ".join(missing))
            return sorted({d for d, o in known.items() if o}
                          | {d for d in _weekday_dates(start, end) if d not in known})
        return sorted(d for d, o in known.items() if o)
    return _with_db(run, db)


def latest_trade_date(as_of: date | None = None, *, db: Session | None = None,
                      pro_factory: Callable | None = None, strict: bool = False) -> str:
    as_of = as_of or date.today()
    dates = open_dates_between(as_of - timedelta(days=14), as_of, db=db, pro_factory=pro_factory, strict=strict)
    if not dates:
        raise TradeCalendarUnavailable(f"{_compact(as_of)} 前 14 天无开市日")
    return dates[-1]


def is_open(day: date, *, db: Session | None = None, pro_factory: Callable | None = None,
            strict: bool = False) -> bool:
    def run(s: Session) -> bool:
        try:
            _ensure(s, day, pro_factory)
        except TradeCalendarUnavailable as exc:
            if strict:
                raise
            guess = day.weekday() < 5
            _alert(f"weekday:{_compact(day)}",
                   f"交易日历无法判定 {_compact(day)}，按工作日规则判为{'开市' if guess else '休市'}: {exc}")
            return guess
        return bool(s.get(TradeCalendar, (EXCHANGE, _compact(day))).is_open)
    return _with_db(run, db)
