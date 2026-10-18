"""盘后补齐 daily_kbars 的 RSI/MACD 缺口（NULL/0），口径 = 通达信 SMA(X,N,1) = Tushare stk_factor。

背景：2026-07-07 起 Tushare stk_factor 大面积返回空 RSI/MACD（2026-08/09 约 45–60% 行缺失），
feature_builder 只能用 rsi_12±常数占位，RSI 类规则实际在用假值。此步只填缺口，绝不覆盖已有非零值。
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from typing import Any

from sqlalchemy import bindparam, text

from app.services.indicator_computer import compute_daily_indicators

logger = logging.getLogger(__name__)
COLS = ("rsi_6", "rsi_12", "rsi_24", "macd_dif", "macd_dea", "macd_bar")


def fill_recent_indicator_gaps(db, trade_date: str, *, lookback_days: int = 20,
                               warmup_days: int = 500, batch_codes: int = 500) -> dict[str, Any]:
    t0 = time.perf_counter()
    end = _dt.date.fromisoformat(str(trade_date)[:10])
    start = end - _dt.timedelta(days=lookback_days)
    warm = end - _dt.timedelta(days=warmup_days)
    codes = [r[0] for r in db.execute(text(
        "SELECT DISTINCT code FROM daily_kbars WHERE trade_date >= :s AND trade_date <= :e "
        "AND (rsi_24 IS NULL OR rsi_24 = 0 OR macd_dif IS NULL OR macd_dif = 0)"),
        {"s": start, "e": end})]
    sets = ", ".join(f"{c} = IF({c} IS NULL OR {c} = 0, :{c}, {c})" for c in COLS)
    upd = text(f"UPDATE daily_kbars SET {sets}, updated_at = UTC_TIMESTAMP() WHERE code = :code AND trade_date = :td")
    load = text("SELECT code, trade_date, close, high FROM daily_kbars WHERE code IN :codes "
                "AND trade_date >= :w AND trade_date <= :e ORDER BY code, trade_date"
                ).bindparams(bindparam("codes", expanding=True))
    rows = 0
    s_iso = start.isoformat()
    for i in range(0, len(codes), batch_codes):
        by: dict[str, list[dict]] = {}
        for r in db.execute(load, {"codes": codes[i:i + batch_codes], "w": warm, "e": end}):
            by.setdefault(r[0], []).append({"trade_date": str(r[1]), "close": float(r[2] or 0),
                                            "high": float(r[3] or 0)})
        params = []
        for code, bars in by.items():
            out = compute_daily_indicators(code, bars)
            for idx, b in enumerate(bars):
                if b["trade_date"] < s_iso:
                    continue
                v = dict(out[b["trade_date"]])
                for n in (6, 12, 24):
                    if idx + 1 <= n:
                        v[f"rsi_{n}"] = None
                params.append({"code": code, "td": b["trade_date"],
                               **{c: (v.get(c) if v.get(c) is not None else 0) for c in COLS}})
        if params:
            db.execute(upd, params)
            db.commit()
        rows += len(params)
    return {"codes": len(codes), "rows": rows, "seconds": round(time.perf_counter() - t0, 2),
            "window": [s_iso, end.isoformat()]}
