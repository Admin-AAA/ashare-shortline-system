"""§575 DINCUVOL_h20：分红上调∩弱反应∩放量≥1.5×amt20；排序增幅。预注册 docs §575。"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
NAME, HOLD, K, MAX_NEW = "DINCUVOL_h20", 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
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
    return _pit(cur, td).group_by("code", "sd").agg(c("inc").max()).rename({"sd": "trade_date"})


def prep():
    df = R25.prep().sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date")
    ev = dinc_events(td)
    df = df.join(ev, on=["code", "trade_date"], how="left")
    band = pl.when(c("code").cast(pl.Utf8).str.starts_with("3") | c("code").cast(pl.Utf8).str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    muted = (c("pct_chg") >= -1.0) & (c("pct_chg") <= 2.0) & ~(c("close") >= ul - 0.005).fill_null(False)
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    return df.with_columns(
        (c("liq") & c("inc").is_not_null() & muted & vol).fill_null(False).alias("DINCUVOL"),
        (c("inc").fill_null(0) - tie).alias("sc_dincuvol"),
    ).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("DINCUVOL/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("DINCUVOL").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "DINCUVOL", "sc_dincuvol", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
