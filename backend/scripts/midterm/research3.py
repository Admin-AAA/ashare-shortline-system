import sys
import polars as pl
from harness import load, run, metrics, PHASES
from research import add_signals
from research2 import add2, ex_fixed, ex_ma60_loose

def add3(df):
    c = pl.col
    d = c("trade_date").str.to_date()
    monthly = (d.dt.weekday() == 5) & (d.dt.day() <= 7)
    weekly = d.dt.weekday() == 5
    liq = (c("amt20") > 3e7) & (c("age") > 250) & ~c("code").str.starts_with("8") & ~c("code").str.starts_with("4") & ~c("code").str.starts_with("9") & (c("pe_ttm") > 0)
    df = df.with_columns(
        c("r20").median().over("trade_date").alias("brd20"),
        pl.when(liq & (c("rk_mv") <= 100)).then(c("r20")).rank().over("trade_date").alias("rk_srev"),
        pl.when(liq & (c("rk_mv") <= 100)).then(c("vol60")).rank().over("trade_date").alias("rk_slv"),
        pl.when(liq & (c("rk_mv") <= 200)).then(-c("r20")).rank().over("trade_date").alias("rk_smom"),
    )
    risk_on = c("brd20") > -0.08
    return df.with_columns(
        (monthly & (c("rk_mv") <= 20)).fill_null(False).alias("S_small20"),
        (monthly & (c("rk_mv") <= 10) & risk_on).fill_null(False).alias("S_small_ro"),
        (weekly & (c("rk_mv") <= 5)).fill_null(False).alias("S_small_wk"),
        (monthly & (c("rk_srev") <= 10)).fill_null(False).alias("S_srev"),
        (monthly & (c("rk_slv") <= 10)).fill_null(False).alias("S_slv"),
        (monthly & (c("rk_smom") <= 10)).fill_null(False).alias("S_smom"),
        (c("S_base") & (c("rk_mv") <= 1000)).fill_null(False).alias("S_base_small"),
    )

C = {
  "small20_m20": ("S_small20", "sc_small", ex_fixed(20), 20, 20, 20),
  "small_riskon": ("S_small_ro", "sc_small", ex_fixed(20), 20, 10, 10),
  "small_wk_hold20": ("S_small_wk", "sc_small", ex_fixed(20), 20, 20, 5),
  "small_rev": ("S_srev", "sc_rev", ex_fixed(20), 20, 10, 10),
  "small_lowvol": ("S_slv", "sc_lv", ex_fixed(20), 20, 10, 10),
  "small_mom": ("S_smom", "r20", ex_fixed(20), 20, 10, 10),
  "base_fixed20": ("S_base", "sc_quiet", ex_fixed(20), 20, 10, 3),
  "base_small_loose": ("S_base_small", "sc_quiet", ex_ma60_loose, 40, 10, 3),
}
if __name__ == "__main__":
    df = add3(add2(add_signals(load())))
    for n in (sys.argv[1:] or C):
        sig, sc, fn, mh, k, mn = C[n]
        print(f"## {n} signals={df[sig].sum()}", flush=True)
        for ph, (s, e) in PHASES.items():
            m = metrics(run(df, sig, sc, fn, s, e, k_slots=k, max_new=mn, max_hold=mh), k)
            print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in m.items()), flush=True)
