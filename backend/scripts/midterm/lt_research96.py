"""[中线/长线] §256 单季总成本占营收同比下降（q_gc_to_gr, QGC*） — 预注册（docs/strategy-research.md §256，结果出来前写定，不调参）。
口径同 lt_research51~95：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  QGC：本期 q_gc_to_gr ∈ [50,105]，去年同报告期 ∈ [50,130]，降幅=去年−本期 ≥ 5.0pp；按降幅（封顶30）降序。
  QGCp：再要求 netprofit_yoy>0。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research96.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
QGC_LO, QGC_HI, QGC_PREV_HI, QGC_DROP, QGC_CAP = 50.0, 105.0, 130.0, 5.0, 30.0


def qgc_events(td):
    cols = ["ts_code", "ann_date", "end_date", "q_gc_to_gr", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("q_gc_to_gr", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "q_gc_to_gr"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("q_gc_to_gr").alias("prev_qgc"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.filter(
        c("prev_qgc").is_not_null()
        & c("q_gc_to_gr").is_between(QGC_LO, QGC_HI)
        & c("prev_qgc").is_between(QGC_LO, QGC_PREV_HI)
    ).with_columns((c("prev_qgc") - c("q_gc_to_gr")).alias("dqgc")).filter(c("dqgc") >= QGC_DROP)
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("dqgc").max().alias("dqgc"),
        c("netprofit_yoy").max().alias("npy"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = qgc_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("dqgc").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("QGC"),
        (sig & (c("npy") > 0)).fill_null(False).alias("QGCp"),
        (c("dqgc").clip(-1e9, QGC_CAP).fill_null(0) - tie).alias("sc_qgc"),
    ).sort("code", "trade_date")


NEW = {
    "QGC_h20": ("QGC", "sc_qgc", 20),
    "QGC_h60": ("QGC", "sc_qgc", 60),
    "QGCp_h60": ("QGCp", "sc_qgc", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("QGC").sum(), c("QGCp").sum(), c("dqgc").is_not_null().sum().alias("raw")
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
