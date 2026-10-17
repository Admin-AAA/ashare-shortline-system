"""[中线] 股东户数下降（筹码集中）事件 — 预设版本，不调参。数据 data/midterm/holdernum (stk_holdernumber, ann_date PIT)。
信号：公告日当日或之后首个交易日收盘出信号，次日开盘买（realistic 引擎：开盘涨停不买、跌停顺延、逐日盯市、0.3%成本）。"""
import sys, json
import polars as pl
import lt_data as L
from lt_research import build, fixed, PH
from lt_research2 import regime
from realistic import run_real, metrics_real
c = pl.col

def holder_events(td):
    fs = list((L.D / "holdernum").glob("*.parquet"))
    h = pl.concat([pl.read_parquet(x) for x in fs], how="diagonal_relaxed").drop_nulls(["ann_date", "end_date", "holder_num"])
    h = h.with_columns([c(x).cast(pl.Utf8).str.strip_chars() for x in ("ann_date", "end_date")])
    h = h.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    h = (h.with_columns(c("ts_code").str.slice(0, 6).alias("code"), c("holder_num").cast(pl.Float64))
          .sort("code", "end_date", "ann_date").unique(["code", "end_date"], keep="first").sort("code", "end_date"))
    h = h.with_columns((c("holder_num") / c("holder_num").shift(1).over("code") - 1).alias("hn_chg"),
                       (c("holder_num").shift(1).over("code") / c("holder_num").shift(2).over("code") - 1).alias("hn_chg_prev"),
                       c("ann_date").shift(1).over("code").alias("prev_ann"))
    # PIT：前一期必须在本期公告前已公告；本期公告日距期末 <= 120 天（剔除陈旧补录）
    h = h.filter((c("prev_ann") <= c("ann_date")) &
                 ((c("ann_date").str.strptime(pl.Date, "%Y%m%d") - c("end_date").str.strptime(pl.Date, "%Y%m%d")).dt.total_days() <= 120))
    h = h.with_columns(L._dash("ann_date").alias("ann")).sort("ann")
    h = h.join_asof(td.rename({"trade_date": "sd"}).with_columns(c("sd").alias("ann2")), left_on="ann", right_on="ann2", strategy="forward")
    return (h.drop_nulls("sd").group_by("code", "sd").agg(c("hn_chg").min(), c("hn_chg_prev").first())
             .rename({"sd": "trade_date"}))

if __name__ == "__main__":
    df = build()
    ev = holder_events(df.select("trade_date").unique().sort("trade_date"))
    df = regime(df.join(ev, on=["code", "trade_date"], how="left"))
    base = c("liq") & c("hn_chg").is_not_null() & (c("hn_chg") <= -0.10)
    g20 = c("ewi") > c("e20")
    df = df.with_columns(
        base.fill_null(False).alias("H1"),
        (base & (c("hn_chg_prev") < 0)).fill_null(False).alias("H2"),
        (base & (c("r20") < 0.15) & (c("ac") > c("m60"))).fill_null(False).alias("H3"),
        (base & g20).fill_null(False).alias("H4"),
        (base & (c("total_mv") < 1.5e6)).fill_null(False).alias("H5"),
        (-c("hn_chg")).fill_null(0).alias("sc_hn"))
    C = {"hn10_h20": ("H1", 20), "hn10_2q_h20": ("H2", 20), "hn10_trend_h20": ("H3", 20),
         "hn10_g20_h20": ("H4", 20), "hn10_small_h20": ("H5", 20), "hn10_h40": ("H1", 40)}
    res = {}
    for n in (sys.argv[1:] or C):
        sig, h = C[n]; print(f"## {n}", flush=True)
        for ph, (s, e) in PH.items():
            t, cur = run_real(df, sig, "sc_hn", fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
            m = metrics_real(t, cur); res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
            print(" ", ph, m, flush=True)
        pos = sum(1 for v in res[n].values() if v.get("avg", -1) > 0); print("  pos", pos, "/8", flush=True)
    json.dump(res, open("/tmp/lt6.json", "w"), indent=1)
