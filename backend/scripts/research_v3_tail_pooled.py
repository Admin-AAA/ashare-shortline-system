"""L-094(候选) v3 强牛高ivol否决【独立终审·合并显著性+逐日尾部风险】（只读）。
在 research_v3_policy_audit 证实三窗逐笔/cohort 同向的基础上：
1) 三窗合并日 cohort 差 bootstrap、分层(按月)block bootstrap、P(Δ>0)；
2) 逐日 Δ 分布：最好/最差 5 日、负向日占比、最大单日逆向、累计 cohort 改善与最大逆向偏移；
3) 结论用于已切默认 v3 的上线后监控标尺，不改生产。
路径全部走真实生产代码 evaluate_selection_policy / _run_arm（冻结 _simulate_card 因果）。
"""
from __future__ import annotations
import sys, json, dataclasses
from pathlib import Path
import numpy as np, pandas as pd

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext
from app.engine.backtest import ExecutionPolicy
from app.services.backtest.policy_experiment import (
    ProductionPolicyBacktestRunner, ScoredCandidateSnapshot, _purged_split,
    CanonicalPolicyExperimentSpec, _thaw_json, _simulate_card)
from app.services.risk_features import load_ivol20_pct_map, attach_ivol20_pct
from app.db.session import SessionLocal
from scripts.research_canonical_two_window import build_forward_canonical, build_market_snapshots, _bootstrap_ci
from scripts.research_third_window import load_run3

OUT = B / "output" / "research"; rng = np.random.default_rng(7)


def block_boot_ci(dser, draws=5000):
    """按月 block：整月重采样，防同月内日相关。传入日期索引的 Series。"""
    dser = pd.Series(dser).sort_index()
    grp = {m: sub.values for m, sub in dser.groupby(dser.index.astype(str).str[:7])}
    ms = list(grp); n = len(ms); means = []
    for _ in range(draws):
        pick = rng.integers(0, n, n)
        means.append(np.concatenate([grp[ms[i]] for i in pick]).mean())
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def frozen_daily(policy, snap, split):
    runner = ProductionPolicyBacktestRunner(repository=None)
    spec = CanonicalPolicyExperimentSpec.__new__(CanonicalPolicyExperimentSpec)
    for k, v in [("candidate_snapshot", snap), ("score_line", 72.), ("output_count", 10),
                 ("execution_policy", ExecutionPolicy())]:
        object.__setattr__(spec, k, v)
    _, rec = runner._run_arm(spec, policy, split)
    day = {}
    for key in rec["selected_keys"]:
        dt, code = key.split(":", 1)
        tr = (rec["outcomes"].get(key) or {}).get("trade")
        if tr is None or not tr.executed: continue
        day.setdefault(dt, []).append(float(tr.net_return_pct))
    return pd.Series({d: np.mean(v) for d, v in day.items()})


def canon_daily(df, snaps, pmap, policy):
    day = {}
    for d, gp in df.groupby("dt"):
        ms = snaps.get(d)
        cards = [{"code": str(r.code), "final_score": float(r.score),
                  "industry": str(r.industry), "signal_price": 10.0} for r in gp.itertuples()]
        attach_ivol20_pct(cards, d, pmap)
        sel = evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, dict(ms or {})), policy).recommendations
        nm = {str(r.code): float(r.net) for r in gp.itertuples()}
        vals = [nm[str(c["code"])] for c in sel if str(c["code"]) in nm]
        if vals: day[d] = np.mean(vals)
    return pd.Series(day)


def analyze(nm, ga, gb, all_dates):
    ga = ga.reindex(all_dates).fillna(0); gb = gb.reindex(all_dates).fillna(0)
    dd = (gb - ga); touched = dd[dd.abs() > 1e-9].sort_values()
    lo, hi = _bootstrap_ci(touched.values, draws=5000)
    blo, bhi = block_boot_ci(touched)
    p = float((dd.groupby(dd.index.str[:7]).mean() > 0).mean())
    print(f"\n===== {nm} =====")
    print(f"变动日 {len(touched)} | 日Δ均{touched.mean():+.4f} 普通CI[{lo:+.4f},{hi:+.4f}] "
          f"月block CI[{blo:+.4f},{bhi:+.4f}] | 月正{p:.2f} P日Δ>0={(touched>0).mean():.2f}")
    print(f"最差5日: {[(i,round(v,2)) for i,v in touched.head(5).items()]}")
    print(f"最好5日: {[(i,round(v,2)) for i,v in touched.tail(5).items()]}")
    # 累计 cohort 差（空仓日0）与最大逆向偏移
    cum = dd.cumsum(); mfe = cum.cummax(); mae = (cum - mfe).min()
    print(f"累计日cohort差 {cum.iloc[-1]:+.2f}pp | 途中最大逆向偏移 {mae:+.2f}pp | "
          f"负向日{int((touched<0).sum())}/正向{int((touched>0).sum())}")
    return dd


def main():
    v2 = load_production_selection_policy(version=2)
    v3 = dataclasses.replace(load_production_selection_policy(version=3), strong_high_ivol_pct_threshold=0.8)
    raw = json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text())
    snap = ScoredCandidateSnapshot.from_payload(raw); ep = ExecutionPolicy()
    split = _purged_split(tuple(d.signal_date for d in snap.days), purge_days=ep.entry_lookahead_days + ep.horizon_days)
    f2, f3 = frozen_daily(v2, snap, split), frozen_daily(v3, snap, split)
    fw = build_forward_canonical(); r3 = load_run3()
    r3 = r3[(r3.dt >= "2026-08-07") & (r3.dt <= "2026-09-02")]
    snf = build_market_snapshots(sorted(fw.dt.unique())); snr = build_market_snapshots(sorted(r3.dt.unique()))
    fw = fw[fw.dt.isin(snf)]; r3 = r3[r3.dt.isin(snr)]
    with SessionLocal() as s:
        pmap = load_ivol20_pct_map(s, sorted(set(fw.dt) | set(r3.dt)))
    w2a, w2b = canon_daily(fw, snf, pmap, v2), canon_daily(fw, snf, pmap, v3)
    w3a, w3b = canon_daily(r3, snr, pmap, v2), canon_daily(r3, snr, pmap, v3)
    d1 = analyze("冻结W1", f2, f3, sorted(set(f2.index) | set(f3.index)))
    d2 = analyze("前瞻W2", w2a, w2b, sorted(set(w2a.index) | set(w2b.index)))
    d3 = analyze("第三W3", w3a, w3b, sorted(set(w3a.index) | set(w3b.index)))
    # 三窗合并（仅变动日）
    allt = pd.concat([d1, d2, d3]); allt = allt[allt.abs() > 1e-9].sort_index()
    lo, hi = _bootstrap_ci(allt.values, draws=8000); blo, bhi = block_boot_ci(allt)
    print("\n########## 三窗合并 ##########")
    print(f"变动日 {len(allt)} 日Δ均{allt.mean():+.4f} 普通CI[{lo:+.4f},{hi:+.4f}] "
          f"月block CI[{blo:+.4f},{bhi:+.4f}] P日Δ>0={(allt>0).mean():.3f} 月正"
          f"{(allt.groupby(allt.index.str[:7]).mean()>0).mean():.3f}")
    (OUT / "v3_tail_pooled.json").write_text(json.dumps({
        "pooled_n": int(len(allt)), "pooled_mean": float(allt.mean()), "ci": [lo, hi],
        "block_ci": [blo, bhi], "p_day_pos": float((allt > 0).mean())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
