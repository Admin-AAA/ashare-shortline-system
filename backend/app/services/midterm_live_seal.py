"""[中长线] 实盘批次的生成时间封签。

信号日 D 收盘后选股、D+1 开盘买。只有在 D+1 集合竞价撮合（09:25）之前生成的批次，才是"事前"的实盘推荐；
之后才落盘的批次（手动补跑过去日期、开盘后重跑）已经能看到开盘价甚至更多信息，不能算实盘。

- stamp(signal_date)：写入 generated_at（生成时刻）与 entry_deadline（D+1 09:25），都带 +08:00 时区。
- is_late(batch)：generated_at 晚于 entry_deadline -> True；没有封签的旧文件 -> None（按旧规则处理，不改判）。
- past_deadline(signal_date)：现在是否已过 D+1 09:25（实盘路径据此拒绝覆盖已落盘的批次）。

下一个交易日只查本地 trade_calendar 表（不打上游）；表里没有时按工作日规则顺延。
"""
from __future__ import annotations

import datetime as _dt
import logging
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")
ENTRY_CUTOFF = _dt.time(9, 25)  # D+1 集合竞价撮合时刻；晚于此刻生成的批次不算实盘


def now_cn() -> _dt.datetime:
    return _dt.datetime.now(CN_TZ)


@lru_cache(maxsize=512)
def _local_next_open(signal_date: str) -> str | None:
    """本地 trade_calendar 里 signal_date 之后的第一个开市日（YYYY-MM-DD）；查不到返回 None。"""
    try:
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
    except Exception as exc:  # noqa: BLE001 - 日历不可用时按工作日规则顺延
        logger.debug("local trade calendar lookup failed for %s: %s", signal_date, exc)
    return None


def next_open_date(signal_date: str) -> _dt.date:
    found = _local_next_open(signal_date)
    if found:
        return _dt.date.fromisoformat(found)
    d = _dt.date.fromisoformat(signal_date) + _dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += _dt.timedelta(days=1)
    return d


def entry_deadline(signal_date: str) -> _dt.datetime:
    return _dt.datetime.combine(next_open_date(signal_date), ENTRY_CUTOFF, tzinfo=CN_TZ)


def stamp(signal_date: str, now: _dt.datetime | None = None) -> dict[str, str]:
    ts = (now or now_cn()).astimezone(CN_TZ)
    return {
        "generated_at": ts.isoformat(timespec="seconds"),
        "entry_deadline": entry_deadline(signal_date).isoformat(timespec="seconds"),
    }


def past_deadline(signal_date: str, now: _dt.datetime | None = None) -> bool:
    return (now or now_cn()).astimezone(CN_TZ) > entry_deadline(signal_date)


def _parse(ts: Any) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        out = _dt.datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=CN_TZ)


def is_late(batch: dict[str, Any]) -> bool | None:
    """True=开盘后才生成（不算实盘）；False=开盘前生成；None=旧文件没有封签。"""
    gen = _parse(batch.get("generated_at"))
    if gen is None:
        return None
    ddl = _parse(batch.get("entry_deadline")) or entry_deadline(str(batch["signal_date"]))
    return gen > ddl
