"""Sprint4 §115：涨跌停榜单（Tushare limit_list_d，经统一数据网关 tushare_client.get_pro，只读）按交易日缓存 parquet（不写库）。

逐个工作日调用 limit_list_d(trade_date=YYYYMMDD)；节假日空表也落盘（断点续跑）。单次 ≥2500 行时 offset 分页并校验翻页。
输出 output/research/sprint4/limit_list/YYYYMMDD.parquet，字段：trade_date,ts_code,industry,name,close,pct_chg,amount,
limit_amount,float_mv,total_mv,turnover_ratio,fd_amount(封单额 元),first_time,last_time,open_times(炸板次数),up_stat,limit_times,limit(U/D/Z)。
用法: python scripts/fetch_limit_list_cache.py --start 20220301 --end 20261016 [--sleep 0.5]
"""
from __future__ import annotations
import argparse, sys, time
from datetime import date, timedelta
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
import pandas as pd
from app.services.tushare_client import get_pro

OUT = B / "output/research/sprint4/limit_list"
FIELDS = ("trade_date,ts_code,industry,name,close,pct_chg,amount,limit_amount,float_mv,total_mv,turnover_ratio,"
          "fd_amount,first_time,last_time,open_times,up_stat,limit_times,limit")
PAGE = 2500
NUM = ("close", "pct_chg", "amount", "limit_amount", "float_mv", "total_mv", "turnover_ratio", "fd_amount",
       "open_times", "limit_times")


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
                x = p.limit_list_d(**kw)
                break
            except Exception:
                time.sleep((10, 30, 90, 180)[attempt])
        if x is None:
            raise RuntimeError(f"limit_list_d {d} offset {off} failed")
        if len(x) and parts and x.head(3).astype(str).reset_index(drop=True).equals(parts[-1].head(3).astype(str).reset_index(drop=True)):
            raise RuntimeError(f"limit_list_d {d} offset {off} returned the previous page")
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
    ap.add_argument("--start", default="20220301")
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
        for k in FIELDS.split(","):
            if k not in df.columns:
                df[k] = None
        df = df[FIELDS.split(",")]
        for k in NUM:
            df[k] = pd.to_numeric(df[k], errors="coerce")
        for k in ("trade_date", "ts_code", "industry", "name", "first_time", "last_time", "up_stat", "limit"):
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
