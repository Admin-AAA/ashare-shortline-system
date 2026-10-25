"""[中线] §303「神奇公式（格林布拉特）」MGF_W_h20（便宜 + 赚钱能力强：盈利收益率 1/PE 高、且 ROE 高的公司）
— 预注册（docs/strategy-research.md §303，结果出来前写定，不调参）。
  ROE_ttm 近似 = pb / pe_ttm（E/B = (P/B)/(P/E)，只用每日估值数据，天然无未来函数）；EY = 1 / pe_ttm。信号日：周五（weekday==5）。
  U = liq & total_mv 非空 & 剔壳（liq 内 total_mv 百分位 > 0.30）。候选 = U & 0 < pe_ttm ≤ 50 & pb > 0。
  score = 候选内 EY 排名 + ROE 排名（越大越好）；score 降序取前 10；次日开盘买、持 20 日。
  K=20、max_new=10（仅回测，实盘不设仓位上限）；cohort_bench 同市值五分位对照（参考项）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research115.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
NAME = "MGF_W_h20"


def prep():
    df = build().sort("code", "trade_date")
    mv = c("total_mv").cast(pl.Float64, strict=False)
    u0 = (c("liq") & mv.is_not_null()).fill_null(False)
    df = df.with_columns(pl.when(u0).then(mv).rank("average").over("trade_date").alias("_rmv"),
                         pl.when(u0).then(1).sum().over("trade_date").alias("_nu0"))
    cand = (u0 & (c("_rmv") / c("_nu0") > 0.30) & (c("pe_ttm") > 0) & (c("pe_ttm") <= 50) & (c("pb") > 0)).fill_null(False)
    df = df.with_columns(cand.alias("cand"), (1 / c("pe_ttm")).alias("ey"), (c("pb") / c("pe_ttm")).alias("roe_x"))
    df = df.with_columns((pl.when(c("cand")).then(c("ey")).rank("average").over("trade_date")
                          + pl.when(c("cand")).then(c("roe_x")).rank("average").over("trade_date")).alias("mgf"))
    df = df.with_columns(pl.when(c("cand")).then(-c("mgf")).rank("ordinal").over("trade_date").alias("_rk"))
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    return df.with_columns(
        (wk & c("cand") & (c("_rk") <= 10)).fill_null(False).alias("MGF_W"),
        (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_mgf"),
    ).drop("_rmv", "_nu0", "_rk").sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        w = df.filter(c("MGF_W") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {}
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "pe_med": round(float(w["pe_ttm"].median()), 2),
                    "roe_med": round(float(w["roe_x"].median()), 3), "pb_med": round(float(w["pb"].median()), 2),
                    "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64).median()) / 1e4, 1), "r20_med": round(float(w["r20"].median()), 3)}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "MGF_W", "sc_mgf", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
