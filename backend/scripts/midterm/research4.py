import sys
import polars as pl
from harness import load, run, metrics, PHASES
from research import add_signals
from research2 import ex_fixed

def build():
    df = add_signals(load())
    c = pl.col
    st = pl.read_parquet("/tmp/st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    b = pl.read_parquet("/tmp/daily_basic.parquet")
    df = df.join(b, on=["code", "trade_date"], how="left").sort("code", "trade_date")
    d = c("trade_date").str.to_date()
    monthly = (d.dt.weekday() == 5) & (d.dt.day() <= 7)
    weekly = d.dt.weekday() == 5
    liq = (c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.starts_with("8") & ~c("code").str.starts_with("4") & ~c("code").str.starts_with("9") & (c("pe_ttm") > 0)
    df = df.with_columns(pl.when(liq).then(c("total_mv")).rank().over("trade_date").alias("rk_mv"))
    df = df.with_columns(pl.when(liq & (c("rk_mv") <= 200)).then(-c("r20")).rank().over("trade_date").alias("rk_smom"),
                         (-c("total_mv")).alias("sc_small"))
    return df.with_columns(
        (monthly & (c("rk_mv") <= 20)).fill_null(False).alias("S_small20"),
        (monthly & (c("rk_mv") <= 30)).fill_null(False).alias("S_small30"),
        (weekly & (c("rk_mv") <= 5)).fill_null(False).alias("S_small_wk"),
        (weekly & (c("rk_mv") <= 10)).fill_null(False).alias("S_small_wk10"),
        (monthly & (c("rk_smom") <= 10)).fill_null(False).alias("S_smom"),
    )
C = {
  "small20_m20": ("S_small20", "sc_small", ex_fixed(20), 20, 20, 20),
  "small30_m20": ("S_small30", "sc_small", ex_fixed(20), 20, 30, 30),
  "small_wk5_h20": ("S_small_wk", "sc_small", ex_fixed(20), 20, 20, 5),
  "small_wk10_h20": ("S_small_wk10", "sc_small", ex_fixed(20), 20, 40, 10),
  "small_mom": ("S_smom", "r20", ex_fixed(20), 20, 10, 10),
}
if __name__ == "__main__":
    df = build()
    for n in (sys.argv[1:] or C):
        sig, sc, fn, mh, k, mn = C[n]
        print(f"## {n} signals={df[sig].sum()}", flush=True)
        for ph, (s, e) in PHASES.items():
            m = metrics(run(df, sig, sc, fn, s, e, k_slots=999, max_new=mn, max_hold=mh), k)
            print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in m.items()), flush=True)
