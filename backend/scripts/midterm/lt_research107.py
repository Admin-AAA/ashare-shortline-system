"""[中线] §287「低位单峰密集」CYQC_W_h20（邢不行第 102 期筹码分布：民间口诀"单峰密集、后市看涨"）— 预注册
（docs/strategy-research.md §287，结果出来前写定，不调参）。筹码算法见 cyq_chips.py。
  信号日：周五（weekday==5）。U = liq & total_mv 非空 & 剔壳（liq 内 total_mv 百分位 > 0.30）。
  候选 = U & ac ≤ m250（低位：在 250 日均线下方）& WR ≥ 0.50（价格站在多数筹码成本之上）& CR70 非空；
  候选内 CR70 升序（越集中越优先；同值按 amt20 降序）取前 10；次日开盘买、持 20 日。
  K=20、max_new=10（仅回测，实盘不设仓位上限）；cohort_bench 同市值五分位对照（参考项）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research107.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
from cyq_chips import add_chips
c = pl.col
NAME = "CYQC_W_h20"


def prep():
    df = add_chips(build()).sort("code", "trade_date")
    mv = c("total_mv").cast(pl.Float64, strict=False)
    u0 = (c("liq") & mv.is_not_null()).fill_null(False)
    df = df.with_columns(pl.when(u0).then(mv).rank("average").over("trade_date").alias("_rmv"),
                         pl.when(u0).then(1).sum().over("trade_date").alias("_nu0"))
    cand = (u0 & (c("_rmv") / c("_nu0") > 0.30) & (c("ac") <= c("m250")) & (c("wr") >= 0.50) & c("cr70").is_not_null()).fill_null(False)
    key = c("cr70") - c("amt20").fill_null(0) * 1e-15
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(key).rank("ordinal").over("trade_date").alias("_rk"))
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    return df.with_columns(
        (wk & c("cand") & (c("_rk") <= 10)).fill_null(False).alias("CYQC_W"),
        (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_cyq"),
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
        w = df.filter(c("CYQC_W") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "cr70_med": round(float(w["cr70"].median()), 3),
                    "wr_med": round(float(w["wr"].median()), 3), "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64).median()) / 1e4, 1)}
            res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "CYQC_W", "sc_cyq", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
