"""[中线] §92 与小盘不相关的第二中线策略 — 预注册（不调参），周度信号（每周最后交易日收盘），次日开盘买，realistic 引擎，K=10，max_new=10，持 20 日。
  ztg_pb_h20 : 涨停基因回调。池 liq；近 20 日 >=2 个涨停收盘；收盘在 m60 之上；距 20 日最高收盘回撤 -20%~-5%；按回撤浅优先。
  ctrl_ztg   : 对照，同一涨停基因池（liq & 近20日>=2涨停）内 hash 随机 10 只。
  hi52_big_h20 : 52 周新高邻近（George-Hwang）。池 liq 且总市值前 800；按 ac/250日最高 降序取前 10。
  ctrl_big   : 对照，同池（liq & 市值前800）hash 随机 10 只。
追加（§92b，hi52/ztg 结果出来后预注册，同一标准）：A 股短期反转在大市值池。
  rev5_big_h10 : 池同 B（liq & 市值前800），按近 5 日收益最低取 10 只，持 10 日；ctrl_big_h10 为同池随机持 10 日。
  rev20_big_h20: 同池按近 20 日收益最低取 10 只，持 20 日；对照 ctrl_big。
采纳标准（预先写定）：8 阶段 >=6 正，R12 与 W00 每笔非负，且每笔优于同池随机对照 >=5/8。"""
import sys, json
import polars as pl
from lt_research import build, fixed, PH
from realistic import run_real, metrics_real
c = pl.col
df = build()
d = c("trade_date").str.to_date()
df = df.with_columns(d.dt.strftime("%G-%V").alias("wk"))
lw = df.group_by("wk").agg(c("trade_date").max().alias("lw"))
df = df.join(lw, on="wk").with_columns((c("trade_date") == c("lw")).alias("wkd"))
df = df.sort("code", "trade_date").with_columns(
    (c("close") >= pl.coalesce(c("up_limit"), (c("close").shift(1).over("code") * pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(1.2).otherwise(1.1)).round(2)) - 0.005).fill_null(False).cast(pl.Int32).alias("zt"),
    c("total_mv").cast(pl.Float64, strict=False).alias("mv"))
df = df.with_columns(c("zt").rolling_sum(20).over("code").alias("zt20"),
                     c("ac").rolling_max(20).over("code").alias("hi20"), c("ac").rolling_max(250).over("code").alias("hi250"))
df = df.with_columns((c("ac") / c("hi20") - 1).alias("dd20"), (c("ac") / c("hi250")).alias("p52"),
                     pl.concat_str([c("code"), c("trade_date")]).hash(7).cast(pl.Float64).alias("hr"))
base = c("wkd") & c("liq")
gene = (base & (c("zt20") >= 2)).fill_null(False)
mvr = pl.when(base & c("mv").is_not_null()).then(-c("mv")).rank("ordinal").over("trade_date")
df = df.with_columns(mvr.alias("mvr"))
big = (base & (c("mvr") <= 800)).fill_null(False)
df = df.with_columns(gene.alias("G"), big.alias("B"),
    (gene & (c("ac") > c("m60")) & c("dd20").is_between(-0.20, -0.05)).fill_null(False).alias("S_ztg"),
    (-(c("ac") / c("ac").shift(5).over("code") - 1)).fill_null(-9).alias("sc_rev5"), (-c("r20")).fill_null(-9).alias("sc_rev20"),
    c("dd20").alias("sc_ztg"), c("p52").fill_null(-1).alias("sc_hi"), c("hr").alias("sc_r"))
print(df.filter(c("wkd")).group_by(c("trade_date").str.slice(0,4)).agg(c("liq").sum(), c("G").sum(), c("S_ztg").sum(), c("B").sum()).sort("trade_date"), flush=True)
C = {"ztg_pb_h20": ("S_ztg", "sc_ztg"), "ctrl_ztg": ("G", "sc_r"), "hi52_big_h20": ("B", "sc_hi"), "ctrl_big": ("B", "sc_r"),
     "rev5_big_h10": ("B", "sc_rev5", 10), "ctrl_big_h10": ("B", "sc_r", 10), "rev20_big_h20": ("B", "sc_rev20")}
res = {}
for n in (sys.argv[1:] or C):
    sig, sc, *hh = C[n]; H = hh[0] if hh else 20; print(f"## {n}", flush=True)
    for ph, (s, e) in PH.items():
        t, cur = run_real(df, sig, sc, fixed(H), s, e, k_slots=10, max_new=10, max_hold=H)
        mm = metrics_real(t, cur); res.setdefault(n, {})[ph] = {a: float(v) for a, v in mm.items()}
        print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in mm.items()), flush=True)
    json.dump(res, open("/tmp/lt13.json", "w"), indent=1)
print("all done", flush=True)
