"""§774 EACASVOL_o25_h20：G 内 orv硬抬≥25（EA∩CAS 现役），同进程 orv≥20 base；sc_g。预注册 docs §774。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research122 as S122
import lt_research89 as R89
c = pl.col
HOLD, K, MAX_NEW = 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = S122.prep()
    cas = R89.prep().select(["code", "trade_date", c("CAS").alias("CAS")])
    df = df.join(cas, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    g20 = c("liq") & (c("pdd") > 0) & (c("dtp") >= 50) & (c("orv") >= 20) & (c("dtp") > c("prev_dtp"))
    g25 = c("liq") & (c("pdd") > 0) & (c("dtp") >= 50) & (c("orv") >= 25) & (c("dtp") > c("prev_dtp"))
    cas_ok = c("CAS").fill_null(False)
    return df.with_columns(
        (g20 & cas_ok & vol).fill_null(False).alias("EACASVOL_base"),
        (g25 & cas_ok & vol).fill_null(False).alias("EACASVOL_o25"),
        (c("sc_g") - tie).alias("sc_eacasvol"),
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
    print("base/o25/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(
        c("EACASVOL_base").sum(), c("EACASVOL_o25").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "EACASVOL_base", "sc_eacasvol", "EACASVOL_base", ph, s, e, out, res)
        one(df, "EACASVOL_o25", "sc_eacasvol", "EACASVOL_o25_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
