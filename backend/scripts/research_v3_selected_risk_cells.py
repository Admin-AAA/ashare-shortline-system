"""v3 选中池内的残余毒格搜索（外科式否决候选）：三窗口生产同构选中票 + 因果净收益，
按风险族特征分桶，找"三窗全负且样本充足"的格；并给出与 ivol 当前否决集的增量关系。
只读。
"""
from __future__ import annotations
import sys, json, warnings
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import (
    load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext)
from app.engine.aggregator import _market_base, _market_sentiment
from app.services.risk_features import load_ivol20_pct_map, attach_ivol20_pct
from app.services.backtest.policy_experiment import _simulate_card
from app.engine.backtest import ExecutionPolicy
from scripts.research_riskveto_canonical import frozen_groups, canon_groups
import copy

OUT = B / "output" / "research"
FEATS = ["ivol20", "ivol_dn20", "ivol_up20", "ivol_asym20", "worst_resid20", "dn_share20",
         "beta20", "beta_dn20", "beta_up20", "rdnvol20", "rdn_ratio20"]


def selected_rows(groups, ivolmap, featmap, ep):
    rows = []
    v3 = load_production_selection_policy(version=3)
    for dt, cards, netmap, dayobj in groups:
        ms = dayobj.market_snapshot if dayobj is not None else {}
        ctx = SelectionPolicyContext(72.0, 10, ms)
        cards = copy.deepcopy(cards)
        attach_ivol20_pct(cards, dt, ivolmap)
        sel = evaluate_selection_policy(cards, ctx, v3).recommendations
        cm = {str(c.get("code")): c for c in cards}
        for c in sel:
            code = str(c.get("code"))
            if netmap is not None:
                net = netmap.get(code)
            else:
                tr = (_simulate_card(dayobj, cm[code], ep) or {}).get("trade")
                net = float(tr.net_return_pct) if (tr is not None and tr.executed) else None
            if net is None:
                continue
            r = {"dt": dt, "code": code, "net": net, "score": float(c.get("final_score") or 0),
                 "base": _market_base(ms), "sent": _market_sentiment(ms),
                 "ivol_pct": c.get("ivol20_pct", np.nan)}
            for f in FEATS:
                r[f] = featmap[f].get((dt, code), np.nan)
            rows.append(r)
    return pd.DataFrame(rows)


def main():
    ep = ExecutionPolicy()
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    all_dates = sorted(set(f_dates) | set(w2_dates) | set(w3_dates))
    from app.db.session import SessionLocal
    with SessionLocal() as s:
        ivolmap = load_ivol20_pct_map(s, all_dates)
    pan = pd.read_pickle(OUT / "resid_downside_panel.pkl")[["trade_date", "code"] + FEATS]
    featmap = {}
    for f in FEATS:
        p = pan[["trade_date", "code", f]].dropna()
        p = p.assign(pct=p.groupby("trade_date")[f].rank(pct=True))
        featmap[f] = {(r.trade_date, str(r.code)): float(r.pct) for r in p.itertuples()}
    print("feat maps ready")

    frames = {}
    for nm, dates, groups in (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups)):
        d = selected_rows(groups, ivolmap, featmap, ep)
        frames[nm] = d
        print(f"{nm}: 选中 {len(d)} 笔 均{d.net.mean():+.3f} 胜{(d.net>0).mean():.3f} 天{d.dt.nunique()}")
        print(f"     regime 分布 {d.base.value_counts().to_dict()}")
    allr = pd.concat([v.assign(w=k) for k, v in frames.items()], ignore_index=True)
    allr.to_pickle(OUT / "v3_selected_risk_rows.pkl")

    print("\n############ A. 选中池内 特征分位分桶（三窗同向找毒格）############")
    print(f"{'feature':<14}{'bucket':<10}" + "".join(f"{w:>22}" for w in ("W1", "W2", "W3")))
    for f in FEATS + ["ivol_pct", "score"]:
        for lo, hi, lab in ((0.8, 1.01, "top20%"), (0.0, 0.2, "bot20%")):
            line = f"{f:<14}{lab:<10}"
            ok = True
            for w in ("W1", "W2", "W3"):
                d = frames[w][[f, "net"]].dropna()
                if len(d) < 20:
                    line += f"{'n/a':>22}"; ok = False; continue
                # 用同名特征在【全市场】上的分位（featmap 已是 pct）；ivol_pct 同理
                sub = d[(d[f] >= lo) & (d[f] < hi)]
                rest = d[(d[f] < lo) | (d[f] >= hi)]
                if len(sub) < 8:
                    line += f"{f'n{len(sub)}太少':>22}"; ok = False; continue
                line += f"{sub.net.mean():>+9.3f}(n{len(sub):>3})".rjust(22)
            print(line)

    print("\n############ B. 三窗均为负且 n>=8 的候选毒格汇总 ############")
    hits = []
    for f in FEATS + ["ivol_pct"]:
        for lab, lo, hi in (("top20%", 0.8, 1.01), ("top10%", 0.9, 1.01), ("bot20%", 0.0, 0.2)):
            means, ns = [], []
            for w in ("W1", "W2", "W3"):
                d = frames[w][[f, "net"]].dropna()
                sub = d[(d[f] >= lo) & (d[f] < hi)]
                means.append(sub.net.mean() if len(sub) else np.nan); ns.append(len(sub))
            if all(m == m and m < 0 for m in means) and min(ns) >= 8:
                hits.append((f, lab, [round(m, 3) for m in means], ns))
    if hits:
        for h in hits:
            print("  ", h)
    else:
        print("   无可三窗同负的毒格")

    print("\n############ C. 强牛子样本内（v3 已砍 top20% ivol，此为残余 80%）############")
    for f in ["ivol_pct", "beta20", "worst_resid20", "score"]:
        print(f"  --- {f} ---")
        for w in ("W1", "W2", "W3"):
            d = frames[w][frames[w].base == "强牛"][[f, "net"]].dropna()
            if len(d) < 15:
                print(f"    {w}: n{len(d)} 太少"); continue
            d = d.assign(q=pd.qcut(d[f], min(5, max(2, len(d) // 8)), labels=False, duplicates="drop"))
            g = d.groupby("q").net.agg(["mean", "size"])
            print(f"    {w}: " + " | ".join(f"Q{int(i)+1} {r['mean']:+.2f}(n{int(r['size'])})" for i, r in g.iterrows()))


if __name__ == "__main__":
    main()
