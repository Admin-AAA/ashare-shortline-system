"""[中线/长线] §269 营收与扣非净利「连续多期双增长」（稳健复利股，CG*） — 预注册（docs/strategy-research.md §269，结果出来前写定，不调参）。
口径同 lt_research51~101：realistic 引擎、liq、K=20、max_new=5（仅回测）、cohort_bench 同日同市值五分位对照。
  报告期序号 = 年×4 + 季；双增 = or_yoy ≥ 5 且 dt_netprofit_yoy ≥ 5；streak = 以本期为终点、逐期相连且每期双增的期数（跳期即断）。
  CG4：streak ≥ 4；CG8：streak ≥ 8。排序分 = min(streak,12) + min(dt_netprofit_yoy,100)/1000 降序。持 20/60。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research102.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
QM = {"0331": 0, "0630": 1, "0930": 2, "1231": 3}


def cg_events(td):
    num = ("or_yoy", "dt_netprofit_yoy")
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
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8)
                 & c("end_date").str.slice(4, 4).is_in(list(QM)))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    d = d.with_columns(
        (c("end_date").str.slice(0, 4).cast(pl.Int32) * 4
         + c("end_date").str.slice(4, 4).replace_strict(QM, return_dtype=pl.Int32)).alias("pidx"),
        ((c("or_yoy") >= 5) & (c("dt_netprofit_yoy") >= 5)).fill_null(False).alias("ok"),
    )
    # streak：逐期相连且 ok
    ts, pi, ok = d["ts_code"].to_list(), d["pidx"].to_list(), d["ok"].to_list()
    st, prev_ts, prev_pi, run = [], None, None, 0
    for t, p, o in zip(ts, pi, ok):
        if t != prev_ts or prev_pi is None or p != prev_pi + 1:
            run = 0
        run = run + 1 if o else 0
        st.append(run)
        prev_ts, prev_pi = t, p
    d = d.with_columns(pl.Series("streak", st, dtype=pl.Int32))
    d = d.filter(c("streak") >= 4).with_columns(
        (pl.min_horizontal(c("streak"), pl.lit(12)).cast(pl.Float64)
         + pl.min_horizontal(c("dt_netprofit_yoy"), pl.lit(100.0)) / 1000).alias("cg"))
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(c("cg").max().alias("cg"), c("streak").max().alias("streak"))


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = cg_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig4 = (c("liq") & (c("streak") >= 4)).fill_null(False)
    sig8 = (c("liq") & (c("streak") >= 8)).fill_null(False)
    return df.with_columns(
        sig4.alias("CG4"),
        sig8.alias("CG8"),
        (c("cg").fill_null(0) - tie).alias("sc_cg"),
    ).sort("code", "trade_date")


NEW = {
    "CG4_h20": ("CG4", "sc_cg", 20),
    "CG4_h60": ("CG4", "sc_cg", 60),
    "CG8_h60": ("CG8", "sc_cg", 60),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(
        c("CG4").sum(), c("CG8").sum(), c("streak").is_not_null().sum().alias("raw")
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
