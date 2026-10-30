"""§RV-A~F 引擎口径（docs/reviews/mid-loss-review-strategy-fix-2026-10-30.md，预注册 e77578b1，规则不改）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python rv1030_cand.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research20 import prep as prep20
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
RUNS = (("EA_base", "G", "sc_g"), ("EA_d0", "G", "sc_d0"), ("EA_mv", "G", "sc_mv"), ("EA_combo", "G", "sc_cbG"),
        ("EA_first", "G_first", "sc_g"), ("FA_base", "FA", "sc_a"), ("FA_mv", "FA", "sc_mv"), ("FA_d0", "FA", "sc_d0"))


def prep():
    df = prep20()
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    df = df.with_columns((c("pct_chg").fill_null(0) / 100 - tie).alias("sc_d0"),
                         (-c("total_mv").fill_null(1e12) - tie).alias("sc_mv"),
                         (c("G") & (c("prev_dtp") <= 50)).fill_null(False).alias("G_first"))
    for s in ("G", "FA"):
        r1 = pl.when(c(s)).then(c("pct_chg")).rank().over("trade_date") / pl.when(c(s)).then(1).sum().over("trade_date")
        r2 = pl.when(c(s)).then(-c("total_mv")).rank().over("trade_date") / pl.when(c(s)).then(1).sum().over("trade_date")
        df = df.with_columns((r1.fill_null(0) + r2.fill_null(0) - tie).alias(f"sc_cb{s}"))
    return df.sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
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
                m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{n}_{ph.split()[0]}.csv"))
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
