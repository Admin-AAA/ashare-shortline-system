"""Sprint4 §105：机构调研（Tushare stk_surv，经统一数据网关 tushare_client.get_pro，只读）按自然日缓存到 parquet（不写库）。

网关 stk_surv 历史只从 2021-08 起有数据（之前所有上游均返回空）；单次最多 400 行，用 offset 分页。
每个自然日一个文件 output/research/sprint4/stk_surv/YYYYMMDD.parquet（空日也落盘为空表，断点续跑）。
用法: python scripts/fetch_stk_surv_cache.py --start 20210701 --end 20261016 --workers 2
"""
from __future__ import annotations
import argparse, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
import pandas as pd
from app.services.tushare_client import get_pro

OUT = B / "output/research/sprint4/stk_surv"
FIELDS = "ts_code,surv_date,rece_mode,rece_org,org_type"  # 只取研究需要的列（行数上限 400 不变，响应更小）
PAGE = 400


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
                x = p.stk_surv(**kw)
                break
            except Exception:
                time.sleep((20, 65, 120, 240)[attempt])  # promax 限流/熔断时 quantdata 不支持 offset，等熔断恢复
        if x is None:
            raise RuntimeError(f"stk_surv {d} offset {off} failed")
        if len(x) and parts and x.head(3).astype(str).equals(parts[-1].head(3).astype(str).reset_index(drop=True)):
            raise RuntimeError(f"stk_surv {d} offset {off} returned the previous page (offset ignored)")
        if len(x):
            parts.append(x.reset_index(drop=True))
        if off > 40000:
            raise RuntimeError(f"stk_surv {d} too many pages")
        if len(x) < PAGE:
            break
        off += PAGE
        time.sleep(1.0)
    return pd.concat(parts, ignore_index=True).astype(str).drop_duplicates() if parts else pd.DataFrame(columns=FIELDS.split(","))


def one(d: str):
    f = OUT / f"{d}.parquet"
    if f.exists():
        return d, -1, 0.0
    t = time.time()
    try:
        df = fetch_day(d)
    except Exception as e:  # 留给下次重试
        return d, -2, time.time() - t
    df.astype(str).to_parquet(f, index=False)
    return d, len(df), time.time() - t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20210701")
    ap.add_argument("--end", default="20261016")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    s = date(int(a.start[:4]), int(a.start[4:6]), int(a.start[6:]))
    e = date(int(a.end[:4]), int(a.end[4:6]), int(a.end[6:]))
    days = [(s + timedelta(i)).strftime("%Y%m%d") for i in range((e - s).days + 1)]
    print("days", len(days), flush=True)
    done = 0
    with ThreadPoolExecutor(a.workers) as ex:
        for d, n, sec in ex.map(one, days):
            done += 1
            if n != -1:
                print(d, n, round(sec, 1), f"{done}/{len(days)}", flush=True)
    miss = [d for d in days if not (OUT / f"{d}.parquet").exists()]
    print("missing", len(miss), miss[:20], flush=True)


if __name__ == "__main__":
    main()
