"""[中线] §343「被错杀的成长股：财报扣非高增长但股价离一年高点深跌 → 次日开盘买、持 20 日」GDD_h20
（Lakonishok–Shleifer–Vishny 1994 外推偏差）。预注册 docs/strategy-research.md §343，结果出来前写定，不调参。
  数据：data/midterm/fina/*.parquet（fina_indicator，ann_date PIT），每个 (code, end_date) 只取最早 ann_date 那条。
  信号日 S = 第一个 > ann_date 的交易日。条件：dt_netprofit_yoy ≥ 30 & or_yoy ≥ 10 & profit_dedt > 0；
  S 日复权收盘 ≤ 过去 250 日复权收盘最高 ×0.70；S 日 liq；同股 60 自然日内只认第一次。同日按 dt_netprofit_yoy 降序前 5。
  引擎 realistic：S+1 开盘买、持 20 日、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research131.py "<phase>" out.json"""
import sys, json, os, glob
from datetime import timedelta
import polars as pl
import lt_data as L
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
NAME, HOLD, K, MAX_NEW, DEDUP_D = "GDD_h20", 20, 20, 5, 60


def fina_events():
    fs = sorted(glob.glob(str(L.D / "fina" / "*.parquet")))
    cols = ["ts_code", "ann_date", "end_date", "dt_netprofit_yoy", "or_yoy", "profit_dedt"]
    f = pl.concat([pl.read_parquet(x, columns=cols) for x in fs], how="diagonal_relaxed")
    f = f.drop_nulls(["ann_date", "end_date"]).filter(c("ann_date").str.len_chars() == 8)
    f = f.with_columns(c("ts_code").str.slice(0, 6).alias("code"), c("ann_date").str.to_date("%Y%m%d").alias("a"),
                       *[c(x).cast(pl.Float64, strict=False) for x in cols[3:]])
    f = f.sort("code", "end_date", "a").group_by("code", "end_date").first()
    f = f.filter((c("dt_netprofit_yoy") >= 30) & (c("or_yoy") >= 10) & (c("profit_dedt") > 0)).sort("code", "a")
    rows = []
    for (code,), x in f.partition_by("code", as_dict=True).items():
        last = None
        for a, g in zip(x["a"].to_list(), x["dt_netprofit_yoy"].to_list()):
            if last is not None and (a - last).days <= DEDUP_D:
                continue
            last = a; rows.append((code, a, float(g)))
    return pl.DataFrame(rows, schema=["code", "a", "g"], orient="row")


def prep():
    df = build().sort("code", "trade_date")
    df = df.with_columns(c("ac").rolling_max(250, min_samples=200).over("code").alias("hi250"))
    df = df.with_columns((c("ac") / c("hi250") - 1).alias("dd250"))
    ev = fina_events().with_columns((c("a") + timedelta(days=1)).alias("a1"))
    td = df.select("trade_date").unique().with_columns(c("trade_date").str.to_date().alias("sdd")).sort("sdd")
    ev = ev.sort("a1").join_asof(td, left_on="a1", right_on="sdd", strategy="forward").drop_nulls("trade_date")
    ev = ev.group_by("code", "trade_date").agg(c("g").max())
    df = df.join(ev, on=["code", "trade_date"], how="left")
    evm = c("g").is_not_null()
    cand = (evm & c("liq") & (c("dd250") <= -0.30)).fill_null(False)
    df = df.with_columns(evm.alias("ev"), cand.alias("cand"))
    df = df.with_columns(pl.when(c("cand")).then(-c("g")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias("GDD"),
                           pl.when(c("cand")).then(c("g")).otherwise(-1e9).alias("sc_gdd")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("GDD") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        a = df.filter(c("ev") & c("liq") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_ev_liq": a.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "g_med": round(float(w["g"].median()), 1),
                         "dd_med": round(float(w["dd250"].median()), 3),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
                         "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "GDD", "sc_gdd", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
