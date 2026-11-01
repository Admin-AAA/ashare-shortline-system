"""§646 TDVOL_i15_h20：营业周期∩放量缩短软扩≥15，同进程 base(≥20)。预注册 docs §646。"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
HOLD, K, MAX_NEW = 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
TD_LO, TD_HI, PREV_HI, TD_CAP = 30.0, 400.0, 800.0, 200.0


def td_events(td, improve_min):
    cols = ["ts_code", "ann_date", "end_date", "turn_days", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("turn_days", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "turn_days"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("turn_days").alias("prev_td"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("prev_td") - c("turn_days")).alias("improve")).filter(
        c("prev_td").is_not_null()
        & (c("improve") >= improve_min)
        & (c("turn_days") >= TD_LO) & (c("turn_days") <= TD_HI)
        & (c("prev_td") >= TD_LO) & (c("prev_td") <= PREV_HI)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(c("improve").max().alias("improve")).rename({"sd": "trade_date"})


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    soft = td_events(td, 15.0).rename({"improve": "imp15"})
    base = td_events(td, 20.0).rename({"improve": "imp20"})
    df = df.join(soft, on=["code", "trade_date"], how="left").join(base, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    return df.with_columns(
        (c("liq") & c("imp20").is_not_null() & vol).fill_null(False).alias("TDVOL_base"),
        (c("liq") & c("imp15").is_not_null() & vol).fill_null(False).alias("TDVOL_i15"),
        (c("imp15").clip(-1e9, TD_CAP).fill_null(0) - tie).alias("sc_tdvol"),
    ).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


def one(df, sig, sc, name, ph, s, e, out, res):
    t, cur = run_real(df, sig, sc, fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
    m = full_metrics(df, t, cur)
    if t is not None and t.height:
        m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
        t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
    res.setdefault(name, {})[ph] = m
    print(name, ph, m, flush=True)


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("TDVOL/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(
        c("TDVOL_base").sum(), c("TDVOL_i15").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "TDVOL_base", "sc_tdvol", "TDVOL_base", ph, s, e, out, res)
        one(df, "TDVOL_i15", "sc_tdvol", "TDVOL_i15_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
