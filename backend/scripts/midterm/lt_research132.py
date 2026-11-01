"""[中线] §345「坏消息不跌反涨：业绩预告预减/亏损，公告后首个交易日跑赢全市场 ≥3% → 次日开盘买、持 20 日」BNR_h20。
预注册 docs/strategy-research.md §345，结果出来前写定，不调参。
  数据：data/midterm/forecast/*.parquet（tushare forecast），同一 (code, end_date) 只取最早一次公告（lt_research20.forecast_first）。
  事件：type ∈ {预减, 略减, 首亏, 续亏, 增亏}。反应日 S = 第一个 > ann_date 的交易日（盘后公告不偷看）。
  条件：S 日 liq；S 日 pct_chg − 当日 liq 股票等权平均 pct_chg ≥ +3（个百分点）；S 收盘未封涨停。同股同 S 多期取超额。
  同日按超额降序前 5。引擎 realistic：S+1 开盘买、持 20 日、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research132.py "<phase>" out.json"""
import sys, json, os
from datetime import timedelta
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research20 import forecast_first
c = pl.col
NAME, HOLD, K, MAX_NEW = "BNR_h20", 20, 20, 5
BAD = ["预减", "略减", "首亏", "续亏", "增亏"]


def prep():
    df = build().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    df = df.with_columns((c("close") >= ul - 0.005).fill_null(False).alias("luc"))
    mkt = df.filter(c("liq")).group_by("trade_date").agg(c("pct_chg").mean().alias("mkt"))
    df = df.join(mkt, on="trade_date", how="left").with_columns((c("pct_chg") - c("mkt")).alias("xs"))
    f = forecast_first().filter(c("type").is_in(BAD))
    f = f.with_columns(c("ts_code").str.slice(0, 6).alias("code"),
                       (c("ann_date").str.to_date("%Y%m%d") + timedelta(days=1)).alias("a1")).sort("a1")
    td = df.select("trade_date").unique().with_columns(c("trade_date").str.to_date().alias("sdd")).sort("sdd")
    f = f.join_asof(td, left_on="a1", right_on="sdd", strategy="forward").drop_nulls("trade_date")
    f = f.group_by("code", "trade_date").agg(c("type").first().alias("ftype")).with_columns(pl.lit(True).alias("ev"))
    df = df.join(f, on=["code", "trade_date"], how="left").with_columns(c("ev").fill_null(False))
    cand = (c("ev") & c("liq") & (c("xs") >= 3.0) & ~c("luc")).fill_null(False)
    df = df.with_columns(cand.alias("cand"))
    df = df.with_columns(pl.when(c("cand")).then(-c("xs")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias("BNR"),
                           pl.when(c("cand")).then(c("xs")).otherwise(-1e9).alias("sc_bnr")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("BNR") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        a = df.filter(c("ev") & c("liq") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_ev_liq": a.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "xs_med": round(float(w["xs"].median()), 2),
                         "types": w["ftype"].value_counts().sort("count", descending=True).rows(),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
                         "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "BNR", "sc_bnr", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
