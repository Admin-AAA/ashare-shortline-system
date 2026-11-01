"""[中线] round 5: validate live mid-small-cap-weekly on 2019-2021 (long history, weekly PIT daily_basic)
+ 3 pre-declared within-small-cap tilts (no grid): low turnover / low vol / short-term reversal among the
30 smallest. Same universe as production: ex-ST/BSE, pe_ttm>0, listed>250d, amt20>3e7; Friday close
signal, next-open entry, hold 20d, K=20, no re-buy of held names, guard = 400-smallest eq-weight index > MA20."""
import json
import polars as pl
from lt_research import build, fixed, PH
from realistic import run_real, metrics_real
c = pl.col

def prep(df):
    df = df.with_columns(
        (c("amount") / (c("circ_mv").cast(pl.Float64, strict=False) * 1e4)).alias("to"),
        (c("pct_chg") / 100).rolling_std(20).over("code").alias("vol20"))
    df = df.with_columns(c("to").rolling_mean(20).over("code").alias("to20"))
    uni = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
           & (c("pe_ttm") > 0) & c("total_mv").is_not_null())
    df = df.with_columns(pl.when(uni.fill_null(False)).then(c("total_mv").cast(pl.Float64, strict=False)).rank("ordinal").over("trade_date").alias("rk_mv"))
    g = (c("total_mv").is_not_null() & (c("pe_ttm") > 0) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
    m = df.filter(g).with_columns(c("total_mv").cast(pl.Float64, strict=False).rank("ordinal").over("trade_date").alias("r4")).filter(c("r4") <= 400)
    idx = (m.group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("s2")).sort("trade_date")
           .with_columns((1 + c("s2") / 100).cum_prod().alias("sci")).with_columns(c("sci").rolling_mean(20).alias("sci20")))
    df = df.join(idx.select("trade_date", "sci", "sci20"), on="trade_date", how="left")
    wk = c("trade_date").str.to_date().dt.weekday() == 5
    on = c("sci") > c("sci20")
    s30 = c("rk_mv") <= 30
    def tilt(col):  # rank ascending col among the 30 smallest -> top 10
        return (pl.when(s30).then(c(col)).rank("ordinal").over("trade_date") <= 10)
    df = df.with_columns((-c("total_mv").cast(pl.Float64, strict=False)).alias("sc_small"),
                         (-c("to20")).alias("sc_to"), (-c("vol20")).alias("sc_vol"), (-c("r20")).alias("sc_rev"))
    return df.with_columns(
        (wk & on & (c("rk_mv") <= 10)).fill_null(False).alias("S_live"),
        (wk & (c("rk_mv") <= 10)).fill_null(False).alias("S_noguard"),
        (wk & on & tilt("to20")).fill_null(False).alias("S_lowto"),
        (wk & on & tilt("vol20")).fill_null(False).alias("S_lowvol"),
        (wk & on & tilt("r20")).fill_null(False).alias("S_rev"),
    ).sort("code", "trade_date")

C = {"live_guard20": ("S_live", "sc_small"), "no_guard": ("S_noguard", "sc_small"),
     "small30_low_turnover": ("S_lowto", "sc_to"), "small30_low_vol": ("S_lowvol", "sc_vol"),
     "small30_reversal": ("S_rev", "sc_rev")}
if __name__ == "__main__":
    df = prep(build()); res = {}
    for n, (s_, sc) in C.items():
        print(f"## {n}", flush=True)
        for ph, (s, e) in PH.items():
            t, cur = run_real(df, s_, sc, fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
            m = metrics_real(t, cur); res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
            print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in m.items()), flush=True)
    json.dump(res, open("/tmp/lt5.json", "w"), indent=1)
