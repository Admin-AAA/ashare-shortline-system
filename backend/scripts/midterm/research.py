"""[中长线] candidate mid-term strategies, evaluated across phases."""
import sys, json, time
import polars as pl
from harness import load, run, metrics, PHASES

def add_signals(df):
    c = pl.col
    df = df.with_columns(
        c("ac").rolling_max(20).over("code").alias("hi20"), c("ac").rolling_min(20).over("code").alias("lo20"),
        c("al").rolling_min(10).over("code").alias("lowA"),
        c("al").shift(10).rolling_min(10).over("code").alias("lowB"),
        c("pct_chg").abs().rolling_max(20).over("code").alias("maxabs20"),
        ((c("macd_dif") > c("macd_dea")) & (c("macd_dif").shift(1) <= c("macd_dea").shift(1))).over("code").alias("gx"),
        c("macd_dif").rolling_min(60).over("code").alias("difmin60"),
    )
    df = df.with_columns(c("gx").cast(pl.Int8).rolling_max(5).over("code").alias("gx5"))
    liquid = (c("amt20") > 5e7) & (c("age") > 250) & ~c("code").str.starts_with("8") & ~c("code").str.starts_with("4") & ~c("code").str.starts_with("9")
    base = (liquid & (c("ac") / c("hi250") < 0.70) & (c("hi20") / c("lo20") < 1.15)
            & (c("v5") / c("v60") < 0.65) & (c("lowA") > c("lowB")) & (c("maxabs20") < 6))
    near = ((c("ac") / c("m60")).is_between(0.96, 1.05)) | ((c("ac") / c("m120")).is_between(0.96, 1.05))
    df = df.with_columns(base.fill_null(False).alias("base"), near.fill_null(False).alias("near"))
    df = df.with_columns(c("base").cast(pl.Int8).rolling_max(15).over("code").alias("base15"))
    brk = ((c("base15") == 1) & (c("pct_chg") >= 7) & (c("volume") > 2 * c("v5")) & (c("ac") > c("m60")))
    df = df.with_columns(
        (c("base") & c("near")).alias("S_base"),
        (c("base") & c("near") & (c("gx5") == 1) & (c("v5") > c("v10"))).alias("S_base_macd"),
        brk.fill_null(False).alias("S_brk"),
        (brk & (c("gx5") == 1)).fill_null(False).alias("S_brk_macd"),
        (-(c("v5") / c("v60"))).alias("sc_quiet"),
        (c("volume") / c("v5")).alias("sc_brkvol"),
    )
    # momentum 12-1 (weekly signal on Fridays-ish: every 5th trading day by age)
    df = df.with_columns(c("r12_1").rank(descending=True).over("trade_date").alias("rk_mom"))
    wk = pl.col("trade_date").str.to_date().dt.weekday() == 5
    df = df.with_columns(
        (wk & liquid & (c("amt20") > 1e8) & (c("ac") > c("m60")) & (c("rk_mom") <= 30)).fill_null(False).alias("S_mom"),
        (liquid & (c("m5") > c("m10")) & (c("m10") > c("m20")) & (c("m20") > c("m60")) & (c("m60") > c("m120"))
         & (c("ac") >= c("hi250") * 0.999) & (c("volume") > 1.5 * c("v60")) & (c("r20") < 0.35)).fill_null(False).alias("S_trend"),
        c("r60").alias("sc_r60"),
    )
    return df

def ex_ma60(a, e, i):
    # close below MA60 two consecutive days, or climax-volume top
    if i > e and a["ac"][i] < a["m60"][i] and a["ac"][i-1] < a["m60"][i-1]: return True
    if a["turnover_rate"][i] > 4 * (a["t60"][i] or 1e9) and a["ac"][i] >= 0.97 * a["hi60"][i] and a["ac"][i] > a["ao"][e] * 1.15: return True
    return False

def ex_ma60_hard(a, e, i):
    if ex_ma60(a, e, i): return True
    return a["ac"][i] < a["ao"][e] * 0.90  # 10% hard stop

def ex_ma20(a, e, i):
    return i > e and a["ac"][i] < a["m20"][i] and a["ac"][i-1] < a["m20"][i-1]

def ex_fixed20(a, e, i):
    return i - e >= 19

CONFIGS = {
  "base_ma60": ("S_base", "sc_quiet", ex_ma60, 60),
  "base_ma60_stop10": ("S_base", "sc_quiet", ex_ma60_hard, 60),
  "base_macd_ma60": ("S_base_macd", "sc_quiet", ex_ma60, 60),
  "brk_ma60": ("S_brk", "sc_brkvol", ex_ma60, 60),
  "brk_ma60_stop10": ("S_brk", "sc_brkvol", ex_ma60_hard, 60),
  "brk_macd_ma60": ("S_brk_macd", "sc_brkvol", ex_ma60, 60),
  "brk_ma20": ("S_brk", "sc_brkvol", ex_ma20, 40),
  "mom12_1_hold20": ("S_mom", "r12_1", ex_fixed20, 20),
  "mom12_1_ma20": ("S_mom", "r12_1", ex_ma20, 60),
  "trend_ma20": ("S_trend", "sc_r60", ex_ma20, 60),
}

if __name__ == "__main__":
    names = sys.argv[1:] or list(CONFIGS)
    t0 = time.time(); df = add_signals(load()); print("load", round(time.time()-t0), "s", flush=True)
    out = {}
    for n in names:
        sig, sc, fn, mh = CONFIGS[n]
        print(f"## {n}  signals={df[sig].sum()}", flush=True)
        for ph, (s, e) in PHASES.items():
            t = run(df, sig, sc, fn, s, e, k_slots=10, max_new=3, max_hold=mh)
            m = metrics(t); out.setdefault(n, {})[ph] = m
            print(f"  {ph:22s} {m}", flush=True)
    json.dump(out, open(f"/tmp/midterm_{int(time.time())}.json", "w"), ensure_ascii=False, indent=1)
