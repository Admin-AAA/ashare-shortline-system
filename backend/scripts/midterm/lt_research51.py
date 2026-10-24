"""[中线/长线] §164 毛利率同比扩张 — 预注册（docs/strategy-research.md §164，结果出来前写定，不调参）。
口径同 lt_research48/50：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  GME：grossprofit_margin − 去年同报告期 ≥ 3.0 百分点，且本期 GM ≥ 10；按 ΔGM（封顶30）降序。
  GMEp：再要求 netprofit_yoy>0。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+120d> PYTHONPATH=../..:. python lt_research51.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
GM_DELTA, GM_FLOOR, GM_CAP = 3.0, 10.0, 30.0


def gme_events(td):
    cols = ["ts_code", "ann_date", "end_date", "grossprofit_margin", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("grossprofit_margin", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "grossprofit_margin"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("grossprofit_margin").alias("prev_gm"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("grossprofit_margin") - c("prev_gm")).alias("dgm")).filter(
        c("prev_gm").is_not_null()
        & (c("dgm") >= GM_DELTA)
        & (c("grossprofit_margin") >= GM_FLOOR)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("dgm").max().alias("dgm"),
        c("netprofit_yoy").max().alias("npy"),
        c("grossprofit_margin").max().alias("gm"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = gme_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("dgm").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("GME"),
        (sig & (c("npy") > 0)).fill_null(False).alias("GMEp"),
        (c("dgm").clip(-1e9, GM_CAP).fill_null(0) - tie).alias("sc_gme"),
    ).sort("code", "trade_date")


NEW = {
    "GME_h20": ("GME", "sc_gme", 20),
    "GME_h60": ("GME", "sc_gme", 60),
    "GMEp_h60": ("GMEp", "sc_gme", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("GME").sum(), c("GMEp").sum(), c("dgm").is_not_null().sum().alias("raw")
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
    print("done", flush=True)
