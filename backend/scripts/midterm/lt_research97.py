"""[中线/长线] §259 盈利质量切换：非经常性损益占比大降、扣非利润上升（EQS*） — 预注册（docs/strategy-research.md §259，结果出来前写定，不调参）。
口径同 lt_research51~96：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  np = profit_dedt + extra_item（累计归母净利近似）；nr = extra_item / |np|（非经常性损益占净利比例）。去年同报告期 = end_date 年份−1 同月日。
  EQS：本期 np>0、profit_dedt>0；去年同期 np>0 且 nr_prev ≥ 0.30；本期 nr ∈ [−0.20, 0.10]；本期 profit_dedt > 去年同期 profit_dedt。
       排序分 = nr_prev − nr（封顶 1.0）降序。
  EQSp：再要求 or_yoy > 0。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research97.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
NR_PREV_MIN, NR_LO, NR_HI, SC_CAP = 0.30, -0.20, 0.10, 1.0


def eqs_events(td):
    cols = ["ts_code", "ann_date", "end_date", "profit_dedt", "extra_item", "or_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("profit_dedt", "extra_item", "or_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "profit_dedt", "extra_item"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    d = d.with_columns((c("profit_dedt") + c("extra_item")).alias("np"))
    d = d.with_columns(pl.when(c("np").abs() > 0).then(c("extra_item") / c("np").abs()).otherwise(None).alias("nr"))
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("nr").alias("nr_p"), c("np").alias("np_p"), c("profit_dedt").alias("pd_p"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.filter(
        (c("np") > 0) & (c("profit_dedt") > 0) & (c("np_p") > 0)
        & (c("nr_p") >= NR_PREV_MIN) & c("nr").is_between(NR_LO, NR_HI)
        & (c("profit_dedt") > c("pd_p"))
    ).with_columns((c("nr_p") - c("nr")).alias("dnr"))
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("dnr").max().alias("dnr"),
        c("or_yoy").max().alias("ory"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = eqs_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("dnr").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("EQS"),
        (sig & (c("ory") > 0)).fill_null(False).alias("EQSp"),
        (c("dnr").clip(-1e9, SC_CAP).fill_null(0) - tie).alias("sc_eqs"),
    ).sort("code", "trade_date")


NEW = {
    "EQS_h20": ("EQS", "sc_eqs", 20),
    "EQS_h60": ("EQS", "sc_eqs", 60),
    "EQSp_h60": ("EQSp", "sc_eqs", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("EQS").sum(), c("EQSp").sum(), c("dnr").is_not_null().sum().alias("raw")
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
