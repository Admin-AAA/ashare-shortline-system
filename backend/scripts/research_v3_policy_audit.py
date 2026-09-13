"""L-089(候选) 政策级 v2 vs v3【强牛高ivol否决】三窗权威终审（只读，直接加载真实政策）。
与研究重构不同，这里直接 load_production_selection_policy(version=2/3)，冻结走 _run_arm
（policy_experiment 在 v3 启用时自动从 daily_kbars.ivol20 计算当日全市场分位并注入），
前瞻/第三窗用 load_ivol20_pct_map 给真实卡附 ivol20_pct 后走真实 evaluate_selection_policy。
度量：逐笔等权/日cohort(SLEEVE)/配对日差 bootstrap CI/月正/被砍票机会成本/递补/出票天。
另跑 v3 阈值敏感性（0.7/0.75/0.8/0.85/0.9）检验是否 knife-edge。
"""
from __future__ import annotations
import sys, json, dataclasses
from pathlib import Path
import numpy as np, pandas as pd

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext
from app.engine.aggregator import _market_base, _market_sentiment
from app.engine.backtest import ExecutionPolicy
from app.services.backtest.policy_experiment import (
    ProductionPolicyBacktestRunner, ScoredCandidateSnapshot, _purged_split,
    CanonicalPolicyExperimentSpec, _thaw_json)
from app.services.risk_features import load_ivol20_pct_map, attach_ivol20_pct
from app.db.session import SessionLocal
from scripts.research_canonical_two_window import build_forward_canonical, build_market_snapshots, _bootstrap_ci
from scripts.research_third_window import load_run3

OUT = B / "output" / "research"; SLEEVE = 1 / 6


def boot(d, draws=5000): return _bootstrap_ci(np.asarray(d, float), draws=draws)


# ---------- 冻结：_run_arm 真实政策 ----------
def frozen_policy(policy, snap, split):
    runner = ProductionPolicyBacktestRunner(repository=None)
    spec = CanonicalPolicyExperimentSpec.__new__(CanonicalPolicyExperimentSpec)
    for k, v in [("candidate_snapshot", snap), ("score_line", 72.), ("output_count", 10),
                 ("execution_policy", ExecutionPolicy())]:
        object.__setattr__(spec, k, v)
    _, rec = runner._run_arm(spec, policy, split)
    rows = []
    for key in rec["selected_keys"]:
        dt, code = key.split(":", 1)
        tr = (rec["outcomes"].get(key) or {}).get("trade")
        if tr is None or not tr.executed: continue
        rows.append({"dt": dt, "code": code, "net": float(tr.net_return_pct)})
    return pd.DataFrame(rows)


# ---------- 前瞻/第三窗：真实 evaluate + 真实政策，卡附 ivol 分位 ----------
def canon_select(df, snaps, pctmap, policy, dbg=""):
    rows = []; nchg = 0
    base_v2 = None
    for d, gp in df.groupby("dt"):
        ms = snaps.get(d)
        cards = [{"code": str(r.code), "final_score": float(r.score),
                  "industry": str(r.industry), "signal_price": 10.0} for r in gp.itertuples()]
        if pctmap is not None:
            attach_ivol20_pct(cards, d, pctmap)
        res = evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, dict(ms or {})), policy)
        sel = [str(c.get("code")) for c in res.recommendations]
        n_att = sum(c.get("ivol20_pct") is not None for c in cards)
        if dbg and getattr(policy, "strong_high_ivol_veto_enabled", False):
            n_hi = sum((c.get("ivol20_pct") or 0) >= 0.8 for c in cards)
            if n_hi: print(f"    [{dbg}] {d} 候选{len(cards)}附ivol{n_att}高ivol{n_hi} 选中{len(sel)}")
        netmap = {str(r.code): float(r.net) for r in gp.itertuples()}
        for code in sel:
            if code in netmap:
                rows.append({"dt": d, "code": code, "net": netmap[code]})
    return pd.DataFrame(rows)


def cmp(name, a, b, dates):
    """a=baseline v2, b=challenger."""
    ga = a.groupby("dt").net.mean().reindex(dates).fillna(0)
    gb = b.groupby("dt").net.mean().reindex(dates).fillna(0)
    dd = (gb - ga)
    touched = dd[dd.abs() > 1e-9]
    lo, hi = boot(touched.values) if len(touched) else (np.nan, np.nan)
    mo = dd.groupby(dd.index.str[:7]).mean()
    sa, sb = set(zip(a.dt, a.code)), set(zip(b.dt, b.code))
    rem = a[[t in sa - sb for t in zip(a.dt, a.code)]].net
    pro = b[[t in sb - sa for t in zip(b.dt, b.code)]].net
    cum = lambda g: ((1 + g * SLEEVE / 100).cumprod().iloc[-1] - 1) * 100
    print(f"[{name}] 逐笔 {a.net.mean():+.4f}(n{len(a)},胜{(a.net>0).mean():.3f}) -> "
          f"{b.net.mean():+.4f}(n{len(b)},胜{(b.net>0).mean():.3f}) | 日cum {cum(ga):+.2f}->{cum(gb):+.2f} "
          f"出票天{(b.groupby('dt').size()>0).sum()}")
    print(f"     配对Δ{touched.mean():+.4f} CI[{lo:+.4f},{hi:+.4f}] 月正{(mo>0).mean():.2f} "
          f"变动日{len(touched)}(更好{int((touched>0).sum())}/更差{int((touched<0).sum())}) | "
          f"砍n{len(rem)}均{rem.mean() if len(rem) else float('nan'):+.3f}(赢家{int((rem>0).sum())}) "
          f"递补n{len(pro)}均{pro.mean() if len(pro) else float('nan'):+.3f}")
    return {"a_avg": a.net.mean(), "b_avg": b.net.mean(), "lo": lo, "hi": hi,
            "month_pos": float((mo > 0).mean()), "rem_n": len(rem), "rem_avg": float(rem.mean()) if len(rem) else None,
            "rem_win_n": int((rem > 0).sum())}


def main():
    v2 = load_production_selection_policy(version=2)
    result = {}
    # 冻结
    raw = json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text())
    snap = ScoredCandidateSnapshot.from_payload(raw)
    ep = ExecutionPolicy()
    split = _purged_split(tuple(d.signal_date for d in snap.days), purge_days=ep.entry_lookahead_days + ep.horizon_days)
    fz2 = frozen_policy(v2, snap, split)
    # 前瞻/第三窗数据
    fw = build_forward_canonical(); r3 = load_run3()
    r3 = r3[(r3.dt >= "2026-08-07") & (r3.dt <= "2026-09-02")]
    snaps_fw = build_market_snapshots(sorted(fw.dt.unique()))
    snaps_r3 = build_market_snapshots(sorted(r3.dt.unique()))
    fw = fw[fw.dt.isin(snaps_fw)]; r3 = r3[r3.dt.isin(snaps_r3)]
    with SessionLocal() as s:
        pmap_fw = load_ivol20_pct_map(s, sorted(set(fw.dt) | set(r3.dt)))
    fw2 = canon_select(fw, snaps_fw, pmap_fw, v2)
    r32 = canon_select(r3, snaps_r3, pmap_fw, v2)

    for thr in (0.7, 0.75, 0.8, 0.85, 0.9):  # 阈值敏感性
        v3 = dataclasses.replace(load_production_selection_policy(version=3),
                                 strong_high_ivol_pct_threshold=thr)
        print(f"\n############ v3 强牛高ivol否决 thr={thr} ############")
        fz3 = frozen_policy(v3, snap, split)
        fw3 = canon_select(fw, snaps_fw, pmap_fw, v3)
        r33 = canon_select(r3, snaps_r3, pmap_fw, v3)
        result[f"thr{thr}"] = {
            "frozen": cmp("冻结W1", fz2, fz3, sorted(set(fz2.dt) | set(fz3.dt))),
            "forward": cmp("前瞻W2", fw2, fw3, sorted(set(fw2.dt) | set(fw3.dt))),
            "run3": cmp("第三W3", r32, r33, sorted(set(r32.dt) | set(r33.dt)))}
    (OUT / "v3_policy_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
