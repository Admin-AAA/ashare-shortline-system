"""[长线] 月度"质量+小市值"长持 — 预注册 3 版（不调参）+ 1 个对照（纯小市值，非候选）。
数据：2018+ 长历史面板（lt_research.build，含 2021 缺口修复）+ PIT 财务（fina_pit，公告日严格早于信号日）。
市值：周度 daily_basic 按价格折算日市值（同 lt_research5_diag2/7）。
信号：每月首个交易日收盘，按折算市值从小到大取前 10，次日开盘买；realistic 引擎（开盘涨停不买、跌停顺延、逐日盯市、0.3%成本）。
  q_small_h60       : 质量过滤 roe_ann>=6 & debt_to_assets<=60 & netprofit_yoy>0，持 60 日，K=30
  q_small_g60_h60   : 同上 + 小盘等权指数 > MA60 才开新仓，持 60 日，K=30
  q_small_div_h120  : 质量过滤 + dv_ttm>0（近 12 月有分红），持 120 日，K=60
  ctrl_small_h60    : 对照，无质量过滤（用于判断质量因子是否贡献超额）
采纳标准（预先写定）：8 阶段 ≥6 正，R12 与 W00 每笔不为负，且优于对照至少 5/8 阶段。"""
import sys, json
import polars as pl
import lt_data as L
from lt_research import build, fixed, PH
from realistic import run_real, metrics_real
c = pl.col

df = build()
px = df.select("code", c("trade_date").alias("bd"), c("ac").alias("ac_bd"))
df = df.join(px, on=["code", "bd"], how="left").with_columns(
    (c("total_mv").cast(pl.Float64, strict=False) * c("ac") / c("ac_bd")).alias("mvd"))
# small-cap eq-weight index (400 smallest, pe>0) and MA60
g = (c("mvd").is_not_null() & (c("pe_ttm") > 0) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
m = df.filter(g).with_columns(c("mvd").rank("ordinal").over("trade_date").alias("r4")).filter(c("r4") <= 400)
idx = (m.group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("s2")).sort("trade_date")
       .with_columns((1 + c("s2") / 100).cum_prod().alias("scid")).with_columns(c("scid").rolling_mean(60).alias("scid60")))
df = df.join(idx.select("trade_date", "scid", "scid60"), on="trade_date", how="left")
# PIT fundamentals: announcement strictly before signal date
f = L.fina_pit().select("code", "trade_date", "roe_ann",
                        *[c(x).cast(pl.Float64, strict=False) for x in ("debt_to_assets", "netprofit_yoy")]).sort("trade_date")
df = df.sort("trade_date").join_asof(f, on="trade_date", by="code", strategy="backward", allow_exact_matches=False)
uni = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
       & (c("pe_ttm") > 0) & c("mvd").is_not_null())
q = (c("roe_ann") >= 6) & (c("debt_to_assets") <= 60) & (c("netprofit_yoy") > 0)
rk = lambda cond: pl.when(cond.fill_null(False)).then(c("mvd")).rank("ordinal").over("trade_date")
df = df.with_columns(rk(uni & q).alias("rk_q"), rk(uni & q & (c("dv_ttm") > 0)).alias("rk_qd"), rk(uni).alias("rk_all"),
                     (-c("mvd")).alias("sc_small"))
gon = c("scid") > c("scid60")
df = df.with_columns((c("mon") & (c("rk_q") <= 10)).fill_null(False).alias("S_q"),
                     (c("mon") & gon & (c("rk_q") <= 10)).fill_null(False).alias("S_qg"),
                     (c("mon") & (c("rk_qd") <= 10)).fill_null(False).alias("S_qd"),
                     (c("mon") & (c("rk_all") <= 10)).fill_null(False).alias("S_all")).sort("code", "trade_date")
print("built", df.height, "q-eligible/month", df.filter(c("mon")).group_by("trade_date").agg(c("rk_q").max()).select(c("rk_q").median()).item(), flush=True)
C = {"q_small_h60": ("S_q", 60, 30), "q_small_g60_h60": ("S_qg", 60, 30),
     "q_small_div_h120": ("S_qd", 120, 60), "ctrl_small_h60": ("S_all", 60, 30)}
res = {}
for n in (sys.argv[1:] or C):
    sig, h, k = C[n]; print(f"## {n}", flush=True)
    for ph, (s, e) in PH.items():
        t, cur = run_real(df, sig, "sc_small", fixed(h), s, e, k_slots=k, max_new=10, max_hold=h)
        mm = metrics_real(t, cur); res.setdefault(n, {})[ph] = {a: float(v) for a, v in mm.items()}
        print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in mm.items()), flush=True)
    pos = sum(1 for v in res[n].values() if v.get("avg", -1) > 0); print("  pos", pos, "/8", flush=True)
json.dump(res, open("/tmp/lt8.json", "w"), indent=1)
print("all done", flush=True)
