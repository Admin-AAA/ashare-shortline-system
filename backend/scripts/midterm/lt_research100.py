"""[中线/长线] §265 有息负债/EBITDA 杠杆大降（interestdebt / ebitda, IDE*） — 预注册（docs/strategy-research.md §265，结果出来前写定，不调参）。
口径同 lt_research51~99：realistic 引擎、liq、K=20、max_new=5（仅回测）、cohort_bench 同日同市值五分位对照。
  去年同报告期 = end_date 年份−1 同月日。
  IDE：去年同期 ebitda_p>0、interestdebt_p>0、lev_p=interestdebt_p/ebitda_p≥1.0；
       本期 ebitda>0、interestdebt≥0、lev=interestdebt/ebitda ≤ 0.6×lev_p；本期 profit_dedt>0。
       排序分 = 1 − lev/lev_p 降序。
  IDEp：再要求 or_yoy > 0。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research100.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
LEV_MIN = 1.0
DROP = 0.6


def ide_events(td):
    num = ("interestdebt", "ebitda", "profit_dedt", "or_yoy")
    cols = ["ts_code", "ann_date", "end_date", *num]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in num],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "interestdebt", "ebitda", "profit_dedt"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("interestdebt").alias("id_p"),
        c("ebitda").alias("eb_p"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.filter((c("eb_p") > 0) & (c("id_p") > 0) & (c("ebitda") > 0) & (c("interestdebt") >= 0) & (c("profit_dedt") > 0))
    d = d.with_columns((c("id_p") / c("eb_p")).alias("lev_p"), (c("interestdebt") / c("ebitda")).alias("lev"))
    d = d.filter((c("lev_p") >= LEV_MIN) & (c("lev") <= DROP * c("lev_p"))).with_columns(
        (1.0 - c("lev") / c("lev_p")).alias("ide"))
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("ide").max().alias("ide"),
        c("or_yoy").max().alias("ory"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = ide_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("ide").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("IDE"),
        (sig & (c("ory") > 0)).fill_null(False).alias("IDEp"),
        (c("ide").fill_null(0) - tie).alias("sc_ide"),
    ).sort("code", "trade_date")


NEW = {
    "IDE_h20": ("IDE", "sc_ide", 20),
    "IDE_h60": ("IDE", "sc_ide", 60),
    "IDEp_h60": ("IDEp", "sc_ide", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("IDE").sum(), c("IDEp").sum(), c("ide").is_not_null().sum().alias("raw")
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
