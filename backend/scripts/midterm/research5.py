"""[中长线] small-cap (+momentum) with crash guards, realistic execution."""
import sys, json
import polars as pl
from harness import PHASES
from research4 import build
from realistic import add_limits, run_real, metrics_real

def fixed(n): return lambda a, e, i: i - e >= n - 1

def build5():
    df = add_limits(build())
    c = pl.col
    d = c("trade_date").str.to_date()
    weekly = d.dt.weekday() == 5
    monthly = weekly & (d.dt.day() <= 7)
    idx = (df.filter(c("rk_mv") <= 400).group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("sret"))
           .join(df.group_by("trade_date").agg((c("ac") > c("m20")).mean().alias("brd"), c("amount").sum().alias("mamt")), on="trade_date")
           .sort("trade_date")
           .with_columns((1 + c("sret") / 100).cum_prod().alias("sci")))
    idx = idx.with_columns(c("sci").rolling_mean(20).alias("sci20"), c("sci").rolling_mean(60).alias("sci60"),
                           (c("sci") / c("sci").shift(5) - 1).alias("sci5"),
                           (c("mamt").rolling_mean(5) / c("mamt").rolling_mean(60)).alias("liq"))
    df = df.join(idx.select("trade_date", "sci", "sci20", "sci60", "sci5", "brd", "liq"), on="trade_date", how="left").sort("code", "trade_date")
    df = df.with_columns(pl.when(c("rk_mv") <= 200).then(-c("r20")).rank().over("trade_date").alias("rk_smom2"))
    on20 = c("sci") > c("sci20"); on60 = c("sci") > c("sci60")
    crash = (c("sci5") < -0.06) | (c("sci") < c("sci60") * 0.95)
    return df.with_columns(
        (weekly & (c("rk_mv") <= 10)).fill_null(False).alias("W_small"),
        (weekly & (c("rk_mv") <= 10) & on20).fill_null(False).alias("W_small_g20"),
        (weekly & (c("rk_mv") <= 10) & on60).fill_null(False).alias("W_small_g60"),
        (weekly & (c("rk_smom2") <= 10)).fill_null(False).alias("W_smom"),
        (weekly & (c("rk_smom2") <= 10) & on20).fill_null(False).alias("W_smom_g20"),
        (weekly & (c("rk_smom2") <= 10) & on60).fill_null(False).alias("W_smom_g60"),
        (monthly & (c("rk_smom2") <= 10)).fill_null(False).alias("M_smom"),
        (monthly & (c("rk_smom2") <= 10) & on60).fill_null(False).alias("M_smom_g60"),
        crash.fill_null(False).alias("G_crash"),
        (~on60).fill_null(False).alias("G_below60"),
    )

C = {  # sig, score, hold, K, max_new, guard
  "W_small":            ("W_small", "sc_small", 20, 40, 10, None),
  "W_small_g20":        ("W_small_g20", "sc_small", 20, 40, 10, None),
  "W_small_g60_crash":  ("W_small_g60", "sc_small", 20, 40, 10, "G_crash"),
  "W_smom":             ("W_smom", "r20", 20, 40, 10, None),
  "W_smom_g20":         ("W_smom_g20", "r20", 20, 40, 10, None),
  "W_smom_g60":         ("W_smom_g60", "r20", 20, 40, 10, None),
  "W_smom_g60_crash":   ("W_smom_g60", "r20", 20, 40, 10, "G_crash"),
  "W_smom_g60_b60":     ("W_smom_g60", "r20", 20, 40, 10, "G_below60"),
  "M_smom":             ("M_smom", "r20", 20, 10, 10, None),
  "M_smom_g60_crash":   ("M_smom_g60", "r20", 20, 10, 10, "G_crash"),
}
if __name__ == "__main__":
    df = build5()
    res = {}
    for n in (sys.argv[1:] or C):
        sig, sc, h, k, mn, g = C[n]
        print(f"## {n}", flush=True)
        for ph, (s, e) in PHASES.items():
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=k, max_new=mn, max_hold=h, guard_col=g)
            m = metrics_real(t, cur); res.setdefault(n, {})[ph] = m
            print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in m.items()), flush=True)
    json.dump(res, open("/tmp/mt5.json", "w"), indent=1)
