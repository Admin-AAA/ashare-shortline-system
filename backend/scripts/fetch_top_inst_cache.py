"""Sprint4 §101：龙虎榜机构席位（Tushare top_inst，经统一数据网关）按交易日缓存到 parquet（只读拉取，不写库）。

用法: python scripts/fetch_top_inst_cache.py --ranges 20220328-20231103,20250113-20261016 --workers 2
每个交易日一个文件 output/research/sprint4/top_inst/YYYYMMDD.parquet（空表也落盘，断点续跑）。
交易日 = daily_kbars 000001 的交易日。
"""
from __future__ import annotations
import argparse, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
import pandas as pd
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.tushare_client import fetch_top_inst

OUT = B / "output/research/sprint4/top_inst"
COLS = ["code", "trade_date", "institution", "side", "buy_amount_yi", "sell_amount_yi", "net_buy_yi", "reason"]


def one(d: str) -> tuple[str, int, float]:
    f = OUT / f"{d}.parquet"
    if f.exists():
        return d, -1, 0.0
    t = time.time()
    rows = []
    for attempt in range(3):
        rows = fetch_top_inst(d)
        if rows:
            break
        time.sleep(2 + 3 * attempt)
    df = pd.DataFrame(rows, columns=COLS) if rows else pd.DataFrame(columns=COLS)
    if rows:  # 只缓存非空；空的留给下次重试
        df.to_parquet(f, index=False)
    return d, len(rows), time.time() - t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ranges", default="20220328-20231103,20250113-20261016")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        cal = sorted({str(r[0]).replace("-", "") for r in db.execute(text(
            "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001'"))})
    days = []
    for rg in a.ranges.split(","):
        s, e = rg.split("-")
        days += [d for d in cal if s <= d <= e]
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
