"""本轮生产同构 canonical 三窗终审：base = v3（当前生产默认），treat = v3 + 新增横截面尾部否决。

与 research_riskveto_canonical 的关键区别：
- base 臂不再是 v2，而是 **v3**（当前默认，含强牛高 ivol 否决）。两个臂都给卡片附加真实
  daily_kbars.ivol20 分位，保证 base 确实是生产行为；treat 臂再附研究因子分位并由
  policy 的新通用规则 `extra_tail_veto_*` 完成"硬剔 + 下一名自然递补"。
- 因此这里的 Δ 是**在 v3 之上的增量**（干净的串行 champion 对照，L-016/L-017）。

三窗口：冻结 holdout_244d（_simulate_card 真实因果）/ 前瞻 btr_fa625 / 第三 btr_2bec6fed。
分位 = 因子在【当日全市场】横截面 pct（与研究面板一致，生产侧需另做持久化）。
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import numpy as np
import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
from app.engine.production_policy import (  # noqa: E402
    load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext)
from app.services.risk_features import load_ivol20_pct_map, attach_ivol20_pct  # noqa: E402
from app.engine.backtest import ExecutionPolicy  # noqa: E402
from app.engine.aggregator import _market_base  # noqa: E402
from scripts.research_canonical_two_window import _bootstrap_ci  # noqa: E402
from scripts.research_riskveto_canonical import frozen_groups, canon_groups, _metrics  # noqa: E402

OUT = B / "output" / "research"


def factor_pct_lookup(panel: Path, col: str) -> dict:
    p = pd.read_pickle(panel)[["trade_date", "code", col]].dropna()
    p["trade_date"] = p.trade_date.astype(str)
    p["code"] = p.code.astype(str)
    p["pct"] = p.groupby("trade_date")[col].rank(pct=True, method="average")
    return {(r.trade_date, r.code): float(r.pct) for r in p.itertuples()}


def run_window(dates_groups, ivol_pct, fac_pct, thr, direction, gate, base_pol, treat_pol, ep=None,
               top_n=None):
    base, treat, removed, promoted = [], [], [], []
    hits = 0
    for dt, cards_raw, netmap, dayobj in dates_groups:
        ms = dayobj.market_snapshot if dayobj is not None else {}
        base_lbl = _market_base(ms)
        gate_ok = (gate is None) or (base_lbl in gate)
        cards = [{k: v for k, v in c.items()
                  if k not in ("policy_drop_owner", "policy_verdict_steps")} for c in cards_raw]
        attach_ivol20_pct(cards, dt, ivol_pct)
        ctx = SelectionPolicyContext(72.0, 10, ms)
        recs0 = list(evaluate_selection_policy(cards, ctx, base_pol).recommendations)
        if top_n:
            recs0 = recs0[:top_n]
        s0 = {str(c.get("code")) for c in recs0}

        # treat 臂：给卡片附研究因子分位
        for c in cards:
            v = fac_pct.get((dt, str(c.get("code"))))
            if v is not None:
                c["extra_tail_veto_pct"] = float(v)
        recs1 = list(evaluate_selection_policy(cards, ctx, treat_pol).recommendations)
        if top_n:
            recs1 = recs1[:top_n]
        s1 = {str(c.get("code")) for c in recs1}
        if gate_ok:
            hits += sum(1 for c in cards
                        if c.get("extra_tail_veto_pct") is not None
                        and (c["extra_tail_veto_pct"] >= thr if direction == "high"
                             else c["extra_tail_veto_pct"] <= thr))
        cm = {str(c.get("code")): c for c in cards}

        def net_of(code: str):
            if netmap is not None:
                return netmap.get(code)
            tr = (_simulate(dayobj, cm[code], ep) or {}).get("trade")
            return float(tr.net_return_pct) if (tr is not None and tr.executed) else None

        for code in s0:
            v = net_of(code)
            if v is not None:
                base.append({"dt": dt, "code": code, "net": v})
        for code in s1:
            v = net_of(code)
            if v is not None:
                treat.append({"dt": dt, "code": code, "net": v})
        for code in s0 - s1:
            v = net_of(code)
            if v is not None:
                removed.append(v)
        for code in s1 - s0:
            v = net_of(code)
            if v is not None:
                promoted.append(v)
    return base, treat, np.array(removed), np.array(promoted), hits


def _simulate(dayobj, card, ep):
    from app.services.backtest.policy_experiment import _simulate_card
    return _simulate_card(dayobj, card, ep)


def report(nm, all_dates, base, treat, rem, pro, hits):
    mb, mv = _metrics(base, all_dates), _metrics(treat, all_dates)
    j = pd.concat([mb["g"].rename("b"), mv["g"].rename("v")], axis=1).fillna(0.0)
    dd = (j.v - j.b)
    lo, hi = _bootstrap_ci(dd.values, draws=5000)
    changed = dd[dd.abs() > 1e-12]
    monthly = dd.groupby(dd.index.str[:7]).mean()
    print(f"[{nm}] base n{mb['n']} 均{mb['avg']:+.3f} 胜{mb['win']:.3f} PF{mb['pf']:.2f} cohort{mb['cohort']:+.4f}"
          f" -> treat n{mv['n']} 均{mv['avg']:+.3f} 胜{mv['win']:.3f} PF{mv['pf']:.2f} cohort{mv['cohort']:+.4f}")
    print(f"      Δcohort{mv['cohort']-mb['cohort']:+.4f} 配对日CI[{lo:+.4f},{hi:+.4f}] "
          f"月正{(monthly > 0).mean():.2f} 变动日{len(changed)}(好{int((changed > 0).sum())}/差{int((changed < 0).sum())}) "
          f"触发行{hits}")
    if len(rem):
        print(f"      被砍 n{len(rem)} 均{rem.mean():+.3f} 胜{(rem > 0).mean():.3f}(正期望{int((rem > 0).sum())}) | "
              f"递补 n{len(pro)} 均{pro.mean() if len(pro) else float('nan'):+.3f} "
              f"胜{(pro > 0).mean() if len(pro) else float('nan'):.3f}")
    return {"base": {k: mb[k] for k in ("n", "avg", "win", "pf", "cohort")},
            "treat": {k: mv[k] for k in ("n", "avg", "win", "pf", "cohort")},
            "delta_cohort": float(mv["cohort"] - mb["cohort"]), "ci": [float(lo), float(hi)],
            "month_pos": float((monthly > 0).mean()), "changed_days": int(len(changed)),
            "better_days": int((changed > 0).sum()), "removed_n": int(len(rem)),
            "removed_mean": float(rem.mean()) if len(rem) else None,
            "removed_win": float((rem > 0).mean()) if len(rem) else None,
            "promoted_n": int(len(pro)), "promoted_mean": float(pro.mean()) if len(pro) else None,
            "hits": int(hits)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--factor", default=None)
    ap.add_argument("--factors", default=None, help="逗号分隔的多个因子（共用 direction/thrs/gate）")
    ap.add_argument("--configs", default=None,
                    help="分号分隔的 f|direction|thr|gate，如 'on_minus_id20|low|0.2|none'")
    ap.add_argument("--panel", default="si_rf_panel.pkl")
    ap.add_argument("--direction", default="high", choices=["high", "low"])
    ap.add_argument("--thrs", default="0.80")
    ap.add_argument("--gate", default="strong", help="none | strong | 逗号分隔 regime")
    ap.add_argument("--windows", default="W1,W2,W3")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    def parse_gate(g):
        if g == "none":
            return None
        if g == "strong":
            return {"强牛"}
        if g == "nonstrong":
            return {"弱势", "震荡", "偏多"}
        return set(g.split(","))

    ALL_REGIMES = ("强牛", "偏多", "震荡", "弱势")

    thrs = [float(x) for x in args.thrs.split(",")]
    base_pol = load_production_selection_policy(version=3)
    ep = ExecutionPolicy()

    print("加载三窗口分组 ...", flush=True)
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    all_dates = sorted(set(f_dates) | set(w2_dates) | set(w3_dates))
    from app.db.session import SessionLocal
    with SessionLocal() as s:
        ivol_pct = load_ivol20_pct_map(s, all_dates)
    print(f"ivol_pct={len(ivol_pct):,}", flush=True)

    factors = (args.factors.split(",") if args.factors else [args.factor])
    gates = [g for g in args.gate.split(";")]
    panel = pd.read_pickle(OUT / args.panel)
    # configs: factor|direction|thr|gate
    configs = []
    if args.configs:
        for spec in args.configs.split(";"):
            f, dr, th, gt = spec.split("|")
            configs.append((f, dr, float(th), gt))
    else:
        for fac in factors:
            for gt in gates:
                for thr in thrs:
                    configs.append((fac, args.direction, thr, gt))
    fac_cache: dict[str, dict] = {}
    res = {}
    for fac, direction, thr, gt in configs:
        if fac in ("ivol20", "ivol20_rf"):
            fac_pct = ivol_pct
        elif fac in fac_cache:
            fac_pct = fac_cache[fac]
        else:
            p = panel[["trade_date", "code", fac]].dropna()
            p["trade_date"] = p.trade_date.astype(str)
            p["code"] = p.code.astype(str)
            p["pct"] = p.groupby("trade_date")[fac].rank(pct=True, method="average")
            fac_pct = {(r.trade_date, r.code): float(r.pct) for r in p.itertuples()}
            fac_cache[fac] = fac_pct
        gate = parse_gate(gt)
        treat_pol = __import__("dataclasses").replace(
            base_pol, extra_tail_veto_enabled=True, extra_tail_veto_pct_threshold=thr,
            extra_tail_veto_direction=direction,
            extra_tail_veto_regimes=tuple(sorted(gate)) if gate else ALL_REGIMES)
        key = f"{fac}|{direction}|{thr}|gate={gt}"
        print(f"\n=========== {key} (pct={len(fac_pct):,}) ===========", flush=True)
        res[key] = {}
        if "W1" in args.windows:
            res[key]["W1"] = report("冻结W1", f_dates, *run_window(
                f_groups, ivol_pct, fac_pct, thr, direction, gate, base_pol, treat_pol, ep))
        if "W2" in args.windows:
            res[key]["W2"] = report("前瞻W2", w2_dates, *run_window(
                w2_groups, ivol_pct, fac_pct, thr, direction, gate, base_pol, treat_pol))
        if "W3" in args.windows:
            res[key]["W3"] = report("三窗W3", w3_dates, *run_window(
                w3_groups, ivol_pct, fac_pct, thr, direction, gate, base_pol, treat_pol))
    out = OUT / (args.out or "si_rf_canonical.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2,
                              default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print("\nsaved", out)


if __name__ == "__main__":
    main()
