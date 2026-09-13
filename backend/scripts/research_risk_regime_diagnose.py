"""诊断：基线 v2 选中票按 regime单元 × ivol20分位 的净收益交叉表（三窗口，只读）。

目的：ivol 否决在强趋势冻结窗有害、普跌三窗有效，疑似 regime 条件化。本表定位
"高 ivol 票在哪些 regime 单元显著更差（该否决）/更好（该保留）"，为条件化否决取规则。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext
from app.engine.aggregator import _market_base, _market_sentiment
from app.services.backtest.policy_experiment import ScoredCandidateSnapshot, _thaw_json, _simulate_card
from app.engine.backtest import ExecutionPolicy
from scripts.research_canonical_two_window import build_forward_canonical, build_market_snapshots
from scripts.research_third_window import load_run3
from scripts.research_riskveto_canonical import factor_pct_lookup

OUT = B / "output" / "research"


def baseline_rows(groups_iter, pctmap, ep=None):
    """产出每个基线选中行: dt, code, net, base, sent, ivol_pct。"""
    rows = []
    for dt, cards, netmap, dayobj in groups_iter:
        ms = dayobj.market_snapshot
        ctx = SelectionPolicyContext(72., 10, ms)
        pol0 = load_production_selection_policy(version=2)
        sel = evaluate_selection_policy(cards, ctx, pol0).recommendations
        cm = {str(c.get("code")): c for c in cards}
        b = _market_base(ms); se = _market_sentiment(ms)
        for c in sel:
            code = str(c.get("code"))
            if netmap is not None:
                net = netmap.get(code)
            else:
                tr = (_simulate_card(dayobj, cm[code], ep) or {}).get("trade")
                net = float(tr.net_return_pct) if (tr is not None and tr.executed) else None
            if net is None: continue
            rows.append({"dt": dt, "code": code, "net": net, "base": b, "sent": se,
                         "pct": pctmap.get((dt, code), np.nan)})
    return pd.DataFrame(rows)


def frozen_groups():
    import json
    snap = ScoredCandidateSnapshot.from_payload(json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    return [(d.signal_date, _thaw_json(d.cards), None, d) for d in snap.days]


class _Snap:
    def __init__(self, ms): self.market_snapshot = dict(ms)


def canon_groups(which):
    fw = build_forward_canonical() if which == "fwd" else load_run3()
    dates = sorted(fw.dt.unique()); snaps = build_market_snapshots(dates)
    fw = fw[fw.dt.isin([d for d in dates if d in snaps])]
    g = []
    for d, gp in fw.groupby("dt"):
        cards = [{"code": str(r.code), "final_score": float(r.score), "industry": str(r.industry), "signal_price": 10.0}
                 for r in gp.itertuples()]
        netmap = {str(r.code): float(r.net) for r in gp.itertuples()}
        g.append((d, cards, netmap, _Snap(snaps[d])))
    return g


def crosstab(df, name):
    print(f"\n========== {name}  n={len(df)} ==========")
    df = df.dropna(subset=["pct"]).copy()
    df["hi"] = np.where(df.pct >= 0.90, "ivol_top10%", "ivol_rest")
    # 按 base（大盘状态）
    print("\n-- 按大盘状态 base × ivol 高低：n | 均净收益 | 胜率 --")
    t = df.groupby(["base", "hi"]).net.agg(["count", "mean", lambda s: (s > 0).mean()]).reset_index()
    pv = df.pivot_table(index="base", columns="hi", values="net", aggfunc=["count", "mean"])
    for b in sorted(df.base.unique()):
        sub = df[df.base == b]
        line = f"  {b:<6}"
        for grp in ["ivol_rest", "ivol_top10%"]:
            x = sub[sub.hi == grp].net
            line += f" | {grp}: n{len(x):<4} 均{x.mean():+.3f} 胜{(x>0).mean():.3f}" if len(x) else f" | {grp}: -"
        # 差值
        a = sub[sub.hi == "ivol_top10%"].net; c = sub[sub.hi == "ivol_rest"].net
        if len(a) and len(c):
            line += f"  || 高-低 {a.mean()-c.mean():+.3f}"
        print(line)
    print("\n-- 按 base·sent 单元 × ivol 高低（均净收益 / n）--")
    df["key"] = df.base + "·" + df.sent
    for key in sorted(df.key.unique()):
        sub = df[df.key == key]
        a = sub[sub.hi == "ivol_top10%"].net; c = sub[sub.hi == "ivol_rest"].net
        if len(a) + len(c) < 6: continue
        diff = a.mean() - c.mean() if len(a) and len(c) else float("nan")
        flag = "  <<<高ivol更差,可否决" if (len(a) >= 3 and diff < -0.5) else ("  (高ivol更好,保留)" if (len(a) >= 3 and diff > 0.5) else "")
        ca = f"n{len(a):<3}均{a.mean():+.3f}" if len(a) else "n0"
        print(f"  {key:<12} rest n{len(c):<3}均{c.mean():+.3f} | top {ca} | Δ{diff:+.3f}{flag}")


def main():
    pctmap = factor_pct_lookup("ivol20")
    ep = ExecutionPolicy()
    df1 = baseline_rows(frozen_groups(), pctmap, ep); crosstab(df1, "冻结W1 2025趋势")
    df2 = baseline_rows(canon_groups("fwd"), pctmap); crosstab(df2, "前瞻W2 2026震荡走弱")
    df3 = baseline_rows(canon_groups("third"), pctmap); crosstab(df3, "三窗W3 08普跌")
    pd.concat([df1.assign(w="W1"), df2.assign(w="W2"), df3.assign(w="W3")]).to_pickle(
        OUT / "risk_regime_baseline_rows.pkl")
    print("\nsaved risk_regime_baseline_rows.pkl")


if __name__ == "__main__":
    main()
