"""定稿检验：阈值平台扩展(t=0.20~0.40)、门控变体、完整版本栈 v2→v3→v4 三窗对照。
只读。
"""
from __future__ import annotations
import sys, json, copy, warnings
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
NON = ("偏多", "震荡", "弱势")
GATES = {"非强牛": NON, "弱势": ("弱势",), "偏多+震荡": ("偏多", "震荡"), "弱势+震荡": ("弱势", "震荡")}


def fmaps():
    pan = pd.read_pickle(OUT / "resid_downside_panel.pkl")[["trade_date", "code", "ivol_dn20", "worst_resid20"]]
    o = {}
    for f in ("ivol_dn20", "worst_resid20"):
        p = pan[["trade_date", "code", f]].dropna()
        p = p.assign(pct=p.groupby("trade_date")[f].rank(pct=True))
        o[f] = {(r.trade_date, str(r.code)): float(r.pct) for r in p.itertuples()}
    return o


def stack(groups, ivolmap, famap, thr, gate, ep, feat="ivol_dn20"):
    """返回 {v2, v3, v4} 的选中行（真实递补）。"""
    v2 = load_production_selection_policy(version=2)
    v3 = load_production_selection_policy(version=3)
    fmap = famap[feat]
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
            if reg in gate:
                v = fmap.get((dt, str(c.get("code"))))
                if v is not None and v <= thr:
                    continue
            keep.append(c)
        s4 = evaluate_selection_policy(keep, ctx, v3).recommendations
        for key, sel in (("v2", s2), ("v3", s3), ("v4", s4)):
            for c in sel:
                code = str(c.get("code")); v = net(code)
                if v is not None:
                    out[key].append({"dt": dt, "code": code, "net": v, "reg": reg, "score": float(c.get("final_score") or 0)})
    return out


def paid(rows, dates):
    m = _metrics(rows, dates)
    return m


def pooled_block(dd_by_month, draws=5000, seed=11):
    rng = np.random.default_rng(seed)
    keys = sorted(dd_by_month); arrs = [np.asarray(dd_by_month[k], float) for k in keys]
    ms = [np.concatenate([arrs[i] for i in rng.integers(0, len(keys), len(keys))]).mean() for _ in range(draws)]
    return float(np.quantile(ms, 0.025)), float(np.quantile(ms, 0.975))


def main():
    ep = ExecutionPolicy()
    famap = fmaps()
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    all_dates = sorted(set(f_dates) | set(w2_dates) | set(w3_dates))
    from app.db.session import SessionLocal
    with SessionLocal() as s:
        ivolmap = load_ivol20_pct_map(s, all_dates)
    print("maps ready")
    WIN = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    res = {}

    print("========== 1. 阈值平台（ivol_dn20<=t @非强牛）三窗 Δ + 合并 block CI ==========")
    for t in (0.20, 0.25, 0.30, 0.35, 0.40):
        rec, pool = {}, {}
        for w, dates, groups in WIN:
            st = stack(groups, ivolmap, famap, t, NON, ep)
            m3, m4 = paid(st["v3"], dates), paid(st["v4"], dates)
            j = pd.concat([m3["g"].rename("b"), m4["g"].rename("v")], axis=1).fillna(0.0)
            dd = (j.v - j.b).dropna(); lo, hi = _bootstrap_ci(dd.values, draws=5000)
            for k, ser in dd.groupby(dd.index.str[:7]):
                pool.setdefault(k, []).append(ser.values)
            rec[w] = dict(d=float(m4["cohort"] - m3["cohort"]), ci=[lo, hi], n_removed=m3["n"] - m4["n"])
        pbm = {k: np.concatenate(v) for k, v in pool.items()}
        flat = np.concatenate(list(pbm.values()))
        bl, bh = pooled_block(pbm); pl, ph = _bootstrap_ci(flat, draws=5000)
        rec["pooled"] = dict(mean=float(flat.mean()), ci=[pl, ph], block_ci=[bl, bh],
                             month_pos=float(np.mean([v.mean() > 0 for v in pbm.values()])), nm=len(pbm))
        res[f"t={t}"] = rec
        print(f"  t={t}: W1 Δ{rec['W1']['d']:+.4f}[{rec['W1']['ci'][0]:+.3f},{rec['W1']['ci'][1]:+.3f}] "
              f"| W2 Δ{rec['W2']['d']:+.4f}[{rec['W2']['ci'][0]:+.3f},{rec['W2']['ci'][1]:+.3f}] "
              f"| W3 Δ{rec['W3']['d']:+.4f} || 合并 {flat.mean():+.4f} 普通[{pl:+.3f},{ph:+.3f}] "
              f"block[{bl:+.3f},{bh:+.3f}] 月正{rec['pooled']['month_pos']:.2f}")

    print("\n========== 2. 门控变体（ivol_dn20<=0.25） ==========")
    for gname, gate in GATES.items():
        rec, pool = {}, {}
        for w, dates, groups in WIN:
            st = stack(groups, ivolmap, famap, 0.25, gate, ep)
            m3, m4 = paid(st["v3"], dates), paid(st["v4"], dates)
            j = pd.concat([m3["g"].rename("b"), m4["g"].rename("v")], axis=1).fillna(0.0)
            dd = (j.v - j.b).dropna(); lo, hi = _bootstrap_ci(dd.values, draws=5000)
            for k, ser in dd.groupby(dd.index.str[:7]):
                pool.setdefault(k, []).append(ser.values)
            rec[w] = dict(d=float(m4["cohort"] - m3["cohort"]), ci=[lo, hi], n_rm=m3["n"] - m4["n"])
        pbm = {k: np.concatenate(v) for k, v in pool.items()}
        flat = np.concatenate(list(pbm.values())); bl, bh = pooled_block(pbm)
        rec["pooled"] = dict(mean=float(flat.mean()), block_ci=[bl, bh],
                             month_pos=float(np.mean([v.mean() > 0 for v in pbm.values()])))
        res[f"gate::{gname}"] = rec
        print(f"  {gname:<8} W1 Δ{rec['W1']['d']:+.4f}[{rec['W1']['ci'][0]:+.3f},{rec['W1']['ci'][1]:+.3f}] 砍{rec['W1']['n_rm']:>4} "
              f"| W2 Δ{rec['W2']['d']:+.4f}[{rec['W2']['ci'][0]:+.3f},{rec['W2']['ci'][1]:+.3f}] 砍{rec['W2']['n_rm']:>4} "
              f"| W3 Δ{rec['W3']['d']:+.4f} || block[{bl:+.3f},{bh:+.3f}] 月正{rec['pooled']['month_pos']:.2f}")

    print("\n========== 3. 完整版本栈 v2 → v3 → v4(ivol_dn20<=0.25@非强牛) ==========")
    stackres = {}
    for w, dates, groups in WIN:
        st = stack(groups, ivolmap, famap, 0.25, NON, ep)
        line = f"  {w}: "
        srec = {}
        for key, lab in (("v2", "v2"), ("v3", "v3"), ("v4", "v4")):
            m = paid(st[key], dates)
            srec[key] = dict(n=m["n"], avg=float(m["avg"]), win=float(m["win"]), pf=float(m["pf"]),
                             cohort=float(m["cohort"]))
            line += f"{lab} n{m['n']} 均{m['avg']:+.3f} 胜{m['win']:.3f} PF{m['pf']:.2f} cohort{m['cohort']:+.4f} | "
        stackres[w] = srec
        print(line)
        # 分 regime 明细
        d3 = pd.DataFrame(st["v3"]); d4 = pd.DataFrame(st["v4"])
        rm = d3.merge(d4, on=["dt", "code"], how="left", indicator=True)
        rem = rm[rm._merge == "left_only"]
        pr = rm[rm._merge == "right_only"]
        print(f"      被砍 {len(rem)} 张：regime " + str(rem.reg_x.value_counts().to_dict()) +
              f" 均{rem.net_x.mean():+.3f} score均{rem.score_x.mean():.1f}")
        if len(pr):
            print(f"      递补 {len(pr)} 张：regime " + str(pr.reg_y.value_counts().to_dict()) +
                  f" 均{pr.net_y.mean():+.3f} score均{pr.score_y.mean():.1f}")
    res["version_stack"] = stackres

    (OUT / "lowvol_veto_final.json").write_text(json.dumps(res, ensure_ascii=False, indent=2,
        default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print("\nsaved output/research/lowvol_veto_final.json")


if __name__ == "__main__":
    main()
