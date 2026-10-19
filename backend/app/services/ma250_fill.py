"""Moving averages ma5/10/20/60/250 computed locally (ma250 since 2026-10-19 03:00; ma5-60 added 03:40).

ma250 (年线) computed locally — 2026-10-19 owner ruling.

Tushare stk_factor has no ma250, so the column was 0 market-wide. Basis matches ma5..ma60: simple mean of
the last 250 stored UNADJUSTED closes (same as indicator_computer). Fewer than 250 bars -> NULL (never 0).
Changed rows bump updated_at (outcome finality gate / auto re-projection contract).
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from typing import Any

import pandas as pd
from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)

WINDOW = 250
_WARMUP_CAL_DAYS = 420  # >= 250 trading days


MA_WINDOWS = (5, 10, 20, 60, 250)


def compute_ma250(closes: pd.Series) -> pd.Series:
    return compute_ma(closes, WINDOW)


def compute_ma(closes: pd.Series, window: int) -> pd.Series:
    """Simple mean of the last ``window`` unadjusted closes; partial window -> NaN (NULL)."""
    return closes.rolling(window, min_periods=window).mean().round(3)


def _col_set(col: str) -> str:
    # only replace missing/zero values, partial-window leftovers (new value NULL) or real mismatches;
    # float-rounding noise (<=0.0015) on already-correct rows is left untouched.
    return (
        f"{col} = CASE WHEN {col} IS NULL OR {col} = 0 OR :{col} IS NULL OR ABS({col} - :{col}) > 0.0015 "
        f"THEN :{col} ELSE {col} END"
    )


def _col_dirty(col: str) -> str:
    return (
        f"(NOT ({col} <=> :{col}) AND ({col} IS NULL OR {col} = 0 OR :{col} IS NULL OR ABS({col} - :{col}) > 0.0015))"
    )


def fill_ma250(db, *, since: str | None = None, end: str | None = None,
               batch_codes: int = 300) -> dict[str, Any]:
    """Recompute ma250 for rows with trade_date in [since, end] (None = all history)."""
    return fill_moving_averages(db, since=since, end=end, batch_codes=batch_codes, windows=(WINDOW,))


def fill_moving_averages(db, *, since: str | None = None, end: str | None = None,
                         batch_codes: int = 300, windows: tuple[int, ...] = MA_WINDOWS) -> dict[str, Any]:
    """Recompute ma{windows} (unadjusted close SMA, partial window -> NULL) for rows in [since, end]."""
    t0 = time.perf_counter()
    params: dict[str, Any] = {}
    where = []
    if since:
        where.append("trade_date >= :since")
        params["since"] = since
    if end:
        where.append("trade_date <= :end")
        params["end"] = end
    codes = [r[0] for r in db.execute(text(
        "SELECT DISTINCT code FROM daily_kbars" + (" WHERE " + " AND ".join(where) if where else "")), params)]
    warm = None
    if since:
        warm = (_dt.date.fromisoformat(since) - _dt.timedelta(days=_WARMUP_CAL_DAYS)).isoformat()
    load = text(
        "SELECT code, trade_date, close FROM daily_kbars WHERE code IN :codes"
        + (" AND trade_date >= :warm" if warm else "")
        + (" AND trade_date <= :end" if end else "")
        + " ORDER BY code, trade_date"
    ).bindparams(bindparam("codes", expanding=True))
    cols = [f"ma{w}" for w in windows]
    upd = text(
        "UPDATE daily_kbars SET " + ", ".join(_col_set(c) for c in cols) + ", updated_at = UTC_TIMESTAMP() "
        "WHERE code = :c AND trade_date = :d AND (" + " OR ".join(_col_dirty(c) for c in cols) + ")"
    )
    changed = 0
    for i in range(0, len(codes), batch_codes):
        chunk = codes[i:i + batch_codes]
        p: dict[str, Any] = {"codes": chunk}
        if warm:
            p["warm"] = warm
        if end:
            p["end"] = end
        df = pd.DataFrame(db.execute(load, p).all(), columns=["code", "trade_date", "close"])
        if df.empty:
            continue
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        grouped = df.groupby("code", sort=False)["close"]
        for w, c in zip(windows, cols):
            df[c] = grouped.transform(lambda s, _w=w: compute_ma(s, _w))
        if since:
            df = df[df["trade_date"] >= since]
        records = df[["code", "trade_date", *cols]].to_dict("records")
        rows = [
            {"c": r["code"], "d": r["trade_date"],
             **{c: (None if pd.isna(r[c]) else float(r[c])) for c in cols}}
            for r in records
        ]
        for j in range(0, len(rows), 5000):
            res = db.execute(upd, rows[j:j + 5000])
            changed += res.rowcount or 0
        db.commit()
    return {"codes": len(codes), "rows_changed": changed, "elapsed_s": round(time.perf_counter() - t0, 1)}
