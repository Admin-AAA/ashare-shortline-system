"""§156 诊断：V1 信号次日的开盘情况、买不进的比例、单笔复核。"""
import sys
sys.argv = ["x", "/dev/null"]
import polars as pl
import theme_leader_s156 as S

c = pl.col
k = S.load_kpl()
df = S.panel()
df, _ = S.signals(df, k)
df = df.with_columns(c("ao").shift(-1).over("code").alias("o1"), c("ac").shift(-1).over("code").alias("c1"),
                     c("lim_up_open").shift(-1).over("code").alias("lu_next"), c("ac").shift(-2).over("code").alias("c2"))
for v in ("V1_FIRST", "V2_IDENT", "FOLLOW"):
    s = df.filter(c(f"sig_{v}") & c("trade_date").is_between(pl.lit("2019-01-01"), pl.lit("2026-10-16")))
    buy = s.filter(~c("lu_next").fill_null(True))
    print(v, "signals", s.height, "next-day limit-up open (can't buy) %.1f%%" % (100 * s["lu_next"].mean()),
          "| buyable: gap %.2f%%  open->close %.2f%%  open->next close %.2f%%" % (
              100 * (buy["o1"] / buy["ac"] - 1).mean(), 100 * (buy["c1"] / buy["o1"] - 1).mean(), 100 * (buy["c2"] / buy["o1"] - 1).mean()),
          "| unbuyable close->next close %.2f%%" % (100 * (s.filter(c("lu_next").fill_null(False))["c1"] / s.filter(c("lu_next").fill_null(False))["ac"] - 1).mean()))
t = pl.read_parquet("../../output/research/s156/trades_V1_FIRST.parquet")
print(t.filter(c("entry") >= "2026-09-01").head(8))
