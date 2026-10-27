"""[中线/长线] §248 有形资产占比同比上升（tbassets_to_totalassets, TBA*） — 预注册（docs/strategy-research.md §248，结果出来前写定，不调参）。
口径同 lt_research51~91：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  TBA：本期 tbassets_to_totalassets∈[30,95]、去年∈[20,100]（百分数口径），升幅=本期−去年 ≥3.0 百分点；按升幅（封顶 25.0）降序。
  TBAp：再要求 netprofit_yoy>0。持 20/60。
  去重：§234 tangibleasset_to_debt（杠杆率）；§242 ca_to_assets；本轮测资产构成占比 tbassets_to_totalassets。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research92.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
CUR_LO, CUR_HI, PREV_LO, PREV_HI = 30.0, 95.0, 20.0, 100.0
DELTA_MIN, DELTA_CAP = 3.0, 25.0


def tba_events(td):
    cols = ["ts_code", "ann_date", "end_date", "tbassets_to_totalassets", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("tbassets_to_totalassets", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "tbassets_to_totalassets"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("tbassets_to_totalassets").alias("prev_tba"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("tbassets_to_totalassets") - c("prev_tba")).alias("delta")).filter(
        c("prev_tba").is_not_null()
        & (c("tbassets_to_totalassets") >= CUR_LO) & (c("tbassets_to_totalassets") <= CUR_HI)
        & (c("prev_tba") >= PREV_LO) & (c("prev_tba") <= PREV_HI)
        & (c("delta") >= DELTA_MIN)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("delta").max().alias("delta"),
        c("netprofit_yoy").max().alias("npy"),
        c("tbassets_to_totalassets").max().alias("tba"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = tba_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("delta").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("TBA"),
        (sig & (c("npy") > 0)).fill_null(False).alias("TBAp"),
        (c("delta").clip(-1e9, DELTA_CAP).fill_null(0) - tie).alias("sc_tba"),
    ).sort("code", "trade_date")


NEW = {
    "TBA_h20": ("TBA", "sc_tba", 20),
    "TBA_h60": ("TBA", "sc_tba", 60),
    "TBAp_h60": ("TBAp", "sc_tba", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("TBA").sum(), c("TBAp").sum(), c("delta").is_not_null().sum().alias("raw")
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
