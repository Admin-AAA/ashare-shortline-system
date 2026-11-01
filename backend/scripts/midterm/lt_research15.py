"""[中线/长线] §95 新权重下复评近期强、老阶段弱的已否决方向（预注册，结果出来前写定，不调参）。
数据按需加载：LT_START/LT_END 环境变量 -> lt_data 只读窗口（+420 天预热），每个阶段组单独进程。
候选（口径与原研究一致）：
  EA_h60 : §93 G 条件（扣非同比>=50、营收同比>=20、扣非>0、同比加速），持 60 日 -> /long
  EA_h20 : 同 G，持 20 日 -> /mid
  SMOM_w : 每周五，流动性合格中总市值最小 200 只里 20 日涨幅最高 10 只，持 20 日，K=40,max_new=10 -> /mid
新采纳标准（2026-10-18 22:14 用户权重）：
  1) R12 每笔均值 > 0 且总收益 > 0；
  2) N3 连续窗口 2024-01-01~2026-10-16 每笔均值 > 0、总收益 > 0、MDD > -35%；
  3) 2024 / 2025-01~09 每笔均值均 > -1%；
  4) 老阶段（2019/2020/2021/W0/W00）无灾难：每笔均值 > -5% 且 MDD > -40%。
  同市值对照仅诊断。"""
import sys, json, os
import polars as pl
from lt_research import build, fixed, PH
from realistic import run_real, metrics_real
from lt_research14 import fina_sig
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")

def prep():
    df = build()
    td = df.select("trade_date").unique().sort("trade_date")
    df = df.join(fina_sig(td), on=["code", "trade_date"], how="left")
    G = c("liq") & (c("pdd") > 0) & (c("dtp") >= 50) & (c("orv") >= 20) & (c("dtp") > c("prev_dtp"))
    df = df.with_columns(G.fill_null(False).alias("G"), c("dtp").clip(-1e9, 300).fill_null(0).alias("sc_g"))
    fri = c("trade_date").str.to_date().dt.weekday() == 5
    df = df.with_columns(pl.when(c("liq")).then(c("total_mv")).rank("ordinal").over("trade_date").alias("rk_mv"))
    df = df.with_columns(pl.when(c("rk_mv") <= 200).then(-c("r20")).rank("ordinal").over("trade_date").alias("rk_sm"))
    return df.with_columns((fri & (c("rk_sm") <= 10) & c("liq")).fill_null(False).alias("SM"), c("r20").fill_null(0).alias("sc_r"))

C = {"EA_h60": ("G", "sc_g", 60, 20, 5), "EA_h20": ("G", "sc_g", 20, 20, 5), "SMOM_w": ("SM", "sc_r", 20, 40, 10)}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for n, (sig, sc, h, k, mn) in C.items():
        for ph in phases:
            s, e = PH[ph]
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=k, max_new=mn, max_hold=h)
            m = metrics_real(t, cur)
            res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
            print(n, ph, m, flush=True)
        json.dump(res, open(out, "w"), indent=1)
    print("done", flush=True)
