"""[中线/长线] §184 营业周期同比缩短 — 预注册（docs/strategy-research.md §184，结果出来前写定，不调参）。
口径同 lt_research51~60：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  TD：去年同报告期 turn_days − 本期 ≥ 20，本期 ∈[30,400]，去年 ∈[30,800]；按缩短天数（封顶 200）降序。
  TDp：再要求 netprofit_yoy>0。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+120d> PYTHONPATH=../..:. python lt_research61.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
TD_IMPROVE, TD_LO, TD_HI, PREV_HI, TD_CAP = 20.0, 30.0, 400.0, 800.0, 200.0


def td_events(td):
    cols = ["ts_code", "ann_date", "end_date", "turn_days", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("turn_days", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "turn_days"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("turn_days").alias("prev_td"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("prev_td") - c("turn_days")).alias("improve")).filter(
        c("prev_td").is_not_null()
        & (c("improve") >= TD_IMPROVE)
        & (c("turn_days") >= TD_LO)
        & (c("turn_days") <= TD_HI)
        & (c("prev_td") >= TD_LO)
        & (c("prev_td") <= PREV_HI)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("improve").max().alias("improve"),
        c("netprofit_yoy").max().alias("npy"),
        c("turn_days").min().alias("turn_days"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = td_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("improve").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("TD"),
        (sig & (c("npy") > 0)).fill_null(False).alias("TDp"),
        (c("improve").clip(-1e9, TD_CAP).fill_null(0) - tie).alias("sc_td"),
    ).sort("code", "trade_date")


NEW = {
    "TD_h20": ("TD", "sc_td", 20),
    "TD_h60": ("TD", "sc_td", 60),
    "TDp_h60": ("TDp", "sc_td", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("TD").sum(), c("TDp").sum(), c("improve").is_not_null().sum().alias("raw")
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
