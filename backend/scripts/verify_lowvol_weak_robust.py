"""L-109 稳健性套件（弱势-only t=0.25）：topK 敏感性、被砍卡时间聚集度/LOMO、出场归因机制。

用 full-market 面板分位（resid_downside_panel.pkl）做 pre-screen 稳健性；
生产列路径的终审见 verify_v4_production_canonical.py。只读。
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
THR = 0.25
TOPK = (4, 6, 8, 10)


def fmap_dn():
    pan = pd.read_pickle(OUT / "resid_downside_panel.pkl")[["trade_date", "code", "ivol_dn20"]]
    p = pan.dropna()
    p = p.assign(pct=p.groupby("trade_date")["ivol_dn20"].rank(pct=True))
    return {(r.trade_date, str(r.code)): float(r.pct) for r in p.itertuples()}


def build(groups, ivolmap, dnmap, ep, topk):
    v3 = load_production_selection_policy(version=3)
    out = {"v3": [], "v4": []}
    for dt, cards, netmap, dayobj in groups:
        ms = dayobj.market_snapshot if dayobj is not None else {}
        reg = _market_base(ms)
        ctx = SelectionPolicyContext(72.0, topk, ms)
        cm = {str(c.get("code")): c for c in cards}

        def net(code):
            if netmap is not None:
                return netmap.get(code)
            tr = (_simulate_card(dayobj, cm[code], ep) or {}).get("trade")
            return float(tr.net_return_pct) if (tr is not None and tr.executed) else None

        c3 = copy.deepcopy(cards); attach_ivol20_pct(c3, dt, ivolmap)
        s3 = evaluate_selection_policy(c3, ctx, v3).recommendations
        keep = []
        for c in c3:
            if reg in GATE:
                v = dnmap.get((dt, str(c.get("code"))))
                if v is not None and v <= THR:
                    continue
            keep.append(c)
        s4 = evaluate_selection_policy(keep, ctx, v3).recommendations
        for key, sel in (("v3", s3), ("v4", s4)):
            for c in sel:
                code = str(c.get("code")); v = net(code)
                if v is not None:
                    out[key].append({"dt": dt, "code": code, "net": v, "reg": reg})
    return out


def day_delta(st, dates):
    m3, m4 = _metrics(st["v3"], dates), _metrics(st["v4"], dates)
    j = pd.concat([m3["g"].rename("b"), m4["g"].rename("v")], axis=1).fillna(0.0)
    return (j.v - j.b).dropna(), float(m4["cohort"] - m3["cohort"])


def main():
    ep = ExecutionPolicy()
    dnmap = fmap_dn()
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    all_dates = sorted(set(f_dates) | set(w2_dates) | set(w3_dates))
    from app.db.session import SessionLocal
    with SessionLocal() as s:
        ivolmap = load_ivol20_pct_map(s, all_dates)
    print("maps ready", flush=True)
    WIN = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    res = {"gate": list(GATE), "thr": THR}

    # 1. topK 敏感性
    print("========== 1. topK 敏感性（弱势 t=0.25） ==========", flush=True)
    res["topk"] = {}
    for k in TOPK:
        line = f"  top{k}: "
        rec = {}
        for w, dates, groups in WIN:
            st = build(groups, ivolmap, dnmap, ep, k)
            dd, d = day_delta(st, dates)
            lo, hi = _bootstrap_ci(dd.values, draws=5000)
            rec[w] = dict(d=d, ci=[lo, hi], n_days=int(len(dd)))
            line += f"{w} Δ{d:+.4f}[{lo:+.3f},{hi:+.3f}] "
        res["topk"][f"top{k}"] = rec
        print(line, flush=True)

    # 2. 被砍卡聚集度 / LOMO（top10）
    print("\n========== 2. 被砍卡时间聚集度 + LOMO（弱势 t=0.25 top10） ==========", flush=True)
    daily_all, monthly = {}, {}
    for w, dates, groups in WIN:
        st = build(groups, ivolmap, dnmap, ep, 10)
        d3 = pd.DataFrame(st["v3"]); d4 = pd.DataFrame(st["v4"])
        rm = d3.merge(d4, on=["dt", "code"], how="left", indicator=True)
        rem = rm[rm._merge == "left_only"]
        n_by_day = rem.groupby("dt").size()
        daily_all[w] = n_by_day.to_dict()
        print(f"  {w}: 被砍 {len(rem)} 张分布 {len(n_by_day)} 日，最大单日 {n_by_day.max()} 张"
              f"（占比 {n_by_day.max()/max(1,len(rem)):.2%}），被砍均收益 {rem.net_x.mean():+.3f}", flush=True)
        dd, _ = day_delta(st, dates)
        for k, ser in dd.groupby(dd.index.str[:7]):
            monthly.setdefault(k, []).append(ser.values)
    pbm = {k: np.concatenate(v) for k, v in monthly.items()}
    lomo = {}
    for drop in sorted(pbm):
        rest = [v for k, v in pbm.items() if k != drop]
        flat = np.concatenate(rest)
        lomo[drop] = float(flat.mean())
    worst = sorted(lomo.items(), key=lambda kv: kv[1])[:3]
    print(f"  LOMO（去掉某月后合并日均 Δ）最小三档: " +
          " ".join(f"{k}:{v:+.4f}" for k, v in worst), flush=True)
    flat_all = np.concatenate(list(pbm.values()))
    print(f"  全量合并日均 Δ {flat_all.mean():+.4f}；月数 {len(pbm)}；"
          f"月正值 {sum(1 for v in pbm.values() if v.mean() > 0)}/{len(pbm)}", flush=True)
    res["lomo"] = {k: v for k, v in lomo.items()}

    # 3. 出场归因机制（W1）
    print("\n========== 3. 出场归因机制（W1，被砍 vs 保留） ==========", flush=True)
    mech = {}
    for w, dates, groups in WIN:
        st = build(groups, ivolmap, dnmap, ep, 10)
        d3 = pd.DataFrame(st["v3"]); d4 = pd.DataFrame(st["v4"])
        rm = d3.merge(d4, on=["dt", "code"], how="left", indicator=True)
        rem = rm[rm._merge == "left_only"]
        kept = d4
        if not len(rem):
            continue
        # 用 W1 的 _simulate_card 重跑被砍/保留卡取 exit_reason
        gmap = {dt: (cards, netmap, dayobj) for dt, cards, netmap, dayobj in groups}
        c_rem, c_kept = Counter(), Counter()
        n_rem, n_kep = Counter(), Counter()
        for tag, df, cc, nn in (("removed", rem, c_rem, n_rem), ("kept", kept, c_kept, n_kep)):
            for r in df.itertuples():
                cards, netmap, dayobj = gmap[r.dt]
                cm = {str(c.get("code")): c for c in cards}
                if r.code not in cm or dayobj is None:
                    continue
                tr = (_simulate_card(dayobj, cm[r.code], ep) or {}).get("trade")
                if tr is None or not tr.executed:
                    continue
                cc[str(getattr(tr, "exit_reason", "?"))] += 1
                nn["n"] += 1
        def share(c, n):
            return {k: round(v / max(1, n["n"]), 3) for k, v in c.most_common()}
        mech[w] = dict(removed_share=share(c_rem, n_rem), kept_share=share(c_kept, n_kep),
                       n_removed=int(n_rem["n"]), n_kept=int(n_kep["n"]))
        print(f"  {w} 被砍 n={n_rem['n']}: {mech[w]['removed_share']}", flush=True)
        print(f"  {w} 保留 n={n_kep['n']}: {mech[w]['kept_share']}", flush=True)
    res["mechanism"] = mech

    (OUT / "lowvol_weak_robust.json").write_text(json.dumps(res, ensure_ascii=False, indent=2,
        default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print("\nsaved output/research/lowvol_weak_robust.json", flush=True)


if __name__ == "__main__":
    main()
