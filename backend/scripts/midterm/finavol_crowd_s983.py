"""§983 FINAVOL 同日拥挤过滤网格。用法: python finavol_crowd_s983.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research70 as R70
c = pl.col
HOLD, KSLOTS, MAX_NEW = 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
GRID = [(5,3),(5,5),(8,3),(8,5),(10,3),(10,5)]
VOL = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))


def prep():
    df = R70.prep()
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("FINA") & VOL).fill_null(False)
    df = df.with_columns(sig.alias("FINAVOL"), (c("dq").fill_null(0) - tie).alias("sc_dq"))
    # daily candidate count
    n_day = pl.when(c("FINAVOL")).then(1).sum().over("trade_date")
    rk = pl.when(c("FINAVOL")).then(c("sc_dq")).rank(descending=True).over("trade_date")
    df = df.with_columns(n_day.alias("n_day"), rk.alias("rk"))
    for K,N in GRID:
        # keep if not crowded OR rank<=N
        keep = c("FINAVOL") & ((c("n_day") <= K) | (c("rk") <= N))
        df = df.with_columns(keep.fill_null(False).alias(f"C_{K}_{N}"))
    return df.sort("code", "trade_date")


def one(df, sig, sc, name, ph, s, e, out, res):
    t, cur = run_real(df, sig, sc, fixed(HOLD), s, e, k_slots=KSLOTS, max_new=MAX_NEW, max_hold=HOLD)
    m = full_metrics(df, t, cur)
    if t is not None and t.height:
        m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
        t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
    res.setdefault(name, {})[ph] = m
    print(name, ph, m, flush=True)


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "FINAVOL", "sc_dq", "FINAVOL_base", ph, s, e, out, res)
        for K,N in GRID:
            one(df, f"C_{K}_{N}", "sc_dq", f"FINAVOL_crowd_K{K}N{N}", ph, s, e, out, res)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
