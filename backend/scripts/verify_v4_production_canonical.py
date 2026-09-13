"""L-109 生产同构终审：用【生产持久化列】daily_kbars.ivol_dn20 的当日全市场分位
（app.services.risk_features.load_ivol_dn20_pct_map），在真实 evaluate_selection_policy
（engine 内否决块 + 真实递补）与真实因果出场上，复核 v3 → v4 的三窗口 Δcohort。

与 verify_lowvol_veto_weak.py 的区别（这是"终审"而非"预筛"）：
- 分位来源改为生产列路径，与 live(adapters)/回测咽喉完全同源，杜绝"面板内排名口径漂移"。
- 打印被砍组/递补组的 regime 与收益，确认机制；给去极值稳健性与 topK 敏感性。

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
from app.services.risk_features import (
    load_ivol20_pct_map, attach_ivol20_pct,
    load_ivol_dn20_pct_map, attach_ivol_dn20_pct,
)
from app.services.backtest.policy_experiment import _simulate_card
from app.engine.backtest import ExecutionPolicy
from scripts.research_canonical_two_window import _bootstrap_ci
from scripts.research_riskveto_canonical import frozen_groups, canon_groups, _metrics

OUT = B / "output" / "research"
GATE = ("弱势",)
THRS = (0.20, 0.25, 0.30)


def stack(groups, iv20map, ivdnmap, thr, ep):
    """返回 v2/v3/v4 选中行；v4 = v3 + 弱势×ivol_dn20 低分位硬否决（真实递补）。

    阈值经 dataclasses.replace 注入 v4 副本（引擎内否决块使用该阈值），
    使 t=0.20/0.25/0.30 的阈值平台在生产宇宙上同样可复核。
    """
    import dataclasses
    v2 = load_production_selection_policy(version=2)
    v3 = load_production_selection_policy(version=3)
    v4 = dataclasses.replace(
        load_production_selection_policy(version=4),
        weak_low_ivoldn20_pct_threshold=float(thr))
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
        # v3 基线：走生产列路径的分位（与 live 同源）
        c3 = copy.deepcopy(cards); attach_ivol20_pct(c3, dt, iv20map)
        s3 = evaluate_selection_policy(c3, ctx, v3).recommendations
        # v4：同一批卡片再附 ivol_dn20 分位，交给 engine 内的否决块（不在脚本里手写过滤）
        c4 = copy.deepcopy(cards)
        attach_ivol20_pct(c4, dt, iv20map); attach_ivol_dn20_pct(c4, dt, ivdnmap)
        s4 = evaluate_selection_policy(c4, ctx, v4).recommendations
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


def load_dn_map(session, all_dates):
    """优先读生产持久化列；列尚未落库（DDL 被并行实例长事务阻塞）时回落到
    生产同源分位图（scripts/build_ivol_dn20_prodmap.py，同一取数查询 + 同一面板函数），
    两者按构造等价，仅差"是否写库"。返回 (map, source)。"""
    try:
        m = load_ivol_dn20_pct_map(session, all_dates)
    except Exception as exc:  # noqa: BLE001
        print(f"生产列不可用（{type(exc).__name__}: {str(exc)[:90]}），回落生产同源图", flush=True)
        m = {}
    if m:
        return m, "db_column:daily_kbars.ivol_dn20"
    import pickle
    p = OUT / "ivol_dn20_prodmap.pkl"
    with open(p, "rb") as fh:
        m = pickle.load(fh)
    return m, f"prodmap_pickle:{p.name}"


def main():
    ep = ExecutionPolicy()
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    all_dates = sorted(set(f_dates) | set(w2_dates) | set(w3_dates))
    from app.db.session import SessionLocal
    with SessionLocal() as s:
        iv20map = load_ivol20_pct_map(s, all_dates)
        ivdnmap, dn_src = load_dn_map(s, all_dates)
    print(f"maps ready: ivol20={len(iv20map):,} ivol_dn20={len(ivdnmap):,} source={dn_src}", flush=True)
    WIN = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    res = {"gate": list(GATE), "dn_map_source": dn_src,
           "maps": {"ivol20": len(iv20map), "ivol_dn20": len(ivdnmap)}, "thr": {}}

    for t in THRS:
        print(f"\n========== t={t} @弱势-only（生产列分位） ==========", flush=True)
        rec, pool = {}, {}
        for w, dates, groups in WIN:
            st = stack(groups, iv20map, ivdnmap, t, ep)
            m2, m3, m4 = paid(st["v2"], dates), paid(st["v3"], dates), paid(st["v4"], dates)
            j = pd.concat([m3["g"].rename("b"), m4["g"].rename("v")], axis=1).fillna(0.0)
            dd = (j.v - j.b).dropna(); lo, hi = _bootstrap_ci(dd.values, draws=5000)
            for k, ser in dd.groupby(dd.index.str[:7]):
                pool.setdefault(k, []).append(ser.values)
            rec[w] = dict(d=float(m4["cohort"] - m3["cohort"]), ci=[lo, hi],
                          d_v2v3=float(m3["cohort"] - m2["cohort"]),
                          d_v2v4=float(m4["cohort"] - m2["cohort"]),
                          m2=dict(n=m2["n"], avg=float(m2["avg"]), win=float(m2["win"]), pf=float(m2["pf"])),
                          m3=dict(n=m3["n"], avg=float(m3["avg"]), win=float(m3["win"]), pf=float(m3["pf"])),
                          m4=dict(n=m4["n"], avg=float(m4["avg"]), win=float(m4["win"]), pf=float(m4["pf"])))
            d3 = pd.DataFrame(st["v3"]); d4 = pd.DataFrame(st["v4"])
            rm = d3.merge(d4, on=["dt", "code"], how="left", indicator=True)
            rem = rm[rm._merge == "left_only"]; pr = rm[rm._merge == "right_only"]
            rec[w]["removed"] = dict(n=int(len(rem)), regimes=rem.reg_x.value_counts().to_dict(),
                                     mean_net=float(rem.net_x.mean()) if len(rem) else None)
            rec[w]["promoted"] = dict(n=int(len(pr)), regimes=pr.reg_y.value_counts().to_dict(),
                                      mean_net=float(pr.net_y.mean()) if len(pr) else None)
            print(f"  {w} v3 n{m3['n']} 均{m3['avg']:+.3f} PF{m3['pf']:.2f} → "
                  f"v4 n{m4['n']} 均{m4['avg']:+.3f} PF{m4['pf']:.2f} | "
                  f"Δ(v3→v4) {rec[w]['d']:+.4f} CI[{lo:+.3f},{hi:+.3f}] | "
                  f"Δ(v2→v3) {rec[w]['d_v2v3']:+.4f} Δ(v2→v4) {rec[w]['d_v2v4']:+.4f}", flush=True)
            print(f"      被砍 {rec[w]['removed']['n']} 张 {rec[w]['removed']['regimes']} "
                  f"均{rec[w]['removed']['mean_net']} | 递补 {rec[w]['promoted']['n']} 张 "
                  f"均{rec[w]['promoted']['mean_net']}", flush=True)
        pbm = {k: np.concatenate(v) for k, v in pool.items()}
        flat = np.concatenate(list(pbm.values())); bl, bh = pooled_block(pbm)
        mp = float(np.mean([v.mean() > 0 for v in pbm.values()]))
        npos = sum(1 for v in pbm.values() if v.mean() > 0)
        rec["pooled"] = dict(mean=float(flat.mean()), block_ci=[bl, bh], month_pos=mp, nm=len(pbm))
        print(f"  >>> 合并 {flat.mean():+.4f} 月block CI[{bl:+.3f},{bh:+.3f}] 月正{mp:.3f}({npos}/{len(pbm)})", flush=True)
        res["thr"][f"t={t}"] = rec

    (OUT / "v4_production_canonical.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2,
                   default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print("\nsaved output/research/v4_production_canonical.json", flush=True)


if __name__ == "__main__":
    main()
