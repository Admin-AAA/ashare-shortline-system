"""[中线/长线] §232 营业成本/营收同比下降（gc_of_gr） — 预注册（docs/strategy-research.md §232，结果出来前写定，不调参）。
口径同 lt_research51~83：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  GC：本期 gc_of_gr∈[30,95]、去年∈[30,100]，降幅=去年−本期 ≥2.0；按降幅（封顶 20）降序。
  GCp：再要求 netprofit_yoy>0。持 20/60。
  去重：毛利率§164 / profit_to_gr§228 / ebit_of_gr§230 / 费用率族已否；本轮测 gc_of_gr 同比下降。
用法：LT_START=<phase start> LT_END=<phase end+120d> PYTHONPATH=../..:. python lt_research84.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
CUR_LO, CUR_HI, PREV_LO, PREV_HI = 30.0, 95.0, 30.0, 100.0
DELTA_MIN, DELTA_CAP = 2.0, 20.0


def gc_events(td):
    cols = ["ts_code", "ann_date", "end_date", "gc_of_gr", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("gc_of_gr", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "gc_of_gr"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("gc_of_gr").alias("prev_gc"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("prev_gc") - c("gc_of_gr")).alias("delta")).filter(
        c("prev_gc").is_not_null()
        & (c("gc_of_gr") >= CUR_LO) & (c("gc_of_gr") <= CUR_HI)
        & (c("prev_gc") >= PREV_LO) & (c("prev_gc") <= PREV_HI)
        & (c("delta") >= DELTA_MIN)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("delta").max().alias("delta"),
        c("netprofit_yoy").max().alias("npy"),
        c("gc_of_gr").max().alias("gc_of_gr"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = gc_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("delta").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("GC"),
        (sig & (c("npy") > 0)).fill_null(False).alias("GCp"),
        (c("delta").clip(-1e9, DELTA_CAP).fill_null(0) - tie).alias("sc_gc"),
    ).sort("code", "trade_date")


NEW = {
    "GC_h20": ("GC", "sc_gc", 20),
    "GC_h60": ("GC", "sc_gc", 60),
    "GCp_h60": ("GCp", "sc_gc", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("GC").sum(), c("GCp").sum(), c("delta").is_not_null().sum().alias("raw")
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
