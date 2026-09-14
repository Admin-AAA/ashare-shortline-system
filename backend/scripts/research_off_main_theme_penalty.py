"""非主线（industry ∉ 当日 main_industries）分数惩罚的 canonical 三窗测量。

动机：`output/research/live_rule_counterfactual.json` 与 `live_plan_ranking.json`
在 LIVE 已结算面板上给出全表最强单规则——"剔除非主线票"（n_keep 194 /
keep_avg +0.561 / cut_avg -1.764 / gain +1.454 / 逐月同号 5/5）。本轮把它搬到
**canonical 三窗**（W1 冻结 244d / W2 前瞻 117d / W3 第三窗 19d）重新测量，
因为 LIVE 面板不是发布证据（L-006/L-002），且 LIVE 面板的"主线"定义未必与
决策时可得的口径一致。

口径（与 `verify_v4_production_canonical.py` 同族）：
- 真实 `evaluate_selection_policy`（v4 artifact）+ 真实因果出场 + 真实递补。
- main_industries 取自决策当日 `market_snapshot`（T 日收盘可得，非未来函数；
  不读 `feature_snapshot_json.market_regime`，遵守 L-013）。
- 惩罚形态 = 从非主线卡的 `final_score` 减去 P（同时影响排序与过线，
  对应"弃弱留强"语义）；P=0 即基线。
- 证据等级 = 诊断 path A，**不是** live_forward canonical 发布证据。

只读，不改生产代码。
用法：cd backend && .venv/bin/python scripts/research_off_main_theme_penalty.py
"""
from __future__ import annotations

import json
import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.engine.production_policy import (  # noqa: E402
    load_production_selection_policy,
    evaluate_selection_policy,
    SelectionPolicyContext,
)
from app.services.backtest.policy_experiment import _simulate_card  # noqa: E402
from app.engine.backtest import ExecutionPolicy  # noqa: E402
from app.services.risk_features import (  # noqa: E402
    load_ivol20_pct_map,
    load_ivol_dn20_pct_map,
    attach_ivol20_pct,
    attach_ivol_dn20_pct,
)
from scripts.research_canonical_two_window import _bootstrap_ci  # noqa: E402
from scripts.research_riskveto_canonical import (  # noqa: E402
    frozen_groups,
    canon_groups,
    _metrics,
)

OUT = B / "output" / "research"
SCORE_LINE = 72.0
OUTPUT_COUNT = 10
PENALTIES = (0.0, 4.0, 6.0, 8.0, 12.0, 16.0)


def _main_industries(ms: dict) -> set:
    raw = (ms or {}).get("main_industries") or ()
    return {str(x) for x in raw if str(x).strip()}


def _penalized(cards, main, p):
    if p <= 0 or not main:
        return cards
    out = []
    for c in cards:
        cc = dict(c)
        if str(cc.get("industry") or "") not in main:
            cc["final_score"] = float(cc.get("final_score") or 0.0) - p
        out.append(cc)
    return out


def _run_window(groups, p, ep, iv20map, ivdnmap):
    rows = []
    for dt, cards, netmap, dayobj in groups:
        ms = dict(getattr(dayobj, "market_snapshot", {}) or {}) if dayobj is not None else {}
        ctx = SelectionPolicyContext(SCORE_LINE, OUTPUT_COUNT, ms)
        # v3/v4 的 regime 尾部否决读卡上的 ivol20_pct / ivol_dn20_pct（缺字段保守放行）。
        # 不预附分位图 = 基线静默退化回 v1/v2 行为——必须显式附上，否则测的是假基线。
        cc = copy.deepcopy(_penalized(cards, _main_industries(ms), p))
        attach_ivol20_pct(cc, dt, iv20map)
        attach_ivol_dn20_pct(cc, dt, ivdnmap)
        sel = evaluate_selection_policy(cc, ctx, _POLICY).recommendations
        cm = {str(c.get("code")): c for c in cards}
        for c in sel:
            code = str(c.get("code"))
            if netmap is not None:
                net = netmap.get(code)
            else:
                tr = (_simulate_card(dayobj, cm[code], ep) or {}).get("trade")
                net = float(tr.net_return_pct) if (tr is not None and tr.executed) else None
            if net is not None:
                rows.append({"dt": dt, "code": code, "net": float(net)})
    return rows


def _gate_coverage(groups):
    """返回 (有 main_industries 的天数, 总天数)。

    L-109(e)：数据驱动的规则上线前必须核查输入可用性，否则会**静默失效**。
    `build_market_snapshots` 产出的 W2/W3 快照只含涨跌停计数，没有当日行业
    广度，因此本规则的闸门输入在 W2/W3 为 0 覆盖——必须显式判定为
    NOT_EVALUABLE，不能把 Δ≡0 当成"规则无影响"的结论。
    """
    have = 0
    groups = list(groups)
    for _dt, _cards, _netmap, dayobj in groups:
        ms = dict(getattr(dayobj, "market_snapshot", {}) or {}) if dayobj is not None else {}
        if _main_industries(ms):
            have += 1
    return have, len(groups)


_POLICY = None


def _summary(rows, all_dates):
    m = _metrics(rows, all_dates)
    g = m.pop("g")
    cum = g.cumsum()
    peak = cum.cummax()
    m["mdd_cohort"] = float((cum - peak).min()) if len(cum) else 0.0
    m["sum_net"] = float(sum(r["net"] for r in rows)) if rows else 0.0
    return m


def _daily(rows):
    days = sorted({r["dt"] for r in rows})
    return pd.Series(
        {d: float(np.mean([r["net"] for r in rows if r["dt"] == d])) for d in days}, dtype=float
    )


def main():
    global _POLICY
    _POLICY = load_production_selection_policy(version=4)
    ep = ExecutionPolicy()
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    windows = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    all_dates = sorted({d for _w, ds, _g in windows for d in ds})
    from app.db.session import SessionLocal
    with SessionLocal() as s:
        iv20map = load_ivol20_pct_map(s, all_dates)
        ivdnmap = load_ivol_dn20_pct_map(s, all_dates)
    print(f"maps: ivol20={len(iv20map):,} ivol_dn20={len(ivdnmap):,} dates={len(all_dates)}", flush=True)
    evaluable = {}
    for w, dates, groups in windows:
        have, total = _gate_coverage(groups)
        evaluable[w] = have == total and total > 0
        print(f"[{w}] main_industries 覆盖 {have}/{total} 天 → "
              f"{'EVALUABLE' if evaluable[w] else 'NOT_EVALUABLE'}", flush=True)
    res = {
        "policy": "production_selection.v4",
        "policy_note": "v4 artifact；卡片已预附 ivol20_pct/ivol_dn20_pct，v3/v4 regime 尾部否决真实生效",
        "score_line": SCORE_LINE,
        "output_count": OUTPUT_COUNT,
        "penalty_form": "non-main-theme cards: final_score - P (T-day main_industries)",
        "evidence_scope": "diagnostic path A; production-isomorphic policy engine; not live_forward",
        "gate_input": {w: {"have_days": _gate_coverage(g)[0], "total_days": _gate_coverage(g)[1],
                           "evaluable": evaluable[w]}
                       for w, _d, g in windows},
        "baseline": {},
        "windows": {},
    }
    base_rows, base_daily = {}, {}
    for w, dates, groups in windows:
        base_rows[w] = _run_window(groups, 0.0, ep, iv20map, ivdnmap)
        b = _summary(base_rows[w], dates)
        base_daily[w] = _daily(base_rows[w])
        res["baseline"][w] = b
        print(f"[{w}] base n{b['n']} 胜{b['win']:.3f} 均{b['avg']:+.4f} PF{b['pf']:.3f} "
              f"sum{b['sum_net']:+.2f} cohort{b['cohort']:+.4f} mdd{b['mdd_cohort']:+.2f}", flush=True)
    for p in PENALTIES:
        if p == 0.0:
            continue
        print(f"\n===== 非主线罚 {p:g} 分 =====", flush=True)
        per_window = {}
        for w, dates, groups in windows:
            if not evaluable[w]:
                print(f"  {w} NOT_EVALUABLE：当日 main_industries 缺失，规则静默失效，不计入结论", flush=True)
                per_window[w] = {"evaluable": False,
                                 "reason": "T-day main_industries missing from market snapshot"}
                continue
            rows = _run_window(groups, p, ep, iv20map, ivdnmap)
            m = _summary(rows, dates)
            idx = base_daily[w].index.union(_daily(rows).index)
            dd = (_daily(rows).reindex(idx).fillna(0.0) - base_daily[w].reindex(idx).fillna(0.0)).values
            lo, hi = _bootstrap_ci(dd, draws=5000)
            month = pd.Series(dd, index=idx).groupby(lambda x: str(x)[:7]).mean()
            samepos = float((month > 0).mean()) if len(month) else float("nan")
            per_window[w] = {"n": m["n"], "win": m["win"], "avg": m["avg"], "pf": m["pf"],
                             "evaluable": True,
                             "sum_net": m["sum_net"], "cohort": m["cohort"],
                             "mdd_cohort": m["mdd_cohort"], "d_cohort": float(np.mean(dd)),
                             "ci": [lo, hi], "month_same_pos": samepos, "n_months": int(len(month))}
            print(f"  {w} n{m['n']} 胜{m['win']:.3f} 均{m['avg']:+.4f} PF{m['pf']:.3f} "
                  f"sum{m['sum_net']:+.2f} Δcohort{per_window[w]['d_cohort']:+.4f} "
                  f"CI[{lo:+.3f},{hi:+.3f}] 月正{samepos:.2f}", flush=True)
        res["windows"][f"p={p:g}"] = per_window
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "off_main_theme_penalty_canonical.json"
    path.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    print(f"\nsaved {path}", flush=True)


if __name__ == "__main__":
    main()
