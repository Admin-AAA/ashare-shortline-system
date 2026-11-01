"""[中线] §297「低 PEG 成长股」PEG_W_h20（彼得·林奇 PEG = 市盈率 / 盈利增速；邢不行《公司估值越低越好吗？买市盈率最低…》
的延伸：只看低估值会踩价值陷阱（§293 已证），用盈利增速给估值"打折"）— 预注册（docs/strategy-research.md §297，结果出来前写定，不调参）。
  npy = 最近一份已披露正式财报（fina netprofit_yoy，归母净利同比 %，同一报告期取最早公告），PIT：公告日后第一个交易日 sd，
        只在 trade_date > sd 时可用（严格晚于，避免盘后公告前视），且 trade_date − sd ≤ 200 天。
  信号日：周五（weekday==5）。U = liq & total_mv 非空 & 剔壳（liq 内 total_mv 百分位 > 0.30）。
  候选 = U & 0 < pe_ttm ≤ 60 & 20 ≤ npy ≤ 300；PEG = pe_ttm / npy 升序取前 10；次日开盘买、持 20 日。
  K=20、max_new=10（仅回测，实盘不设仓位上限）；cohort_bench 同市值五分位对照（参考项）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research112.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics, ear_events
c = pl.col
NAME = "PEG_W_h20"


def prep():
    df = build().sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date")
    ev = ear_events(td).select("code", "sd", "npy").drop_nulls("npy").sort("sd")
    df = df.sort("trade_date").join_asof(ev.with_columns(c("sd").alias("trade_date")), on="trade_date", by="code",
                                         strategy="backward", allow_exact_matches=False)
    age = (c("trade_date").str.to_date() - c("sd").str.to_date()).dt.total_days()
    df = df.with_columns(pl.when(age <= 200).then(c("npy")).otherwise(None).alias("npy")).sort("code", "trade_date")
    mv = c("total_mv").cast(pl.Float64, strict=False)
    u0 = (c("liq") & mv.is_not_null()).fill_null(False)
    df = df.with_columns(pl.when(u0).then(mv).rank("average").over("trade_date").alias("_rmv"),
                         pl.when(u0).then(1).sum().over("trade_date").alias("_nu0"))
    cand = (u0 & (c("_rmv") / c("_nu0") > 0.30) & c("pe_ttm").is_between(0, 60, closed="right")
            & c("npy").is_between(20, 300)).fill_null(False)
    df = df.with_columns(cand.alias("cand"), (c("pe_ttm") / c("npy")).alias("peg"))
    df = df.with_columns(pl.when(c("cand")).then(c("peg")).rank("ordinal").over("trade_date").alias("_rk"))
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    return df.with_columns(
        (wk & c("cand") & (c("_rk") <= 10)).fill_null(False).alias("PEG_W"),
        (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_peg"),
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
        w = df.filter(c("PEG_W") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {}
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "peg_med": round(float(w["peg"].median()), 3),
                    "pe_med": round(float(w["pe_ttm"].median()), 1), "npy_med": round(float(w["npy"].median()), 1),
                    "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64).median()) / 1e4, 1), "r20_med": round(float(w["r20"].median()), 3)}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "PEG_W", "sc_peg", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
