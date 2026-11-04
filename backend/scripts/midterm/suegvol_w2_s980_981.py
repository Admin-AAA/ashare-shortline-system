"""§980/§981 SUEGVOL 同 id 改进（预注册 docs §980/§981）。用法：python suegvol_w2_s980_981.py <r20c|combo> "<phase>" out.json"""
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
    sue22 = c("liq") & (c("sue") >= 2.2) & (c("q_sq") > 0)
    base = (sue22 & c("G") & vol).fill_null(False)
    df = df.with_columns(base.alias("BASE"), (c("sc_sue") - tie).alias("sc_sue"))
    df = df.with_columns((c("BASE") & (c("r20") <= 0.30).fill_null(False)).alias("R20C"))
    r1 = pl.when(c("BASE")).then(c("pct_chg")).rank().over("trade_date") / pl.when(c("BASE")).then(1).sum().over("trade_date")
    r2 = pl.when(c("BASE")).then(-c("total_mv")).rank().over("trade_date") / pl.when(c("BASE")).then(1).sum().over("trade_date")
    return df.with_columns((r1.fill_null(0) + r2.fill_null(0) - tie).alias("sc_combo")).sort("code", "trade_date")


def one(df, sig, sc, name, ph, s, e, out, res):
    t, cur = run_real(df, sig, sc, fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
    m = full_metrics(df, t, cur)
    if t is not None and t.height:
        m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
        m["median"] = round(100 * float(t["ret"].median()), 2)
        t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
    res.setdefault(name, {})[ph] = m
    print(name, ph, m, flush=True)


if __name__ == "__main__":
    mode, phases, out = sys.argv[1], sys.argv[2].split(","), sys.argv[3]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "BASE", "sc_sue", "SUEGVOL_base", ph, s, e, out, res)
        if mode == "r20c":
            one(df, "R20C", "sc_sue", "SUEGVOL_r20c_h20", ph, s, e, out, res)
        else:
            one(df, "BASE", "sc_combo", "SUEGVOL_combo_h20", ph, s, e, out, res)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
