"""[中线/长线] §192 现金比率同比改善 — 预注册（docs/strategy-research.md §192，结果出来前写定，不调参）。
口径同 lt_research51~64：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  CASH：本期 cash_ratio∈[0.05,3]、去年∈[0.02,5]，升幅=本期−去年 ≥0.10；按升幅（封顶 1.5）降序。
  CASHp：再要求 netprofit_yoy>0。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+120d> PYTHONPATH=../..:. python lt_research65.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
DCASH_MIN, CR_LO, CR_HI, PREV_LO, PREV_HI, DCASH_CAP = 0.10, 0.05, 3.0, 0.02, 5.0, 1.5


def cash_events(td):
    cols = ["ts_code", "ann_date", "end_date", "cash_ratio", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("cash_ratio", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "cash_ratio"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("cash_ratio").alias("prev_cash"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("cash_ratio") - c("prev_cash")).alias("dcash")).filter(
        c("prev_cash").is_not_null()
        & (c("dcash") >= DCASH_MIN)
        & (c("cash_ratio") >= CR_LO)
        & (c("cash_ratio") <= CR_HI)
        & (c("prev_cash") >= PREV_LO)
        & (c("prev_cash") <= PREV_HI)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("dcash").max().alias("dcash"),
        c("netprofit_yoy").max().alias("npy"),
        c("cash_ratio").max().alias("cash_ratio"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = cash_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("dcash").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("CASH"),
        (sig & (c("npy") > 0)).fill_null(False).alias("CASHp"),
        (c("dcash").clip(-1e9, DCASH_CAP).fill_null(0) - tie).alias("sc_cash"),
    ).sort("code", "trade_date")


NEW = {
    "CASH_h20": ("CASH", "sc_cash", 20),
    "CASH_h60": ("CASH", "sc_cash", 60),
    "CASHp_h60": ("CASHp", "sc_cash", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("CASH").sum(), c("CASHp").sum(), c("dcash").is_not_null().sum().alias("raw")
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
