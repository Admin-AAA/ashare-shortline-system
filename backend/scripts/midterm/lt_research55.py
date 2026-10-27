"""[中线/长线] §172 自由现金流每股同比改善 — 预注册（docs/strategy-research.md §172，结果出来前写定，不调参）。
口径同 lt_research51/52/53/54：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  FCFF：本期 fcff_ps − 去年同报告期 ≥ 0.5 元/股，且本期 fcff_ps > 0；按 ΔFCFF_PS（封顶5）降序。
  FCFFp：再要求 netprofit_yoy>0。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+120d> PYTHONPATH=../..:. python lt_research55.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
FCFF_DELTA, FCFF_FLOOR, FCFF_CAP = 0.5, 0.0, 5.0


def fcff_events(td):
    cols = ["ts_code", "ann_date", "end_date", "fcff_ps", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("fcff_ps", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "fcff_ps"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("fcff_ps").alias("prev_fcff"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("fcff_ps") - c("prev_fcff")).alias("dfcff")).filter(
        c("prev_fcff").is_not_null()
        & (c("dfcff") >= FCFF_DELTA)
        & (c("fcff_ps") > FCFF_FLOOR)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("dfcff").max().alias("dfcff"),
        c("netprofit_yoy").max().alias("npy"),
        c("fcff_ps").max().alias("fcff_ps"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = fcff_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("dfcff").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("FCFF"),
        (sig & (c("npy") > 0)).fill_null(False).alias("FCFFp"),
        (c("dfcff").clip(-1e9, FCFF_CAP).fill_null(0) - tie).alias("sc_fcff"),
    ).sort("code", "trade_date")


NEW = {
    "FCFF_h20": ("FCFF", "sc_fcff", 20),
    "FCFF_h60": ("FCFF", "sc_fcff", 60),
    "FCFFp_h60": ("FCFFp", "sc_fcff", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("FCFF").sum(), c("FCFFp").sum(), c("dfcff").is_not_null().sum().alias("raw")
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
