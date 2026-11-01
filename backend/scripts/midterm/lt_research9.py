"""[长线] 非小盘方向：大盘质量月度长持 — 预注册 3 版（不调参）+ 1 个同池随机对照（非候选）。
动机：§81 质量+小市值主要是小盘 beta；本轮限定大盘池，对照为同池伪随机 10 只，排除规模 beta。
数据：2018+ 长历史面板（lt_research.build，含 2021 缺口修复）+ PIT 财务（fina_pit，公告日严格早于信号日）。
市值：周度 daily_basic 按价格折算日市值（同 lt_research8）。
大盘池 L：流动性/上市>250日/非ST/非北交所、pe_ttm>0，折算市值从大到小前 800（约中证800）。
质量 Q：roe_ann>=12 & debt_to_assets<=70 & netprofit_yoy>0。
信号：每月首个交易日收盘取前 10，次日开盘买；realistic 引擎（开盘涨停不买、跌停顺延、逐日盯市、0.3%成本），持 60 日，K=30。
  lq_roe_h60  : L&Q，按 roe_ann 从高到低
  lq_val_h60  : L&Q，按 EP+BP 截面秩和（便宜）从高到低
  lq_mom_h60  : L&Q&收盘>MA250，按 60 日涨幅从高到低
  ctrl_large_h60 : 对照，L 内按 hash(code,月) 伪随机 10 只
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
f = L.fina_pit().select("code", "trade_date", "roe_ann",
                        *[c(x).cast(pl.Float64, strict=False) for x in ("debt_to_assets", "netprofit_yoy")]).sort("trade_date")
df = df.sort("trade_date").join_asof(f, on="trade_date", by="code", strategy="backward", allow_exact_matches=False)
uni = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
       & (c("pe_ttm") > 0) & c("mvd").is_not_null())
df = df.with_columns(pl.when(uni.fill_null(False)).then(-c("mvd")).rank("ordinal").over("trade_date").alias("rk_big"))
Lg = (c("rk_big") <= 800).fill_null(False)
q = ((c("roe_ann") >= 12) & (c("debt_to_assets") <= 70) & (c("netprofit_yoy") > 0)).fill_null(False)
ep = pl.when(Lg & q).then(1 / c("pe_ttm")).rank().over("trade_date")
bp = pl.when(Lg & q & (c("pb") > 0)).then(1 / c("pb")).rank().over("trade_date")
df = df.with_columns((ep + bp).alias("sc_val"), c("roe_ann").alias("sc_roe"), c("r60").alias("sc_mom"),
                     pl.concat_str([c("code"), c("ym")]).hash(7).cast(pl.Float64).alias("sc_rand"))
top = lambda cond, sc: pl.when(cond).then(-c(sc)).rank("ordinal").over("trade_date")
mt = (c("ac") > c("m250")).fill_null(False)
df = df.with_columns(top(Lg & q, "sc_roe").alias("rk_roe"), top(Lg & q & c("sc_val").is_not_null(), "sc_val").alias("rk_val"),
                     top(Lg & q & mt & c("r60").is_not_null(), "sc_mom").alias("rk_mom"), top(Lg, "sc_rand").alias("rk_rand"))
df = df.with_columns(*[(c("mon") & (c(f"rk_{k}") <= 10)).fill_null(False).alias(f"S_{k}") for k in ("roe", "val", "mom", "rand")]
                     ).sort("code", "trade_date")
print("built", df.height, "LQ-eligible/month median",
      df.filter(c("mon")).group_by("trade_date").agg(c("rk_roe").max()).select(c("rk_roe").median()).item(), flush=True)
C = {"lq_roe_h60": ("S_roe", "sc_roe"), "lq_val_h60": ("S_val", "sc_val"),
     "lq_mom_h60": ("S_mom", "sc_mom"), "ctrl_large_h60": ("S_rand", "sc_rand")}
res = {}
for n in (sys.argv[1:] or C):
    sig, sc = C[n]; print(f"## {n}", flush=True)
    for ph, (s, e) in PH.items():
        t, cur = run_real(df, sig, sc, fixed(60), s, e, k_slots=30, max_new=10, max_hold=60)
        mm = metrics_real(t, cur); res.setdefault(n, {})[ph] = {a: float(v) for a, v in mm.items()}
        print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in mm.items()), flush=True)
    pos = sum(1 for v in res[n].values() if v.get("avg", -1) > 0); print("  pos", pos, "/8", flush=True)
json.dump(res, open("/tmp/lt9.json", "w"), indent=1)
print("all done", flush=True)
