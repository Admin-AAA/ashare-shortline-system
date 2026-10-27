"""[中线/长线] §176 资产周转率同比扩张 — 预注册（docs/strategy-research.md §176，结果出来前写定，不调参）。
口径同 lt_research51/52/53/54/55/56：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  AT：本期 assets_turn − 去年同报告期 ≥ 0.05，且本期 assets_turn ≥ 0.20；按 ΔAT（封顶 0.50）降序。
  ATp：再要求 netprofit_yoy>0。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+120d> PYTHONPATH=../..:. python lt_research57.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
AT_DELTA, AT_FLOOR, AT_CAP = 0.05, 0.20, 0.50


def at_events(td):
    cols = ["ts_code", "ann_date", "end_date", "assets_turn", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("assets_turn", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "assets_turn"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("assets_turn").alias("prev_at"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("assets_turn") - c("prev_at")).alias("dat")).filter(
        c("prev_at").is_not_null()
        & (c("dat") >= AT_DELTA)
        & (c("assets_turn") >= AT_FLOOR)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("dat").max().alias("dat"),
        c("netprofit_yoy").max().alias("npy"),
        c("assets_turn").max().alias("at"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = at_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("dat").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("AT"),
        (sig & (c("npy") > 0)).fill_null(False).alias("ATp"),
        (c("dat").clip(-1e9, AT_CAP).fill_null(0) - tie).alias("sc_at"),
    ).sort("code", "trade_date")


NEW = {
    "AT_h20": ("AT", "sc_at", 20),
    "AT_h60": ("AT", "sc_at", 60),
    "ATp_h60": ("ATp", "sc_at", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("AT").sum(), c("ATp").sum(), c("dat").is_not_null().sum().alias("raw")
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
