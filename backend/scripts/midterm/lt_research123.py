"""[中线] §322「业绩加速后创一年新高」CSL_h20（欧奈尔 CAN SLIM 的 C+N+L：当季业绩大增(C)、股价创新高(N)、相对强势领涨(L)）
— 预注册（docs/strategy-research.md §322，结果出来前写定，不调参）。
  E = 现役「财报业绩加速」§93 G 条件（lt_research14.fina_sig：liq & 扣非>0 & 扣非同比≥50 & 营收同比≥20 & 扣非同比>上期），E 日 = 财报公告后首个交易日。
  信号日 d：最近一次 E 日在 d 之前 0~40 个交易日内（含 E 日当天）；d 收盘复权价 ≥ 前 250 日最高复权收盘（创一年新高）；
            且这是该次 E 之后第一次创新高；d 当日 liq；近 120 日涨幅在当日 liq 股中的百分位 ≥ 0.8（相对强势前 20%）。
  排序分 = 120 日涨幅百分位降序；d 收盘出信号，次日开盘买、持 20 日、0.3% 成本；K=20、max_new=5（同现役业绩加速回测口径；仅回测，实盘不设仓位上限）。
  参照（同进程，非候选）：EA_ref = 现役业绩加速（G，公告日次日买）。报重叠与"是否更赚"。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research123.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research14 import fina_sig
c = pl.col
NAME, WIN = "CSL_h20", 40


def prep():
    df = build()
    td = df.select("trade_date").unique().sort("trade_date")
    df = df.join(fina_sig(td), on=["code", "trade_date"], how="left").sort("code", "trade_date")
    G = (c("liq") & (c("pdd") > 0) & (c("dtp") >= 50) & (c("orv") >= 20) & (c("dtp") > c("prev_dtp"))).fill_null(False)
    o = "code"
    df = df.with_columns(G.alias("G"), c("dtp").clip(-1e9, 300).fill_null(0).alias("sc_g"),
                         pl.int_range(pl.len()).over(o).alias("ix"),
                         (c("ac") >= c("ac").shift(1).rolling_max(250)).over(o).fill_null(False).alias("nh"),
                         (c("ac") / c("ac").shift(120).over(o) - 1).alias("r120"))
    df = df.with_columns(pl.when(c("G")).then(c("ix")).otherwise(None).forward_fill().over(o).alias("eix"))
    df = df.with_columns(pl.when(c("eix").is_not_null()).then(c("nh").cast(pl.Int32).cum_sum().over([o, "eix"])).alias("nh_cum"),
                         (pl.when(c("liq")).then(c("r120")).rank() .over("trade_date")
                          / pl.when(c("liq") & c("r120").is_not_null()).then(1).sum().over("trade_date")).alias("rs"))
    S = c("liq") & c("eix").is_not_null() & ((c("ix") - c("eix")) <= WIN) & c("nh") & (c("nh_cum") == 1) & (c("rs") >= 0.8)
    return df.with_columns(S.fill_null(False).alias("CSL"), c("rs").fill_null(-1e9).alias("sc_csl")).sort("code", "trade_date")


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
        w = df.filter(c("CSL") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "n_codes": w["code"].n_unique(), "same_day_as_E": round(float(w["G"].mean()), 3),
                         "lag_med": float((w["ix"] - w["eix"]).median()), "r20_med": round(float(w["r20"].median()), 3),
                         "r120_med": round(float(w["r120"].median()), 3),
                         "mv_med_yi": round(float(w["total_mv"].median()) / 1e4, 1) if "total_mv" in w.columns else None})
        del w
        tt = {}
        for n, sig, sc in ((NAME, "CSL", "sc_csl"), ("EA_ref", "G", "sc_g")):
            t, cur = run_real(df, sig, sc, fixed(20), s, e, k_slots=20, max_new=5, max_hold=20)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                tt[n] = t
                if n == NAME:
                    t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        if NAME in tt and "EA_ref" in tt:
            a = tt[NAME].select("code", "entry"); b = tt["EA_ref"].select("code").unique()
            info["code_ovl_EA"] = round(a.join(b, on="code", how="inner").height / a.height, 3)
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
