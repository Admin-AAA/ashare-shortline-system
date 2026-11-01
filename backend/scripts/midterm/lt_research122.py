"""[中线] §319「标准化意外盈利」SUE_h20（Bernard–Thomas / Foster 1977 经典 PEAD：单季扣非净利相对去年同季的变化，
除以该公司过去 8 个季度这一变化的波动 = "这次业绩比它自己平时的波动大多少倍"）— 预注册（docs/strategy-research.md §319，结果出来前写定，不调参）。
数据：data/midterm/fina/<code>.parquet（tushare fina_indicator，profit_dedt 为年初至今累计扣非净利），同一 (code, end_date) 只取最早一次公告（PIT）。
  qi = 年×4 + 季；单季扣非 q = Q1 本身，Q2–Q4 = 本期累计 − 同年上一季累计（上一季缺失则 q 为空）。
  UE = q(qi) − q(qi−4)；sigma = 该公司此前 8 期 UE 的标准差（按报告期排序的前 8 行，至少 6 个非空，sigma>0）；SUE = UE / sigma。
  PIT：ann_date → ≥ann_date 的第一个交易日 sd，sd 收盘出信号，次日开盘买（同 §93 / 现役业绩加速口径）。同一 sd 多期只取最新 end_date。
  SUE_h20 = liq（amt20>5000万 & 上市>250日 & 非ST & 非北交所）& SUE ≥ 2 & q > 0（本季单季扣非为正）；
  排序分 = min(SUE, 20) 降序；持 20 日；K=20、max_new=5（同现役业绩加速回测口径；仅回测，实盘不设仓位上限）。
  参照（同进程，非候选）：EA_ref = 现役「财报业绩加速」§93 G 条件（lt_research14.fina_sig），用来算重叠与"是否明显不同"。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research122.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
import lt_data as L
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research12 import _pit
from lt_research14 import fina_sig
c = pl.col
NAME = "SUE_h20"


def sue_events(td):
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            sch = pl.read_parquet_schema(f)
            if not {"ts_code", "ann_date", "end_date", "profit_dedt"} <= set(sch): continue
            parts.append(pl.read_parquet(f, columns=["ts_code", "ann_date", "end_date", "profit_dedt"])
                         .with_columns(c("ts_code").cast(pl.Utf8), c("ann_date").cast(pl.Utf8), c("end_date").cast(pl.Utf8),
                                       c("profit_dedt").cast(pl.Float64, strict=False)))
        except Exception:
            continue
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = d.filter(c("end_date").str.slice(4, 4).is_in(["0331", "0630", "0930", "1231"]))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first")
    d = d.with_columns((c("end_date").str.slice(0, 4).cast(pl.Int32) * 4 + c("end_date").str.slice(4, 2).cast(pl.Int32) // 3).alias("qi"),
                       (c("end_date").str.slice(4, 2).cast(pl.Int32) // 3).alias("qn"))
    prv = d.select("ts_code", (c("qi") + 1).alias("qi"), c("profit_dedt").alias("pd_prev"))
    d = d.join(prv, on=["ts_code", "qi"], how="left")
    d = d.with_columns(pl.when(c("qn") == 1).then(c("profit_dedt")).otherwise(c("profit_dedt") - c("pd_prev")).alias("q"))
    ly = d.select("ts_code", (c("qi") + 4).alias("qi"), c("q").alias("q_ly"))
    d = d.join(ly, on=["ts_code", "qi"], how="left").with_columns((c("q") - c("q_ly")).alias("ue")).sort("ts_code", "qi")
    d = d.with_columns(c("ue").shift(1).rolling_std(8, min_samples=6).over("ts_code").alias("sig"))
    d = d.with_columns(pl.when(c("sig") > 0).then(c("ue") / c("sig")).alias("sue"))
    d = _pit(d, td)
    return (d.sort("end_date").group_by("code", "sd").agg(c("sue").last(), c("q").last().alias("q_sq"), c("end_date").last().alias("sue_ed"))
             .rename({"sd": "trade_date"}))


def prep():
    df = build()
    td = df.select("trade_date").unique().sort("trade_date")
    df = df.join(sue_events(td), on=["code", "trade_date"], how="left").join(fina_sig(td), on=["code", "trade_date"], how="left")
    S = c("liq") & (c("sue") >= 2) & (c("q_sq") > 0)
    G = c("liq") & (c("pdd") > 0) & (c("dtp") >= 50) & (c("orv") >= 20) & (c("dtp") > c("prev_dtp"))
    return df.with_columns(S.fill_null(False).alias("SUE"), c("sue").clip(-1e9, 20).fill_null(-1e9).alias("sc_sue"),
                           G.fill_null(False).alias("G"), c("dtp").clip(-1e9, 300).fill_null(0).alias("sc_g")).sort("code", "trade_date")


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
        w = df.filter(c("SUE") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "sig_also_EA": round(float(w["G"].mean()), 3),
                         "sue_med": round(float(w["sue"].median()), 2), "r20_med": round(float(w["r20"].median()), 3),
                         "mv_med_yi": round(float(w["total_mv"].median()) / 1e4, 1) if "total_mv" in w.columns else None,
                         "n_codes": w["code"].n_unique()})
        del w
        tt = {}
        for n, sig, sc in ((NAME, "SUE", "sc_sue"), ("EA_ref", "G", "sc_g")):
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
            a = tt[NAME].select("code", "entry"); b = tt["EA_ref"].select("code", "entry").unique()
            info["trade_ovl_EA"] = round(a.join(b, on=["code", "entry"], how="inner").height / a.height, 3)
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
