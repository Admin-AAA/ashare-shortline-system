"""rv1030 诊断报表：分组（五分位，切点只用开发期 2019–2023 定）平均收益/超额 + 各年稳定性。
用法：python rv1030_diag_report.py EA|FA dev|oos"""
import sys, os
import polars as pl
c = pl.col
S, MODE = sys.argv[1], sys.argv[2]
D = os.path.join(os.path.dirname(__file__), "../../output/research/rv1030")
x = pl.read_parquet(os.path.join(D, f"sig_{S}.parquet")).filter(~c("lu_e").fill_null(False) & c("fwd").is_not_null())
x = x.with_columns(c("trade_date").str.slice(0, 4).alias("y"))
dev = x.filter(c("trade_date").is_between(pl.lit("2019-01-01"), pl.lit("2023-12-31")) & ~c("partial"))
NUM = ["pre_r20", "pre_r5", "d0", "r20", "r60", "pos250", "pos60", "up250", "pe_rank", "mv_rank", "pb", "vol120", "ind_rel20",
       "mkt_r20", "ld_cnt", "ld5", "lu_cnt", "gap_e", "score"]
NUM += {"EA": ["dtp", "orv", "prev_dtp", "accel", "base_mv", "pdd_mv", "dedt_share", "ocf_eps", "npy", "qsy", f"nsig_{S}"],
        "FA": ["pcmin", "pc_width", "lpn_mv", "np_mv", f"nsig_{S}"]}[S]
CAT = ["mkt_ma20", "mkt_ma60", "cap300", "rpt"] + (["fa_type"] if S == "FA" else [])
if MODE == "dev":
    per = [("ALL", dev)] + [(y, dev.filter(c("y") == y)) for y in ("2019", "2020", "2021", "2022", "2023")]
else:
    cm = ~c("partial")
    per = [("2024", x.filter(c("y") == "2024")), ("2025-01~09", x.filter(c("trade_date").is_between(pl.lit("2025-01-01"), pl.lit("2025-09-30")))),
           ("R12", x.filter(c("trade_date").is_between(pl.lit("2025-10-01"), pl.lit("2026-10-16")) & cm)),
           ("R3m", x.filter(c("trade_date").is_between(pl.lit("2026-07-16"), pl.lit("2026-10-16")) & cm)),
           ("LIVE09-18+", x.filter(c("trade_date") >= "2026-09-18"))]
print(f"#### {S} {MODE}  n_dev={dev.height} periods=" + " ".join(f"{a}:{b.height}" for a, b in per))
for nm, p in per:
    if p.height: print(f"  {nm}: n={p.height} avg={100*p['fwd'].mean():+.2f} win={100*(p['fwd']>0).mean():.1f} ex={100*p['ex'].mean():+.2f}")
rows = []
for f in NUM + CAT:
    if f not in x.columns: continue
    if f in NUM:
        v = dev[f].drop_nulls().drop_nans() if dev[f].dtype.is_float() else dev[f].drop_nulls()
        if v.n_unique() < 5: continue
        qs = [float(v.quantile(q)) for q in (0.2, 0.4, 0.6, 0.8)]
        bexpr = (pl.when(c(f).is_null()).then(pl.lit("na")).when(c(f) <= qs[0]).then(pl.lit("Q1")).when(c(f) <= qs[1]).then(pl.lit("Q2"))
                 .when(c(f) <= qs[2]).then(pl.lit("Q3")).when(c(f) <= qs[3]).then(pl.lit("Q4")).otherwise(pl.lit("Q5")))
        cut = "切点 " + "/".join(f"{q:.3g}" for q in qs)
    else:
        bexpr = c(f).cast(pl.Utf8).fill_null("na"); cut = ""
    print(f"\n== {f} {cut}")
    spreads = []
    for nm, p in per:
        if p.height == 0: continue
        g = p.with_columns(bexpr.alias("b")).group_by("b").agg(pl.len().alias("n"), (100 * c("fwd").mean()).round(2).alias("avg"),
                                                             (100 * (c("fwd") > 0).mean()).round(1).alias("win"), (100 * c("ex").mean()).round(2).alias("ex")).sort("b")
        d = {r["b"]: r for r in g.to_dicts()}
        print(f"  {nm:11s} " + " | ".join(f"{b}:{r['n']} {r['avg']:+.2f}/{r['ex']:+.2f}/{r['win']:.0f}" for b, r in d.items()))
        if f in NUM and "Q1" in d and "Q5" in d: spreads.append((nm, round(d["Q5"]["ex"] - d["Q1"]["ex"], 2)))
    if spreads:
        s0 = spreads[0][1]; cons = sum((s > 0) == (s0 > 0) for _, s in spreads[1:])
        print(f"  Q5-Q1 超额差: " + " ".join(f"{a}{b:+.2f}" for a, b in spreads) + f"  同号 {cons}/{len(spreads)-1}")
        rows.append((f, s0, cons, len(spreads) - 1))
print("\n#### 排名（按 |Q5−Q1| 开发期合并超额差，带同号年数）")
for f, s, k, n in sorted(rows, key=lambda r: -abs(r[1])):
    print(f"  {f:12s} {s:+6.2f}  同号 {k}/{n}")
