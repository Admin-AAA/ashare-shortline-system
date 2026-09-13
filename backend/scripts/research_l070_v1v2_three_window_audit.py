"""L-070 第三真前瞻窗独立复核：v1 vs v2（偏多/震荡/弱势·高潮 85→120 硬排除）三窗审计（只读）。

L-070 自验证窗口截至 2026-08-06；本脚本用真实 load_production_selection_policy(version=1/2)
在三个窗口重跑生产同构 evaluate 路径，如实报告复现还是翻号：
  W1 冻结 holdout_244d（_run_arm 完全生产同构，含因果出场）
  W2 真前瞻 2026-02-09~08-06（canonical run，evaluate_selection_policy）
  W3 新第三窗 2026-08-07~09-02（run btr_2bec6fed，evaluate_selection_policy）
度量：逐笔等权/日 cohort(SLEEVE=1/6)/配对日差 bootstrap 95% CI/月正占比/被砍票与递补票经济方向。
只读，不写生产、不消耗 holdout 台账（对照 L-094 先例）。
"""
from __future__ import annotations
import sys, json
from pathlib import Path
import numpy as np, pandas as pd

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext
from app.engine.backtest import ExecutionPolicy
from app.services.backtest.policy_experiment import (
    ProductionPolicyBacktestRunner, ScoredCandidateSnapshot, _purged_split,
    CanonicalPolicyExperimentSpec)
from scripts.research_canonical_two_window import build_forward_canonical, build_market_snapshots, _bootstrap_ci
from scripts.research_third_window import load_run3

OUT = B / "output" / "research"; SLEEVE = 1 / 6


def boot(d, draws=5000): return _bootstrap_ci(np.asarray(d, float), draws=draws)


def policy_diff() -> list[str]:
    """sanity 守卫：确认 v1->v2 语义差异恰好只有三个高潮格 85->120。"""
    p1 = json.loads((B / "app/policies/production_selection.v1.json").read_text())
    p2 = json.loads((B / "app/policies/production_selection.v2.json").read_text())
    diffs: list[str] = []

    def walk(a, b, path=""):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                walk(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        elif a != b:
            diffs.append(f"{path}: {a!r} -> {b!r}")

    walk(p1, p2)
    return diffs


def frozen_arm(policy, snap, split):
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


def canon_select(df, snaps, policy):
    rows = []
    for d, gp in df.groupby("dt"):
        ms = snaps.get(d)
        cards = [{"code": str(r.code), "final_score": float(r.score),
                  "industry": str(r.industry), "signal_price": 10.0} for r in gp.itertuples()]
        res = evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, dict(ms or {})), policy)
        netmap = {str(r.code): float(r.net) for r in gp.itertuples()}
        for c in res.recommendations:
            code = str(c.get("code"))
            if code in netmap:
                rows.append({"dt": d, "code": code, "net": netmap[code]})
    return pd.DataFrame(rows)


def cmp(name, a, b, dates):
    """a=baseline v1（无高潮排除）, b=challenger v2（高潮硬排除）。"""
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
    neg = -a.net[a.net < 0].sum()
    pf = a.net[a.net > 0].sum() / neg if neg > 0 else float("nan")
    negb = -b.net[b.net < 0].sum()
    pfb = b.net[b.net > 0].sum() / negb if negb > 0 else float("nan")
    print(f"[{name}] v1 逐笔{a.net.mean():+.4f}(n{len(a)},胜{(a.net>0).mean():.3f},PF{pf:.2f}) -> "
          f"v2 {b.net.mean():+.4f}(n{len(b)},胜{(b.net>0).mean():.3f},PF{pfb:.2f}) | "
          f"日cum {cum(ga):+.2f}->{cum(gb):+.2f} 出票天{(b.groupby('dt').size()>0).sum()}")
    print(f"     配对Δ{touched.mean():+.4f} CI[{lo:+.4f},{hi:+.4f}] 月正{(mo>0).mean():.2f} "
          f"变动日{len(touched)}(v2更好{int((touched>0).sum())}/更差{int((touched<0).sum())}) | "
          f"被砍n{len(rem)}均{rem.mean() if len(rem) else float('nan'):+.3f}(其中赢家{int((rem>0).sum())}) "
          f"递补n{len(pro)}均{pro.mean() if len(pro) else float('nan'):+.3f}")
    return {"v1_avg": float(a.net.mean()) if len(a) else None, "v2_avg": float(b.net.mean()) if len(b) else None,
            "v1_n": int(len(a)), "v2_n": int(len(b)),
            "paired_delta": float(touched.mean()) if len(touched) else None,
            "ci_lo": float(lo) if len(touched) else None, "ci_hi": float(hi) if len(touched) else None,
            "touched_days": int(len(touched)), "v2_better": int((touched > 0).sum()),
            "v2_worse": int((touched < 0).sum()), "month_pos": float((mo > 0).mean()) if len(mo) else None,
            "removed_n": int(len(rem)), "removed_avg": float(rem.mean()) if len(rem) else None,
            "removed_winners": int((rem > 0).sum()),
            "added_n": int(len(pro)), "added_avg": float(pro.mean()) if len(pro) else None}


def main():
    diffs = policy_diff()
    print("== v1->v2 政策语义差异（sanity 守卫）==")
    for d in diffs: print("  ", d)
    v1 = load_production_selection_policy(version=1)
    v2 = load_production_selection_policy(version=2)
    result = {"policy_diff": diffs, "windows": {}}

    # W1 冻结
    raw = json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text())
    snap = ScoredCandidateSnapshot.from_payload(raw)
    ep = ExecutionPolicy()
    split = _purged_split(tuple(d.signal_date for d in snap.days),
                          purge_days=ep.entry_lookahead_days + ep.horizon_days)
    fz1 = frozen_arm(v1, snap, split)
    fz2 = frozen_arm(v2, snap, split)
    result["windows"]["frozen_244d"] = cmp("冻结W1", fz1, fz2, sorted(set(fz1.dt) | set(fz2.dt)))

    # W2 真前瞻
    fw = build_forward_canonical()
    snaps_fw = build_market_snapshots(sorted(fw.dt.unique()))
    fw = fw[fw.dt.isin(snaps_fw)]
    fw1 = canon_select(fw, snaps_fw, v1)
    fw2 = canon_select(fw, snaps_fw, v2)
    result["windows"]["forward_0209_0806"] = cmp("前瞻W2", fw1, fw2, sorted(set(fw1.dt) | set(fw2.dt)))

    # W3 新第三窗
    r3 = load_run3()
    snaps_r3 = build_market_snapshots(sorted(r3.dt.unique()))
    r3 = r3[r3.dt.isin(snaps_r3)]
    r31 = canon_select(r3, snaps_r3, v1)
    r32 = canon_select(r3, snaps_r3, v2)
    result["windows"]["third_0807_0902"] = cmp("第三W3", r31, r32, sorted(set(r31.dt) | set(r32.dt)))

    (OUT / "l070_v1v2_three_window_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print("\nsaved ->", OUT / "l070_v1v2_three_window_audit.json")


if __name__ == "__main__":
    main()
