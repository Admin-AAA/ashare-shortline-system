"""[中线/长线] §158 分红增加 — 预注册（docs/strategy-research.md §158，结果出来前写定，不调参）。
口径同 lt_research47：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  DINC：分红预案每股税前派现 > 0，较去年同一报告期（优先实施记录，否则最早预案）每股派现提高 >= 30%（去年同期 > 0），按增幅降序。持 20 / 60。
用法：PYTHONPATH=backend python lt_research48.py "<phase>,<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
INC_TH = 0.30


def dinc_events(td):
    cols = ["ts_code", "end_date", "ann_date", "div_proc", "cash_div_tax"]
    d = pl.concat([pl.read_parquet(f, columns=cols) for f in sorted(glob.glob(str(L.D / "dividend" / "*.parquet")))], how="diagonal_relaxed")
    d = d.with_columns(c("cash_div_tax").cast(pl.Float64, strict=False).fill_null(0),
                       *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ann_date", "end_date")])
    d = d.filter(c("end_date").str.len_chars() == 8)
    plan = d.filter(c("div_proc").is_in(["预案", "预披露"]) & (c("ann_date").str.len_chars() == 8))
    plan_first = plan.sort("ann_date").unique(["ts_code", "end_date"], keep="first")
    impl = d.filter(c("div_proc") == "实施").sort("ann_date").unique(["ts_code", "end_date"], keep="first")
    ref = plan_first.select("ts_code", "end_date", c("cash_div_tax").alias("p_dps")).join(
        impl.select("ts_code", "end_date", c("cash_div_tax").alias("i_dps")), on=["ts_code", "end_date"], how="full", coalesce=True)
    ref = ref.select("ts_code", ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
                     pl.coalesce("i_dps", "p_dps").alias("prev_dps"))
    cur = plan_first.filter(c("cash_div_tax") > 0).join(ref, on=["ts_code", "end_date"], how="inner")
    cur = cur.filter(c("prev_dps") > 0).with_columns((c("cash_div_tax") / c("prev_dps") - 1).alias("inc")).filter(c("inc") >= INC_TH)
    return _pit(cur, td).group_by("code", "sd").agg(c("inc").max())


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    df = df.join(dinc_events(td).rename({"sd": "trade_date"}), on=["code", "trade_date"], how="left")
    return df.with_columns((c("liq") & c("inc").is_not_null()).fill_null(False).alias("DINC"),
                           (c("inc").fill_null(0) - tie).alias("sc_inc")).sort("code", "trade_date")


NEW = {f"DINC_h{h}": ("DINC", "sc_inc", h) for h in (20, 60)}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(c("DINC").sum(), c("inc").is_not_null().sum().alias("raw")).sort("y").to_dicts(), flush=True)
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
