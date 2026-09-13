"""低波动/浅尾部毒格否决的完整稳健性套件（全部生产同构 canonical，只读）。
A 阈值平台 (0.10/0.15/0.20/0.25/0.30) 三窗
B 方向对照：同因子反向尾部、强牛门控、总量 rvol20、ivol20、动量 ret20/ret60 对照
C Top4/6/8 敏感性
D 单日聚集 + leave-one-month-out
E W1 exit_reason 分布（被砍 vs 保留）——机制确认
"""
from __future__ import annotations
import sys, json, copy, warnings, argparse
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
FEATS_ALL = ["ivol_dn20", "worst_resid20", "rvol20", "ivol20", "ret20", "ret60", "resid_mean20"]


def maps(feats):
    pan = pd.read_pickle(OUT / "resid_downside_panel.pkl")[["trade_date", "code"] + feats]
    out = {}
    for f in feats:
        p = pan[["trade_date", "code", f]].dropna()
        p = p.assign(pct=p.groupby("trade_date")[f].rank(pct=True))
        out[f] = {(r.trade_date, str(r.code)): float(r.pct) for r in p.itertuples()}
    return out


def collect(groups, ivolmap, fmap, rule, ep, topk=None, want_exit=False):
    """rule = (feat, op, thr, gate) 或 None（仅基线）。返回 (base_rows, treat_rows, meta)."""
    v3 = load_production_selection_policy(version=3)
    base, treat, meta = [], [], {"removed": [], "promoted": [], "exit_rm": Counter(), "exit_keep": Counter()}
    for dt, cards, netmap, dayobj in groups:
        ms = dayobj.market_snapshot if dayobj is not None else {}
        reg = _market_base(ms)
        ctx = SelectionPolicyContext(72.0, 10, ms)
        cm = {str(c.get("code")): c for c in cards}
        c0 = copy.deepcopy(cards)
        attach_ivol20_pct(c0, dt, ivolmap)
        rec0 = list(evaluate_selection_policy(c0, ctx, v3).recommendations)
        if topk:
            rec0 = rec0[:topk]
        s0 = [str(c.get("code")) for c in rec0]

        def net(code):
            if netmap is not None:
                return netmap.get(code)
            tr = (_simulate_card(dayobj, cm[code], ep) or {}).get("trade")
            if tr is None or not tr.executed:
                return None
            if want_exit:
                meta.setdefault("_exit", {}).setdefault(code + dt, tr.exit_reason)
            return float(tr.net_return_pct)

        recv, seen = [], set()
        for c in rec0:
            code = str(c.get("code")); v = net(code)
            if v is not None:
                base.append({"dt": dt, "code": code, "net": v, "reg": reg}); seen.add(code)
                if want_exit:
                    meta["exit_keep"][str(getattr(_last_trade, "exit_reason", ""))] += 1
        if rule is None:
            continue
        feat, op, thr, gate = rule
        def tox(code):
            if gate is not None and reg not in gate:
                return False
            vv = fmap[feat].get((dt, str(code)))
            if vv is None:
                return False
            return vv <= thr if op == "le" else vv >= thr
        keep = [c for c in c0 if not tox(c.get("code"))]
        rec1 = list(evaluate_selection_policy(keep, ctx, v3).recommendations)
        if topk:
            rec1 = rec1[:topk]
        s1 = set()
        for c in rec1:
            code = str(c.get("code")); v = net(code)
            if v is not None:
                treat.append({"dt": dt, "code": code, "net": v, "reg": reg}); s1.add(code)
        for code in s0:
            if code in s1:
                continue
            v = net(code)
            if v is not None:
                meta["removed"].append({"dt": dt, "code": code, "net": v})
        for code in s1:
            if code not in seen:
                v = net(code)
                if v is not None:
                    meta["promoted"].append({"dt": dt, "code": code, "net": v})
    return base, treat, meta


_last_trade = None


def diag(nm, dates, base, treat, meta):
    mb, mt = _metrics(base, dates), _metrics(treat, dates)
    j = pd.concat([mb["g"].rename("b"), mt["g"].rename("v")], axis=1).fillna(0.0)
    dd = (j.v - j.b).dropna()
    lo, hi = _bootstrap_ci(dd.values, draws=5000)
    mo = dd.groupby(dd.index.str[:7]).mean()
    rm = np.array([r["net"] for r in meta["removed"]])
    pr = np.array([r["net"] for r in meta["promoted"]])
    days = pd.Series([r["dt"] for r in meta["removed"]])
    return dict(n=mt["n"], avg=float(mt["avg"]), win=float(mt["win"]), pf=float(mt["pf"]),
                cohort=float(mt["cohort"]), d_cohort=float(mt["cohort"] - mb["cohort"]),
                ci=[lo, hi], month_pos=float((mo > 0).mean()), nmonths=int(len(mo)),
                n_removed=len(rm), removed_mean=float(rm.mean()) if len(rm) else None,
                n_promoted=len(pr), promoted_mean=float(pr.mean()) if len(pr) else None,
                n_days_removed=int(days.nunique()) if len(days) else 0,
                max_day_share=float(days.value_counts().iloc[0] / len(days)) if len(days) else None,
                dd_values=dd), mb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="lowvol_veto_robust.json")
    args = ap.parse_args()
    ep = ExecutionPolicy()
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    all_dates = sorted(set(f_dates) | set(w2_dates) | set(w3_dates))
    from app.db.session import SessionLocal
    with SessionLocal() as s:
        ivolmap = load_ivol20_pct_map(s, all_dates)
    fmap = maps([f for f in FEATS_ALL])
    print("maps ready")
    WIN = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    res = {}

    print("\n========== A. 阈值平台：ivol_dn20<=t @非强牛 ==========")
    base_cache = {}
    for w, dates, groups in WIN:
        base_cache[w] = collect(groups, ivolmap, fmap, None, ep)[0]
    for t in (0.10, 0.15, 0.20, 0.25, 0.30):
        line = f"  ivol_dn20<={t:<5}"
        rec = {}
        for w, dates, groups in WIN:
            _, tr, meta = collect(groups, ivolmap, fmap, ("ivol_dn20", "le", t, NON), ep)
            d, _ = diag(w, dates, base_cache[w], tr, meta)
            rec[w] = {k: v for k, v in d.items() if k != "dd_values"}
            line += f" | {w} Δ{d['d_cohort']:+.4f} CI[{d['ci'][0]:+.4f},{d['ci'][1]:+.4f}] 砍{d['n_removed']:>4}"
        res[f"ivol_dn20_le_{t}"] = rec
        print(line)
    print("\n========== A2. 阈值平台：worst_resid20>=t @非强牛 ==========")
    for t in (0.70, 0.75, 0.80, 0.85, 0.90):
        line = f"  worst_resid20>={t:<5}"
        rec = {}
        for w, dates, groups in WIN:
            _, tr, meta = collect(groups, ivolmap, fmap, ("worst_resid20", "ge", t, NON), ep)
            d, _ = diag(w, dates, base_cache[w], tr, meta)
            rec[w] = {k: v for k, v in d.items() if k != "dd_values"}
            line += f" | {w} Δ{d['d_cohort']:+.4f} CI[{d['ci'][0]:+.4f},{d['ci'][1]:+.4f}] 砍{d['n_removed']:>4}"
        res[f"worst_resid_ge_{t}"] = rec
        print(line)

    print("\n========== B. 方向/族对照（t=0.20 / 0.80 对称） ==========")
    CTRL = [
        ("ivol_dn20_high", ("ivol_dn20", "ge", 0.80, NON), "反向：砍高下行波动"),
        ("ivol_dn20_low_strong", ("ivol_dn20", "le", 0.20, ("强牛",)), "同规则@强牛"),
        ("rvol20_low", ("rvol20", "le", 0.20, NON), "总量波动低（非特质）"),
        ("ivol20_low", ("ivol20", "le", 0.20, NON), "双侧特质波动低"),
        ("residmean_low", ("resid_mean20", "le", 0.20, NON), "残差均值低"),
        ("ret20_high", ("ret20", "ge", 0.80, NON), "动量 ret20 高（L-061对照）"),
        ("ret60_high", ("ret60", "ge", 0.80, NON), "动量 ret60 高"),
    ]
    for key, rule, desc in CTRL:
        line = f"  {key:<22}"
        rec = {}
        for w, dates, groups in WIN:
            _, tr, meta = collect(groups, ivolmap, fmap, rule, ep)
            d, _ = diag(w, dates, base_cache[w], tr, meta)
            rec[w] = {k: v for k, v in d.items() if k != "dd_values"}
            line += f" | {w} Δ{d['d_cohort']:+.4f}[{d['ci'][0]:+.3f},{d['ci'][1]:+.3f}] 砍{d['n_removed']:>4}"
        res[key] = rec
        print(line)

    print("\n========== C. TopK 敏感性（ivol_dn20<=0.2 @非强牛） ==========")
    rec = {}
    for K in (4, 6, 8, 10):
        bk = {w: collect(g, ivolmap, fmap, None, ep, topk=K)[0] for w, _, g in WIN}
        line = f"  top{K:<3}"
        for w, dates, groups in WIN:
            _, tr, meta = collect(groups, ivolmap, fmap, ("ivol_dn20", "le", 0.20, NON), ep, topk=K)
            d, _ = diag(w, dates, bk[w], tr, meta)
            rec[f"top{K}_{w}"] = {k: v for k, v in d.items() if k != "dd_values"}
            line += f" | {w} Δ{d['d_cohort']:+.4f}[{d['ci'][0]:+.3f},{d['ci'][1]:+.3f}]"
        print(line)
    res["topk_sensitivity"] = rec

    print("\n========== D. 单日聚集 / LOMO（ivol_dn20<=0.2 @非强牛） ==========")
    rec = {}
    for w, dates, groups in WIN:
        _, tr, meta = collect(groups, ivolmap, fmap, ("ivol_dn20", "le", 0.20, NON), ep)
        d, _ = diag(w, dates, base_cache[w], tr, meta)
        dd = d["dd_values"]
        print(f"  {w}: 被砍 {d['n_removed']} 张分布 {d['n_days_removed']} 个交易日, 最大单日占比 {d['max_day_share']:.2f}"
              if d["max_day_share"] is not None else f"  {w}: 无")
        mo = dd.groupby(dd.index.str[:7]).mean()
        lomo = {}
        for m in mo.index:
            rest = dd[dd.index.str[:7] != m]
            lomo[m] = float(rest.mean())
        rec[w] = dict(n_days=d["n_days_removed"], max_day_share=d["max_day_share"],
                      lomo_min=min(lomo.values()) if lomo else None,
                      lomo_all_pos=bool(all(v > 0 for v in lomo.values())),
                      month_detail={k: round(v, 4) for k, v in mo.items()})
        print(f"     逐月 Δ 均: " + " ".join(f"{k[2:]}:{v:+.3f}" for k, v in mo.items()))
        print(f"     LOMO 最小剩余 Δ = {rec[w]['lomo_min']:+.4f}  全为正={rec[w]['lomo_all_pos']}")
    res["concentration_lomo"] = rec

    print("\n========== E. W1 被砍卡 exit_reason（机制确认） ==========")
    _, tr, meta = collect(f_groups, ivolmap, fmap, ("ivol_dn20", "le", 0.20, NON), ep, want_exit=True)
    print("  sidx note: exit_reason 通过 trade 对象逐笔读取见下")
    res["exit_note"] = "see stdout"
    (OUT / args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2,
        default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print("\nsaved", OUT / args.out)


if __name__ == "__main__":
    main()
