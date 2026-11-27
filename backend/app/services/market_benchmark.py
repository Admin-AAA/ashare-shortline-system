"""全市场等权基准（合成指数 EWALL.A，存 index_daily）。

中线/短线复盘要回答「跑输还是市场跌」：L-224 归因里 2026-10 中线净均 -13.7% 有七成是市场 -9.6%。
等权全 A 每日收益 = 当日所有 close>0 且 pct_chg 非空的日K涨跌幅算术平均；按日复利成点位，
以合成代码 EWALL.A 写进 index_daily（与 QVIX 同一做法），读侧按 ts_code 取，走索引，不扫 daily_kbars。

写入口：index_daily 的日更/补数（data_gap_remediation_service._backfill_index_daily_dates）之后调
upsert_equal_weight，补齐到目标日为止缺失的交易日；当日日K覆盖不足 MIN_SAMPLE 时不写（等晚间补数后再算）。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.market import IndexDaily

EW_TS_CODE = "EWALL.A"
EW_BASE_LEVEL = 1000.0
# 全市场约 5500 只；低于此数说明当日日K尚未到齐，不写。
MIN_SAMPLE = 4000
# 最多向前补多少个缺失交易日（日更只差 1 天；初次回填用脚本）。
MAX_FILL_DAYS = 40
# 初次建点位的起点：daily_kbars 2022 年起才有全市场（2021 年只有 5 只），再早的日子样本不足。
EW_START_DATE = "2022-01-04"
# 中长线结算口径：次日开盘买、第 hold 个交易日开盘卖，扣与结算同源的往返成本（midterm_service._realize_rows）。
from app.engine.trading_costs import TOTAL_ROUND_TRIP_COST_PCT as MIDTERM_ROUND_TRIP_COST_PCT  # noqa: E402

_CURVE_TTL_SECONDS = 600.0
_curve_cache: tuple[float, list[str], dict[str, float]] | None = None
_curve_lock = threading.Lock()


def equal_weight_day(db: Session, trade_date: str) -> tuple[float | None, int]:
    row = db.execute(text(
        "SELECT AVG(pct_chg), COUNT(*) FROM daily_kbars WHERE trade_date=:d AND close>0 AND pct_chg IS NOT NULL"
    ), {"d": trade_date}).one()
    return (float(row[0]) if row[0] is not None else None), int(row[1] or 0)


def upsert_equal_weight(db: Session, upto_trade_date: str) -> dict[str, Any]:
    """把 EWALL.A 补齐到 upto_trade_date（含）。点位从库里最后一行的 close 续乘；没有历史时从 EW_BASE_LEVEL 起。"""
    last = db.execute(text(
        "SELECT trade_date, close FROM index_daily WHERE ts_code=:c ORDER BY trade_date DESC LIMIT 1"
    ), {"c": EW_TS_CODE}).first()
    since = str(last[0]) if last else None
    level = float(last[1]) if last else EW_BASE_LEVEL
    params: dict[str, Any] = {"u": upto_trade_date, "n": MAX_FILL_DAYS}
    cond = "trade_date<=:u AND trade_date>:s"
    params["s"] = since or EW_START_DATE
    if not since:
        cond = "trade_date<=:u AND trade_date>=:s"
    days = [str(r[0]) for r in db.execute(text(
        f"SELECT DISTINCT trade_date FROM daily_kbars WHERE {cond} ORDER BY trade_date LIMIT :n"
    ), params).all()]
    written: list[str] = []
    skipped: list[str] = []
    for d in days:
        pct, n = equal_weight_day(db, d)
        if pct is None or n < MIN_SAMPLE:
            skipped.append(d)
            break  # 点位必须连续：某天样本不足就停在它前面，等它补齐
        level = level * (1 + pct / 100)
        row = db.execute(select(IndexDaily).where(IndexDaily.ts_code == EW_TS_CODE, IndexDaily.trade_date == d)).scalar_one_or_none()
        if row is None:
            row = IndexDaily(ts_code=EW_TS_CODE, trade_date=d)
            db.add(row)
        lvl = round(level, 6)
        row.close = row.open = row.high = row.low = lvl
        row.pct_chg = round(pct, 6)
        row.volume = float(n)  # volume 列记当日样本数，便于巡检看覆盖
        written.append(d)
    if written:
        db.commit()
        invalidate_curve_cache()
    return {"ts_code": EW_TS_CODE, "written": written, "skipped_insufficient": skipped, "last_level": round(level, 4)}


def invalidate_curve_cache() -> None:
    global _curve_cache
    with _curve_lock:
        _curve_cache = None


def equal_weight_curve(db: Session) -> tuple[list[str], dict[str, float]]:
    """(升序交易日列表, {trade_date: 当日等权涨跌幅%})，10 分钟缓存。"""
    global _curve_cache
    now = time.monotonic()
    with _curve_lock:
        if _curve_cache is not None and now - _curve_cache[0] < _CURVE_TTL_SECONDS:
            return _curve_cache[1], _curve_cache[2]
    rows = db.execute(text(
        "SELECT trade_date, pct_chg FROM index_daily WHERE ts_code=:c ORDER BY trade_date"
    ), {"c": EW_TS_CODE}).all()
    dates = [str(r[0]) for r in rows]
    pct = {str(r[0]): float(r[1]) for r in rows}
    with _curve_lock:
        _curve_cache = (now, dates, pct)
    return dates, pct


def holding_benchmark_return(dates: list[str], pct: dict[str, float], signal_date: str, hold_days: int,
                             *, cost_pct: float = MIDTERM_ROUND_TRIP_COST_PCT) -> float | None:
    """信号日 T 收盘后出票、T+1 开盘买、T+1+hold 开盘卖 的等权基准收益（%）。

    日线只有收盘价，用 T+1 收盘→T+1+hold 收盘（即 T+2..T+1+hold 共 hold 根日收益）的复利近似开盘到开盘，
    再扣与策略结算相同的成本。信号日不在曲线里或持有期尚未走完时返回 None。"""
    import bisect

    i = bisect.bisect_left(dates, signal_date)
    if i >= len(dates) or dates[i] != signal_date:
        return None
    end = i + 1 + hold_days
    if end >= len(dates):
        return None
    cum = 1.0
    for j in range(i + 2, end + 1):
        cum *= 1 + pct[dates[j]] / 100
    return round((cum - 1) * 100 - cost_pct, 2)
