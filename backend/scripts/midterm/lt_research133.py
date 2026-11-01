"""[中线] §347「放量不大动的高成交量溢价·周度版」HVP_W_h20（Gervais–Kaniel–Mingelgrin 2001：成交量异常放大、价格没怎么动的股票，
关注度上升，之后 1–3 个月跑赢）— 预注册 docs/strategy-research.md §347，结果出来前写定，不调参。
  vs5 = 近 5 日（含 T）平均成交额 / T−55..T−6 这 50 日平均成交额；r5 = ac_T/ac_{T−5} − 1。信号日：周五（weekday==5）。
  U = liq & total_mv 非空 & 剔壳（liq 内 total_mv 百分位 > 0.30）。候选 = U & vs5 ≥ 1.5 & |r5| ≤ 5% & T 收盘未封涨停；
  vs5 降序取前 10；次日开盘买、持 20 日。K=20、max_new=10（仅回测，实盘不设仓位上限）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research133.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
NAME = "HVP_W_h20"


def prep():
    df = build().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    df = df.with_columns((c("close") >= ul - 0.005).fill_null(False).alias("luc"),
                         (c("amount").rolling_mean(5).over("code") / c("amount").shift(5).rolling_mean(50).over("code")).alias("vs5"),
                         (c("ac") / c("ac").shift(5).over("code") - 1).alias("r5"))
    mv = c("total_mv").cast(pl.Float64, strict=False)
    u0 = (c("liq") & mv.is_not_null()).fill_null(False)
    df = df.with_columns(pl.when(u0).then(mv).rank("average").over("trade_date").alias("_rmv"),
                         pl.when(u0).then(1).sum().over("trade_date").alias("_nu0"))
    cand = (u0 & (c("_rmv") / c("_nu0") > 0.30) & (c("vs5") >= 1.5) & (c("r5").abs() <= 0.05) & ~c("luc")).fill_null(False)
    df = df.with_columns(cand.alias("cand"))
    df = df.with_columns(pl.when(c("cand")).then(-c("vs5")).rank("ordinal").over("trade_date").alias("_rk"))
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    return df.with_columns(
        (wk & c("cand") & (c("_rk") <= 10)).fill_null(False).alias("HVP_W"),
        (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_hvp"),
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
        w = df.filter(c("HVP_W") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {}
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "n_codes": w["code"].n_unique(),
                    "vs5_med": round(float(w["vs5"].median()), 2), "r5_med": round(float(w["r5"].median()), 3),
                    "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64).median()) / 1e4, 1),
                    "amt20_med_yi": round(float(w["amt20"].median()) / 1e8, 2)}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "HVP_W", "sc_hvp", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
