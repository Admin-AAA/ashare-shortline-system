"""[长线] round 4: ROE quality + value (PIT fina via ann_date). Pre-declared 4 variants, no grid."""
import sys, json
import polars as pl
import lt_data as L
from lt_research import build, fixed, PH
from realistic import run_real, metrics_real
c = pl.col
def add_fina(df):
    f = L.fina_pit().select("code", "trade_date", c("roe_ann").cast(pl.Float64, strict=False), c("debt_to_assets").cast(pl.Float64, strict=False),
                            c("netprofit_yoy").cast(pl.Float64, strict=False), c("ocfps").cast(pl.Float64, strict=False)).rename({"trade_date": "fd"})
    f = f.with_columns(c("fd").alias("trade_date")).sort("trade_date")
    # ann_date may be after close -> only usable from next day: shift availability by requiring fd < trade_date
    df = df.sort("trade_date").join_asof(f, on="trade_date", by="code", strategy="backward", allow_exact_matches=False)
    return df.sort("code", "trade_date")
def sig(df):
    base = c("liq") & c("roe_ann").is_not_null() & (c("pe_ttm") > 0) & (c("pb") > 0)
    q = base & (c("roe_ann") >= 15) & (c("debt_to_assets") < 60) & (c("ocfps") > 0)
    v = q & (c("pe_ttm") <= 25)
    ep = pl.when(v).then(1 / c("pe_ttm")).rank().over("trade_date"); rq = pl.when(v).then(c("roe_ann")).rank().over("trade_date")
    df = df.with_columns((ep + rq).alias("sc_rq"), pl.when(v).then(c("roe_ann") / c("pb")).alias("sc_rpb"))
    def top(cond, sc, n=20): return (c("mon") & (pl.when(cond).then(-c(sc)).rank("ordinal").over("trade_date") <= n)).fill_null(False)
    return df.with_columns(top(v, "sc_rq").alias("S_rq"), top(v, "sc_rpb").alias("S_rpb"),
                           top(v & (c("ac") > c("m250")), "sc_rq").alias("S_rqt"),
                           top(v & (c("netprofit_yoy") > 0), "sc_rq").alias("S_rqg"))
C = {"roe_ep": ("S_rq", "sc_rq"), "roe_over_pb": ("S_rpb", "sc_rpb"), "roe_ep_trend250": ("S_rqt", "sc_rq"), "roe_ep_growth": ("S_rqg", "sc_rq")}
if __name__ == "__main__":
    df = sig(add_fina(build())); res = {}
    for n, (s_, sc) in C.items():
        print(f"## {n}", flush=True)
        for ph, (s, e) in PH.items():
            t, cur = run_real(df, s_, sc, fixed(20), s, e, k_slots=20, max_new=20, max_hold=20)
            m = metrics_real(t, cur); res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
            print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in m.items()), flush=True)
    json.dump(res, open("/tmp/lt4.json", "w"), indent=1)
