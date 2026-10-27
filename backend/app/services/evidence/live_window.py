"""实盘判定窗口（纯函数，不查库）：推荐在「入场日开盘前」生成才算实盘。

信号日 D 收盘后选股、下一个开市日 D+1 开盘买。只有在 D+1 09:15（集合竞价开始、可以下单）之前生成的推荐，
才是事前的实盘推荐；之后生成的（开盘后补跑、隔几天补跑）已经能看到入场日行情，只能算回放。
D 收盘后到 D+1 开盘前，不管是正常日更、凌晨补跑还是安全网补跑，都按生成时间算实盘。

短线（evidence.identity / provenance_writer / official_sample_scope）和中长线（midterm_live_seal）
都只用这里的规则；下一个开市日由调用方给（查本地交易日历，见 midterm_live_seal.next_open_info），
给不了时按「下一个工作日」算——这只会让窗口更短（节假日前偏严），不会把补跑错算成实盘。
"""
from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
ENTRY_CUTOFF = _dt.time(9, 15)  # 入场日集合竞价开始；在此之后生成的推荐不算实盘
# 读回已持久化的 LIVE 记录时只做宽松的一致性校验（生成日不早于信号日、且在信号日后这么多自然日内）；
# 精确判定在生成那一刻按交易日历做。A 股最长休市（春节）连同周末约 10 个自然日。
MAX_LIVE_WINDOW_DAYS = 15


def weekday_next_open(signal_date: _dt.date) -> _dt.date:
    d = signal_date + _dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += _dt.timedelta(days=1)
    return d


def entry_open_deadline(next_open_date: _dt.date) -> _dt.datetime:
    return _dt.datetime.combine(next_open_date, ENTRY_CUTOFF, tzinfo=CN_TZ)


def _aware(ts: _dt.datetime) -> _dt.datetime:
    return ts if ts.tzinfo is not None and ts.utcoffset() is not None else ts.replace(tzinfo=_dt.timezone.utc)


def is_before_entry_open(
    signal_date: _dt.date,
    generated_at: _dt.datetime,
    next_open_date: _dt.date | None = None,
) -> bool:
    """生成时刻是否在信号日当天（含盘中、收盘后）到入场日 09:15 之间。naive 时间按 UTC（库内口径）。"""
    local = _aware(generated_at).astimezone(CN_TZ)
    if local.date() < signal_date:
        return False
    deadline = entry_open_deadline(next_open_date or weekday_next_open(signal_date))
    return local < deadline


def within_live_window(signal_date: _dt.date, generated_at: _dt.datetime) -> bool:
    """读回校验用的宽松窗口：生成日在 [信号日, 信号日 + MAX_LIVE_WINDOW_DAYS) 内。"""
    local_date = _aware(generated_at).astimezone(CN_TZ).date()
    return signal_date <= local_date < signal_date + _dt.timedelta(days=MAX_LIVE_WINDOW_DAYS)


__all__ = [
    "CN_TZ",
    "ENTRY_CUTOFF",
    "MAX_LIVE_WINDOW_DAYS",
    "entry_open_deadline",
    "is_before_entry_open",
    "weekday_next_open",
    "within_live_window",
]
