"""[中线/长线] §190 流动比率同比改善 — 预注册（docs/strategy-research.md §190，结果出来前写定，不调参）。
口径同 lt_research51~63：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  CR：本期 current_ratio∈[0.5,5]、去年∈[0.3,8]，升幅=本期−去年 ≥0.20；按升幅（封顶 2.0）降序。
  CRp：再要求 netprofit_yoy>0。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+120d> PYTHONPATH=../..:. python lt_research64.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
DCR_MIN, CR_LO, CR_HI, PREV_LO, PREV_HI, DCR_CAP = 0.20, 0.5, 5.0, 0.3, 8.0, 2.0


def cr_events(td):
    cols = ["ts_code", "ann_date", "end_date", "current_ratio", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("current_ratio", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "current_ratio"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("current_ratio").alias("prev_cr"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("current_ratio") - c("prev_cr")).alias("dcr")).filter(
        c("prev_cr").is_not_null()
        & (c("dcr") >= DCR_MIN)
        & (c("current_ratio") >= CR_LO)
        & (c("current_ratio") <= CR_HI)
        & (c("prev_cr") >= PREV_LO)
        & (c("prev_cr") <= PREV_HI)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("dcr").max().alias("dcr"),
        c("netprofit_yoy").max().alias("npy"),
        c("current_ratio").max().alias("current_ratio"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = cr_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("dcr").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("CR"),
        (sig & (c("npy") > 0)).fill_null(False).alias("CRp"),
        (c("dcr").clip(-1e9, DCR_CAP).fill_null(0) - tie).alias("sc_cr"),
    ).sort("code", "trade_date")


NEW = {
    "CR_h20": ("CR", "sc_cr", 20),
    "CR_h60": ("CR", "sc_cr", 60),
    "CRp_h60": ("CRp", "sc_cr", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("CR").sum(), c("CRp").sum(), c("dcr").is_not_null().sum().alias("raw")
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
