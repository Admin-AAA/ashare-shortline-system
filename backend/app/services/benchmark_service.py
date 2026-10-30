"""基准对比服务：沪深300指数日线查询与收益计算。

供 backtest_service / outcome_service 共享使用，避免重复查询逻辑。
收益曲线叠加用的对齐序列也从这里读本地 index_daily，不另接行情源。
"""

from __future__ import annotations

import bisect
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.trading_calendar import normalize_trade_date
from app.models.tables import IndexDaily

DEFAULT_BENCHMARK_TS_CODE = "000300.SH"
"""默认基准指数：沪深300。"""

BENCHMARK_NAMES = {
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
}
# 沿用上一交易日收盘时，间隔超过该自然日数视为行情缺口（长假大约 10 天，留余量）。
BENCHMARK_STALE_GAP_DAYS = 15
MAX_ALIGNED_DATES = 5000


def get_benchmark_close_map(
    db: Session,
    dates: list[str],
    ts_code: str = DEFAULT_BENCHMARK_TS_CODE,
) -> dict[str, float]:
    """批量查询基准指数在指定日期的收盘价。

    Parameters
    ----------
    dates : list[str]
        日期列表 (YYYY-MM-DD)。
    ts_code : str
        指数代码，默认沪深300。

    Returns
    -------
    dict[str, float]  {trade_date: close}，缺数据的日期不在 dict 中。
    """
    if not dates:
        return {}
    rows = db.execute(
        select(IndexDaily.trade_date, IndexDaily.close)
        .where(
            IndexDaily.ts_code == ts_code,
            IndexDaily.trade_date.in_(dates),
        )
    ).all()
    return {str(row.trade_date): float(row.close) for row in rows}


def compute_benchmark_return(
    close_map: dict[str, float],
    entry_date: str,
    exit_date: str,
) -> float:
    """计算基准指数在 [entry_date, exit_date] 期间收益率。

    若缺 entry 或 exit 的收盘价，取最接近的有效日期代替；
    若完全无法计算，返回 0.0。
    """
    entry_close = close_map.get(entry_date)
    exit_close = close_map.get(exit_date)

    if entry_close is None or exit_close is None or entry_close <= 0:
        return 0.0

    return round((exit_close - entry_close) / entry_close * 100, 3)


def seed_index_data(
    db: Session,
    *,
    lookback_days: int = 1095,
) -> dict[str, int]:
    """快速补全 index_daily 表（增量写入）。

    供启动初始化或数据修复用。
    Returns {code: written_count}
    """
    from datetime import date, timedelta

    from app.services.tushare_client import fetch_index_daily

    targets = ["000300.SH", "000905.SH"]
    end = date.today()
    start = end - timedelta(days=lookback_days)
    counts: dict[str, Any] = {}

    for ts_code in targets:
        rows = fetch_index_daily(
            ts_code=ts_code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if not rows:
            counts[ts_code] = 0
            continue
        written = 0
        for r in rows:
            existing = db.execute(
                select(IndexDaily).where(
                    IndexDaily.ts_code == r["ts_code"],
                    IndexDaily.trade_date == r["trade_date"],
                )
            ).scalar_one_or_none()
            if existing:
                continue
            db.add(IndexDaily(
                ts_code=r["ts_code"],
                trade_date=r["trade_date"],
                close=r["close"],
                pct_chg=r["pct_chg"],
                open=r["open"],
                high=r["high"],
                low=r["low"],
                volume=r["volume"],
            ))
            written += 1
        db.commit()
        counts[ts_code] = written
    return counts


def _valid_iso_date(value: Any) -> str:
    """归一成真实存在的 YYYY-MM-DD；无法解析则返回空串。"""
    normalized = normalize_trade_date(value)
    if len(normalized) != 10 or normalized[4] != "-" or normalized[7] != "-":
        return ""
    try:
        date.fromisoformat(normalized)
    except ValueError:
        return ""
    return normalized


def load_index_closes(
    db: Session,
    ts_code: str = DEFAULT_BENCHMARK_TS_CODE,
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, float]:
    """读取一只指数的收盘价。

    日期在 Python 里归一后再过滤。index_daily 里曾并存 ``YYYYMMDD`` 与
    ``YYYY-MM-DD``，直接用字符串范围比较会把旧格式整段滤掉。
    非正收盘价视为无效，不进入序列。
    """
    rows = db.execute(
        select(IndexDaily.trade_date, IndexDaily.close).where(IndexDaily.ts_code == ts_code)
    ).all()
    start_iso = _valid_iso_date(start) if start else ""
    end_iso = _valid_iso_date(end) if end else ""
    closes: dict[str, float] = {}
    for trade_date, close in rows:
        key = _valid_iso_date(trade_date)
        if not key:
            continue
        if start_iso and key < start_iso:
            continue
        if end_iso and key > end_iso:
            continue
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        if value > 0:
            closes[key] = value
    return closes


def _asof_date(sorted_dates: list[str], day: str) -> str | None:
    """取 ``day`` 当天或之前最近一个有行情的日期。"""
    if not day or not sorted_dates:
        return None
    index = bisect.bisect_right(sorted_dates, day) - 1
    if index < 0:
        return None
    return sorted_dates[index]


def align_benchmark_curve(
    closes: dict[str, float],
    dates: list[str],
    *,
    ts_code: str = DEFAULT_BENCHMARK_TS_CODE,
) -> dict[str, Any]:
    """把基准收盘价对齐到给定日期，并在最早可对齐日归零。

    口径：买入持有。最早一个能取到收盘价（当天或更早的交易日）的请求日
    为起点，``nav = 1``、``cum_pct = 0``；之后 ``cum_pct = close / base - 1``。
    请求日当天没有行情时，沿用不晚于该日的最近收盘价，点仍保留。
    完全没有更早行情的日期 ``cum_pct`` 为 null，调用方跳过基准点即可，
    不应因此删掉策略曲线上的日期。
    收盘价间隔超过 ``BENCHMARK_STALE_GAP_DAYS`` 时仍画（沿用旧价），但标 ``stale``。
    """
    name = BENCHMARK_NAMES.get(ts_code, ts_code)
    empty = {
        "ts_code": ts_code,
        "name": name,
        "status": "empty",
        "method": "buy_hold_asof",
        "rebase": "earliest_aligned_close_to_0pct_nav_1",
        "fill": "asof_previous_close",
        "base_date": None,
        "base_close": None,
        "message": None,
        "aligned_count": 0,
        "stale_count": 0,
        "points": [],
    }
    if not dates:
        return empty

    usable: dict[str, float] = {}
    for key, value in closes.items():
        iso = _valid_iso_date(key)
        if not iso:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            usable[iso] = number
    sorted_dates = sorted(usable)
    prepared: list[tuple[str, str, str | None]] = []
    for raw in dates:
        requested = str(raw or "").strip()
        normalized = _valid_iso_date(requested)
        prepared.append((requested, normalized, _asof_date(sorted_dates, normalized)))

    base_norm = ""
    base_index_date = ""
    for _requested, normalized, index_date in prepared:
        if index_date and normalized and (not base_norm or normalized < base_norm):
            base_norm = normalized
            base_index_date = index_date
    base_close = usable.get(base_index_date) if base_index_date else None

    points: list[dict[str, Any]] = []
    stale_count = 0
    null_count = 0
    for requested, normalized, index_date in prepared:
        if base_close is None or index_date is None:
            null_count += 1
            points.append({
                "date": requested,
                "normalized_date": normalized or None,
                "index_date": None,
                "close": None,
                "nav": None,
                "cum_pct": None,
                "filled": False,
                "stale": False,
                "gap_days": None,
            })
            continue
        close = usable[index_date]
        gap_days = (date.fromisoformat(normalized) - date.fromisoformat(index_date)).days
        stale = gap_days > BENCHMARK_STALE_GAP_DAYS
        if stale:
            stale_count += 1
        nav = round(close / base_close, 6)
        cum_pct = round((nav - 1.0) * 100.0, 4)
        if index_date == base_index_date and normalized == base_norm:
            nav = 1.0
            cum_pct = 0.0
        points.append({
            "date": requested,
            "normalized_date": normalized,
            "index_date": index_date,
            "close": round(close, 4),
            "nav": nav,
            "cum_pct": cum_pct,
            "filled": index_date != normalized,
            "stale": stale,
            "gap_days": gap_days,
        })

    if base_close is None:
        status = "unavailable"
        message = f"本地 index_daily 没有{name}（{ts_code}）收盘价，基准线未绘制。"
    elif null_count or stale_count:
        status = "partial"
        notes: list[str] = []
        if null_count:
            notes.append("部分日期早于本地指数行情，这些日期不画基准点，策略线保持连续。")
        if stale_count:
            notes.append(
                f"有日期缺少近端行情（间隔超过 {BENCHMARK_STALE_GAP_DAYS} 天），"
                "已沿用更早收盘价，曲线未截断。"
            )
        message = "".join(notes)
    else:
        status = "ok"
        message = None

    aligned_count = sum(1 for point in points if point["cum_pct"] is not None)
    return {
        "ts_code": ts_code,
        "name": name,
        "status": status,
        "method": "buy_hold_asof",
        "rebase": "earliest_aligned_close_to_0pct_nav_1",
        "fill": "asof_previous_close",
        "base_date": base_norm or None,
        "base_close": round(base_close, 4) if base_close is not None else None,
        "message": message,
        "aligned_count": aligned_count,
        "stale_count": stale_count,
        "points": points,
    }


def benchmark_curve_for_dates(
    db: Session,
    dates: list[str],
    ts_code: str = DEFAULT_BENCHMARK_TS_CODE,
) -> dict[str, Any]:
    """按调用方给出的日期（收益曲线的平仓日）返回对齐后的基准累计收益。"""
    if len(dates) > MAX_ALIGNED_DATES:
        raise ValueError(f"dates 最多 {MAX_ALIGNED_DATES} 个")
    valid = [_valid_iso_date(item) for item in dates]
    valid = [item for item in valid if item]
    end = max(valid) if valid else None
    closes = load_index_closes(db, ts_code, end=end) if end else {}
    return align_benchmark_curve(closes, dates, ts_code=ts_code)


def benchmark_daily_curve(
    db: Session,
    start: str,
    end: str,
    ts_code: str = DEFAULT_BENCHMARK_TS_CODE,
) -> dict[str, Any]:
    """区间内每个有行情的交易日一条，首日归零。供手工核对本地指数序列。"""
    start_iso = _valid_iso_date(start)
    end_iso = _valid_iso_date(end)
    if not start_iso or not end_iso:
        raise ValueError("start 和 end 需要是 YYYY-MM-DD")
    if start_iso > end_iso:
        raise ValueError("start 不能晚于 end")
    closes = load_index_closes(db, ts_code, start=start_iso, end=end_iso)
    if not closes:
        name = BENCHMARK_NAMES.get(ts_code, ts_code)
        curve = align_benchmark_curve({}, [], ts_code=ts_code)
        curve["status"] = "unavailable"
        curve["message"] = (
            f"本地 index_daily 没有{name}（{ts_code}）在 {start_iso} 至 {end_iso} 的收盘价，基准线未绘制。"
        )
        return curve
    return align_benchmark_curve(closes, sorted(closes), ts_code=ts_code)
