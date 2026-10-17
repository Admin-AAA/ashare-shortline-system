"""[中线] round 7: why mid-small-cap-weekly lost in 2021 + 3 PRE-DECLARED risk variants (no grid, no tuning):
  rel_guard : also require small-cap index / large-cap (300 largest eq-weight) ratio > its MA20
  guard_exit: exit all holdings at next open when small-cap index closes < MA20 (live guard turns off)
  stop10    : per-position close-based stop at -10% from entry (sell next open)
Panel = lt_research5_diag2 (weekly daily_basic scaled by price -> daily mcap). Adoption bar: no phase
worse by >0.3%/trade, 2021 improved, R12 not lower, MDD not worse in >2 phases."""
import json
import polars as pl
from lt_research import build, fixed, PH
from realistic import run_real, metrics_real
c = pl.col
df = build()
px = df.select("code", c("trade_date").alias("bd"), c("ac").alias("ac_bd"))
df = df.join(px, on=["code", "bd"], how="left").with_columns(
    (c("total_mv").cast(pl.Float64, strict=False) * c("ac") / c("ac_bd")).alias("mvd"))
g = (c("mvd").is_not_null() & (c("pe_ttm") > 0) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
r = df.filter(g).with_columns(c("mvd").rank("ordinal").over("trade_date").alias("r4"),
                              c("mvd").rank("ordinal", descending=True).over("trade_date").alias("rL"))
def eqidx(sub, name):
    return (sub.group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("s")).sort("trade_date")
            .with_columns((1 + c("s") / 100).cum_prod().alias(name)).select("trade_date", name))
idx = eqidx(r.filter(c("r4") <= 400), "scid").join(eqidx(r.filter(c("rL") <= 300), "lcid"), on="trade_date").sort("trade_date")
idx = idx.with_columns(c("scid").rolling_mean(20).alias("scid20"), (c("scid") / c("lcid")).alias("rel"))
idx = idx.with_columns(c("rel").rolling_mean(20).alias("rel20"))
df = df.join(idx, on="trade_date", how="left")
uni = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]) & (c("pe_ttm") > 0) & c("mvd").is_not_null())
df = df.with_columns(pl.when(uni.fill_null(False)).then(c("mvd")).rank("ordinal").over("trade_date").alias("rk_mv"),
                     (-c("mvd")).alias("sc_small"))
wk = c("trade_date").str.to_date().dt.weekday() == 5
on = c("scid") > c("scid20")
df = df.with_columns((wk & on & (c("rk_mv") <= 10)).fill_null(False).alias("S"),
                     (wk & on & (c("rel") > c("rel20")) & (c("rk_mv") <= 10)).fill_null(False).alias("S_rel"),
                     (~on).fill_null(False).alias("G_off")).sort("code", "trade_date")
def stop10(a, e, i):
    return i - e >= 19 or a["ac"][i] / a["ao"][e] - 1 <= -0.10
V = {"live": ("S", fixed(20), None), "rel_guard": ("S_rel", fixed(20), None),
     "guard_exit": ("S", fixed(20), "G_off"), "stop10": ("S", stop10, None)}
res = {}
for n, (s_, ex, gc) in V.items():
    print("##", n, flush=True)
    for ph, (s, e) in PH.items():
        t, cur = run_real(df, s_, "sc_small", ex, s, e, k_slots=20, max_new=10, max_hold=20, guard_col=gc)
        mm = metrics_real(t, cur); res.setdefault(n, {})[ph] = {a: float(v) for a, v in mm.items()}
        print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in mm.items()), flush=True)
        if n == "live" and ph.startswith("P21") and t is not None:
            mo = t.with_columns(c("entry").str.slice(0, 7).alias("m")).group_by("m").agg(
                pl.len().alias("n"), (c("ret").mean() * 100).round(2).alias("avg")).sort("m")
            print(mo, flush=True); res["live_2021_monthly"] = mo.to_dicts()
# regime context 2021: small vs large index returns by quarter
q = idx.filter(c("trade_date").is_between(pl.lit("2020-12-31"), pl.lit("2021-12-31")))
print(q.select("trade_date", "scid", "lcid", "rel").gather_every(20), flush=True)
json.dump(res, open("/tmp/lt7.json", "w"), indent=1)
