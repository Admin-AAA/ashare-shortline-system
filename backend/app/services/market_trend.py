"""全市场等权指数趋势（Sprint4 §84）。

等权指数 = 每日全市场（成交额>0）个股 pct_chg（截尾 ±20%）均值的累乘。
输出信号日收盘可得的两个比值，供 strategy.yaml phases 当日级闸门使用：
  mkt_eqw_ma20_ratio = 指数收盘 / MA20
  mkt_eqw_ma60_ratio = 指数收盘 / MA60
历史不足 60 个交易日时为 0（闸门表达式统一写成 `x > 0 and x < 1`，缺数据不拦）。
回测（research_runner）与实盘（feature_builder）共用本模块，口径一致。
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from sqlalchemy import func, select

from app.models.market import DailyKbar

FIELDS = ("mkt_eqw_ma20_ratio", "mkt_eqw_ma60_ratio")
_MIN_SAMPLE = 1000
_LOOKBACK_CAL_DAYS = 130


def _clip(v: float) -> float:
    return max(-20.0, min(20.0, float(v)))


_RET_CACHE: dict[str, float] = {}
_COVER: list[str] = []  # [start, last_date_with_data]：_RET_CACHE 在该区间内完整


def daily_mean_returns(session: Any, start: str, end: str) -> dict[str, float]:
    """按日均值（进程内增量缓存：回放逐日递增调用时只补查新增日期）。"""
    if _COVER and start >= _COVER[0] and end <= _COVER[1]:
        return {d: r for d, r in _RET_CACHE.items() if start <= d <= end}
    q_start = start
    if _COVER and _COVER[0] <= start <= _COVER[1]:
        q_start = (date.fromisoformat(_COVER[1]) + timedelta(days=1)).isoformat()
    fresh = _query_mean_returns(session, q_start, end)
    if q_start == start:
        _RET_CACHE.clear()
        _COVER[:] = [start, start]
    _RET_CACHE.update(fresh)
    if fresh:
        _COVER[1] = max(_COVER[1], max(fresh))
    out = {d: r for d, r in _RET_CACHE.items() if start <= d <= end}
    return out


def _query_mean_returns(session: Any, start: str, end: str) -> dict[str, float]:
    pct = func.least(func.greatest(DailyKbar.pct_chg, -20.0), 20.0)
    rows = session.execute(
        select(DailyKbar.trade_date, func.avg(pct), func.count())
        .where(DailyKbar.trade_date >= start, DailyKbar.trade_date <= end, DailyKbar.amount > 0)
        .group_by(DailyKbar.trade_date)
    ).all()
    return {str(d): float(r) for d, r, n in rows if r is not None and int(n or 0) >= _MIN_SAMPLE}


def ratios_from_returns(returns: Mapping[str, float]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    level = 1.0
    closes: list[float] = []
    for d in sorted(returns):
        level *= 1.0 + returns[d] / 100.0
        closes.append(level)
        r20 = level / (sum(closes[-20:]) / 20) if len(closes) >= 60 else 0.0
        r60 = level / (sum(closes[-60:]) / 60) if len(closes) >= 60 else 0.0
        out[d] = {"mkt_eqw_ma20_ratio": round(r20, 6), "mkt_eqw_ma60_ratio": round(r60, 6)}
    return out


def trend_by_dates(session: Any, dates: Iterable[str]) -> dict[str, dict[str, float]]:
    ds = sorted(str(d) for d in dates)
    if not ds:
        return {}
    start = (date.fromisoformat(ds[0]) - timedelta(days=_LOOKBACK_CAL_DAYS)).isoformat()
    try:
        return ratios_from_returns(daily_mean_returns(session, start, ds[-1]))
    except Exception:  # noqa: BLE001 - 闸门字段缺失时退化为 0（不拦），不阻断出票
        return {}


def trend_for_live(session: Any, trade_date: str, stocks: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """实盘：DB 里还没有当日 K 线时，用当日行情 pct 均值补上最后一天。"""
    zero = {k: 0.0 for k in FIELDS}
    if not trade_date or os.environ.get("ASHARE_MKT_TREND_OFF") == "1":  # 研究对照：关闭趋势闸门
        return zero
    td = str(trade_date)[:10]
    try:
        start = (date.fromisoformat(td) - timedelta(days=_LOOKBACK_CAL_DAYS)).isoformat()
        rets = daily_mean_returns(session, start, td)
        if td in rets:
            return ratios_from_returns(rets).get(td, zero)
        if td not in rets:
            pcts = [_clip(s.get("pct") or 0) for s in stocks if float(s.get("amount") or 1) > 0]
            if len(pcts) < _MIN_SAMPLE:
                return zero
            rets[str(trade_date)[:10]] = sum(pcts) / len(pcts)
        return ratios_from_returns(rets).get(str(trade_date)[:10], zero)
    except Exception:  # noqa: BLE001
        return zero
