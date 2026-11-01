"""[中线/长线] §162 经营现金流同比加速 — 预注册（docs/strategy-research.md §162，结果出来前写定，不调参）。
口径同 lt_research48：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  OCF：ocfps>0、ocf_yoy>=50、本期 ocf_yoy > 上期（同长度 end_date 年-1 优先，否则按 end_date 时间序上一期）。
  OCFn：再要求 netprofit_yoy>0。持 20/60。排序 min(ocf_yoy,300) 降序。
用法：LT_START=<phase start> LT_END=<phase end+120d> PYTHONPATH=../..:. python lt_research50.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
OCF_TH = 50.0


def ocf_events(td):
    cols = ["ts_code", "ann_date", "end_date", "ocf_yoy", "ocfps", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("ocf_yoy", "ocfps", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    # 同长度上一年：end_date 年-1 同月日
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("ocf_yoy").alias("prev_same"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    # 时间序上一期
    d = d.with_columns(c("ocf_yoy").shift(1).over("ts_code").alias("prev_shift"))
    d = d.with_columns(pl.coalesce("prev_same", "prev_shift").alias("prev_ocf"))
    d = d.filter(
        (c("ocfps") > 0) & (c("ocf_yoy") >= OCF_TH) & c("prev_ocf").is_not_null() & (c("ocf_yoy") > c("prev_ocf"))
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("ocf_yoy").max().alias("ocf"),
        c("netprofit_yoy").max().alias("npy"),
        c("prev_ocf").max().alias("prev_ocf"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = ocf_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("ocf").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("OCF"),
        (sig & (c("npy") > 0)).fill_null(False).alias("OCFn"),
        (c("ocf").clip(-1e9, 300).fill_null(0) - tie).alias("sc_ocf"),
    ).sort("code", "trade_date")


NEW = {
    "OCF_h20": ("OCF", "sc_ocf", 20),
    "OCF_h60": ("OCF", "sc_ocf", 60),
    "OCFn_h60": ("OCFn", "sc_ocf", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("OCF").sum(), c("OCFn").sum(), c("ocf").is_not_null().sum().alias("raw")
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
