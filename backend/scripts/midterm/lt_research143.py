"""§365 回购预案 RP_base vs RP_mv_combo（金额/市值百分位 + 小市值百分位等权）。预注册 docs §365。"""
from __future__ import annotations
import json, os, sys
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research23 import prep as prep23

c = pl.col
PH = dict(PH)
PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
RUNS = (("RP_base", "RP_r05", "sc_r"), ("RP_mv_combo", "RP_r05", "sc_rpmv"))


def prep():
    df = prep23()
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    r1 = pl.when(c("RP_r05")).then(c("sc_r")).rank().over("trade_date") / pl.when(c("RP_r05")).then(1).sum().over("trade_date")
    r2 = pl.when(c("RP_r05")).then(-c("total_mv")).rank().over("trade_date") / pl.when(c("RP_r05")).then(1).sum().over("trade_date")
    df = df.with_columns((r1.fill_null(0) + r2.fill_null(0) - tie).alias("sc_rpmv"))
    return df.sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    outd = os.path.dirname(out) or "."
    for ph in phases:
        s, e = PH[ph]
        for n, sig, sc in RUNS:
            t, cur = run_real(df, sig, sc, fixed(20), s, e, k_slots=20, max_new=5, max_hold=20)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t))
                m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                t.write_csv(os.path.join(outd, f"trades_{n}_{ph.split()[0]}.csv"))
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        os.makedirs(outd, exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
