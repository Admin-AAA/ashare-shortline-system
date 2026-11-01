"""[中线] §293「估值处于自身一年区间底部」PBL_W_h20（邢不行精华帖《低估值还能涨多久？》——用个股自身历史市净率位置判断贵/便宜，
"高股息品种估值到了近10年高位、短期风险很大"的反面：买估值跌到自身近一年最低位置的赚钱公司）— 预注册（docs/strategy-research.md §293，结果出来前写定，不调参）。
  pbpos = (pb − pb 近 250 个交易日最小值) / (近 250 日最大值 − 最小值)（含 T，窗口不足 250 行为空；max ≤ min×1.0001 为空）。
  信号日：周五（weekday==5）。U = liq & total_mv 非空 & 剔壳（liq 内 total_mv 百分位 > 0.30）。
  候选 = U & pb > 0 & 0 < pe_ttm ≤ 50 & dv_ttm > 0 & pbpos 非空；pbpos 升序（同值按 dv_ttm 降序）取前 10；次日开盘买、持 20 日。
  K=20、max_new=10（仅回测，实盘不设仓位上限）；cohort_bench 同市值五分位对照（参考项）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research110.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
NAME = "PBL_W_h20"


def prep():
    df = build().sort("code", "trade_date")
    pb = c("pb").cast(pl.Float64)
    df = df.with_columns(pb.rolling_min(250).over("code").alias("_pbmin"), pb.rolling_max(250).over("code").alias("_pbmax"))
    df = df.with_columns(pl.when((c("_pbmax") > c("_pbmin") * 1.0001) & (c("_pbmin") > 0))
                         .then((pb - c("_pbmin")) / (c("_pbmax") - c("_pbmin"))).otherwise(None).alias("pbpos")).drop("_pbmin", "_pbmax")
    mv = c("total_mv").cast(pl.Float64, strict=False)
    u0 = (c("liq") & mv.is_not_null()).fill_null(False)
    df = df.with_columns(pl.when(u0).then(mv).rank("average").over("trade_date").alias("_rmv"),
                         pl.when(u0).then(1).sum().over("trade_date").alias("_nu0"))
    cand = (u0 & (c("_rmv") / c("_nu0") > 0.30) & (c("pb") > 0) & c("pe_ttm").is_between(0, 50, closed="right")
            & (c("dv_ttm") > 0) & c("pbpos").is_not_null() & c("pbpos").is_finite()).fill_null(False)
    key = c("pbpos") - c("dv_ttm").fill_null(0) * 1e-6
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(key).rank("ordinal").over("trade_date").alias("_rk"))
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    return df.with_columns(
        (wk & c("cand") & (c("_rk") <= 10)).fill_null(False).alias("PBL_W"),
        (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_pbl"),
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
        w = df.filter(c("PBL_W") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "pbpos_med": round(float(w["pbpos"].median()), 3), "pb_med": round(float(w["pb"].median()), 2), "dv_med": round(float(w["dv_ttm"].median()), 2),
                    "r20_med": round(float(w["r20"].median()), 3), "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64).median()) / 1e4, 1)}
            res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "PBL_W", "sc_pbl", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
