"""Sprint4 §116：业绩快报（Tushare express，经统一数据网关 tushare_client.get_pro，只读）缓存到 parquet（不写库）。

阶段 1  express：按自然月 start_date/end_date 拉（单月 ≥1000 行时拆上下半月复核行数一致，不一致报错），
        输出 output/research/sprint4/express/express_YYYYMM.parquet；空月也落盘（断点续跑）。
阶段 2  income：对快报里出现过的股票逐股 income(ts_code=…) 拉营业收入（去年同期营收基数），
        输出 output/research/sprint4/express/income/<code6>.parquet。
用法: python scripts/midterm/fetch_express_cache.py --start 201801 --end 202610 [--stage express|income|all] [--sleep 0.4]
"""
from __future__ import annotations
import argparse, calendar, glob, sys, time
from pathlib import Path

B = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(B))
import pandas as pd
from app.services.tushare_client import get_pro

OUT = B / "output/research/sprint4/express"
INC = OUT / "income"
EFIELDS = ("ts_code,ann_date,end_date,revenue,operate_profit,total_profit,n_income,total_assets,"
           "diluted_eps,diluted_roe,yoy_net_profit,update_flag")
IFIELDS = "ts_code,ann_date,f_ann_date,end_date,report_type,revenue,total_revenue"
LAST_DAY = "20261016"


def _call(fn, **kw):
    for attempt in range(4):
        try:
            return fn(**kw)
        except Exception as ex:  # noqa: BLE001
            last = ex
            time.sleep((5, 20, 60, 120)[attempt])
    raise RuntimeError(f"{kw} failed: {last!r}"[:300])


def fetch_month(p, ym: str) -> pd.DataFrame:
    y, m = int(ym[:4]), int(ym[4:])
    s, e = f"{ym}01", f"{ym}{calendar.monthrange(y, m)[1]:02d}"
    e = min(e, LAST_DAY)
    x = _call(p.express, start_date=s, end_date=e, fields=EFIELDS)
    if len(x) >= 1000:
        time.sleep(0.5)
        a = _call(p.express, start_date=s, end_date=f"{ym}15", fields=EFIELDS)
        time.sleep(0.5)
        b = _call(p.express, start_date=f"{ym}16", end_date=e, fields=EFIELDS)
        if len(a) + len(b) != len(x):
            print(f"  {ym} month {len(x)} != halves {len(a)}+{len(b)} -> use halves", flush=True)
            x = pd.concat([a, b], ignore_index=True)
            if len(a) >= 1000 or len(b) >= 1000:
                raise RuntimeError(f"{ym} half-month still >=1000 rows; need finer split")
    return x


def stage_express(start: str, end: str, sleep: float):
    OUT.mkdir(parents=True, exist_ok=True)
    p = get_pro()
    yms, y, m = [], int(start[:4]), int(start[4:])
    while f"{y}{m:02d}" <= end:
        yms.append(f"{y}{m:02d}"); m += 1
        if m == 13: y, m = y + 1, 1
    for ym in yms:
        f = OUT / f"express_{ym}.parquet"
        if f.exists():
            continue
        x = fetch_month(p, ym)
        for k in ("ts_code", "ann_date", "end_date"):
            x[k] = x[k].astype(str)
        for k in [k for k in x.columns if k not in ("ts_code", "ann_date", "end_date")]:
            x[k] = pd.to_numeric(x[k], errors="coerce")
        x.to_parquet(f, index=False)
        print(ym, len(x), flush=True)
        time.sleep(sleep)


def stage_income(sleep: float, codes_file: str | None = None):
    """codes_file：只拉其中的股票（如只拉 E_any 候选股——营收基数/同期正式报告只对候选有影响）。"""
    INC.mkdir(parents=True, exist_ok=True)
    e = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(str(OUT / "express_*.parquet")))], ignore_index=True)
    codes = sorted({c for c in e["ts_code"].astype(str) if not c.endswith(".BJ") and c[:1] not in "489"})
    if codes_file:
        want = {x.strip() for x in open(codes_file) if x.strip()}
        codes = [c for c in codes if c in want]
    print("income codes", len(codes), flush=True)
    p = get_pro(); done = fail = 0; t0 = time.time()
    for ts in codes:
        f = INC / f"{ts[:6]}.parquet"
        if f.exists():
            continue
        try:
            x = _call(p.income, ts_code=ts, fields=IFIELDS)
        except Exception as ex:  # noqa: BLE001
            fail += 1; print("FAIL", ts, repr(ex)[:200], flush=True); continue
        for k in ("ts_code", "ann_date", "f_ann_date", "end_date", "report_type"):
            if k in x.columns: x[k] = x[k].astype(str)
        for k in ("revenue", "total_revenue"):
            if k in x.columns: x[k] = pd.to_numeric(x[k], errors="coerce")
        x.to_parquet(f, index=False)
        done += 1
        if done % 100 == 0:
            print(f"income {done} done, {fail} fail, {time.time() - t0:.0f}s", flush=True)
        time.sleep(sleep)
    print(f"income finished: {done} new, {fail} fail", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="201801"); ap.add_argument("--end", default="202610")
    ap.add_argument("--stage", default="all"); ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--codes-file", default=None)
    a = ap.parse_args()
    if a.stage in ("express", "all"):
        stage_express(a.start, a.end, a.sleep)
    if a.stage in ("income", "all"):
        stage_income(a.sleep, a.codes_file)
