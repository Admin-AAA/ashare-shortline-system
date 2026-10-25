"""[中线/长线] §254 三季报高资本公积+未分配利润「高送转预期」（capital_rese_ps, HSZ*） — 预注册（docs/strategy-research.md §254，结果出来前写定，不调参）。
口径同 lt_research51~94：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  HSZ：end_date 为当年 0930（三季报）、capital_rese_ps ≥ 3.0、undist_profit_ps ≥ 1.0；按 capital_rese_ps+undist_profit_ps（封顶30）降序。
  HSZp：再要求 netprofit_yoy>0。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research95.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
HSZ_CRP, HSZ_UDP, HSZ_CAP = 3.0, 1.0, 30.0


def hsz_events(td):
    cols = ["ts_code", "ann_date", "end_date", "capital_rese_ps", "undist_profit_ps", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("capital_rese_ps", "undist_profit_ps", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "capital_rese_ps", "undist_profit_ps"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    d = d.filter(
        (c("end_date").str.slice(4, 4) == "0930")
        & (c("capital_rese_ps") >= HSZ_CRP)
        & (c("undist_profit_ps") >= HSZ_UDP)
    ).with_columns((c("capital_rese_ps") + c("undist_profit_ps")).alias("hsz"))
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("hsz").max().alias("hsz"),
        c("netprofit_yoy").max().alias("npy"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = hsz_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("hsz").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("HSZ"),
        (sig & (c("npy") > 0)).fill_null(False).alias("HSZp"),
        (c("hsz").clip(-1e9, HSZ_CAP).fill_null(0) - tie).alias("sc_hsz"),
    ).sort("code", "trade_date")


NEW = {
    "HSZ_h20": ("HSZ", "sc_hsz", 20),
    "HSZ_h60": ("HSZ", "sc_hsz", 60),
    "HSZp_h60": ("HSZp", "sc_hsz", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("HSZ").sum(), c("HSZp").sum(), c("hsz").is_not_null().sum().alias("raw")
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
