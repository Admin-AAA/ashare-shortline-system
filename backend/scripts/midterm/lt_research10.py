"""[长线/中线] 行业动量轮动 — 预注册 3 版（不调参）+ 2 个同池随机对照（非候选）。
动机：§81/§82 小盘与大盘质量长持都无稳定超额；行业动量是与规模 beta 不同的方向。
行业：data/industry_cache.json（tushare industry，约 110 类，当前分类静态映射；行业归属变动少，前视偏差有限，已知局限）。
池 U：成交额20日均>3000万、上市>250日、非ST、非北交所，折算日市值非空；行业须有 >=8 只 U 成员才参与排名。
行业动量：U 成员个股收益（截尾 [-50%,+150%]）的截面中位数。
信号：每月首个交易日收盘，取动量前 3 行业，每行业取折算市值最大的 3 只（龙头），共 9 只；次日开盘买；
realistic 引擎（开盘涨停不买、跌停顺延、逐日盯市、0.3%成本），K=30，max_new=10。
  im60_h20      : 行业 r60 中位数前 3，持 20 日（月度轮动）
  im60_h60      : 同上，持 60 日
  im120x20_h60  : 行业 r120 中位数 − r20 中位数（中期动量、剔除最近一月反转）前 3，持 60 日
  ctrl_h20/h60  : 对照，按 hash(行业,月) 伪随机 3 个合格行业，同样各取 3 只龙头（排除龙头/大盘 beta）
追加（§83b，看到动量版系统性跑输对照后预注册的反转假设，同一标准；因由本轮结果启发，需额外谨慎）：
  ir60rev_h20   : 行业 r60 中位数最低 3 个，持 20 日
  ir20rev_h20   : 行业 r20 中位数最低 3 个，持 20 日
采纳标准（预先写定）：8 阶段 ≥6 正，R12 与 W00 每笔不为负，且优于同持有期对照至少 5/8 阶段。"""
import sys, json
import polars as pl
from lt_research import build, fixed, PH
from realistic import run_real, metrics_real
c = pl.col

ind = json.load(open(__file__.rsplit("/scripts/", 1)[0] + "/data/industry_cache.json"))
indf = pl.DataFrame({"code": list(ind), "ind": [str(v) if v else None for v in ind.values()]}).filter(c("ind").is_not_null())
df = build()
px = df.select("code", c("trade_date").alias("bd"), c("ac").alias("ac_bd"))
df = df.join(px, on=["code", "bd"], how="left").with_columns(
    (c("total_mv").cast(pl.Float64, strict=False) * c("ac") / c("ac_bd")).alias("mvd"))
df = df.sort("code", "trade_date").with_columns((c("ac") / c("ac").shift(120).over("code") - 1).alias("r120"))
df = df.join(indf, on="code", how="left")
uni = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
       & c("mvd").is_not_null() & c("ind").is_not_null()).fill_null(False)
df = df.with_columns(uni.alias("U"))
cl = lambda x: c(x).clip(-0.5, 1.5)
g = (df.filter(c("mon") & c("U")).group_by("trade_date", "ind")
     .agg(pl.len().alias("n"), cl("r60").median().alias("m60i"), cl("r120").median().alias("m120i"), cl("r20").median().alias("m20i"))
     .filter(c("n") >= 8)
     .with_columns((c("m120i") - c("m20i")).alias("mx"), pl.concat_str([c("ind"), c("trade_date").str.slice(0, 7)]).hash(11).cast(pl.Float64).alias("hr")))
g = g.with_columns(
    pl.when(c("m60i").is_not_null()).then(-c("m60i")).rank("ordinal").over("trade_date").alias("ir60"),
    pl.when(c("mx").is_not_null()).then(-c("mx")).rank("ordinal").over("trade_date").alias("irx"),
    c("hr").rank("ordinal").over("trade_date").alias("irr"),
    pl.when(c("m60i").is_not_null()).then(c("m60i")).rank("ordinal").over("trade_date").alias("ir60r"),
    pl.when(c("m20i").is_not_null()).then(c("m20i")).rank("ordinal").over("trade_date").alias("ir20r"))
df = df.join(g.select("trade_date", "ind", "m60i", "mx", "hr", "ir60", "irx", "irr", "ir60r", "ir20r", "m20i"), on=["trade_date", "ind"], how="left")
df = df.with_columns(pl.when(c("mon") & c("U") & c("ir60").is_not_null()).then(-c("mvd")).rank("ordinal").over("trade_date", "ind").alias("lr"))
lead = (c("lr") <= 3).fill_null(False)
df = df.with_columns(
    (lead & (c("ir60") <= 3)).fill_null(False).alias("S_im60"),
    (lead & (c("irx") <= 3)).fill_null(False).alias("S_imx"),
    (lead & (c("irr") <= 3)).fill_null(False).alias("S_rand"),
    (lead & (c("ir60r") <= 3)).fill_null(False).alias("S_r60r"), (lead & (c("ir20r") <= 3)).fill_null(False).alias("S_r20r"),
    (-c("m60i")).alias("sc_r60r"), (-c("m20i")).alias("sc_r20r"),
    c("m60i").alias("sc_im60"), c("mx").alias("sc_imx"), (-c("hr")).alias("sc_rand")).sort("code", "trade_date")
chk = df.filter(c("mon")).group_by("trade_date").agg(c("S_im60").sum(), c("S_imx").sum(), c("S_rand").sum(), c("ind").filter(c("S_im60")).unique().alias("i"))
print("built", df.height, "months", chk.height, "picks/month median", chk.select(c("S_im60").median(), c("S_imx").median(), c("S_rand").median()).row(0), flush=True)
print(chk.sort("trade_date").tail(6).select("trade_date", "i"), flush=True)
C = {"im60_h20": ("S_im60", "sc_im60", 20), "im60_h60": ("S_im60", "sc_im60", 60), "im120x20_h60": ("S_imx", "sc_imx", 60),
     "ir60rev_h20": ("S_r60r", "sc_r60r", 20), "ir20rev_h20": ("S_r20r", "sc_r20r", 20),
     "ctrl_h20": ("S_rand", "sc_rand", 20), "ctrl_h60": ("S_rand", "sc_rand", 60)}
res = {}
for n in (sys.argv[1:] or C):
    sig, sc, h = C[n]; print(f"## {n}", flush=True)
    for ph, (s, e) in PH.items():
        t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=30, max_new=10, max_hold=h)
        mm = metrics_real(t, cur); res.setdefault(n, {})[ph] = {a: float(v) for a, v in mm.items()}
        print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in mm.items()), flush=True)
    pos = sum(1 for v in res[n].values() if v.get("avg", -1) > 0); print("  pos", pos, "/8", flush=True)
    json.dump(res, open("/tmp/lt10_" + "_".join(sys.argv[1:] or ["all"]) + ".json", "w"), indent=1)
print("all done", flush=True)
