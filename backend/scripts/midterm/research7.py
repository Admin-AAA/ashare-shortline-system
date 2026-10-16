"""[中长线] guard with SQL-reproducible small-cap index: equal-weight mean pct_chg of the 400 smallest
total_mv stocks (daily_basic same day, pe_ttm>0, non-BSE) -> sci; guard = sci > MA20(sci)."""
import polars as pl
from harness import PHASES
from research5 import build5, fixed
from realistic import run_real, metrics_real
import json

def add_simple_guard(df):
    c = pl.col
    m = df.filter(c("total_mv").is_not_null() & (c("pe_ttm") > 0) & ~c("code").str.starts_with("8") & ~c("code").str.starts_with("4") & ~c("code").str.starts_with("9"))
    m = m.with_columns(c("total_mv").rank("ordinal").over("trade_date").alias("r")).filter(c("r") <= 400)
    idx = m.group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("s2")).sort("trade_date")
    idx = idx.with_columns((1 + c("s2") / 100).cum_prod().alias("sciS")).with_columns(c("sciS").rolling_mean(20).alias("sciS20"))
    return df.join(idx.select("trade_date", "sciS", "sciS20"), on="trade_date", how="left").sort("code", "trade_date")

if __name__ == "__main__":
    df = add_simple_guard(build5()); c = pl.col
    wk = c("trade_date").str.to_date().dt.weekday() == 5
    df = df.with_columns((wk & (c("rk_mv") <= 10) & (c("sciS") > c("sciS20"))).fill_null(False).alias("S"))
    out = {}
    for k in (20,):
        print("## top10_simpleguard_hold20 K", k, flush=True)
        for ph, (s, e) in PHASES.items():
            t, cur = run_real(df, "S", "sc_small", fixed(20), s, e, k_slots=k, max_new=10, max_hold=20)
            m = metrics_real(t, cur); out[ph] = {a: float(v) for a, v in m.items()}
            print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in m.items()), flush=True)
    json.dump(out, open("/tmp/mt7.json", "w"))
    # export guard series for production cross-check
    df.select("trade_date", "sciS", "sciS20").unique().sort("trade_date").write_csv("/tmp/sciS.csv")
