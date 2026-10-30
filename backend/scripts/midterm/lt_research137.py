"""§353 回购预案 RP_base vs RP_combo（公告日涨跌幅百分位 + 回购金额/市值百分位等权）。预注册见 docs §353。"""
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
RUNS = (("RP_base", "RP_r05", "sc_r"), ("RP_combo", "RP_r05", "sc_rpc"))


def prep():
    df = prep23()
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    # within RP_r05 day: rank pct_chg and sc_r to [0,1], equal weight
    r1 = pl.when(c("RP_r05")).then(c("pct_chg")).rank().over("trade_date") / pl.when(c("RP_r05")).then(1).sum().over("trade_date")
    r2 = pl.when(c("RP_r05")).then(c("sc_r")).rank().over("trade_date") / pl.when(c("RP_r05")).then(1).sum().over("trade_date")
    df = df.with_columns((r1.fill_null(0) + r2.fill_null(0) - tie).alias("sc_rpc"))
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
    for ph in phases:
        s, e = PH[ph]
        for n, sig, sc in RUNS:
            t, cur = run_real(df, sig, sc, fixed(20), s, e, k_slots=20, max_new=5, max_hold=20)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t))
                m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{n}_{ph.split()[0]}.csv"))
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
