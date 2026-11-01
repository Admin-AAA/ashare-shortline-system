"""[中线] §289「量价相关性最低」CPV_W_h20（邢不行精华帖《量价因子——技术分析的底层逻辑究竟硬不硬？》；
东吴金工 CPV 价量相关性因子：近 20 日收盘价与成交额相关性越低，未来越好）— 预注册（docs/strategy-research.md §289，结果出来前写定，不调参）。
  cpv20 = 近 20 个交易日（含 T）复权收盘 ac 与成交额 amount 的 Pearson 相关系数（窗口不足为空）。
  信号日：周五（weekday==5）。U = liq & total_mv 非空 & 剔壳（liq 内 total_mv 百分位 > 0.30）。
  候选 = U & cpv20 非空且有限；cpv20 升序（同值按 amt20 降序）取前 10；次日开盘买、持 20 日。
  K=20、max_new=10（仅回测，实盘不设仓位上限）；cohort_bench 同市值五分位对照（参考项）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research108.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
NAME = "CPV_W_h20"


def prep():
    df = build().sort("code", "trade_date")
    df = df.with_columns(pl.rolling_corr(c("ac"), c("amount").cast(pl.Float64), window_size=20).over("code").alias("cpv20"))
    mv = c("total_mv").cast(pl.Float64, strict=False)
    u0 = (c("liq") & mv.is_not_null()).fill_null(False)
    df = df.with_columns(pl.when(u0).then(mv).rank("average").over("trade_date").alias("_rmv"),
                         pl.when(u0).then(1).sum().over("trade_date").alias("_nu0"))
    cand = (u0 & (c("_rmv") / c("_nu0") > 0.30) & c("cpv20").is_not_null() & c("cpv20").is_finite()).fill_null(False)
    key = c("cpv20") - c("amt20").fill_null(0) * 1e-15
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(key).rank("ordinal").over("trade_date").alias("_rk"))
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    return df.with_columns(
        (wk & c("cand") & (c("_rk") <= 10)).fill_null(False).alias("CPV_W"),
        (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_cpv"),
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
        w = df.filter(c("CPV_W") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "cpv_med": round(float(w["cpv20"].median()), 3),
                    "r20_med": round(float(w["r20"].median()), 3), "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64).median()) / 1e4, 1)}
            res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "CPV_W", "sc_cpv", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
