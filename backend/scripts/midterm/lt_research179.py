"""§432 DINC_sc_dps_h20：现役分红上调事件不变，排序键增幅→每股派现。同进程 DINC_base。预注册 docs §432。"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research12 import _pit
import lt_research25 as R25
import lt_data as L
c = pl.col
HOLD, K, MAX_NEW = 20, 20, 5
INC_TH = 0.30
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


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
    return _pit(cur, td).group_by("code", "sd").agg(
        c("inc").max().alias("inc"),
        c("cash_div_tax").max().alias("dps"),
    )


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = dinc_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    return df.with_columns(
        (c("liq") & c("inc").is_not_null()).fill_null(False).alias("DINC"),
        (c("inc").fill_null(0) - tie).alias("sc_inc"),
        (c("dps").fill_null(0) - tie).alias("sc_dps"),
    ).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


def one(df, sc, name, ph, s, e, out, res):
    t, cur = run_real(df, "DINC", sc, fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
    m = full_metrics(df, t, cur)
    if t is not None and t.height:
        m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
        t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
    res.setdefault(name, {})[ph] = m
    print(name, ph, m, flush=True)


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("DINC/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("DINC").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "sc_inc", "DINC_base", ph, s, e, out, res)
        one(df, "sc_dps", "DINC_sc_dps_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
