from pathlib import Path
import sys, json, time
import polars as pl
from harness import load, run, metrics, PHASES
from research import add_signals, ex_ma60, ex_ma20

def ex_fixed(n):
    return lambda a, e, i: i - e >= n - 1
def ex_ma60_loose(a, e, i):
    if i > e and a["ac"][i] < 0.97*a["m60"][i] and a["ac"][i-1] < 0.97*a["m60"][i-1]: return True
    if a["turnover_rate"][i] > 4 * (a["t60"][i] or 1e9) and a["ac"][i] >= 0.97 * a["hi60"][i] and a["ac"][i] > a["ao"][e] * 1.15: return True
    return False

def add2(df):
    c = pl.col
    b = pl.read_parquet(str(Path(__file__).resolve().parents[2] / "data/midterm/daily_basic.parquet"))
    df = df.join(b, on=["code", "trade_date"], how="left")
    d = c("trade_date").str.to_date()
    monthly = (d.dt.weekday() == 5) & (d.dt.day() <= 7)
    weekly = d.dt.weekday() == 5
    liq = (c("amt20") > 3e7) & (c("age") > 250) & ~c("code").str.starts_with("8") & ~c("code").str.starts_with("4") & ~c("code").str.starts_with("9") & (c("pe_ttm") > 0)
    df = df.with_columns(
        pl.when(liq).then(c("total_mv")).rank().over("trade_date").alias("rk_mv"),
        pl.when(liq & (c("pe_ttm") < 30)).then(c("vol60")).rank().over("trade_date").alias("rk_lv"),
        pl.when(liq & (c("amt20") > 1e8)).then(c("r20")).rank().over("trade_date").alias("rk_rev"),
        pl.when(liq & (c("total_mv") < 1e6)).then(c("vol60")).rank().over("trade_date").alias("rk_smlv"),
    )
    return df.with_columns(
        (monthly & (c("rk_mv") <= 10)).fill_null(False).alias("S_small"),
        (monthly & (c("rk_lv") <= 10)).fill_null(False).alias("S_lowvol"),
        (weekly & (c("rk_rev") <= 10)).fill_null(False).alias("S_rev"),
        (monthly & (c("rk_smlv") <= 10)).fill_null(False).alias("S_smlv"),
        (-c("total_mv")).alias("sc_small"), (-c("vol60")).alias("sc_lv"), (-c("r20")).alias("sc_rev"),
    )

CONFIGS = {
  "base_hold40_loose": ("S_base", "sc_quiet", ex_ma60_loose, 40, 3),
  "base_macd_loose": ("S_base_macd", "sc_quiet", ex_ma60_loose, 40, 3),
  "small_m20": ("S_small", "sc_small", ex_fixed(20), 20, 10),
  "lowvol_m20": ("S_lowvol", "sc_lv", ex_fixed(20), 20, 10),
  "smlv_m20": ("S_smlv", "sc_lv", ex_fixed(20), 20, 10),
  "rev_w10": ("S_rev", "sc_rev", ex_fixed(10), 10, 10),
  "rev_w20": ("S_rev", "sc_rev", ex_fixed(20), 20, 10),
}
if __name__ == "__main__":
    names = sys.argv[1:] or list(CONFIGS)
    df = add2(add_signals(load()))
    for n in names:
        sig, sc, fn, mh, mn = CONFIGS[n]
        print(f"## {n} signals={df[sig].sum()}", flush=True)
        for ph, (s, e) in PHASES.items():
            m = metrics(run(df, sig, sc, fn, s, e, k_slots=10, max_new=mn, max_hold=mh))
            print(f"  {ph:22s} " + " ".join(f"{k}={float(v):g}" for k, v in m.items()), flush=True)
