"""公告时间对齐到「收盘出信号、下一交易日开盘买」。

精确时间且落在交易日 15:00 之前：当日收盘可见，下一交易日开盘买。
15:00 及之后、非交易日、或只有日期（按盘后公告）：下一交易日收盘才可见，
再下一交易日开盘买。只有日期时不假设盘中已经看到，避免把未知时点当成盘中信息。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re

_CLOCK = re.compile(r"(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?")


@dataclass(frozen=True)
class TimeAlignment:
    publish_date: str
    publish_time: str | None
    time_quality: str  # precise | date_only
    signal_trade_date: str | None
    entry_trade_date: str | None
    available_at: str | None  # 信号日 15:00，研究上第一次允许使用该文本的收盘


def weekday_sessions(start: date, end: date) -> list[str]:
    """没有交易所日历时的兜底：周一到周五。节假日会偏一天，调用方要在结果里标明。"""
    out: list[str] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def parse_publish_time(publish_date: str, raw: object) -> str | None:
    """把日期 + 上游时点收成 ``YYYY-MM-DD HH:MM:SS``。解析不出钟点则返回 None。"""
    if not publish_date or len(publish_date) < 10:
        return None
    text = str(raw or "").strip()
    if not text or text.lower() in {"nan", "none", "nat", "null"}:
        return None
    if len(text) >= 19 and text[4] == "-" and text[7] == "-":
        return text[:19]
    compact = "".join(ch for ch in text if ch.isdigit())
    if len(compact) >= 14 and compact[:8].isdigit():
        return (
            f"{compact[:4]}-{compact[4:6]}-{compact[6:8]} "
            f"{compact[8:10]}:{compact[10:12]}:{compact[12:14]}"
        )
    match = _CLOCK.search(text)
    if match:
        second = match.group("s") or "00"
        return f"{publish_date} {int(match.group('h')):02d}:{match.group('m')}:{second}"
    if len(compact) in {4, 5, 6} and ":" not in text and "-" not in text:
        padded = compact.zfill(6)
        hour, minute, second = int(padded[:2]), int(padded[2:4]), int(padded[4:6])
        if hour <= 23 and minute <= 59 and second <= 59:
            return f"{publish_date} {hour:02d}:{minute:02d}:{second:02d}"
    return None


def _next_after(sessions: list[str], day: str) -> str | None:
    for item in sessions:
        if item > day:
            return item
    return None


def align_publication(
    *,
    publish_date: str,
    publish_time: str | None,
    sessions: list[str] | tuple[str, ...],
) -> TimeAlignment:
    ordered = sorted(set(sessions))
    clock = _clock_minutes(publish_time) if publish_time else None
    precise = clock is not None and bool(publish_time) and publish_time[:10] == publish_date
    session_set = set(ordered)
    if precise and publish_date in session_set and clock is not None and clock < 15 * 60:
        signal = publish_date
        quality = "precise"
    else:
        signal = _next_after(ordered, publish_date)
        quality = "precise" if precise else "date_only"
    entry = _next_after(ordered, signal) if signal else None
    available = f"{signal} 15:00:00" if signal else None
    return TimeAlignment(
        publish_date=publish_date,
        publish_time=publish_time if precise else None,
        time_quality=quality,
        signal_trade_date=signal,
        entry_trade_date=entry,
        available_at=available,
    )


def _clock_minutes(publish_time: str | None) -> int | None:
    if not publish_time or len(publish_time) < 16:
        return None
    try:
        stamp = datetime.strptime(publish_time[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return stamp.hour * 60 + stamp.minute
