"""[中线] §291「彩票股回避：近 20 日最大单日涨幅最低」MAX_W_h20（Bali–Cakici–Whitelaw 2011 JFE MAX 效应；
邢不行第 106 期《抓了几千万条热门股数据…》"大热必死、热门的东西不要碰"）— 预注册（docs/strategy-research.md §291，结果出来前写定，不调参）。
  max20 = 近 20 个交易日（含 T）日收益 pct_chg 的最大值（窗口不足为空）。
  信号日：周五（weekday==5）。U = liq & total_mv 非空 & 剔壳（liq 内 total_mv 百分位 > 0.30）。
  候选 = U & max20 非空；max20 升序（同值按 amt20 降序）取前 10；次日开盘买、持 20 日。
  K=20、max_new=10（仅回测，实盘不设仓位上限）；cohort_bench 同市值五分位对照（参考项）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research109.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
NAME = "MAX_W_h20"


def prep():
    df = build().sort("code", "trade_date")
    df = df.with_columns(c("pct_chg").cast(pl.Float64).rolling_max(20).over("code").alias("max20"))
    mv = c("total_mv").cast(pl.Float64, strict=False)
    u0 = (c("liq") & mv.is_not_null()).fill_null(False)
    df = df.with_columns(pl.when(u0).then(mv).rank("average").over("trade_date").alias("_rmv"),
                         pl.when(u0).then(1).sum().over("trade_date").alias("_nu0"))
    cand = (u0 & (c("_rmv") / c("_nu0") > 0.30) & c("max20").is_not_null() & c("max20").is_finite()).fill_null(False)
    key = c("max20") - c("amt20").fill_null(0) * 1e-15
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(key).rank("ordinal").over("trade_date").alias("_rk"))
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    return df.with_columns(
        (wk & c("cand") & (c("_rk") <= 10)).fill_null(False).alias("MAX_W"),
        (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_max"),
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
        w = df.filter(c("MAX_W") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "max20_med": round(float(w["max20"].median()), 3),
                    "r20_med": round(float(w["r20"].median()), 3), "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64).median()) / 1e4, 1)}
            res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "MAX_W", "sc_max", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
