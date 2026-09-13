"""多日广度趋势 risk-off 门控双窗口检验（只读）。

当日 up_ratio abstain 已证伪（冻结反转票为正）。本脚本测"持续走弱"框架：
近 k 日 up_ratio 均值 / 连续弱日数，是否在两窗口都识别出负 cohort 日。
仍只决定出票与否（abstain），不做仓位。
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.aggregator import _market_base, _market_sentiment
from app.services.backtest.policy_experiment import ScoredCandidateSnapshot
from scripts.research_canonical_two_window import build_forward_canonical, build_market_snapshots, _bootstrap_ci, SLEEVE

OUT = B / "output" / "research"


def frozen_panel():
    sel = pd.read_pickle(OUT / "canon_frozen_v2_selected.pkl")
    snap = ScoredCandidateSnapshot.from_payload(json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    rows = []
    for d in snap.days:
        s = d.market_snapshot or {}; n = int(s.get("sample_count") or 0)
        rows.append({"dt": d.signal_date, "up": (s.get("up_count") or 0) / n if n else np.nan})
    br = pd.DataFrame(rows).set_index("dt").sort_index()
    co = sel.groupby("dt").net.mean()
    return br.join(co.rename("cohort"))


def forward_panel():
    sel = pd.read_pickle(OUT / "canon_forward_v2_selected.pkl")
    fw = build_forward_canonical(); dates = sorted(fw.dt.unique())
    snaps = build_market_snapshots(dates)
    rows = []
    for d in dates:
        s = snaps.get(d)
        if not s: continue
        n = int(s.get("sample_count") or 0)
        rows.append({"dt": d, "up": s["up_count"] / n})
    br = pd.DataFrame(rows).set_index("dt").sort_index()
    co = sel.groupby("dt").net.mean()
    return br.join(co.rename("cohort"))


def scan(panel, name):
    p = panel.copy()
    p["ticket"] = p.cohort.notna()
    for k in (3, 5):
        p[f"ma{k}"] = p.up.rolling(k, min_periods=1).mean()
    print(f"\n#### {name}（仅出票日，n={p.ticket.sum()}）####")
    for col in ("ma3", "ma5"):
        for lo, hi, lab in [(0,.30,"<.30"),(.30,.38,".30-.38"),(.38,.45,".38-.45"),(.45,.55,".45-.55"),(.55,1.01,">=.55")]:
            g = p[p.ticket & (p[col]>=lo) & (p[col]<hi)]
            if len(g):
                print(f"  {col} {lab:9s} days{len(g):>3} cohort{g.cohort.mean():+.4f} winDay{(g.cohort>0).mean():.2f}")
    # abstain 规则：ma5<thr 置空仓，配对 CI
    for col in ("ma3", "ma5"):
        for thr in (.30,.33,.35,.38):
            m = p[p.ticket].copy()
            mask = m[col] < thr
            alt = m.cohort.where(~mask, 0.0); chg = (alt-m.cohort)[mask]
            lo,hi = _bootstrap_ci(chg.values) if len(chg) else (np.nan,np.nan)
            eq=(1+m.cohort*SLEEVE/100).cumprod(); eq2=(1+alt*SLEEVE/100).cumprod()
            print(f"  abstain {col}<{thr}: days{int(mask.sum())} 被砍日均{m.cohort[mask].mean() if mask.sum() else 0:+.3f} "
                  f"cum{(eq.iloc[-1]-1)*100:+.2f}->{(eq2.iloc[-1]-1)*100:+.2f} CI[{lo:+.3f},{hi:+.3f}]")


def main():
    scan(frozen_panel(), "FROZEN")
    scan(forward_panel(), "FORWARD")


if __name__ == "__main__":
    main()
