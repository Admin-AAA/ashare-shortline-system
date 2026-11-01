"""[中长线] sensitivity of small-cap + small-cap-index MA guard."""
import sys
import polars as pl
from harness import PHASES
from research5 import build5, fixed
from realistic import run_real, metrics_real
df = build5(); c = pl.col
d = c("trade_date").str.to_date(); wk = d.dt.weekday() == 5
df = df.with_columns(*[c("sci").rolling_mean(n).alias(f"sciM{n}") for n in (10, 30)])
df = df.sort("code", "trade_date")
V = {
 "top10_g10": (wk & (c("rk_mv") <= 10) & (c("sci") > c("sciM10"))),
 "top10_g30": (wk & (c("rk_mv") <= 10) & (c("sci") > c("sciM30"))),
 "top5_g20": (wk & (c("rk_mv") <= 5) & (c("sci") > c("sci20"))),
 "top20_g20": (wk & (c("rk_mv") <= 20) & (c("sci") > c("sci20"))),
 "top10_g20_brd": (wk & (c("rk_mv") <= 10) & (c("sci") > c("sci20")) & (c("brd") > 0.4)),
}
for n, expr in V.items():
    df = df.with_columns(expr.fill_null(False).alias(n))
    for h in (10, 20):
        print(f"## {n} hold{h}", flush=True)
        for ph, (s, e) in PHASES.items():
            k = 40 if "top20" in n else 20 if "top5" in n else 40
            t, cur = run_real(df, n, "sc_small", fixed(h), s, e, k_slots=k, max_new=20, max_hold=h)
            m = metrics_real(t, cur)
            print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in m.items()), flush=True)
