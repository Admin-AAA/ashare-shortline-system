"""[中线/长线] §250 每股股权自由现金流同比改善（fcfe_ps, FCFE*） — 预注册（docs/strategy-research.md §250，结果出来前写定，不调参）。
口径同 lt_research51~92：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  FCFE：本期 fcfe_ps − 去年同报告期 fcfe_ps ≥ 0.5 元/股，且本期 fcfe_ps > 0；按 ΔFCFE_PS（封顶5）降序。
  FCFEp：再要求 netprofit_yoy>0。持 20/60。
  去重：§172 FCFF_PS（企业自由现金流）已否；本轮测股权侧 fcfe_ps。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research93.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
FCFE_DELTA, FCFE_FLOOR, FCFE_CAP = 0.5, 0.0, 5.0


def fcfe_events(td):
    cols = ["ts_code", "ann_date", "end_date", "fcfe_ps", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("fcfe_ps", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "fcfe_ps"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("fcfe_ps").alias("prev_fcfe"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("fcfe_ps") - c("prev_fcfe")).alias("dfcfe")).filter(
        c("prev_fcfe").is_not_null()
        & (c("dfcfe") >= FCFE_DELTA)
        & (c("fcfe_ps") > FCFE_FLOOR)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("dfcfe").max().alias("dfcfe"),
        c("netprofit_yoy").max().alias("npy"),
        c("fcfe_ps").max().alias("fcfe_ps"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = fcfe_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("dfcfe").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("FCFE"),
        (sig & (c("npy") > 0)).fill_null(False).alias("FCFEp"),
        (c("dfcfe").clip(-1e9, FCFE_CAP).fill_null(0) - tie).alias("sc_fcfe"),
    ).sort("code", "trade_date")


NEW = {
    "FCFE_h20": ("FCFE", "sc_fcfe", 20),
    "FCFE_h60": ("FCFE", "sc_fcfe", 60),
    "FCFEp_h60": ("FCFEp", "sc_fcfe", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("FCFE").sum(), c("FCFEp").sum(), c("dfcfe").is_not_null().sum().alias("raw")
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