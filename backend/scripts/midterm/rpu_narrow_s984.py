"""§984 RPU20 窄过滤：nold / pct_core。用法: python rpu_narrow_s984.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research23 as R23
c = pl.col
HOLD, K, MAX_NEW = 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = R23.prep().sort("code", "trade_date")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    dl = pl.coalesce(c("down_limit"), c("close").shift(1).over("code") * (1 - band))
    muted = (c("pct_chg") >= -1.0) & (c("pct_chg") <= 2.0) & ~(c("close") >= ul - 0.005).fill_null(False)
    base = (c("RP_r05") & muted).fill_null(False)
    # prev day limit-down close
    prev_ld = ((c("close").shift(1).over("code") <= dl.shift(1).over("code") + 0.005) |
               (c("pct_chg").shift(1).over("code") <= -band * 100 + 0.05)).fill_null(False)
    nold = (base & ~prev_ld).fill_null(False)
    core = (c("RP_r05") & (c("pct_chg") >= -0.5) & (c("pct_chg") <= 1.5) & ~(c("close") >= ul - 0.005).fill_null(False)).fill_null(False)
    return df.with_columns(
        base.alias("RPU20"), nold.alias("RPU_nold"), core.alias("RPU_core"),
        (-c("pct_chg").fill_null(0) - tie).alias("sc_pct"),
    ).sort("code", "trade_date")


def one(df, sig, sc, name, ph, s, e, out, res):
    t, cur = run_real(df, sig, sc, fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
    m = full_metrics(df, t, cur)
    if t is not None and t.height:
        m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
        t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
    res.setdefault(name, {})[ph] = m
    print(name, ph, m, flush=True)


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "RPU20", "sc_pct", "RPU20_base", ph, s, e, out, res)
        one(df, "RPU_nold", "sc_pct", "RPU_nold_h20", ph, s, e, out, res)
        one(df, "RPU_core", "sc_pct", "RPU_core_h20", ph, s, e, out, res)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
