"""[中长线] 实盘批次的生成时间封签。

信号日 D 收盘后选股、D+1 开盘买。只有在 D+1 集合竞价撮合（09:25）之前生成的批次，才是"事前"的实盘推荐；
之后才落盘的批次（手动补跑过去日期、开盘后重跑）已经能看到开盘价甚至更多信息，不能算实盘。

- stamp(signal_date)：写入 generated_at（生成时刻）、entry_deadline（D+1 09:25，+08:00）和 deadline_source。
- is_late(batch)：generated_at 晚于 entry_deadline -> True；没有封签、或截止时间是按工作日兜底猜的 -> None（不改判）。
- past_deadline(signal_date)：现在是否已过 D+1 09:25。只有截止时间来自交易日历时才可能为 True——
  按工作日兜底猜的截止时间可能落在节假日上，不能据此锁住批次。

下一个交易日只查本地 trade_calendar 表（不打上游）。查不到时不缓存（日历补上后立即生效），按工作日规则顺延并告警。
"""
from __future__ import annotations

import datetime as _dt
import logging
import threading
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")
ENTRY_CUTOFF = _dt.time(9, 25)  # D+1 集合竞价撮合时刻；晚于此刻生成的批次不算实盘
SOURCE_CALENDAR = "calendar"
SOURCE_FALLBACK = "weekday_fallback"

_found: dict[str, str] = {}  # 只缓存查到的结果；查不到的下次重查
_alerted: set[str] = set()
_lock = threading.Lock()


def now_cn() -> _dt.datetime:
    return _dt.datetime.now(CN_TZ)


def _query_next_open(signal_date: str) -> str | None:
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.market import TradeCalendar
    from app.services.trade_calendar import EXCHANGE

    d = _dt.date.fromisoformat(signal_date)
    lo = (d + _dt.timedelta(days=1)).strftime("%Y%m%d")
    hi = (d + _dt.timedelta(days=20)).strftime("%Y%m%d")
    with SessionLocal() as db:
        rows = db.execute(
            select(TradeCalendar.cal_date, TradeCalendar.is_open)
            .where(TradeCalendar.exchange == EXCHANGE, TradeCalendar.cal_date >= lo, TradeCalendar.cal_date <= hi)
            .order_by(TradeCalendar.cal_date)
        ).all()
    for cal_date, is_open in rows:
        if is_open:
            s = str(cal_date)
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


ALERT_RECENT_DAYS = 10  # 只对近 10 天的信号日告警：历史回放批次（日历表从 2025-09 起）查不到日历是正常的，不刷告警


def _alert_missing(signal_date: str, reason: str) -> None:
    if signal_date < (now_cn().date() - _dt.timedelta(days=ALERT_RECENT_DAYS)).isoformat():
        logger.debug("trade calendar missing for historical signal date %s (%s)", signal_date, reason)
        return
    with _lock:
        if signal_date in _alerted:
            return
        _alerted.add(signal_date)
    msg = f"本地交易日历查不到 {signal_date} 之后的开市日（{reason}），实盘封签按工作日兜底，且不锁批次"
    logger.warning(msg)
    try:
        from app.core.alerts import emit_alert

        emit_alert("trade_calendar", f"seal:{signal_date}", msg)
    except Exception:  # noqa: BLE001 - 告警失败不影响出票
        logger.debug("emit trade_calendar alert failed", exc_info=True)


def _local_next_open(signal_date: str) -> str | None:
    """本地 trade_calendar 里 signal_date 之后的第一个开市日（YYYY-MM-DD）；查不到返回 None（不缓存）并告警。"""
    hit = _found.get(signal_date)
    if hit:
        return hit
    try:
        found = _query_next_open(signal_date)
    except Exception as exc:  # noqa: BLE001 - 日历不可用时按工作日规则顺延
        _alert_missing(signal_date, f"查询失败：{type(exc).__name__}")
        return None
    if found:
        _found[signal_date] = found
        return found
    _alert_missing(signal_date, "日历未覆盖")
    return None


def next_open_info(signal_date: str) -> tuple[_dt.date, str]:
    found = _local_next_open(signal_date)
    if found:
        return _dt.date.fromisoformat(found), SOURCE_CALENDAR
    d = _dt.date.fromisoformat(signal_date) + _dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += _dt.timedelta(days=1)
    return d, SOURCE_FALLBACK


def next_open_date(signal_date: str) -> _dt.date:
    return next_open_info(signal_date)[0]


def deadline_info(signal_date: str) -> tuple[_dt.datetime, str]:
    d, source = next_open_info(signal_date)
    return _dt.datetime.combine(d, ENTRY_CUTOFF, tzinfo=CN_TZ), source


def entry_deadline(signal_date: str) -> _dt.datetime:
    return deadline_info(signal_date)[0]


def stamp(signal_date: str, now: _dt.datetime | None = None) -> dict[str, str]:
    ts = (now or now_cn()).astimezone(CN_TZ)
    ddl, source = deadline_info(signal_date)
    return {
        "generated_at": ts.isoformat(timespec="seconds"),
        "entry_deadline": ddl.isoformat(timespec="seconds"),
        "deadline_source": source,
    }


def past_deadline(signal_date: str, now: _dt.datetime | None = None) -> bool:
    """已过 D+1 09:25 且截止时间来自交易日历才为 True；兜底猜的截止时间不锁批次。"""
    ddl, source = deadline_info(signal_date)
    if source != SOURCE_CALENDAR:
        return False
    return (now or now_cn()).astimezone(CN_TZ) > ddl


def _parse(ts: Any) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        out = _dt.datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=CN_TZ)


def is_late(batch: dict[str, Any]) -> bool | None:
    """True=开盘后才生成（不算实盘）；False=开盘前生成；None=旧文件没有封签，或截止时间是兜底猜的（不改判）。"""
    gen = _parse(batch.get("generated_at"))
    if gen is None or batch.get("deadline_source") == SOURCE_FALLBACK:
        return None
    ddl = _parse(batch.get("entry_deadline"))
    if ddl is None:
        ddl, source = deadline_info(str(batch["signal_date"]))
        if source != SOURCE_CALENDAR:
            return None
    return gen > ddl
