"""Sprint4 §114：大宗交易（Tushare block_trade，经统一数据网关 tushare_client.get_pro，只读）按交易日缓存到 parquet（不写库）。

逐个工作日调用 block_trade(trade_date=YYYYMMDD)；节假日返回空表也落盘（断点续跑）。
单次上限 1000 行：满页时用 offset/limit 分页，并校验翻页确实前进。
输出 output/research/sprint4/block_trade/YYYYMMDD.parquet（字段 ts_code,trade_date,price,vol(万股),amount(万元),buyer,seller）。
用法: python scripts/midterm/fetch_block_trade_cache.py --start 20190101 --end 20261016 [--sleep 0.5]
"""
from __future__ import annotations
import argparse, sys, time
from datetime import date, timedelta
from pathlib import Path

B = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(B))
import pandas as pd
from app.services.tushare_client import get_pro

OUT = B / "output/research/sprint4/block_trade"
FIELDS = "ts_code,trade_date,price,vol,amount,buyer,seller"
PAGE = 1000


def fetch_day(d: str) -> pd.DataFrame:
    p = get_pro()
    parts, off = [], 0
    while True:
        x = None
        for attempt in range(4):
            try:
                kw = dict(trade_date=d, fields=FIELDS)
                if off:
                    kw.update(offset=off, limit=PAGE)
                x = p.block_trade(**kw)
                break
            except Exception:
                time.sleep((10, 30, 90, 180)[attempt])
        if x is None:
            raise RuntimeError(f"block_trade {d} offset {off} failed")
        if len(x) and parts and x.head(3).astype(str).reset_index(drop=True).equals(parts[-1].head(3).astype(str).reset_index(drop=True)):
            raise RuntimeError(f"block_trade {d} offset {off} returned the previous page (offset ignored)")
        if len(x):
            parts.append(x.reset_index(drop=True))
        if len(x) < PAGE or off > 20000:
            break
        off += PAGE
        time.sleep(1.0)
    if not parts:
        return pd.DataFrame(columns=FIELDS.split(","))
    return pd.concat(parts, ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20190101")
    ap.add_argument("--end", default="20261016")
    ap.add_argument("--sleep", type=float, default=0.5)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    s = date(int(a.start[:4]), int(a.start[4:6]), int(a.start[6:]))
    e = date(int(a.end[:4]), int(a.end[4:6]), int(a.end[6:]))
    days = [(s + timedelta(i)) for i in range((e - s).days + 1)]
    days = [x.strftime("%Y%m%d") for x in days if x.weekday() < 5]
    print("weekdays", len(days), flush=True)
    done = fail = rows = 0
    t0 = time.time()
    for d in days:
        f = OUT / f"{d}.parquet"
        if f.exists():
            continue
        try:
            df = fetch_day(d)
        except Exception as ex:
            fail += 1
            print("FAIL", d, repr(ex)[:200], flush=True)
            continue
        for k in ("price", "vol", "amount"):
            df[k] = pd.to_numeric(df[k], errors="coerce")
        for k in ("ts_code", "trade_date", "buyer", "seller"):
            df[k] = df[k].astype(str)
        df.to_parquet(f, index=False)
        done += 1; rows += len(df)
        if len(df) >= PAGE:
            print("MULTIPAGE", d, len(df), flush=True)
        if done % 50 == 0:
            print(f"{d} done={done} rows={rows} fail={fail} {time.time()-t0:.0f}s", flush=True)
        time.sleep(a.sleep)
    print(f"finished done={done} rows={rows} fail={fail} {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
