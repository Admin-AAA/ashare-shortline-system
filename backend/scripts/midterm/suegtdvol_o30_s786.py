"""§786 SUEGTDVOL_o30_h20：G 内 orv硬抬≥30（SUEG∩TD 现役 orv≥25 dtp≥55），同进程 orv≥25 base；sc_sue。预注册 docs §786。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research122 as S122
import lt_research61 as R61
c = pl.col
HOLD, K, MAX_NEW = 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = S122.prep()
    td = R61.prep().select(["code", "trade_date", c("TD").alias("TD")])
    df = df.join(td, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    sue22 = c("liq") & (c("sue") >= 2.2) & (c("q_sq") > 0)
    # 现役 §782：dtp≥55 orv≥25；本题抬 orv→30
    g25 = (c("pdd") > 0) & (c("dtp") >= 55) & (c("orv") >= 25) & (c("dtp") > c("prev_dtp"))
    g30 = (c("pdd") > 0) & (c("dtp") >= 55) & (c("orv") >= 30) & (c("dtp") > c("prev_dtp"))
    td_ok = c("TD").fill_null(False)
    return df.with_columns(
        (sue22 & g25 & td_ok & vol).fill_null(False).alias("SUEGTDVOL_base"),
        (sue22 & g30 & td_ok & vol).fill_null(False).alias("SUEGTDVOL_o30"),
        (c("sc_sue") - tie).alias("sc_suegtdvol"),
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
    print("base/o30/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(
        c("SUEGTDVOL_base").sum(), c("SUEGTDVOL_o30").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "SUEGTDVOL_base", "sc_suegtdvol", "SUEGTDVOL_base", ph, s, e, out, res)
        one(df, "SUEGTDVOL_o30", "sc_suegtdvol", "SUEGTDVOL_o30_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
