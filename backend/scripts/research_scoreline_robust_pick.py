"""对三个跨窗口稳健杠杆做小网格 + 逐月分解，选非刀刃参数（只读）。

杠杆：A 弱势·高潮 线（拦毒区）；B 强牛·活跃 64→?（两窗口都在72更优）；C 偏多·活跃 71.4→?（前瞻全负）。
约束：两窗口 cum/MDD 都不劣于 P0，且逐月改善是广谱的（非少数日）。
"""
from __future__ import annotations
import sys, itertools, json
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_intraday_portfolio_levers import _metric_summary, TOP_N  # type: ignore
from scripts.research_scoreline_two_window import frozen_panel, forward_panel, P0  # type: ignore

OUT = Path(__file__).resolve().parents[1] / "output" / "research"
ALL = pd.concat([frozen_panel(), forward_panel()], ignore_index=True).assign(
    key=lambda d: d.base + "·" + d.sent)


def daily(df, lm):
    rows = []
    for (date, win), gp in df.groupby(["signal_date", "window"]):
        eff = gp.key.map(lm).fillna(72.0)
        g = gp.assign(eff=eff)
        top = g[g.score >= g.eff].sort_values("score", ascending=False).head(TOP_N)
        rows.append({"date": date, "win": win, "b": top.net.mean() if len(top) else 0.0, "n": len(top)})
    return pd.DataFrame(rows)


def perf(d, win):
    x = d[d.win == win]; m = _metric_summary(x.b)
    mon = x.assign(m=x.date.str[:7]).groupby("m").b.mean()
    return m["cumulative_pct"], m["max_drawdown_pct"], round((mon > 0).mean(), 2), round(m["avg_cohort_return_pct"], 4)


base_d = daily(ALL, P0)
f0 = perf(base_d, "frozen"); w0 = perf(base_d, "forward")
print(f"P0 baseline: frozen cum{f0[0]:+.2f} MDD{f0[1]:.2f} 月正{f0[2]} | forward cum{w0[0]:+.2f} MDD{w0[1]:.2f} 月正{w0[2]}\n")

results = []
for wc in [85]:
    for qn in [68, 70, 72, 74]:
        for da in [72, 74, 76, 80]:
            lm = dict(P0); lm["弱势·高潮"] = wc; lm["强牛·活跃"] = qn; lm["偏多·活跃"] = da
            d = daily(ALL, lm)
            f = perf(d, "frozen"); w = perf(d, "forward")
            pareto = f[0] >= f0[0]-0.3 and w[0] >= w0[0] and f[1] >= f0[1]-0.3 and w[1] >= w0[1]
            results.append((wc, qn, da, f, w, pareto))
results.sort(key=lambda r: (-(r[3][0] + r[4][0]), r[3][1] + r[4][1]))
print(f"{'wc':>3}{'强牛活':>6}{'偏多活':>6} | {'frozCum':>8}{'fMDD':>7} | {'fwdCum':>8}{'wMDD':>7} | Pareto")
for wc, qn, da, f, w, p in results:
    print(f"{wc:>3}{qn:>6}{da:>6} | {f[0]:>+8.2f}{f[1]:>7.2f} | {w[0]:>+8.2f}{w[1]:>7.2f} | {'✓' if p else ''}")

# 选综合最优的 Pareto 组合做逐月分解
best = [r for r in results if r[5]]
pick = best[0] if best else results[0]
wc, qn, da = pick[0], pick[1], pick[2]
lm = dict(P0); lm["弱势·高潮"] = wc; lm["强牛·活跃"] = qn; lm["偏多·活跃"] = da
d = daily(ALL, lm)
print(f"\n选定: 弱势·高潮={wc} 强牛·活跃={qn} 偏多·活跃={da}")
for win in ["frozen", "forward"]:
    a = base_d[base_d.win == win].set_index("date").b
    b = d[d.win == win].set_index("date").b
    mon_a = a.groupby(a.index.str[:7]).mean(); mon_b = b.groupby(b.index.str[:7]).mean()
    cmp = pd.DataFrame({"P0": mon_a, "new": mon_b}); cmp["delta"] = cmp.new - cmp.P0
    improved_months = (cmp.delta > 1e-9).sum(); hurt_months = (cmp.delta < -1e-9).sum()
    print(f"\n{win}: 逐月 P0→new，改善月{improved_months} 受损月{hurt_months} 持平{len(cmp)-improved_months-hurt_months}")
    print(cmp.round(3).to_string())
(OUT / "scoreline_robust_pick.json").write_text(json.dumps(
    {"weak_climax": wc, "strong_active": qn, "duo_active": da,
     "frozen": {"cum": pick[3][0], "mdd": pick[3][1]}, "forward": {"cum": pick[4][0], "mdd": pick[4][1]}}, ensure_ascii=False, indent=2))
