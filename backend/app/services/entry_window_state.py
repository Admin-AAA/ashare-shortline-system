"""入场窗口的交易日与「第 1 个交易日」状态：按交易所日历数天数，区分停牌与缺数据。

- 推荐天数按本地交易所日历 ``trade_calendar``（SSE is_open=1）数：推荐日之后到 data_as_of（含）的开市日数。
  日历缺这段时回退到全市场日 K 的日期并记 warning（不猜工作日）。
- 第 1 个交易日的状态（决定 2 天窗口的第 2 日能否再确认）：
  ``buyable``         该股第 1 日日 K 在、按回测规则当日可买（那么第 2 日不再追入）
  ``blocked``         该股第 1 日日 K 在、被回测规则挡住（无成交 / 一字涨停 / 跳空超限）
  ``suspended``       该股第 1 日没有日 K，但当日行情快照显示成交额/成交量为 0 → 停牌（回测同样跳过该日）
  ``missing_market``  全市场第 1 日日 K 都没入库（行数不足）→ 数据未就绪
  ``missing_code``    全市场有、该股没有，且没有停牌证据 → 数据未就绪
  缺数据时盘中确认判「数据未就绪」并告警：不买，也不判过期。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.market import TradeCalendar
from app.models.tables import DailyKbar
from app.services.entry_day_checks import first_day_blocked

logger = logging.getLogger(__name__)

# 全市场日 K 行数低于这个数视为当日日 K 未入库（正常 5,500+）
MARKET_MIN_ROWS = 3000
EXCHANGE = "SSE"
MISSING_STATUSES = frozenset({"missing_market", "missing_code"})
SKIPPED_STATUSES = frozenset({"blocked", "suspended"})


def _iso(value: Any) -> str:
    s = str(value or "").strip()[:10]
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s


def _compact(day: str) -> str:
    return str(day or "").replace("-", "")[:8]


def open_days_after(db: Session, start_exclusive: str, end_inclusive: str) -> list[str]:
    """交易所日历里 (start, end] 的开市日（YYYY-MM-DD 升序）。日历没覆盖这段时回退到全市场日 K 日期。

    只读本地表，不在线拉 trade_cal（盘中禁止）。
    """
    if not start_exclusive or not end_inclusive or end_inclusive <= start_exclusive:
        return []
    s, e = _compact(start_exclusive), _compact(end_inclusive)
    rows = db.execute(
        select(TradeCalendar.cal_date, TradeCalendar.is_open)
        .where(TradeCalendar.exchange == EXCHANGE, TradeCalendar.cal_date > s, TradeCalendar.cal_date <= e)
        .order_by(TradeCalendar.cal_date)
    ).all()
    if rows and str(rows[-1][0]) >= e:
        _resolve_calendar_gap()
        return [_iso(d) for d, is_open in rows if is_open]
    # 日历没覆盖到结束日（整段缺，或最后一天早于结束日）：不能拿半截日历数天数，退回全市场日 K 日期并告警
    last = _iso(rows[-1][0]) if rows else None
    logger.warning("交易所日历只到 %s，没覆盖 %s~%s，入场窗口天数回退到全市场日 K 日期",
                   last or "—", start_exclusive, end_inclusive)
    _alert_calendar_gap(last, end_inclusive)
    return sorted(
        str(v) for v in db.scalars(
            select(DailyKbar.trade_date)
            .where(DailyKbar.trade_date > start_exclusive, DailyKbar.trade_date <= end_inclusive)
            .distinct()
        ).all()
    )


CALENDAR_GAP_ALERT_KIND = "trade_calendar_not_covering"
CALENDAR_GAP_ALERT_KEY = "SSE"
_RESOLVE_CHECK_INTERVAL = 600.0
_last_resolve_check = 0.0


def _alert_calendar_gap(last: str | None, needed: str) -> None:
    try:
        from app.core.alerts import emit_alert

        emit_alert(CALENDAR_GAP_ALERT_KIND, CALENDAR_GAP_ALERT_KEY,
                   f"交易所日历只到 {last or '—'}，没覆盖 {needed}；入场窗口天数临时按全市场日 K 日期数，请同步日历",
                   cooldown=20 * 3600)
    except Exception:  # noqa: BLE001 - 告警失败不能挡住买入确认
        logger.debug("calendar gap alert failed", exc_info=True)


def _resolve_calendar_gap() -> None:
    """日历重新覆盖到要查的日子 → 解除告警（每 10 分钟最多查一次登记，避免每次调用都读文件）。"""
    global _last_resolve_check
    now = time.monotonic()
    if now - _last_resolve_check < _RESOLVE_CHECK_INTERVAL:
        return
    _last_resolve_check = now
    try:
        from app.core.alerts import resolve_alert

        resolve_alert(CALENDAR_GAP_ALERT_KIND, CALENDAR_GAP_ALERT_KEY, "交易所日历已覆盖")
    except Exception:  # noqa: BLE001
        logger.debug("calendar gap resolve failed", exc_info=True)


def recent_open_days(db: Session, end_inclusive: str, count: int) -> list[str]:
    """截至 end（含）最近 count 个开市日（升序）。日历没覆盖时回退到全市场日 K 日期。"""
    e = _compact(end_inclusive)
    rows = db.scalars(
        select(TradeCalendar.cal_date)
        .where(TradeCalendar.exchange == EXCHANGE, TradeCalendar.is_open.is_(True), TradeCalendar.cal_date <= e)
        .order_by(TradeCalendar.cal_date.desc())
        .limit(count)
    ).all()
    if rows:
        return sorted(_iso(d) for d in rows)
    return sorted(
        str(v) for v in db.scalars(
            select(DailyKbar.trade_date).where(DailyKbar.trade_date <= end_inclusive).distinct()
            .order_by(DailyKbar.trade_date.desc()).limit(count)
        ).all()
    )


def signal_age_trade_days(db: Session, signal_date: str, data_as_of: str) -> int:
    """推荐日之后到 data_as_of（含）的交易日数；data_as_of 即当前交易日，日历没同步到也计入。"""
    days = set(open_days_after(db, signal_date, data_as_of))
    if data_as_of and data_as_of > signal_date:
        days.add(data_as_of)
    return len(days)


def first_day_status(
    db: Session,
    code: str,
    signal_date: str,
    data_as_of: str,
    recommend_price: float,
    *,
    is_st: bool = False,
) -> tuple[str, str | None]:
    """返回 (状态, 第 1 个交易日)。只在 data_as_of 已是第 2 个及以后交易日时有意义。"""
    days = [d for d in open_days_after(db, signal_date, data_as_of) if d < data_as_of]
    if not days:
        return "none", None
    day1 = days[0]
    bar = db.scalar(select(DailyKbar).where(DailyKbar.code == code, DailyKbar.trade_date == day1))
    if bar is not None:
        return ("blocked" if first_day_blocked(code, bar, recommend_price, is_st=is_st) else "buyable"), day1
    market_rows = db.scalar(select(func.count()).select_from(DailyKbar).where(DailyKbar.trade_date == day1)) or 0
    if market_rows < MARKET_MIN_ROWS:
        return "missing_market", day1
    try:
        quote = db.execute(
            text("SELECT amount_yi, volume FROM stock_quotes WHERE code = :c AND trade_date = :d LIMIT 1"),
            {"c": code, "d": day1},
        ).first()
    except Exception:  # noqa: BLE001 - 行情快照表不可用时不能当停牌，按缺数据处理
        logger.debug("stock_quotes 查询失败 %s %s", code, day1, exc_info=True)
        quote = None
    if quote is not None and (float(quote[0] or 0) <= 0 or float(quote[1] or 0) <= 0):
        return "suspended", day1
    return "missing_code", day1


__all__ = [
    "MISSING_STATUSES",
    "SKIPPED_STATUSES",
    "first_day_status",
    "open_days_after",
    "recent_open_days",
    "signal_age_trade_days",
]
