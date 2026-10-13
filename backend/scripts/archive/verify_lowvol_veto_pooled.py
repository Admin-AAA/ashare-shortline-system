"""低波动毒格否决的合并显著性 + 机制确认（生产同构 canonical，只读）。
- 三窗合并的【普通日 bootstrap】与【按月 block bootstrap】（L-094 口径）；
- topK 敏感性；
- W1 被砍卡 vs 保留卡的 exit_reason 分布（确认机制是"止损/跳空主导"）。
"""
from __future__ import annotations
import sys, json, copy, warnings
from pathlib import Path
from collections import Counter
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
NON = ("偏多", "震荡", "弱势")
RULES = [("ivol_dn20", "le", 0.20), ("ivol_dn20", "le", 0.25),
         ("worst_resid20", "ge", 0.75), ("worst_resid20", "ge", 0.80)]
FEATS = ["ivol_dn20", "worst_resid20"]


def fmaps():
    pan = pd.read_pickle(OUT / "resid_downside_panel.pkl")[["trade_date", "code"] + FEATS]
    o = {}
    for f in FEATS:
        p = pan[["trade_date", "code", f]].dropna()
        p = p.assign(pct=p.groupby("trade_date")[f].rank(pct=True))
        o[f] = {(r.trade_date, str(r.code)): float(r.pct) for r in p.itertuples()}
    return o


def run(groups, ivolmap, fmap, rule, ep, topk=None, exits=False):
    v3 = load_production_selection_policy(version=3)
    base, treat = [], []
    ex = {"rm": Counter(), "pm": Counter(), "keep": Counter(), "cores": {}}
    for dt, cards, netmap, dayobj in groups:
        ms = dayobj.market_snapshot if dayobj is not None else {}
        reg = _market_base(ms)
        ctx = SelectionPolicyContext(72.0, 10, ms)
        cm = {str(c.get("code")): c for c in cards}
        c0 = copy.deepcopy(cards)
        attach_ivol20_pct(c0, dt, ivolmap)

        def trade_of(code):
            if netmap is not None:
                return netmap.get(code), None
            t = (_simulate_card(dayobj, cm[code], ep) or {}).get("trade")
            if t is None or not t.executed:
                return None, None
            return float(t.net_return_pct), t

        r0 = list(evaluate_selection_policy(c0, ctx, v3).recommendations)
        if topk:
            r0 = r0[:topk]
        s0 = set()
        for c in r0:
            code = str(c.get("code")); v, t = trade_of(code)
            if v is None:
                continue
            base.append({"dt": dt, "code": code, "net": v}); s0.add(code)
            if exits:
                ex["keep"][str(getattr(t, "exit_reason", "") or "?")] += 1
                ex["cores"].setdefault(code + dt, float(6.0 if v > 0 else -4.5))
        if rule is None:
            continue
        f, op, thr = rule

        def tox(code):
            if reg not in NON:
                return False
            vv = fmap[f].get((dt, str(code)))
            if vv is None:
                return False
            return vv <= thr if op == "le" else vv >= thr
        keep = [c for c in c0 if not tox(c.get("code"))]
        r1 = list(evaluate_selection_policy(keep, ctx, v3).recommendations)
        if topk:
            r1 = r1[:topk]
        s1 = set()
        for c in r1:
            code = str(c.get("code")); v, t = trade_of(code)
            if v is None:
                continue
            treat.append({"dt": dt, "code": code, "net": v}); s1.add(code)
            if exits and code not in s0:
                ex["pm"][str(getattr(t, "exit_reason", "") or "?")] += 1
        for c in r0:
            code = str(c.get("code"))
            if code in s1 or code not in s0:
                continue
            v, t = trade_of(code)
            if v is None:
                continue
            if exits:
                ex["rm"][str(getattr(t, "exit_reason", "") or "?")] += 1
                ex.setdefault("rm_details", []).append((dt, code, v, str(getattr(t, "exit_reason", ""))))
    return base, treat, ex


def block_bootstrap(dd_by_month, draws=5000, seed=11):
    """dd_by_month: dict[month] -> np.array(日Δ)。整月重采样。"""
    rng = np.random.default_rng(seed)
    keys = list(dd_by_month)
    arrs = [dd_by_month[k] for k in keys]
    means = []
    for _ in range(draws):
        idx = rng.integers(0, len(keys), size=len(keys))
        vals = np.concatenate([arrs[i] for i in idx])
        means.append(vals.mean())
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def main():
    ep = ExecutionPolicy()
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    all_dates = sorted(set(f_dates) | set(w2_dates) | set(w3_dates))
    from app.db.session import SessionLocal
    with SessionLocal() as s:
        ivolmap = load_ivol20_pct_map(s, all_dates)
    fmap = fmaps()
    print("maps ready")
    WIN = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    base_all = {}
    for w, dates, groups in WIN:
        base_all[w] = run(groups, ivolmap, fmap, None, ep)[0]

    res = {}
    for rule in RULES:
        key = f"{rule[0]}_{rule[1]}{rule[2]}"
        print(f"\n########## {key} @非强牛 ##########")
        rec = {}
        pooled = {}   # month -> list of day deltas
        for w, dates, groups in WIN:
            _, tr, _ = run(groups, ivolmap, fmap, rule, ep)
            mb, mt = _metrics(base_all[w], dates), _metrics(tr, dates)
            j = pd.concat([mb["g"].rename("b"), mt["g"].rename("v")], axis=1).fillna(0.0)
            dd = (j.v - j.b).dropna()
            lo, hi = _bootstrap_ci(dd.values, draws=5000)
            mo = dd.groupby(dd.index.str[:7])
            for m, ser in mo:
                pooled.setdefault(m, []).append(ser.values)
            print(f"  {w}: v3基线 n{mb['n']} cohort{mb['cohort']:+.4f} -> n{mt['n']} cohort{mt['cohort']:+.4f} "
                  f"Δ{mt['cohort']-mb['cohort']:+.4f} CI[{lo:+.4f},{hi:+.4f}] 逐笔{mt['avg']:+.3f} "
                  f"胜{mt['win']:.3f} PF{mt['pf']:.2f} 逐月正{(mo.mean()>0).mean():.2f}({mo.ngroups}m)")
            rec[w] = dict(n=mt["n"], cohort=float(mt["cohort"]), d=float(mt["cohort"] - mb["cohort"]),
                          ci=[lo, hi], avg=float(mt["avg"]), win=float(mt["win"]), pf=float(mt["pf"]))
        flat = np.concatenate([np.asarray(v) for k in sorted(pooled) for v in pooled[k]])
        bl, bh = block_bootstrap({k: np.concatenate(v).astype(float) for k, v in pooled.items()})
        pl, ph = _bootstrap_ci(flat, draws=5000)
        nm = len(pooled)
        pos = np.mean([np.concatenate(pooled[k]).mean() > 0 for k in pooled])
        print(f"  合并 {len(flat)} 变动日 日Δ均{flat.mean():+.4f} 普通CI[{pl:+.4f},{ph:+.4f}] "
              f"月block CI[{bl:+.4f},{bh:+.4f}] 月正{pos:.2f}({nm}m)")
        rec["pooled"] = dict(n_days=int(len(flat)), mean=float(flat.mean()), ci=[pl, ph],
                             block_ci=[bl, bh], month_pos=float(pos), nmonths=nm)
        res[key] = rec

    print("\n########## topK 敏感性（worst_resid20>=0.75 @非强牛） ##########")
    tk = {}
    for K in (4, 6, 8):
        bk = {w: run(g, ivolmap, fmap, None, ep, topk=K)[0] for w, _, g in WIN}
        line = f"  top{K}"
        for w, dates, groups in WIN:
            _, tr, _ = run(groups, ivolmap, fmap, ("worst_resid20", "ge", 0.75), ep, topk=K)
            mb, mt = _metrics(bk[w], dates), _metrics(tr, dates)
            j = pd.concat([mb["g"].rename("b"), mt["g"].rename("v")], axis=1).fillna(0.0)
            dd = (j.v - j.b).dropna(); lo, hi = _bootstrap_ci(dd.values, draws=5000)
            tk[f"top{K}_{w}"] = dict(d=float(mt["cohort"] - mb["cohort"]), ci=[lo, hi])
            line += f" | {w} Δ{mt['cohort']-mb['cohort']:+.4f}[{lo:+.3f},{hi:+.3f}]"
        print(line)
    res["topk_worst075"] = tk

    print("\n########## W1 exit_reason（ivol_dn20<=0.2 @非强牛） ##########")
    _, tr, ex = run(f_groups, ivolmap, fmap, ("ivol_dn20", "le", 0.20), ep, exits=True)
    print("  被砍:", dict(ex["rm"]))
    print("  保留:", dict(ex["keep"]))
    print("  递补:", dict(ex["pm"]))
    res["exit_reason_W1"] = {"removed": dict(ex["rm"]), "kept": dict(ex["keep"]), "promoted": dict(ex["pm"])}
    det = pd.DataFrame(ex.get("rm_details") or [], columns=["dt", "code", "net", "exit_reason"])
    if len(det):
        print("  被砍卡 exit_reason×均净收益:\n" + det.groupby("exit_reason").net.agg(["mean", "size"]).round(3).to_string())
        res["exit_reason_W1_detail"] = det.groupby("exit_reason").net.agg(["mean", "size"]).to_dict()

    (OUT / "lowvol_veto_pooled.json").write_text(json.dumps(res, ensure_ascii=False, indent=2,
        default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print("\nsaved output/research/lowvol_veto_pooled.json")


if __name__ == "__main__":
    main()
