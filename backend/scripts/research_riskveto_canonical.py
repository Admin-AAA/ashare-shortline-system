"""风险/彩票族因子的生产同构【三窗口】否决+递补因果终审（只读，不改生产）。

机制（L-065 合法出路：未挖过的横截面维度）：全市场三窗口分诊发现 ivol20（个股特质波动）
Q5-Q1 三窗同负、|IC| 最大，且 Q1→Q5 的"5日内≤-5%大亏率"三窗单调飙升（4%→23% / 14%→35%
/ 4%→26%）。生产为非对称因果出场（+6%止盈/移动止盈截断赢家，-4.5%止损+跳空放大输家），
全市场均值在常态市接近打平，但剔除高左尾风险票可能改善生产净收益——必须在【v2 选中池】用
生产同构选择+因果出场做"全卡 vs 排序前剔除高风险卡让下一名递补"对照才能定论（L-071/L-075）。

三窗口：冻结 holdout_244d（_simulate_card 真实因果）/ 前瞻 btr_fa625 / 第三 btr_2bec6fed。
分位 = 因子值在【当日全市场】横截面 pct（regime 相对，无前视）；pct>thr 即剔除。
"""
from __future__ import annotations
import sys, json, pickle, argparse
from pathlib import Path
import numpy as np, pandas as pd

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import (
    load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext)
from app.services.backtest.policy_experiment import ScoredCandidateSnapshot, _thaw_json, _simulate_card
from app.engine.backtest import ExecutionPolicy
from scripts.research_canonical_two_window import build_forward_canonical, build_market_snapshots, _bootstrap_ci
from scripts.research_third_window import load_run3
from app.engine.aggregator import _market_base

OUT = B / "output" / "research"
PANEL = OUT / "anomaly_panel.pkl"


def factor_pct_lookup(col: str, panel_name: str = "anomaly_panel.pkl") -> dict:
    p = pd.read_pickle(OUT / panel_name)[["trade_date", "code", col]].dropna()
    p["pct"] = p.groupby("trade_date")[col].rank(pct=True)
    return {(r.trade_date, str(r.code)): float(r.pct) for r in p.itertuples()}


def _metrics(rows, all_dates):
    if not rows:
        return {"n": 0, "avg": 0.0, "win": 0.0, "pf": float("nan"), "cohort": 0.0, "g": pd.Series(dtype=float)}
    df = pd.DataFrame(rows)
    win = (df.net > 0).mean()
    gp, gl = df.net[df.net > 0].sum(), abs(df.net[df.net < 0].sum())
    pf = gp / gl if gl > 1e-9 else float("inf")
    g = df.groupby("dt").net.mean().reindex(sorted(all_dates)).fillna(0.0)
    return {"n": len(df), "avg": df.net.mean(), "win": win, "pf": pf, "cohort": g.mean(), "g": g}


def _run_window(dates_groups, pctmap, thr, pol, ep=None, gate_base=None):
    """dates_groups: iterable of (dt, cards, netmap|None, dayobj_for_sim|None).
    gate_base: 若非空，仅当当日大盘状态 ∈ gate_base 时才允许否决（regime 条件化）。
    返回 base/veto 交易行 + removed/promoted 净收益数组。"""
    base, veto, removed, promoted = [], [], [], []
    for dt, cards, netmap, dayobj in dates_groups:
        ms = dayobj.market_snapshot if dayobj is not None else {}
        base_lbl = _market_base(ms)
        gate_ok = (gate_base is None) or (base_lbl in gate_base)
        tox = lambda c: gate_ok and (v := pctmap.get((dt, str(c.get("code"))))) is not None and v > thr
        ctx = SelectionPolicyContext(72., 10, ms)
        s0 = {str(c.get("code")) for c in evaluate_selection_policy(cards, ctx, pol).recommendations}
        keep = [c for c in cards if not tox(c)]
        s1 = {str(c.get("code")) for c in evaluate_selection_policy(keep, ctx, pol).recommendations}
        cm = {str(c.get("code")): c for c in cards}

        def net_of(code):
            if netmap is not None:
                return netmap.get(code)
            tr = (_simulate_card(dayobj, cm[code], ep) or {}).get("trade")
            return float(tr.net_return_pct) if (tr is not None and tr.executed) else None

        for code in s0:
            v = net_of(code)
            if v is not None: base.append({"dt": dt, "code": code, "net": v})
        for code in s1:
            v = net_of(code)
            if v is not None: veto.append({"dt": dt, "code": code, "net": v})
        for code in s0 - s1:
            v = net_of(code)
            if v is not None: removed.append(v)
        for code in s1 - s0:
            v = net_of(code)
            if v is not None: promoted.append(v)
    return base, veto, np.array(removed), np.array(promoted)


def report(nm, all_dates, base, veto, rem, pro):
    mb, mv = _metrics(base, all_dates), _metrics(veto, all_dates)
    j = pd.concat([mb["g"].rename("b"), mv["g"].rename("v")], axis=1).fillna(0.0)
    dd = (j.v - j.b)
    lo, hi = _bootstrap_ci(dd.values, draws=5000)
    monthly = dd.groupby(dd.index.str[:7]).mean()
    samepos = float((np.sign(monthly) > 0).mean())
    better_day = float((dd.values > 0).mean())
    line1 = (f"[{nm}] base n{mb['n']} 均{mb['avg']:+.3f} 胜{mb['win']:.3f} PF{mb['pf']:.2f} cohort{mb['cohort']:+.4f} -> "
             f"veto n{mv['n']} 均{mv['avg']:+.3f} 胜{mv['win']:.3f} PF{mv['pf']:.2f} cohort{mv['cohort']:+.4f}")
    line2 = (f"      Δcohort{mv['cohort']-mb['cohort']:+.4f} 配对日CI[{lo:+.4f},{hi:+.4f}] "
             f"盈天{samepos:.2f} better-day{better_day:.2f}")
    line3 = ""
    if len(rem):
        line3 = (f"      被砍 n{len(rem)} 均{rem.mean():+.3f} 胜{(rem>0).mean():.3f}(正期望{int((rem>0).sum())}) | "
                 f"递补 n{len(pro)} 均{pro.mean() if len(pro) else float('nan'):+.3f} 胜{(pro>0).mean() if len(pro) else float('nan'):.3f}")
    print(line1); print(line2)
    if line3: print(line3)
    return {"mb": {k: mb[k] for k in ("n", "avg", "win", "pf", "cohort")},
            "mv": {k: mv[k] for k in ("n", "avg", "win", "pf", "cohort")},
            "lo": lo, "hi": hi, "samepos": samepos, "better_day": better_day,
            "removed": rem.tolist(), "promoted": pro.tolist()}


def frozen_groups():
    snap = ScoredCandidateSnapshot.from_payload(
        __import__("json").loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    dates = [d.signal_date for d in snap.days]
    groups = [(d.signal_date, _thaw_json(d.cards), None, d) for d in snap.days]
    return dates, groups


def canon_groups(which="forward"):
    fw = build_forward_canonical() if which == "forward" else load_run3()
    dates = sorted(fw.dt.unique())
    snaps = build_market_snapshots(dates)
    have = [d for d in dates if d in snaps]
    fw = fw[fw.dt.isin(have)]
    groups = []
    for d, gp in fw.groupby("dt"):
        cards = [{"code": str(r.code), "final_score": float(r.score),
                  "industry": str(r.industry), "signal_price": 10.0} for r in gp.itertuples()]
        netmap = {str(r.code): float(r.net) for r in gp.itertuples()}
        # 包一个轻量 dayobj 仅提供 market_snapshot
        groups.append((d, cards, netmap, _Snap(snaps[d])))
    return have, groups


class _Snap:
    def __init__(self, ms): self.market_snapshot = dict(ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--factors", default="ivol20,vol20,max1_20,skew20,ret60")
    ap.add_argument("--thrs", default="0.95,0.90,0.85")
    ap.add_argument("--gate", default="none", help="none=全regime; strong=仅强牛; 或逗号分隔的base集合")
    args = ap.parse_args()
    factors = args.factors.split(","); thrs = [float(x) for x in args.thrs.split(",")]
    if args.gate == "none":
        gate = None
    elif args.gate == "strong":
        gate = {"强牛"}
    else:
        gate = set(args.gate.split(","))
    pol = load_production_selection_policy(version=2); ep = ExecutionPolicy()

    print("加载三窗口分组 ...")
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")

    all_res = {}
    for col in factors:
        panel_name = "ivol_variants.pkl" if col.startswith("ivol") and col != "ivol20" else "anomaly_panel.pkl"
        pctmap = factor_pct_lookup(col, panel_name)
        all_res[col] = {}
        for thr in thrs:
            print(f"\n================ {col} gate={args.gate} 否决当日全市场分位 pct>{thr}（top{round((1-thr)*100)}%）================")
            r1 = report("冻结W1", f_dates, *_run_window(f_groups, pctmap, thr, pol, ep, gate))
            r2 = report("前瞻W2", w2_dates, *_run_window(w2_groups, pctmap, thr, pol, gate_base=gate))
            r3 = report("三窗W3", w3_dates, *_run_window(w3_groups, pctmap, thr, pol, gate_base=gate))
            all_res[col][str(thr)] = {"W1": r1, "W2": r2, "W3": r3}
    tag = args.gate.replace(",", "-")
    (OUT / f"riskveto_canonical_{tag}.json").write_text(json.dumps(all_res, ensure_ascii=False, indent=2,
        default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print(f"\nsaved output/research/riskveto_canonical_{tag}.json")


if __name__ == "__main__":
    main()
