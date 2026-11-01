"""[中线/长线] §178 总资产净利率同比扩张 — 预注册（docs/strategy-research.md §178，结果出来前写定，不调参）。
口径同 lt_research51/52/53/54/55/56/57：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  NPTA：本期 npta − 去年同报告期 ≥ 1.0，且本期 npta ≥ 3.0；按 Δnpta（封顶 10.0）降序。
  NPTAp：再要求 netprofit_yoy>0。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+120d> PYTHONPATH=../..:. python lt_research58.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
NPTA_DELTA, NPTA_FLOOR, NPTA_CAP = 1.0, 3.0, 10.0


def npta_events(td):
    cols = ["ts_code", "ann_date", "end_date", "npta", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("npta", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "npta"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("npta").alias("prev_npta"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("npta") - c("prev_npta")).alias("dnpta")).filter(
        c("prev_npta").is_not_null()
        & (c("dnpta") >= NPTA_DELTA)
        & (c("npta") >= NPTA_FLOOR)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("dnpta").max().alias("dnpta"),
        c("netprofit_yoy").max().alias("npy"),
        c("npta").max().alias("npta"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = npta_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("dnpta").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("NPTA"),
        (sig & (c("npy") > 0)).fill_null(False).alias("NPTAp"),
        (c("dnpta").clip(-1e9, NPTA_CAP).fill_null(0) - tie).alias("sc_npta"),
    ).sort("code", "trade_date")


NEW = {
    "NPTA_h20": ("NPTA", "sc_npta", 20),
    "NPTA_h60": ("NPTA", "sc_npta", 60),
    "NPTAp_h60": ("NPTAp", "sc_npta", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("NPTA").sum(), c("NPTAp").sum(), c("dnpta").is_not_null().sum().alias("raw")
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
