"""生产同构 canonical 三窗终审：在 v3（强牛×高 ivol 否决）之上叠加/替换【市场 beta】风险否决。

口径（严格遵守 L-071/L-084/L-093）：
- 冻结 W1：holdout_244d 快照 + 真实 evaluate_selection_policy→apply_pool_rules + _simulate_card 因果出场。
- 前瞻 W2 / 第三窗 W3：canonical run（btr_fa625 / btr_2bec6fed）真实分数 + 真实 evaluate_selection_policy
  选哪些 + join canonical 净收益。真实递补（剔除后由下一名补位，不空仓）。
- ivol20 分位用【生产持久化列】daily_kbars.ivol20（load_ivol20_pct_map），保证 v3 基线可复现；
  beta20 分位用研究面板 resid_downside_panel.pkl（同一 20 日滚动回归的 beta，尚未持久化——若结论
  成立需按 ivol20 同路径落列）。
臂：
  P2   = v2（无 ivol 否决）                      基线
  P3   = v3（强牛×ivol top20% 硬否决）          现行生产
  P3+B = v3 + beta top20% 硬否决（gate 可配）
  P2+B = v2 + beta top20% 硬否决（单独 beta）
  P3+R = v3 + beta 反向（低 beta top20% 硬否决，用于确认符号不是伪影）
只读，不改生产。
"""
from __future__ import annotations
import sys, json, argparse, warnings
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
THR = 0.80


def beta_pct_map() -> dict:
    p = pd.read_pickle(OUT / "resid_downside_panel.pkl")[["trade_date", "code", "beta20"]].dropna()
    p["pct"] = p.groupby("trade_date")["beta20"].rank(pct=True)
    return {(r.trade_date, str(r.code)): float(r.pct) for r in p.itertuples()}


def _run(groups, ivolmap, betamap, pol_engine, beta_gate, beta_dir, beta_thr, ep):
    """pol_engine: 引擎内政策（v2/v3）。beta_gate: 允许 beta 否决的 regime 集合（None=全部）。
    beta_dir: 'high'=剔高 beta, 'low'=剔低 beta。返回各臂交易行 + 变动明细。"""
    arms = {k: [] for k in ("P2", "P3", "P3B", "P2B", "P3R")}
    for dt, cards, netmap, dayobj in groups:
        ms = dayobj.market_snapshot if dayobj is not None else {}
        reg = _market_base(ms)
        ctx = SelectionPolicyContext(72.0, 10, ms)
        cm = {str(c.get("code")): c for c in cards}

        def net_of(code):
            if netmap is not None:
                return netmap.get(code)
            tr = (_simulate_card(dayobj, cm[code], ep) or {}).get("trade")
            return float(tr.net_return_pct) if (tr is not None and tr.executed) else None

        v2 = load_production_selection_policy(version=2)
        v3 = load_production_selection_policy(version=3)

        # --- 基线臂 ---
        s2 = {str(c.get("code")) for c in evaluate_selection_policy(cards, ctx, v2).recommendations}
        # --- v3 臂（引擎内 ivol 否决；注意必须先做 base 再 attach，避免污染）---
        import copy
        cards3 = copy.deepcopy(cards)
        attach_ivol20_pct(cards3, dt, ivolmap)
        s3 = {str(c.get("code")) for c in evaluate_selection_policy(cards3, ctx, v3).recommendations}

        def beta_tox(code, high=True):
            v = betamap.get((dt, str(code)))
            if v is None:
                return False
            if beta_gate is not None and reg not in beta_gate:
                return False
            return (v >= beta_thr) if high else (v <= (1 - beta_thr))

        keepB = [c for c in cards3 if not beta_tox(c.get("code"), beta_dir == "high")]
        s3b = {str(c.get("code")) for c in evaluate_selection_policy(keepB, ctx, v3).recommendations}
        keep2B = [c for c in cards if not beta_tox(c.get("code"), beta_dir == "high")]
        s2b = {str(c.get("code")) for c in evaluate_selection_policy(keep2B, ctx, v2).recommendations}
        keepR = [c for c in cards3 if not beta_tox(c.get("code"), False)]
        s3r = {str(c.get("code")) for c in evaluate_selection_policy(keepR, ctx, v3).recommendations}

        for key, sel in (("P2", s2), ("P3", s3), ("P3B", s3b), ("P2B", s2b), ("P3R", s3r)):
            for code in sel:
                v = net_of(code)
                if v is not None:
                    arms[key].append({"dt": dt, "code": code, "net": v, "reg": reg})
    return arms


def _summary(rows, all_dates, base_rows):
    m = _metrics(rows, all_dates); mb = _metrics(base_rows, all_dates)
    j = pd.concat([mb["g"].rename("b"), m["g"].rename("v")], axis=1).fillna(0.0)
    dd = (j.v - j.b).dropna()
    lo, hi = _bootstrap_ci(dd.values, draws=5000)
    return m, mb, float(m["cohort"] - mb["cohort"]), (lo, hi), dd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="beta_canonical_three_window.json")
    ap.add_argument("--beta_thr", type=float, default=THR)
    ap.add_argument("--regimes", default="强牛")  # 逗号分隔，ALL=全部
    args = ap.parse_args()
    gate = None if args.regimes.upper() == "ALL" else tuple(args.regimes.split(","))
    ep = ExecutionPolicy()
    print(f"beta 门控 regime = {gate or '全部'} thr={args.beta_thr}")

    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    all_dates = sorted(set(f_dates) | set(w2_dates) | set(w3_dates))
    with __import__("app.db.session", fromlist=["SessionLocal"]).SessionLocal() as s:
        ivolmap = load_ivol20_pct_map(s, all_dates)
    bmap = beta_pct_map()
    print(f"ivolmap {len(ivolmap):,}  betamap {len(bmap):,}")

    res = {}
    for nm, dates, groups in (("W1_frozen", f_dates, f_groups),
                              ("W2_forward", w2_dates, w2_groups),
                              ("W3_third", w3_dates, w3_groups)):
        arms = _run(groups, ivolmap, bmap, None, gate, "high", args.beta_thr, ep)
        base = arms["P2"]
        print(f"\n########## {nm} ##########")
        out = {}
        for key, label in (("P2", "v2基线"), ("P3", "v3(iv)"), ("P3B", "v3+beta"),
                           ("P2B", "v2+beta"), ("P3R", "v3+低beta反向")):
            m, mb, dc, ci, dd = _summary(arms[key], dates, base)
            out[key] = {"n": m["n"], "avg": m["avg"], "win": m["win"], "pf": m["pf"],
                        "cohort": m["cohort"], "d_cohort": dc, "ci": list(ci)}
            print(f"  {label:<14} n{m['n']:>5} 逐笔{m['avg']:>+7.3f} 胜{m['win']:.3f} PF{m['pf']:>5.2f} "
                  f"cohort{m['cohort']:>+8.4f}  Δcohort{dc:>+8.4f} CI[{ci[0]:+.4f},{ci[1]:+.4f}]")
        # 变动明细
        s2 = {(r["dt"], r["code"]): r["net"] for r in arms["P2"]}
        for key in ("P3", "P3B"):
            sk = {(r["dt"], r["code"]): r["net"] for r in arms[key]}
            rm = [v for k, v in s2.items() if k not in sk]
            pr = [v for k, v in sk.items() if k not in s2]
            if rm or pr:
                print(f"    {key} vs P2: 砍 n{len(rm)} 均{np.mean(rm) if rm else float('nan'):+.3f} | "
                      f"递补 n{len(pr)} 均{np.mean(pr) if pr else float('nan'):+.3f}")
                out[key + "_removed"] = {"n": len(rm), "mean": float(np.mean(rm)) if rm else None}
                out[key + "_promoted"] = {"n": len(pr), "mean": float(np.mean(pr)) if pr else None}
        res[nm] = out
        # 逐月同号
        for key in ("P3", "P3B"):
            m, mb, dc, ci, dd = _summary(arms[key], dates, base)
            mo = dd.groupby(dd.index.str[:7]).mean()
            out[key + "_month_pos"] = float((mo > 0).mean())
            out[key + "_nmonths"] = int(len(mo))
            print(f"    {key} 逐月正Δ占比 {float((mo>0).mean()):.2f} ({int(len(mo))}m)")

    (OUT / args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2,
                                           default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print("\nsaved", OUT / args.out)


if __name__ == "__main__":
    main()
