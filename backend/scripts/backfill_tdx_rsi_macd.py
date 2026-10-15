"""Fill RSI/MACD (TDX formula == Tushare stk_factor) only where the column is NULL/0.

Never overwrites an existing non-zero value. Backs up the six columns for the
window to parquet first. Warmup uses all earlier bars in daily_kbars.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import time

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import bindparam, text

from app.db.session import SessionLocal
from app.services.indicator_computer import compute_daily_indicators

COLS = ("rsi_6", "rsi_12", "rsi_24", "macd_dif", "macd_dea", "macd_bar")


def backup(db, start, end, path):
    rows = db.execute(text(
        "SELECT code, trade_date, " + ", ".join(COLS) + " FROM daily_kbars "
        "WHERE trade_date >= :s AND trade_date <= :e"), {"s": start, "e": end}).all()
    cols = list(zip(*rows)) if rows else [[] for _ in range(2 + len(COLS))]
    names = ["code", "trade_date", *COLS]
    tbl = pa.table({n: pa.array([None if v is None else (str(v) if i < 2 else float(v)) for v in c])
                    for i, (n, c) in enumerate(zip(names, cols))})
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(tbl, path, compression="zstd")
    return tbl.num_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--backup", required=True)
    ap.add_argument("--batch-codes", type=int, default=200)
    a = ap.parse_args()
    db = SessionLocal()
    t0 = time.time()
    print("backup rows", backup(db, a.start, a.end, a.backup), flush=True)
    codes = [r[0] for r in db.execute(text(
        "SELECT DISTINCT code FROM daily_kbars WHERE trade_date >= :s AND trade_date <= :e "
        "AND (rsi_24 IS NULL OR rsi_24 = 0 OR macd_dif IS NULL OR macd_dif = 0)"),
        {"s": a.start, "e": a.end})]
    print("codes", len(codes), flush=True)
    sets = ", ".join(f"{c} = IF({c} IS NULL OR {c} = 0, :{c}, {c})" for c in COLS)
    upd = text(f"UPDATE daily_kbars SET {sets} WHERE code = :code AND trade_date = :td")
    load = text("SELECT code, trade_date, close, high FROM daily_kbars WHERE code IN :codes "
                "AND trade_date <= :e ORDER BY code, trade_date").bindparams(bindparam("codes", expanding=True))
    total = 0
    for i in range(0, len(codes), a.batch_codes):
        chunk = codes[i:i + a.batch_codes]
        by = {}
        for r in db.execute(load, {"codes": chunk, "e": a.end}):
            by.setdefault(r[0], []).append({"trade_date": str(r[1]), "close": float(r[2] or 0), "high": float(r[3] or 0)})
        params = []
        for code, bars in by.items():
            out = compute_daily_indicators(code, bars)
            for idx, b in enumerate(bars):
                td = b["trade_date"]
                if td < a.start:
                    continue
                v = dict(out[td])
                for n in (6, 12, 24):
                    if idx + 1 <= n:  # rsi() 样本不足时返回 50.0 占位，不写
                        v[f"rsi_{n}"] = None
                # None -> 0：IF(col IS NULL OR col = 0, 0, col) 仍保持"缺失"语义
                params.append({"code": code, "td": td, **{c: (v.get(c) if v.get(c) is not None else 0) for c in COLS}})
        if params:
            db.execute(upd, params)
            db.commit()
        total += len(params)
        print(f"codes {i + len(chunk)}/{len(codes)} rows {total} {time.time() - t0:.0f}s", flush=True)
    print("done", total)


if __name__ == "__main__":
    main()
