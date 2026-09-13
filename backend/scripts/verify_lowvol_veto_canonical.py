"""生产同构 canonical 三窗终审：在 v3 之上叠加【低特质波动/浅尾部】毒格硬否决。

候选规则（全部 T 收盘可得、横截面分位、缺失不否决）：
  R1 = ivol_dn20_pct <= lo   （低下行半波动 = 无波动的"安静票"）
  R2 = worst_resid20_pct >= hi（窗口内最差残差很浅 = 近期无波折）
  gate ∈ {非强牛, 全部, 强牛}
臂：P0 = v3（现行生产）；其它臂 = v3 + 外置毒格硬否决（真实递补）。
口径：冻结 W1 = holdout_244d + _simulate_card 因果出场；
      W2/W3 = canonical run 真实分数 + 真实 evaluate_selection_policy + join canonical 净收益。
只读。
"""
from __future__ import annotations
import sys, json, argparse, copy, warnings
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import (
    load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext)
from app.engine.aggregator import _market_base
from app.services.risk_features import load_ivol20_pct_map, attach_ivol20_pct
from app.services.backtest.policy_experiment import _simulate_card
from app.engine.backtest import ExecutionPolicy
from scripts.research_canonical_two_window import _bootstrap_ci
from scripts.research_riskveto_canonical import frozen_groups, canon_groups, _metrics

OUT = B / "output" / "research"
FEATS = ["ivol_dn20", "worst_resid20", "ivol20", "beta20"]


def feat_pct_maps():
    pan = pd.read_pickle(OUT / "resid_downside_panel.pkl")[["trade_date", "code"] + FEATS]
    maps = {}
    for f in FEATS:
        p = pan[["trade_date", "code", f]].dropna()
        p = p.assign(pct=p.groupby("trade_date")[f].rank(pct=True))
        maps[f] = {(r.trade_date, str(r.code)): float(r.pct) for r in p.itertuples()}
    return maps


def run_window(groups, ivolmap, fmap, rules, ep):
    """rules: list of (feat, op, thr, gate)  gate: None=全部 / tuple=仅这些 regime。
    返回 {arm_name: [rows]}；arm_0 为纯 v3 基线。"""
    v3 = load_production_selection_policy(version=3)
    v2 = load_production_selection_policy(version=2)
    arms = {0: []}
    for i, _ in enumerate(rules, 1):
        arms[i] = []
    for dt, cards, netmap, dayobj in groups:
        ms = dayobj.market_snapshot if dayobj is not None else {}
        reg = _market_base(ms)
        ctx = SelectionPolicyContext(72.0, 10, ms)
        cm = {str(c.get("code")): c for c in cards}

        def net_of(code):
            if netmap is not None:
                return netmap.get(code)
            tr = (_simulate_card(dayobj, cm[code], ep) or {}).get("trade")
            return float(tr.net_return_pct) if (tr is not None and tr.executed) else None

        def pick(sel):
            for code in sel:
                v = net_of(code)
                if v is not None:
                    yield {"dt": dt, "code": code, "net": v, "reg": reg}

        # P0 = v3
        c0 = copy.deepcopy(cards)
        attach_ivol20_pct(c0, dt, ivolmap)
        s0 = {str(c.get("code")) for c in evaluate_selection_policy(c0, ctx, v3).recommendations}
        arms[0].extend(pick(s0))

        for i, (feat, op, thr, gate) in enumerate(rules, 1):
            def tox(code):
                if gate is not None and reg not in gate:
                    return False
                v = fmap[feat].get((dt, str(code)))
                if v is None:
                    return False
                return v <= thr if op == "le" else v >= thr
            keep = [c for c in c0 if not tox(c.get("code"))]
            s = {str(c.get("code")) for c in evaluate_selection_policy(keep, ctx, v3).recommendations}
            arms[i].extend(pick(s))
    return arms


def report(nm, dates, arms, rules, tag):
    base = arms[0]
    mb_all = _metrics(base, dates)
    print(f"\n########## {nm}  (P0=v3: n{mb_all['n']} 均{mb_all['avg']:+.3f} 胜{mb_all['win']:.3f} "
          f"PF{mb_all['pf']:.2f} cohort{mb_all['cohort']:+.4f}) ##########")
    out = {"P0": {k: (float(mb_all[k]) if k != "g" else None) for k in ("n", "avg", "win", "pf", "cohort")}}
    s0 = {(r["dt"], r["code"]): r["net"] for r in base}
    for i, (feat, op, thr, gate) in enumerate(rules, 1):
        rows = arms[i]
        m = _metrics(rows, dates)
        j = pd.concat([mb_all["g"].rename("b"), m["g"].rename("v")], axis=1).fillna(0.0)
        dd = (j.v - j.b).dropna()
        lo, hi = _bootstrap_ci(dd.values, draws=5000)
        mo = dd.groupby(dd.index.str[:7]).mean()
        si = {(r["dt"], r["code"]): r["net"] for r in rows}
        rm = [v for k, v in s0.items() if k not in si]
        pr = [v for k, v in si.items() if k not in s0]
        name = f"{feat}{'<=' if op=='le' else '>='}{thr}@{gate or 'ALL'}"
        out[name] = dict(n=m["n"], avg=float(m["avg"]), win=float(m["win"]), pf=float(m["pf"]),
                         cohort=float(m["cohort"]), d_cohort=float(m["cohort"] - mb_all["cohort"]),
                         ci=[lo, hi], month_pos=float((mo > 0).mean()), nmonths=int(len(mo)),
                         n_removed=len(rm), removed_mean=float(np.mean(rm)) if rm else None,
                         n_promoted=len(pr), promoted_mean=float(np.mean(pr)) if pr else None)
        print(f"  {name:<30} n{m['n']:>5} 逐笔{m['avg']:>+7.3f} 胜{m['win']:.3f} PF{m['pf']:>5.2f} "
              f"cohort{m['cohort']:>+8.4f} Δ{m['cohort']-mb_all['cohort']:>+8.4f} "
              f"CI[{lo:+.4f},{hi:+.4f}] 月正{(mo>0).mean():.2f}({len(mo)}m)")
        if rm or pr:
            print(f"      砍 n{len(rm)}(均{np.mean(rm) if rm else float('nan'):+.3f}) | "
                  f"递补 n{len(pr)}(均{np.mean(pr) if pr else float('nan'):+.3f})")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="lowvol_veto_canonical.json")
    ap.add_argument("--rules", default="ivol_dn20<=0.2:非强牛|ivol_dn20<=0.2:ALL|worst_resid20>=0.8:非强牛|ivol_dn20<=0.2:强牛")
    args = ap.parse_args()
    NON = ("偏多", "震荡", "弱势")
    rules = []
    for spec in args.rules.split("|"):
        expr, gate = spec.split(":")
        for op in ("<=", ">="):
            if op in expr:
                f, t = expr.split(op)
                rules.append((f, "le" if op == "<=" else "ge", float(t),
                              None if gate == "ALL" else (NON if gate == "非强牛" else tuple(gate.split(",")))))
                break
    print("rules:", rules)

    ep = ExecutionPolicy()
    fmap = feat_pct_maps()
    print("feat maps ready")
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    all_dates = sorted(set(f_dates) | set(w2_dates) | set(w3_dates))
    from app.db.session import SessionLocal
    with SessionLocal() as s:
        ivolmap = load_ivol20_pct_map(s, all_dates)

    res = {}
    for nm, dates, groups in (("W1_frozen", f_dates, f_groups),
                              ("W2_forward", w2_dates, w2_groups),
                              ("W3_third", w3_dates, w3_groups)):
        arms = run_window(groups, ivolmap, fmap, rules, ep)
        res[nm] = report(nm, dates, arms, rules, nm)
    (OUT / args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2,
        default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print("\nsaved", OUT / args.out)


if __name__ == "__main__":
    main()
