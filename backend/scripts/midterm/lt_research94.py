"""[中线/长线] §252 每股未分配利润同比高增（undist_profit_ps, UDP*） — 预注册（docs/strategy-research.md §252，结果出来前写定，不调参）。
口径同 lt_research51~93：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  UDP：去年同报告期 undist_profit_ps > 0、本期 ≥ 0.5、增速=本期/去年−1 ≥ 0.30；按增速（封顶2.0）降序。
  UDPp：再要求 netprofit_yoy>0。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research94.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
UDP_PREV_MIN, UDP_FLOOR, UDP_G, UDP_CAP = 0.0, 0.5, 0.30, 2.0


def udp_events(td):
    cols = ["ts_code", "ann_date", "end_date", "undist_profit_ps", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("undist_profit_ps", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "undist_profit_ps"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("undist_profit_ps").alias("prev_udp"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.filter(c("prev_udp").is_not_null() & (c("prev_udp") > UDP_PREV_MIN))
    d = d.with_columns((c("undist_profit_ps") / c("prev_udp") - 1.0).alias("gudp")).filter(
        (c("gudp") >= UDP_G) & (c("undist_profit_ps") >= UDP_FLOOR)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("gudp").max().alias("gudp"),
        c("netprofit_yoy").max().alias("npy"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = udp_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("gudp").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("UDP"),
        (sig & (c("npy") > 0)).fill_null(False).alias("UDPp"),
        (c("gudp").clip(-1e9, UDP_CAP).fill_null(0) - tie).alias("sc_udp"),
    ).sort("code", "trade_date")


NEW = {
    "UDP_h20": ("UDP", "sc_udp", 20),
    "UDP_h60": ("UDP", "sc_udp", 60),
    "UDPp_h60": ("UDPp", "sc_udp", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("UDP").sum(), c("UDPp").sum(), c("gudp").is_not_null().sum().alias("raw")
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