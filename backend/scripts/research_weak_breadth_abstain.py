"""极弱广度 abstain 门控的双窗口正式检验（只读，基于 canonical 选中帧）。

假设 H：v2 出票日里，up_ratio<T（极弱广度，弱势中的危机档）日 cohort 两窗口同向为负，
对这些日 abstain（置0票=现金）能改善风险调整后收益。只决定出不出票，不做仓位。
要求：两窗口同向、配对日差 bootstrap95%CI 下界>0、被砍日/票机会成本为负、阈值不敏感。
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


def frozen_breadth_cohort():
    sel = pd.read_pickle(OUT / "canon_frozen_v2_selected.pkl")
    snap = ScoredCandidateSnapshot.from_payload(
        json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    br = {}
    for d in snap.days:
        s = d.market_snapshot or {}; n = int(s.get("sample_count") or 0)
        br[d.signal_date] = {"up_ratio": (s.get("up_count") or 0) / n if n else np.nan,
                             "ld": s.get("limit_down_count") or 0,
                             "base": _market_base(s), "sent": _market_sentiment(s)}
    co = sel.groupby("dt").agg(cohort=("net", "mean"), n=("net", "size"))
    df = pd.DataFrame(br).T.join(co, how="right")
    return df.reset_index().rename(columns={"index": "dt"})


def forward_breadth_cohort():
    sel = pd.read_pickle(OUT / "canon_forward_v2_selected.pkl")
    fw = build_forward_canonical(); dates = sorted(fw.dt.unique())
    snaps = build_market_snapshots(dates)
    co = sel.groupby("dt").agg(cohort=("net", "mean"), n=("net", "size"))
    rows = {}
    for d in co.index:
        s = snaps.get(d); n = int(s.get("sample_count") or 0)
        rows[d] = {"up_ratio": s["up_count"] / n, "ld": s["limit_down_count"],
                   "base": _market_base(s), "sent": _market_sentiment(s)}
    return co.join(pd.DataFrame(rows).T).reset_index().rename(columns={"dt": "dt"})


def cum_mdd(co: pd.Series):
    eq = (1 + co * SLEEVE / 100).cumprod()
    return round((eq.iloc[-1] - 1) * 100, 2), round(((eq / eq.cummax() - 1) * 100).min(), 2)


def eval_abstain(df, thr, col="up_ratio"):
    d = df.copy()
    mask = d[col] < thr
    base = d.cohort.copy(); alt = d.cohort.where(~mask, 0.0)
    delta = alt - base  # only nonzero on abstained days
    chg = delta[mask]
    lo, hi = _bootstrap_ci(chg.values) if len(chg) else (np.nan, np.nan)
    c0, m0 = cum_mdd(base); c1, m1 = cum_mdd(alt)
    cut_pos = int((base[mask] > 0).sum()); cut_neg = int((base[mask] < 0).sum())
    return {"thr": thr, "abstain_days": int(mask.sum()),
            "abstain_day_cohort_avg": round(base[mask].mean(), 4) if mask.sum() else 0,
            "cut_pos_days": cut_pos, "cut_neg_days": cut_neg,
            "base_cum": c0, "new_cum": c1, "base_mdd": m0, "new_mdd": m1,
            "delta_ci95": [round(lo, 4), round(hi, 4)]}


def main():
    fz = frozen_breadth_cohort(); fw = forward_breadth_cohort()
    for name, d in [("frozen", fz), ("forward", fw)]:
        print(f"\n########## {name}: v2 出票日 n={len(d)} ##########")
        # 出票日按 up_ratio 分档（只含有出票的日子）
        for lo, hi, lab in [(0,.20,"<.20"),(.20,.25,".20-.25"),(.25,.30,".25-.30"),
                           (.30,.35,".30-.35"),(.35,.50,".35-.50"),(.50,1.01,">=.50")]:
            g = d[(d.up_ratio>=lo)&(d.up_ratio<hi)]
            if len(g):
                print(f"  up {lab:9s} days{len(g):>3} cohort{g.cohort.mean():+.4f} winDay{(g.cohort>0).mean():.2f} tickets{int(g.n.sum())}")
        print("  -- abstain 规则扫描（up_ratio<thr 当天置空仓）--")
        for thr in (.18,.20,.22,.25,.28,.30,.35):
            print("   ", eval_abstain(d, thr))
        # 交叉：up<.25 都是什么 regime·sent
        weak = d[d.up_ratio<.25]
        print("  up<.25 日的 base·sent 分布:", (weak.base+"·"+weak.sent).value_counts().to_dict())
        print("  up<.25 出票日明细 cohort:", [round(x,2) for x in weak.cohort.tolist()])


if __name__ == "__main__":
    main()
