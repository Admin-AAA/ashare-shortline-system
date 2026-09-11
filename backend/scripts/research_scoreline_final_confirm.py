"""最终确认：保守规范版 vs P0，配对 bootstrap CI + topN 敏感性 + 逐月（只读）。"""
from __future__ import annotations
import sys, json
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_intraday_portfolio_levers import _metric_summary, _bootstrap_mean_ci  # type: ignore
from scripts.research_scoreline_two_window import frozen_panel, forward_panel, P0  # type: ignore

OUT = Path(__file__).resolve().parents[1] / "output" / "research"
ALL = pd.concat([frozen_panel(), forward_panel()], ignore_index=True).assign(
    key=lambda d: d.base + "·" + d.sent)
NEW = dict(P0); NEW["弱势·高潮"] = 85; NEW["强牛·活跃"] = 72; NEW["偏多·活跃"] = 72.0


def daily(df, lm, topn):
    rows = []
    for (date, win), gp in df.groupby(["signal_date", "window"]):
        eff = gp.key.map(lm).fillna(72.0)
        g = gp.assign(eff=eff)
        top = g[g.score >= g.eff].sort_values("score", ascending=False).head(topn)
        rows.append({"date": date, "win": win, "b": top.net.mean() if len(top) else 0.0, "n": len(top)})
    return pd.DataFrame(rows)


def boot_delta(a, b, n=5000, seed=7):
    # 配对日度差（new - P0），bootstrap 均值 CI
    diff = (b.set_index("date").b - a.set_index("date").b).dropna()
    rng = np.random.default_rng(seed); vals = diff.to_numpy()
    bs = [rng.choice(vals, len(vals), replace=True).mean() for _ in range(n)]
    return diff.mean(), np.percentile(bs, [2.5, 97.5]), (diff > 1e-9).sum(), (diff < -1e-9).sum()


for topn in [4, 6, 8]:
    print(f"\n########## top{topn} ##########")
    a = daily(ALL, P0, topn); b = daily(ALL, NEW, topn)
    for win in ["frozen", "forward"]:
        aa, bb = a[a.win == win], b[b.win == win]
        ma, mb = _metric_summary(aa.b), _metric_summary(bb.b)
        dm, ci, up, dn = boot_delta(aa, bb)
        print(f"{win:8s} P0 cum{ma['cumulative_pct']:+7.2f} MDD{ma['max_drawdown_pct']:6.2f} → "
              f"NEW cum{mb['cumulative_pct']:+7.2f} MDD{mb['max_drawdown_pct']:6.2f} | "
              f"日均Δ{dm:+.4f} CI[{ci[0]:+.4f},{ci[1]:+.4f}] 改善日{up}/受损日{dn}")

# top6 逐月对照（最终）
print("\n===== top6 逐月 P0→NEW =====")
a = daily(ALL, P0, 6); b = daily(ALL, NEW, 6)
for win in ["frozen", "forward"]:
    ma = a[a.win == win].set_index("date").b; mb = b[b.win == win].set_index("date").b
    c = pd.DataFrame({"P0": ma.groupby(ma.index.str[:7]).mean(),
                      "NEW": mb.groupby(mb.index.str[:7]).mean()})
    c["delta"] = (c.NEW - c.P0).round(3)
    print(f"\n{win}:\n", c.round(3).to_string())
# 出票变化
ta = daily(ALL, P0, 6); tb = daily(ALL, NEW, 6)
print("\n出票天数 P0/NEW:",
      {w: (int((ta[ta.win==w].n>0).sum()), int((tb[tb.win==w].n>0).sum())) for w in ["frozen","forward"]})
