"""[中线/长线] §244 权益同比加速（eqt_yoy） — 预注册（docs/strategy-research.md §244，结果出来前写定，不调参）。
口径同 lt_research51~89：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  EQT：本期 eqt_yoy ≥ 20，去年同报告期 eqt_yoy 有值，加速 = 本期 − 去年 ≥ 20；按加速（封顶 100）降序。
  EQTp：再要求 netprofit_yoy>0。持 20/60。
  去重：§218 equity_yoy 已否（不同 Tushare 字段，本地 corr≈0.11）；本轮测 eqt_yoy 同比差分加速。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research90.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
EQ_MIN, ACCEL_MIN, ACCEL_CAP = 20.0, 20.0, 100.0


def eqt_events(td):
    cols = ["ts_code", "ann_date", "end_date", "eqt_yoy", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("eqt_yoy", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "eqt_yoy"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("eqt_yoy").alias("prev_eqt"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("eqt_yoy") - c("prev_eqt")).alias("accel")).filter(
        c("prev_eqt").is_not_null()
        & (c("eqt_yoy") >= EQ_MIN)
        & (c("accel") >= ACCEL_MIN)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("accel").max().alias("accel"),
        c("netprofit_yoy").max().alias("npy"),
        c("eqt_yoy").max().alias("eqt_yoy"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = eqt_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("accel").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("EQT"),
        (sig & (c("npy") > 0)).fill_null(False).alias("EQTp"),
        (c("accel").clip(-1e9, ACCEL_CAP).fill_null(0) - tie).alias("sc_eqt"),
    ).sort("code", "trade_date")


NEW = {
    "EQT_h20": ("EQT", "sc_eqt", 20),
    "EQT_h60": ("EQT", "sc_eqt", 60),
    "EQTp_h60": ("EQTp", "sc_eqt", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("EQT").sum(), c("EQTp").sum(), c("accel").is_not_null().sum().alias("raw")
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
