"""[中线] diag2: approximate DAILY mcap pre-2022 by scaling weekly total_mv with price since basic date;
check guard agreement vs research7 daily-basic index (2022+), then validate live rule 2019-2026."""
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
m = df.filter(g).with_columns(c("mvd").rank("ordinal").over("trade_date").alias("r4")).filter(c("r4") <= 400)
idx = (m.group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("s2")).sort("trade_date")
       .with_columns((1 + c("s2") / 100).cum_prod().alias("scid")).with_columns(c("scid").rolling_mean(20).alias("scid20")))
df = df.join(idx.select("trade_date", "scid", "scid20"), on="trade_date", how="left")
ref = pl.read_csv("/tmp/sciS.csv", schema_overrides={"trade_date": pl.Utf8}).drop_nulls()
uni = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]) & (c("pe_ttm") > 0) & c("mvd").is_not_null())
df = df.with_columns(pl.when(uni.fill_null(False)).then(c("mvd")).rank("ordinal").over("trade_date").alias("rk_mv"),
                     (-c("mvd")).alias("sc_small"))
wk = c("trade_date").str.to_date().dt.weekday() == 5
fr = df.filter(wk).select("trade_date", (c("scid") > c("scid20")).alias("a")).unique().join(ref, on="trade_date").with_columns((c("sciS") > c("sciS20")).alias("b"))
print("fridays", fr.height, "agree", (fr["a"] == fr["b"]).sum(), flush=True)
res = {}
for gname, on in (("guard_daily_approx", c("scid") > c("scid20")),
                  ("guard_ma10", c("scid") > c("scid").rolling_mean(10).over("code")),):
    if gname == "guard_ma10": continue
    df2 = df.with_columns((wk & on & (c("rk_mv") <= 10)).fill_null(False).alias("S"))
    print("##", gname, flush=True)
    for ph, (s, e) in PH.items():
        t, cur = run_real(df2, "S", "sc_small", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
        mm = metrics_real(t, cur); res.setdefault(gname, {})[ph] = {a: float(v) for a, v in mm.items()}
        print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in mm.items()), flush=True)
json.dump(res, open("/tmp/lt5d2.json", "w"), indent=1)
