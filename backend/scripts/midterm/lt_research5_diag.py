"""[中线] diag: live mid strategy with guard from daily-mcap index (research7 /tmp/sciS.csv) vs weekly-mcap index."""
import polars as pl
from lt_research import build, fixed, PH
from realistic import run_real, metrics_real
from lt_research5 import prep
c = pl.col
df = prep(build())
g = pl.read_csv("/tmp/sciS.csv", schema_overrides={"trade_date": pl.Utf8}).drop_nulls()
df = df.join(g, on="trade_date", how="left")
wk = c("trade_date").str.to_date().dt.weekday() == 5
fr = df.filter(wk & c("sciS").is_not_null()).select("trade_date", (c("sci") > c("sci20")).alias("a"), (c("sciS") > c("sciS20")).alias("b")).unique().sort("trade_date")
print("fridays", fr.height, "agree", (fr["a"] == fr["b"]).sum(), "weekly-on", fr["a"].sum(), "daily-on", fr["b"].sum())
for y in ("2022", "2023", "2024", "2025", "2026"):
    f = fr.filter(c("trade_date").str.starts_with(y)); print(y, f.height, "agree", (f["a"] == f["b"]).sum())
df = df.with_columns((wk & (c("sciS") > c("sciS20")) & (c("rk_mv") <= 10)).fill_null(False).alias("S_d"))
for ph, (s, e) in PH.items():
    if s < "2022": continue
    t, cur = run_real(df, "S_d", "sc_small", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
    m = metrics_real(t, cur); print(f"  dailyguard {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in m.items()), flush=True)
