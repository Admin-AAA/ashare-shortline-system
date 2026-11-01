"""§367 IMP_h20：资产减值 impai_ttm 同比下降。预注册 docs §367。"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
NAME, HOLD, K, MAX_NEW = "IMP_h20", 20, 20, 5
DROP_MIN, Q_LO, Q_HI, PREV_LO, PREV_HI, DROP_CAP = 0.5, 0.0, 30.0, 0.0, 50.0, 10.0
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def fina_events(td):
    cols = ["ts_code", "ann_date", "end_date", "impai_ttm", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("impai_ttm", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "impai_ttm"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    same = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("impai_ttm").alias("prev_q"),
    )
    d = d.join(same, on=["ts_code", "end_date"], how="left")
    d = d.with_columns((c("prev_q") - c("impai_ttm")).alias("dq")).filter(
        c("prev_q").is_not_null()
        & (c("dq") >= DROP_MIN)
        & (c("impai_ttm") >= Q_LO)
        & (c("impai_ttm") <= Q_HI)
        & (c("prev_q") >= PREV_LO)
        & (c("prev_q") <= PREV_HI)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(c("dq").max().alias("dq"), c("impai_ttm").max().alias("impai_ttm"))


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = fina_events(td).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = (c("liq") & c("dq").is_not_null()).fill_null(False)
    return df.with_columns(
        sig.alias("IMP"),
        (c("dq").clip(-1e9, DROP_CAP).fill_null(0) - tie).alias("sc_imp"),
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
    print("IMP per year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("IMP").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    outd = os.path.dirname(out) or "."
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "IMP", "sc_imp", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t))
            m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(outd, f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(outd, exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
