"""弱势 regime × 当日涨幅半吊子区间（追高毒区）的 pool 级 canonical 三窗测量。

动机：这套"弱市半吊子追高毒区"知识目前**只存在于 DSA 自己的 strategy.yaml
phases 里**（`daily-stock-analysis-skill/strategy.yaml`：弱势 & pct∈[3,5] →
cap 68；LIVE 依据 n=64 WR 23.4% avg −1.52%，而弱市 pct<0 WR 61.4%、
pct∈[0,3) 66.7%、pct∈[5,7) 70.4%、pct≥9 69.7%）。而 DSA 在
`production_selection.v4` 的 `excluded_20cm_buckets` 里被排除出 20cm 池——
也就是说这条已验证的知识**从未作用到生产 20cm 选择层**。
production_selection 现有字段（strong_score_penalty / weak_low_chg20_penalty /
panic_score_penalty / regime 覆盖线）里没有任何按当日涨幅的毒区规则。

口径（与 `research_off_main_theme_penalty.py` 同一族）：
- 真实 `evaluate_selection_policy`（v4 artifact + 预附 ivol 分位）+ 真实因果出场 + 真实递补。
- `pct` 统一取 `daily_kbars.pct_chg`（已核验与冻结卡 `pct` 逐位一致，最大差 0.0005=卡端 3 位舍入）。
- 只在 `_market_base == '弱势'` 时生效；两个形态：惩罚（final_score − P）与硬剔。
- 先按 L-112(a) 断言基线 n/cohort 必须等于既有 canonical 记录，否则 SystemExit。

只读，不改生产代码。
用法：cd backend && .venv/bin/python scripts/research_weak_chase_poison_penalty.py
"""
from __future__ import annotations

import copy
import json
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
from app.engine.aggregator import _market_base  # noqa: E402
from app.engine.backtest import ExecutionPolicy  # noqa: E402
from app.services.backtest.policy_experiment import _simulate_card  # noqa: E402
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
from scripts.research_off_main_theme_penalty import (  # noqa: E402
    EXPECTED_BASELINE,
    OUTPUT_COUNT,
    SCORE_LINE,
    _daily,
    _main_industries_from_db,
    _summary,
    _attach_main_industries,
)

OUT = B / "output" / "research"
BANDS = ((3.0, 5.0), (2.0, 5.0), (3.0, 6.0))
VARIANTS = (
    ("veto", 0.0),
    ("penalty", 8.0),
    ("penalty", 12.0),
)
_POLICY = None


def _pct_from_db(dates) -> dict:
    from sqlalchemy import text
    from app.db.session import SessionLocal

    out: dict = {}
    with SessionLocal() as s:
        for d in dates:
            rows = s.execute(
                text("SELECT code, pct_chg FROM daily_kbars WHERE trade_date = :d"),
                {"d": d},
            ).all()
            out[d] = {str(c): float(p) for c, p in rows if p is not None}
    return out


def _attach_pct(groups, pct_map) -> int:
    """只补缺 `pct` 的卡（冻结 W1 卡自带且与库值一致），不覆盖既有值。"""
    filled = 0
    for dt, cards, _netmap, _dayobj in groups:
        dm = pct_map.get(dt) or {}
        for c in cards:
            if c.get("pct") is None:
                v = dm.get(str(c.get("code")))
                if v is not None:
                    c["pct"] = v
                    filled += 1
    return filled


def _transform(cards, base, dt_map, band, mode, value):
    if base != "弱势":
        return cards, 0
    lo, hi = band
    out, hit = [], 0
    for c in cards:
        p = dt_map.get(str(c.get("code")))
        if p is not None and lo <= float(p) < hi:
            hit += 1
            if mode == "veto":
                continue
            cc = dict(c)
            cc["final_score"] = float(cc.get("final_score") or 0.0) - value
            out.append(cc)
        else:
            out.append(c)
    return out, hit


def _run_window(groups, band, mode, value, ep, iv20map, ivdnmap, pct_map):
    rows, hits, weak_days = [], 0, 0
    for dt, cards, netmap, dayobj in groups:
        ms = dict(getattr(dayobj, "market_snapshot", {}) or {}) if dayobj is not None else {}
        base = _market_base(ms)
        if base == "弱势":
            weak_days += 1
        cc, hit = _transform(cards, base, pct_map.get(dt) or {}, band, mode, value)
        hits += hit
        cc = copy.deepcopy(cc)
        attach_ivol20_pct(cc, dt, iv20map)
        attach_ivol_dn20_pct(cc, dt, ivdnmap)
        ctx = SelectionPolicyContext(SCORE_LINE, OUTPUT_COUNT, ms)
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
    return rows, {"hits": hits, "weak_days": weak_days}


def main():
    global _POLICY
    _POLICY = load_production_selection_policy(version=4)
    ep = ExecutionPolicy()
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    windows = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    all_dates = sorted({d for _w, ds, _g in windows for d in ds})
    mi_map = _main_industries_from_db(all_dates)
    for _w, _d, g in windows:
        _attach_main_industries(g, mi_map)
    pct_map = _pct_from_db(all_dates)
    filled = sum(_attach_pct(g, pct_map) for _w, _d, g in windows)
    print(f"pct 补齐 {filled} 张卡；main_industries 已就位", flush=True)

    from app.db.session import SessionLocal
    with SessionLocal() as s:
        iv20map = load_ivol20_pct_map(s, all_dates)
        ivdnmap = load_ivol_dn20_pct_map(s, all_dates)

    res = {
        "policy": "production_selection.v4",
        "score_line": SCORE_LINE,
        "output_count": OUTPUT_COUNT,
        "evidence_scope": "diagnostic path A; production-isomorphic policy engine; not live_forward",
        "rule": "weak regime only: pct in [lo,hi) -> penalty or veto; motivation = DSA-level validated poison zone that never reached the 20cm pool",
        "baseline": {},
        "variants": {},
    }
    base_rows, base_daily = {}, {}
    for w, dates, groups in windows:
        rows, _ = _run_window(groups, (0, 0), "penalty", 0.0, ep, iv20map, ivdnmap, pct_map)
        base_rows[w] = rows
        b = _summary(rows, dates)
        base_daily[w] = _daily(rows)
        res["baseline"][w] = b
        exp_n, exp_cohort = EXPECTED_BASELINE[w]
        if b["n"] != exp_n or abs(b["cohort"] - exp_cohort) > 1e-9:
            raise SystemExit(f"[{w}] 基线漂移 n={b['n']}/{exp_n} cohort={b['cohort']:.6f}/{exp_cohort:.6f}")
        print(f"[{w}] base n{b['n']} 胜{b['win']:.3f} 均{b['avg']:+.4f} PF{b['pf']:.3f} "
              f"sum{b['sum_net']:+.2f} cohort{b['cohort']:+.4f}", flush=True)

    for band in BANDS:
        for mode, value in VARIANTS:
            key = f"{'剔' if mode == 'veto' else f'罚{value:g}'}|pct[{band[0]:g},{band[1]:g})"
            print(f"\n===== 弱势专属 {key} =====", flush=True)
            per_window = {}
            for w, dates, groups in windows:
                rows, info = _run_window(groups, band, mode, value, ep, iv20map, ivdnmap, pct_map)
                m = _summary(rows, dates)
                idx = base_daily[w].index.union(_daily(rows).index)
                dd = (_daily(rows).reindex(idx).fillna(0.0) - base_daily[w].reindex(idx).fillna(0.0)).values
                lo, hi = _bootstrap_ci(dd, draws=5000)
                month = pd.Series(dd, index=idx).groupby(lambda x: str(x)[:7]).mean()
                samepos = float((month > 0).mean()) if len(month) else float("nan")
                per_window[w] = {"n": m["n"], "win": m["win"], "avg": m["avg"], "pf": m["pf"],
                                 "sum_net": m["sum_net"], "cohort": m["cohort"],
                                 "d_cohort": float(np.mean(dd)), "ci": [lo, hi],
                                 "month_same_pos": samepos, "n_months": int(len(month)),
                                 "hits": info["hits"], "weak_days": info["weak_days"]}
                print(f"  {w} n{m['n']} 胜{m['win']:.3f} 均{m['avg']:+.4f} PF{m['pf']:.3f} "
                      f"sum{m['sum_net']:+.2f} Δcohort{per_window[w]['d_cohort']:+.4f} "
                      f"CI[{lo:+.3f},{hi:+.3f}] 月正{samepos:.2f} 命中{info['hits']}张/弱势日{info['weak_days']}",
                      flush=True)
            res["variants"][key] = per_window
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "weak_chase_poison_canonical.json"
    path.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    print(f"\nsaved {path}", flush=True)


if __name__ == "__main__":
    main()
