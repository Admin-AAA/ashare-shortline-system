"""延伸检验：把已验证的 ivol top20% 硬否决（L-093）的 regime 门控从「强牛」扩到其他 regime /
全部 regime，三窗生产同构 + 真实递补。目的：判断 v3 只在强牛生效是否是"太保守"。
臂：v2 基线上，对 regime ∈ {强牛} / {强牛,偏多} / {强牛,偏多,震荡} / ALL 做同一 ivol 否决。
只读。
"""
from __future__ import annotations
import sys, json, warnings
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.services.risk_features import load_ivol20_pct_map
from app.services.backtest.policy_experiment import _simulate_card
from app.engine.backtest import ExecutionPolicy
from app.engine.production_policy import load_production_selection_policy
from scripts.research_canonical_two_window import _bootstrap_ci
from scripts.research_riskveto_canonical import frozen_groups, canon_groups, _metrics, _run_window

OUT = B / "output" / "research"
THR = 0.80
GATES = {
    "G_strong": ("强牛",),
    "G_strong_bull": ("强牛", "偏多"),
    "G_strong_bull_range": ("强牛", "偏多", "震荡"),
    "G_all": None,
    "G_bullish_only": ("偏多",),
    "G_weak": ("弱势",),
    "G_range": ("震荡",),
}


def main():
    ep = ExecutionPolicy()
    pol = load_production_selection_policy(version=2)  # 引擎内无 ivol，否决全部外置以保证臂间可比
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    all_dates = sorted(set(f_dates) | set(w2_dates) | set(w3_dates))
    from app.db.session import SessionLocal
    with SessionLocal() as s:
        pctmap = load_ivol20_pct_map(s, all_dates)
    print(f"pctmap {len(pctmap):,}")

    res = {}
    for nm, dates, groups in (("W1_frozen", f_dates, f_groups),
                              ("W2_forward", w2_dates, w2_groups),
                              ("W3_third", w3_dates, w3_groups)):
        print(f"\n########## {nm} ##########")
        res[nm] = {}
        for gname, gate in GATES.items():
            base, veto, rem, pro = _run_window(groups, pctmap, THR, pol, ep, gate_base=gate)
            mb, mv = _metrics(base, dates), _metrics(veto, dates)
            j = pd.concat([mb["g"].rename("b"), mv["g"].rename("v")], axis=1).fillna(0.0)
            dd = (j.v - j.b).dropna()
            lo, hi = _bootstrap_ci(dd.values, draws=5000)
            mo = dd.groupby(dd.index.str[:7]).mean()
            res[nm][gname] = dict(n=mv["n"], avg=mv["avg"], win=mv["win"], pf=float(mv["pf"]),
                                  cohort=mv["cohort"], d_cohort=float(mv["cohort"] - mb["cohort"]),
                                  ci=[lo, hi], n_removed=int(len(rem)),
                                  removed_mean=float(rem.mean()) if len(rem) else None,
                                  promoted_mean=float(pro.mean()) if len(pro) else None,
                                  month_pos=float((mo > 0).mean()), nmonths=int(len(mo)))
            print(f"  {gname:<22} 砍n{len(rem):>4}(均{rem.mean() if len(rem) else float('nan'):+7.3f}) "
                  f"递补n{len(pro):>4}(均{pro.mean() if len(pro) else float('nan'):+7.3f}) "
                  f"Δcohort{res[nm][gname]['d_cohort']:>+8.4f} CI[{lo:+.4f},{hi:+.4f}] 月正{(mo>0).mean():.2f}")
    (OUT / "ivol_gate_breadth.json").write_text(json.dumps(res, ensure_ascii=False, indent=2,
        default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print("\nsaved output/research/ivol_gate_breadth.json")


if __name__ == "__main__":
    main()
