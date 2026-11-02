"""§745 NCSSUEGVOL_h20：NCS ∩ SUEG ∩ 放量；排序 sc_ncs。预注册 docs §745。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research98 as R98
import lt_research122 as S122
c = pl.col
NAME, HOLD, K, MAX_NEW = "NCSSUEGVOL_h20", 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = R98.prep()
    sue = S122.prep().select(["code", "trade_date", c("sue").alias("sue"), c("q_sq").alias("q_sq"), c("G").alias("G"), c("liq").alias("liq_sue")])
    df = df.join(sue, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    sueg = c("liq_sue").fill_null(False) & (c("sue") >= 2.2) & (c("q_sq") > 0) & c("G").fill_null(False)
    return df.with_columns(
        (c("NCS") & sueg & vol).fill_null(False).alias("NCSSUEGVOL"),
        (c("sc_ncs") - tie).alias("sc_ncssuegvol"),
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
    print("NCSSUEGVOL/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(
        c("NCSSUEGVOL").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "NCSSUEGVOL", "sc_ncssuegvol", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
