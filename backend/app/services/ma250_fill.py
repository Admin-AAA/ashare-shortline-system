"""ma250 (年线) computed locally — 2026-10-19 owner ruling.

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


def compute_ma250(closes: pd.Series) -> pd.Series:
    return closes.rolling(WINDOW, min_periods=WINDOW).mean().round(3)


def fill_ma250(db, *, since: str | None = None, end: str | None = None,
               batch_codes: int = 300) -> dict[str, Any]:
    """Recompute ma250 for rows with trade_date in [since, end] (None = all history)."""
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
    upd = text(
        "UPDATE daily_kbars SET ma250 = :v, updated_at = UTC_TIMESTAMP() "
        "WHERE code = :c AND trade_date = :d AND NOT (ma250 <=> :v)"
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
        df["ma250"] = df.groupby("code", sort=False)["close"].transform(compute_ma250)
        if since:
            df = df[df["trade_date"] >= since]
        rows = [
            {"c": c, "d": d, "v": (None if pd.isna(v) else float(v))}
            for c, d, v in zip(df["code"], df["trade_date"], df["ma250"])
        ]
        for j in range(0, len(rows), 5000):
            res = db.execute(upd, rows[j:j + 5000])
            changed += res.rowcount or 0
        db.commit()
    return {"codes": len(codes), "rows_changed": changed, "elapsed_s": round(time.perf_counter() - t0, 1)}
