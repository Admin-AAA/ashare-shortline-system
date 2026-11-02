"""§780 SUEGVOL_o25_h20：G 内 orv硬抬≥25（SUEG∩放量现役），同进程 orv≥20 base；sc_sue。预注册 docs §780。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research122 as S122
c = pl.col
HOLD, K, MAX_NEW = 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = S122.prep()
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    sue = c("liq") & (c("sue") >= 2.2) & (c("q_sq") > 0)
    g20 = (c("pdd") > 0) & (c("dtp") >= 50) & (c("orv") >= 20) & (c("dtp") > c("prev_dtp"))
    g25 = (c("pdd") > 0) & (c("dtp") >= 50) & (c("orv") >= 25) & (c("dtp") > c("prev_dtp"))
    return df.with_columns(
        (sue & g20 & vol).fill_null(False).alias("SUEGVOL_base"),
        (sue & g25 & vol).fill_null(False).alias("SUEGVOL_o25"),
        (c("sc_sue") - tie).alias("sc_suegvol"),
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
        c("SUEGVOL_base").sum(), c("SUEGVOL_o25").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "SUEGVOL_base", "sc_suegvol", "SUEGVOL_base", ph, s, e, out, res)
        one(df, "SUEGVOL_o25", "sc_suegvol", "SUEGVOL_o25_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
