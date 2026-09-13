"""生产同构终审 v3：用真实生产选择函数对比 v2 vs v3（强牛×高ivol否决），三窗口。

与 research_riskveto_canonical 的区别（关键）：
- 对照臂不再"手筛卡后用 v2 重评"，而是给同一批卡附加【持久化 daily_kbars.ivol20 经
  load_ivol20_pct_map 算出的当日全市场分位】，再用 load_production_selection_policy(version=3)
  走真实 evaluate_selection_policy → apply_pool_rules 内部否决+自然递补。即端到端验证生产代码路径。
- 冻结 W1 用 holdout 快照 + _simulate_card 因果出场；前瞻 W2 / 第三 W3 join canonical run 净收益。
复现目标（研究手筛口径）：Δcohort 约 +0.079 / +0.155 / +0.288，三窗全正。
"""
from __future__ import annotations
import sys, json, argparse
from pathlib import Path
import numpy as np, pandas as pd

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import (
    load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext)
from app.services.risk_features import load_ivol20_pct_map, attach_ivol20_pct
from app.services.backtest.policy_experiment import _simulate_card
from app.engine.backtest import ExecutionPolicy
from scripts.research_canonical_two_window import _bootstrap_ci
from scripts.research_riskveto_canonical import frozen_groups, canon_groups, _metrics

OUT = B / "output" / "research"


def run_window(dates_groups, pctmap, pol_v2, pol_v3, ep=None):
    base, veto, removed, promoted = [], [], [], []
    for dt, cards, netmap, dayobj in dates_groups:
        ms = dayobj.market_snapshot if dayobj is not None else {}
        ctx = SelectionPolicyContext(72., 10, ms)
        s0 = {str(c.get("code")) for c in evaluate_selection_policy(cards, ctx, pol_v2).recommendations}
        # 生产 v3：附加持久化 ivol 分位（深拷贝避免污染 base 已不必——base 已先算完）
        attach_ivol20_pct(cards, dt, pctmap)
        s1 = {str(c.get("code")) for c in evaluate_selection_policy(cards, ctx, pol_v3).recommendations}
        cm = {str(c.get("code")): c for c in cards}

        def net_of(code):
            if netmap is not None:
                return netmap.get(code)
            tr = (_simulate_card(dayobj, cm[code], ep) or {}).get("trade")
            return float(tr.net_return_pct) if (tr is not None and tr.executed) else None

        for code in s0:
            v = net_of(code)
            if v is not None: base.append({"dt": dt, "code": code, "net": v})
        for code in s1:
            v = net_of(code)
            if v is not None: veto.append({"dt": dt, "code": code, "net": v})
        for code in s0 - s1:
            v = net_of(code)
            if v is not None: removed.append(v)
        for code in s1 - s0:
            v = net_of(code)
            if v is not None: promoted.append(v)
    return base, veto, np.array(removed), np.array(promoted)


def report(nm, all_dates, base, veto, rem, pro):
    mb, mv = _metrics(base, all_dates), _metrics(veto, all_dates)
    j = pd.concat([mb["g"].rename("b"), mv["g"].rename("v")], axis=1).fillna(0.0)
    dd = j.v - j.b
    lo, hi = _bootstrap_ci(dd.values, draws=5000)
    print(f"[{nm}] v2 n{mb['n']} 均{mb['avg']:+.3f} 胜{mb['win']:.3f} PF{mb['pf']:.2f} cohort{mb['cohort']:+.4f} -> "
          f"v3 n{mv['n']} 均{mv['avg']:+.3f} 胜{mv['win']:.3f} PF{mv['pf']:.2f} cohort{mv['cohort']:+.4f}")
    print(f"      Δcohort{mv['cohort']-mb['cohort']:+.4f} 配对日CI[{lo:+.4f},{hi:+.4f}] 正Δ天占比{(dd.values>0).mean():.2f}")
    if len(rem):
        print(f"      被砍 n{len(rem)} 均{rem.mean():+.3f} 胜{(rem>0).mean():.3f} | 递补 n{len(pro)} 均{pro.mean() if len(pro) else float('nan'):+.3f} 胜{(pro>0).mean() if len(pro) else float('nan'):.3f}")
    return {"v2": {k: mb[k] for k in ("n","avg","win","pf","cohort")},
            "v3": {k: mv[k] for k in ("n","avg","win","pf","cohort")},
            "delta_cohort": mv["cohort"]-mb["cohort"], "ci": [lo, hi],
            "removed_mean": float(rem.mean()) if len(rem) else None,
            "promoted_mean": float(pro.mean()) if len(pro) else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="v3_production_canonical.json")
    args = ap.parse_args()
    v2 = load_production_selection_policy(version=2)
    v3 = load_production_selection_policy(version=3)
    ep = ExecutionPolicy()
    print("加载三窗口分组 ...")
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    all_dates = sorted(set(f_dates) | set(w2_dates) | set(w3_dates))
    print("从持久化 daily_kbars.ivol20 构建全窗口分位 map ...")
    from app.db.session import SessionLocal
    with SessionLocal() as s:
        pctmap = load_ivol20_pct_map(s, all_dates)
    print(f"pctmap 大小 {len(pctmap):,}")
    res = {}
    res["W1_frozen"] = report("冻结W1", f_dates, *run_window(f_groups, pctmap, v2, v3, ep))
    res["W2_forward"] = report("前瞻W2", w2_dates, *run_window(w2_groups, pctmap, v2, v3, ep))
    res["W3_third"] = report("三窗W3", w3_dates, *run_window(w3_groups, pctmap, v2, v3, ep))
    (OUT / args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2,
        default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print("\nsaved", OUT / args.out)


if __name__ == "__main__":
    main()
