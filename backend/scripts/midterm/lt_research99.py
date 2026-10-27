"""[中线/长线] §263 留存收益由负转正（retained_earnings, RES*） — 预注册（docs/strategy-research.md §261，结果出来前写定，不调参）。
口径同 lt_research51~98：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  去年同报告期 = end_date 年份−1 同月日。
  RES：去年同期 retained_earnings < 0；本期 retained_earnings > 0；本期 profit_dedt > 0。
       排序分 = profit_dedt / |retained_earnings_prev|（封顶 3.0）降序。
  RESp：再要求 or_yoy > 0。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research99.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
SC_CAP = 3.0


def res_events(td):
    cols = ["ts_code", "ann_date", "end_date", "retained_earnings", "profit_dedt", "or_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("retained_earnings", "profit_dedt", "or_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "retained_earnings", "profit_dedt"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("retained_earnings").alias("re_p"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.filter(
        (c("re_p") < 0) & (c("retained_earnings") > 0) & (c("profit_dedt") > 0)
    ).with_columns((c("profit_dedt") / c("re_p").abs()).alias("res"))
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("res").max().alias("res"),
        c("or_yoy").max().alias("ory"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = res_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("res").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("RES"),
        (sig & (c("ory") > 0)).fill_null(False).alias("RESp"),
        (c("res").clip(-1e9, SC_CAP).fill_null(0) - tie).alias("sc_res"),
    ).sort("code", "trade_date")


NEW = {
    "RES_h20": ("RES", "sc_res", 20),
    "RES_h60": ("RES", "sc_res", 60),
    "RESp_h60": ("RESp", "sc_res", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("RES").sum(), c("RESp").sum(), c("res").is_not_null().sum().alias("raw")
    ).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for n, (sig, sc, h) in NEW.items():
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
            res.setdefault(n, {})[ph] = full_metrics(df, t, cur)
            print(n, ph, res[n][ph], flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
