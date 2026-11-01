"""[短线研究 §362]「业绩预告首次预喜后弱反应」FDU_h5。
预注册 docs/strategy-research.md §362，结果出来前写定，不调参。
  事件：lt_research20 FA（预增/扭亏/略增、p_change_min>0、net_profit_min>0，同报告期最早）；
  S=公告后首个交易日；S 日 pct∈[-1,+2] 且未涨停、liq；同股60自然日去重；按 fsc 降序前5；次日开盘买持5日。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/fdu_s362.py "<phase>" out.json"""
import sys, json, os
from datetime import timedelta
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research20 import prep as prep20
c = pl.col
NAME, HOLD, K, MAX_NEW, DEDUP_D, YOY_CAP = "FDU_h5", 5, 20, 5, 60, 300.0
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = prep20().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    df = df.with_columns(
        (c("close") >= ul - 0.005).fill_null(False).alias("luc"),
        c("pct_chg").alias("pct"),
        c("sc_a").clip(-1e9, YOY_CAP).alias("g"),
    )
    # FA 事件日；同股 60 自然日去重（按交易日近似：用日期差）
    ev = df.filter(c("FA")).select("code", "trade_date", "g").with_columns(
        c("trade_date").str.to_date().alias("d")
    ).sort("code", "d")
    ev = ev.with_columns(c("d").shift(1).over("code").alias("pd"))
    ev = ev.filter(c("pd").is_null() | ((c("d") - c("pd")).dt.total_days() > DEDUP_D))
    ev = ev.select("code", "trade_date", pl.lit(True).alias("ev_ok"))
    df = df.join(ev, on=["code", "trade_date"], how="left")
    muted = (c("pct") >= -1.0) & (c("pct") <= 2.0) & ~c("luc")
    cand = (c("ev_ok").fill_null(False) & c("liq") & muted).fill_null(False)
    df = df.with_columns(cand.alias("cand"))
    df = df.with_columns(
        pl.when(c("cand")).then(-c("g").clip(-1e9, YOY_CAP)).rank("ordinal").over("trade_date").alias("_rk")
    )
    return df.with_columns(
        (c("cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias("FDU"),
        pl.when(c("cand")).then(c("g").clip(-1e9, YOY_CAP)).otherwise(-1e9).alias("sc_fdu"),
    ).drop("_rk").sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        w = df.filter(c("FDU") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        a = df.filter(c("FA") & c("liq") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_fa_liq": a.height}
        if w.height:
            info.update({
                "sigdays": w["trade_date"].n_unique(),
                "g_med": round(float(w["g"].median()), 1),
                "pct_med": round(float(w["pct"].median()), 2),
                "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
            })
        res.setdefault("_info", {})[ph] = info
        print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "FDU", "sc_fdu", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t))
            m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
