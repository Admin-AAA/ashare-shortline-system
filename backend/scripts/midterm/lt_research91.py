"""[中线/长线] §246 年ROE同比 roe_yoy（ROY*） — 预注册（docs/strategy-research.md §246，结果出来前写定，不调参）。
口径同 lt_research51~90：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  ROY：本期 roe_yoy ≥ 20（字段已是 YoY%，水平事件）；按 roe_yoy（封顶 100）降序。
  ROYp：再要求 netprofit_yoy>0。持 20/60。
  去重：§168 ΔROE / §170 ΔROIC / §222 q_roe 单季差分；本轮测 fina roe_yoy 水平。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research91.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
ROY_MIN, ROY_CAP = 20.0, 100.0


def roy_events(td):
    cols = ["ts_code", "ann_date", "end_date", "roe_yoy", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("roe_yoy", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "roe_yoy"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    d = d.filter(c("roe_yoy") >= ROY_MIN)
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("roe_yoy").max().alias("roe_yoy"),
        c("netprofit_yoy").max().alias("npy"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = roy_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("roe_yoy").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("ROY"),
        (sig & (c("npy") > 0)).fill_null(False).alias("ROYp"),
        (c("roe_yoy").clip(-1e9, ROY_CAP).fill_null(0) - tie).alias("sc_roy"),
    ).sort("code", "trade_date")


NEW = {
    "ROY_h20": ("ROY", "sc_roy", 20),
    "ROY_h60": ("ROY", "sc_roy", 60),
    "ROYp_h60": ("ROYp", "sc_roy", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("ROY").sum(), c("ROYp").sum(), c("roe_yoy").is_not_null().sum().alias("raw")
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
