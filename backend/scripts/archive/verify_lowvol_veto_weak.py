"""弱势-only 门控的定稿检验：阈值平台 + 三窗版本栈 v2→v3→v4 + regime 天数分布。
只读，不改生产代码。
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
GATE = ("弱势",)
THRS = (0.15, 0.20, 0.25, 0.30, 0.35, 0.40)


def fmaps():
    pan = pd.read_pickle(OUT / "resid_downside_panel.pkl")[["trade_date", "code", "ivol_dn20"]]
    p = pan[["trade_date", "code", "ivol_dn20"]].dropna()
    p = p.assign(pct=p.groupby("trade_date")["ivol_dn20"].rank(pct=True))
    return {"ivol_dn20": {(r.trade_date, str(r.code)): float(r.pct) for r in p.itertuples()}}


def stack(groups, ivolmap, fmap, thr, ep):
    v2 = load_production_selection_policy(version=2)
    v3 = load_production_selection_policy(version=3)
    out = {"v2": [], "v3": [], "v4": []}
    for dt, cards, netmap, dayobj in groups:
        ms = dayobj.market_snapshot if dayobj is not None else {}
        reg = _market_base(ms)
        ctx = SelectionPolicyContext(72.0, 10, ms)
        cm = {str(c.get("code")): c for c in cards}

        def net(code):
            if netmap is not None:
                return netmap.get(code)
            tr = (_simulate_card(dayobj, cm[code], ep) or {}).get("trade")
            return float(tr.net_return_pct) if (tr is not None and tr.executed) else None

        s2 = evaluate_selection_policy(cards, ctx, v2).recommendations
        c3 = copy.deepcopy(cards); attach_ivol20_pct(c3, dt, ivolmap)
        s3 = evaluate_selection_policy(c3, ctx, v3).recommendations
        keep = []
        for c in c3:
            if reg in GATE:
                v = fmap.get((dt, str(c.get("code"))))
                if v is not None and v <= thr:
                    continue
            keep.append(c)
        s4 = evaluate_selection_policy(keep, ctx, v3).recommendations
        for key, sel in (("v2", s2), ("v3", s3), ("v4", s4)):
            for c in sel:
                code = str(c.get("code")); v = net(code)
                if v is not None:
                    out[key].append({"dt": dt, "code": code, "net": v, "reg": reg,
                                     "score": float(c.get("final_score") or 0)})
    return out


def paid(rows, dates):
    return _metrics(rows, dates)


def pooled_block(dd_by_month, draws=5000, seed=11):
    rng = np.random.default_rng(seed)
    keys = sorted(dd_by_month); arrs = [np.asarray(dd_by_month[k], float) for k in keys]
    ms = [np.concatenate([arrs[i] for i in rng.integers(0, len(keys), len(keys))]).mean() for _ in range(draws)]
    return float(np.quantile(ms, 0.025)), float(np.quantile(ms, 0.975))


def main():
    ep = ExecutionPolicy()
    fmap = fmaps()["ivol_dn20"]
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    all_dates = sorted(set(f_dates) | set(w2_dates) | set(w3_dates))
    from app.db.session import SessionLocal
    with SessionLocal() as s:
        ivolmap = load_ivol20_pct_map(s, all_dates)
    print("maps ready")
    WIN = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    res = {"gate": list(GATE), "thresholds": {}, "version_stack": {}, "regime_days": {}}

    # 0. regime 天数分布
    print("========== 0. regime 天数分布（三窗）==========")
    for w, dates, groups in WIN:
        cnt = Counter()
        for dt, cards, netmap, dayobj in groups:
            ms = dayobj.market_snapshot if dayobj is not None else {}
            cnt[_market_base(ms)] += 1
        res["regime_days"][w] = dict(cnt)
        n = sum(cnt.values())
        print(f"  {w} n={n} " + " ".join(f"{k}={v}({v/n:.0%})" for k, v in cnt.most_common()))

    # 1. 弱势门控阈值平台
    print("\n========== 1. 阈值平台（ivol_dn20<=t @弱势-only）==========")
    for t in THRS:
        rec, pool = {}, {}
        for w, dates, groups in WIN:
            st = stack(groups, ivolmap, fmap, t, ep)
            m2, m3, m4 = paid(st["v2"], dates), paid(st["v3"], dates), paid(st["v4"], dates)
            j = pd.concat([m3["g"].rename("b"), m4["g"].rename("v")], axis=1).fillna(0.0)
            dd = (j.v - j.b).dropna(); lo, hi = _bootstrap_ci(dd.values, draws=5000)
            for k, ser in dd.groupby(dd.index.str[:7]):
                pool.setdefault(k, []).append(ser.values)
            rec[w] = dict(d=float(m4["cohort"] - m3["cohort"]), ci=[lo, hi], n_rm=m3["n"] - m4["n"],
                          m2=dict(n=m2["n"], avg=float(m2["avg"]), cohort=float(m2["cohort"])),
                          m3=dict(n=m3["n"], avg=float(m3["avg"]), cohort=float(m3["cohort"])),
                          m4=dict(n=m4["n"], avg=float(m4["avg"]), cohort=float(m4["cohort"])))
        pbm = {k: np.concatenate(v) for k, v in pool.items()}
        flat = np.concatenate(list(pbm.values())); bl, bh = pooled_block(pbm)
        rec["pooled"] = dict(mean=float(flat.mean()), block_ci=[bl, bh],
                             month_pos=float(np.mean([v.mean() > 0 for v in pbm.values()])), nm=len(pbm))
        res["thresholds"][f"t={t}"] = rec
        print(f"  t={t}: W1 Δ{rec['W1']['d']:+.4f}[{rec['W1']['ci'][0]:+.3f},{rec['W1']['ci'][1]:+.3f}] 砍{rec['W1']['n_rm']:>3} "
              f"| W2 Δ{rec['W2']['d']:+.4f} 砍{rec['W2']['n_rm']:>3} "
              f"| W3 Δ{rec['W3']['d']:+.4f} 砍{rec['W3']['n_rm']:>3} "
              f"|| 合并{flat.mean():+.4f} block[{bl:+.3f},{bh:+.3f}] 月正{rec['pooled']['month_pos']:.2f}")

    # 2. 版本栈 t=0.25
    print("\n========== 2. 完整版本栈 v2→v3→v4(ivol_dn20<=0.25@弱势-only) ==========")
    for t in (0.25, 0.30):
        print(f"  --- t={t} ---")
        for w, dates, groups in WIN:
            st = stack(groups, ivolmap, fmap, t, ep)
            line = f"  {w}: "
            srec = {}
            for key in ("v2", "v3", "v4"):
                m = paid(st[key], dates)
                srec[key] = dict(n=m["n"], avg=float(m["avg"]), win=float(m["win"]), pf=float(m["pf"]),
                                 cohort=float(m["cohort"]))
                line += f"{key} n{m['n']} 均{m['avg']:+.3f} 胜{m['win']:.3f} PF{m['pf']:.2f} cohort{m['cohort']:+.4f} | "
            res["version_stack"].setdefault(f"t={t}", {})[w] = srec
            print(line)
            d3 = pd.DataFrame(st["v3"]); d4 = pd.DataFrame(st["v4"])
            rm = d3.merge(d4, on=["dt", "code"], how="left", indicator=True)
            rem = rm[rm._merge == "left_only"]; pr = rm[rm._merge == "right_only"]
            print(f"      被砍 {len(rem)} 张：regime {rem.reg_x.value_counts().to_dict()} 均{rem.net_x.mean():+.3f}")
            if len(pr):
                print(f"      递补 {len(pr)} 张：regime {pr.reg_y.value_counts().to_dict()} 均{pr.net_y.mean():+.3f}")
        res["version_stack"][f"t={t}"].pop(None, None)
    (OUT / "lowvol_veto_weak.json").write_text(json.dumps(res, ensure_ascii=False, indent=2,
        default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print("\nsaved output/research/lowvol_veto_weak.json")


if __name__ == "__main__":
    main()
