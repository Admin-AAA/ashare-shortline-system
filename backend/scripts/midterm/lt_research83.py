"""[中线/长线] §230 EBIT/营收同比改善（ebit_of_gr） — 预注册（docs/strategy-research.md §230，结果出来前写定，不调参）。
口径同 lt_research51~81：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  EBIT：本期 ebit_of_gr ≥ 5.0，去年同报告期有值，升幅 = 本期 − 去年 ≥ 2.0；按升幅（封顶 20）降序。
  EBITp：再要求 netprofit_yoy>0。持 20/60。
  去重：毛利率§164 / 净利率§174 已否；本轮测 ebit_of_gr（EBIT/营收）（EBIT/营业总收入）同比改善。
用法：LT_START=<phase start> LT_END=<phase end+120d> PYTHONPATH=../..:. python lt_research83.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
EBIT_MIN, DELTA_MIN, DELTA_CAP = 5.0, 2.0, 20.0


def ebit_events(td):
    cols = ["ts_code", "ann_date", "end_date", "ebit_of_gr", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("ebit_of_gr", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "ebit_of_gr"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("ebit_of_gr").alias("prev_ebit"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("ebit_of_gr") - c("prev_ebit")).alias("delta")).filter(
        c("prev_ebit").is_not_null()
        & (c("ebit_of_gr") >= EBIT_MIN)
        & (c("delta") >= DELTA_MIN)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("delta").max().alias("delta"),
        c("netprofit_yoy").max().alias("npy"),
        c("ebit_of_gr").max().alias("ebit_of_gr"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = ebit_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("delta").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("EBIT"),
        (sig & (c("npy") > 0)).fill_null(False).alias("EBITp"),
        (c("delta").clip(-1e9, DELTA_CAP).fill_null(0) - tie).alias("sc_ebit"),
    ).sort("code", "trade_date")


NEW = {
    "EBIT_h20": ("EBIT", "sc_ebit", 20),
    "EBIT_h60": ("EBIT", "sc_ebit", 60),
    "EBITp_h60": ("EBITp", "sc_ebit", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("EBIT").sum(), c("EBITp").sum(), c("delta").is_not_null().sum().alias("raw")
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
