"""[中线] §284「慢牛：温水煮青蛙动量」FIP_W_h20（Da–Gurun–Warachka 2014 information discreteness；邢不行「最新研报解读」栏目所列
国泰君安数量化专题之一百零四《抱团行为和慢牛股研究》的"慢牛"思路）— 预注册（docs/strategy-research.md §284，结果出来前写定，不调参）。
口径同 lt_research104/105：realistic 引擎、K=20、max_new=10（仅回测，实盘不设仓位上限）、cohort_bench 同日同市值五分位对照（参考项）。
  信号日：周五（weekday==5，节假日周五则该周无信号）。
  U = liq（amt20>5000万、age>250、非ST、非北交所）& total_mv 非空 & 剔壳（U0 内 total_mv 百分位 > 0.30）。
  PRET = ac_T / ac_{T-120} − 1；ID = (近120日 pct_chg<0 的天数 − pct_chg>0 的天数) / 120（PRET>0 时 sign=+1，ID 越低=越多"小步上涨"日）。
  候选 = U 内 PRET 百分位 ≥ 0.80 且 PRET > 0；候选内 ID 升序（同值 PRET 降序）取前 10；次日开盘买，持 20 日。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research106.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
N = 120


def prep():
    df = build().sort("code", "trade_date")
    pc = c("pct_chg").fill_null(0)
    df = df.with_columns(
        (c("ac") / c("ac").shift(N).over("code") - 1).alias("pret"),
        (((pc < 0).cast(pl.Float64).rolling_sum(N) - (pc > 0).cast(pl.Float64).rolling_sum(N)) / N).over("code").alias("idsc"))
    mv = c("total_mv").cast(pl.Float64, strict=False)
    u0 = (c("liq") & mv.is_not_null()).fill_null(False)
    df = df.with_columns(pl.when(u0).then(mv).rank("average").over("trade_date").alias("_rmv"),
                         pl.when(u0).then(1).sum().over("trade_date").alias("_nu0"))
    U = (u0 & (c("_rmv") / c("_nu0") > 0.30) & c("pret").is_not_null() & c("idsc").is_not_null()).fill_null(False)
    df = df.with_columns(U.alias("U"))
    df = df.with_columns(pl.when(c("U")).then(c("pret")).rank("average").over("trade_date").alias("_rp"),
                         pl.when(c("U")).then(1).sum().over("trade_date").alias("_nu"))
    cand = (c("U") & (c("_rp") / c("_nu") >= 0.80) & (c("pret") > 0)).fill_null(False)
    key = c("idsc") - c("pret") * 1e-6
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(key).rank("ordinal").over("trade_date").alias("_rk"))
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    return df.with_columns(
        (wk & c("cand") & (c("_rk") <= 10)).fill_null(False).alias("FIP_W"),
        (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_fip"),
    ).drop("_rmv", "_nu0", "_rp", "_nu", "_rk").sort("code", "trade_date")


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
        w = df.filter(c("FIP_W") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "pret_med": round(float(w["pret"].median()), 3),
                    "id_med": round(float(w["idsc"].median()), 3), "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64).median()) / 1e4, 1)}
            res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "FIP_W", "sc_fip", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_FIP_W_h20_{ph.split()[0]}.csv"))
        res.setdefault("FIP_W_h20", {})[ph] = m
        print("FIP_W_h20", ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
