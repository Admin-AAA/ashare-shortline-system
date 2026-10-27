"""§257 事后诊断（探索性，不用于采纳）：龙头/跟风/全市场涨停股，「假设 L 日收盘按涨停价能买到」时的上界收益，
按封板时间分桶——判断龙头的收益是不是集中在买不到的一字/秒板。用法同 theme_pb_s257.py。"""
import sys
sys.argv = ["x", "/dev/null"]
from pathlib import Path
import polars as pl, numpy as np
import theme_pb_s257 as P
import theme_leader_s156 as S
c = pl.col
k = S.load_kpl(); df = P.panel()
lu = k.select("code", "trade_date").with_columns(pl.lit(1).alias("is_lu"))
df = df.join(lu, on=["code", "trade_date"], how="left").with_columns(c("is_lu").fill_null(0))
df = df.with_columns(c("is_lu").shift(1).rolling_sum(120, min_samples=1).over("code").fill_null(0).alias("gx"))
th = k.with_columns(c("theme").fill_null("").str.split("、").alias("th")).explode("th").with_columns(c("th").str.strip_chars())
th = th.filter((c("th").str.len_chars() > 0) & ~pl.any_horizontal([c("th").str.contains(b, literal=True) for b in S.BAD]))
th = th.join(df.select("code", "trade_date", "gx", "chong10", "age", "st", c("amount").alias("pamt")), on=["code", "trade_date"], how="left")
th = th.with_columns(pl.coalesce("amount", "pamt").alias("amt"))
heat = th.group_by("trade_date", "th").agg(pl.len().cast(pl.Int64).alias("n"), c("amt").sum().alias("tamt"))
heat = heat.with_columns(pl.struct(n=-c("n"), a=-c("tamt")).rank("ordinal").over("trade_date").alias("hr"))
hot = heat.filter((c("hr") <= 3) & (c("n") >= 5))
t = th.join(hot.select("trade_date", "th"), on=["trade_date", "th"], how="inner")
t = t.with_columns(c("lu_time").fill_null("99:99:99"))
t = t.with_columns(pl.struct(a=c("lu_time"), b=c("code")).rank("ordinal").over("trade_date", "th").alias("r_lu"),
                   (-c("amt").fill_null(0)).rank("min").over("trade_date", "th").alias("r_amt"),
                   (-c("gx").fill_null(0)).rank("min").over("trade_date", "th").alias("r_gx"))
t = t.with_columns((c("r_lu") + c("r_amt") + c("r_gx")).alias("ident"))
t = t.with_columns(pl.struct(a=c("ident"), b=c("lu_time"), d=c("code")).rank("ordinal").over("trade_date", "th").alias("r_id"))
uni = (~c("st").fill_null(True)) & (c("age").fill_null(0) >= 60) & (c("amt").fill_null(0) >= 1e8) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
t = t.filter(uni & c("nb").is_not_null() & (c("nb") <= 3))
lead = t.filter(c("r_id") <= 2).group_by("code", "trade_date").agg(c("lu_time").first()).with_columns(pl.lit("龙头").alias("grp"))
fol = t.filter(c("r_id") >= 4).group_by("code", "trade_date").agg(c("lu_time").first()).with_columns(pl.lit("跟风").alias("grp"))
alk = k.join(df.select("code", "trade_date", "age", "st", c("amount").alias("pamt")), on=["code", "trade_date"], how="left")
alk = alk.filter((~c("st").fill_null(True)) & (c("age").fill_null(0) >= 60) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]) & c("nb").is_not_null() & (c("nb") <= 3))
mkt = alk.select("code", "trade_date", c("lu_time").fill_null("99:99:99")).with_columns(pl.lit("全市场涨停").alias("grp"))
g = pl.concat([lead, fol, mkt], how="diagonal_relaxed")
p = df.select("code", "trade_date", "close", "ac", "ao", "lim_up_open").sort("code", "trade_date")
p = p.with_columns(c("ao").shift(-1).over("code").alias("o1"), c("ac").shift(-1).over("code").alias("c1"), c("ac").shift(-3).over("code").alias("c3"),
                   c("lim_up_open").shift(-1).over("code").alias("lu1"))
x = g.join(p, on=["code", "trade_date"], how="inner").drop_nulls(["o1", "c1", "c3"])
x = x.with_columns(
    (c("o1") / c("ac") - 1).alias("gap"), (c("c1") / c("ac") - 1).alias("d1"), (c("c3") / c("ac") - 1).alias("d3"),
    (c("c1") / c("o1") - 1).alias("oc"))
sec = lambda s: c(s).str.slice(0, 2).cast(pl.Int32, strict=False) * 3600 + c(s).str.slice(3, 2).cast(pl.Int32, strict=False) * 60
x = x.with_columns(pl.when(sec("lu_time") < 9 * 3600 + 35 * 60).then(pl.lit("a 09:30-09:35秒板"))
                   .when(sec("lu_time") < 10 * 3600 + 30 * 60).then(pl.lit("b 09:35-10:30"))
                   .when(sec("lu_time") < 14 * 3600).then(pl.lit("c 10:30-14:00")).otherwise(pl.lit("d 14:00后")).alias("bk"))
for lab, (a, b) in {"N3": ("2024-01-01", "2026-10-16"), "R12": ("2025-10-01", "2026-10-16"), "全期": ("2019-01-01", "2026-10-16")}.items():
    xx = x.filter(c("trade_date").is_between(pl.lit(a), pl.lit(b)))
    print("==", lab)
    r = xx.group_by("grp", "bk").agg(pl.len().alias("n"), (c("gap").mean() * 100 - 0.3).round(2).alias("买L收盘→L+1开盘(扣0.3)"),
        (c("d1").mean() * 100 - 0.3).round(2).alias("→L+1收盘"), (c("d3").mean() * 100 - 0.3).round(2).alias("→L+3收盘"),
        c("lu1").mean().round(2).alias("L+1开盘涨停占比")).sort("grp", "bk")
    print(r)
    r2 = xx.group_by("grp").agg(pl.len().alias("n"), (c("gap").mean() * 100 - 0.3).round(2).alias("gap"), (c("d1").mean() * 100 - 0.3).round(2).alias("d1"),
                               (c("d3").mean() * 100 - 0.3).round(2).alias("d3"), (c("oc").mean() * 100).round(2).alias("L+1开→收(不扣)"), c("lu1").mean().round(2).alias("lu1"))
    print(r2)
