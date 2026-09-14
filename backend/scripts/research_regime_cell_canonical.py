"""regime×情绪 16 格在 canonical 三窗的细胞级体检 + 细胞级提线候选测量。

为什么是这一轮：L-060 明确"任何 regime 规则必须按 regime×情绪二维大样本验证"，
并据此落地了若干格子的分数线 override。但那些 override 的原始证据来自旧口径；
而且 10 轮下来只重排/剔除当前候选池的杠杆已全部否证，细胞级提线是**同一条被
L-060 授权、却从未在三窗 canonical 上系统扫过**的杠杆。

口径（与 round-8 一致）：选择走真实 `evaluate_selection_policy`（v4 + 预附分位），
出场用 `daily_kbars` 重建 bar 逐笔重算；regime=`_market_base`，情绪=`_market_sentiment`
（两者都是生产同源判定，不读 feature_snapshot_json.market_regime，遵守 L-013）。
候选形态：把某格的 `regime_sentiment_line_overrides` 提到 R（等价于该格出票门槛抬高），
用 `evaluate_challenger()` 判闸门（L-010）。

只读，不改生产代码。
用法：cd backend && .venv/bin/python scripts/research_regime_cell_canonical.py
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.db.session import SessionLocal  # noqa: E402
from app.engine.aggregator import _market_base, _market_sentiment  # noqa: E402
from app.engine.backtest import ExecutionPolicy  # noqa: E402
from app.engine.production_policy import (  # noqa: E402
    load_production_selection_policy,
)
from app.services.backtest.challenger_decision import (  # noqa: E402
    PerformanceSnapshot,
    evaluate_challenger,
)
from app.services.backtest.policy_experiment import (  # noqa: E402
    ScoredCandidateDay,
    _simulate_card,
)
from app.services.risk_features import (  # noqa: E402
    load_ivol20_pct_map,
    load_ivol_dn20_pct_map,
    attach_ivol20_pct,
    attach_ivol_dn20_pct,
)
from scripts.research_canonical_two_window import _bootstrap_ci  # noqa: E402
from scripts.research_riskveto_canonical import frozen_groups, canon_groups  # noqa: E402
from scripts.research_off_main_theme_penalty import (  # noqa: E402
    EXPECTED_BASELINE,
    OUTPUT_COUNT,
    SCORE_LINE,
    _main_industries_from_db,
    _attach_main_industries,
)
from scripts.research_exit_policy_canonical import (  # noqa: E402
    _bars_for,
    _calendar,
    _closes_for,
)
import scripts.research_exit_policy_canonical as exit_harness  # noqa: E402

OUT = B / "output" / "research"


def _select(policy, groups, iv20, ivdn):
    """返回 {(dt, code): card}（该 policy 下的选择）。"""
    picks = {}
    for dt, cards, _nm, day in groups:
        ms = dict(getattr(day, "market_snapshot", {}) or {})
        cc = [dict(c) for c in cards]
        attach_ivol20_pct(cc, dt, iv20)
        attach_ivol_dn20_pct(cc, dt, ivdn)
        from app.engine.production_policy import evaluate_selection_policy, SelectionPolicyContext

        for c in evaluate_selection_policy(
            cc, SelectionPolicyContext(SCORE_LINE, OUTPUT_COUNT, ms), policy
        ).recommendations:
            picks[(dt, str(c.get("code")))] = dict(c)
    return picks


def _sim_window(w, groups, picks, ep, bars, closes):
    rows = []
    for dt, _cards, _nm, day in groups:
        sel = [(c, picks[(dt, c)]) for (d, c) in picks if d == dt]
        if not sel:
            continue
        ms = dict(getattr(day, "market_snapshot", {}) or {})
        built = {}
        for code, _card in sel:
            bs = tuple(bars[(code, d)] for d in exit_harness.bars_dates[dt] if (code, d) in bars)
            if bs:
                built[code] = bs
        use = ScoredCandidateDay(
            signal_date=dt, market_snapshot=ms,
            cards=tuple(c for _c, c in sel), outcome_bars=built,
        )
        for code, card in sel:
            cc = dict(card)
            if not cc.get("recommend_price"):
                cc["recommend_price"] = closes.get((code, dt))
            tr = (_simulate_card(use, cc, ep) or {}).get("trade")
            if tr is not None and tr.executed:
                rows.append({"dt": dt, "code": code, "net": float(tr.net_return_pct),
                             "window": w, "regime": _market_base(ms), "sent": _market_sentiment(ms)})
    return rows


def _stats(rows) -> dict:
    if not rows:
        return {"n": 0, "win": 0.0, "avg": 0.0, "pf": float("nan"), "sum_net": 0.0}
    df = pd.DataFrame(rows)
    gp, gl = df.net[df.net > 0].sum(), abs(df.net[df.net < 0].sum())
    return {"n": len(df), "win": float((df.net > 0).mean()), "avg": float(df.net.mean()),
            "pf": float(gp / gl) if gl > 1e-9 else float("inf"), "sum_net": float(df.net.sum())}


def _gate(rows, base_rows, dates) -> dict:
    def series(rs):
        dd = pd.DataFrame(rs)
        return dd.groupby("dt").net.mean().reindex(sorted(dates)).fillna(0.0) if len(dd) else pd.Series(dtype=float)

    gb, gc = series(base_rows), series(rows)
    dd = (gc - gb).values
    lo, hi = _bootstrap_ci(dd, draws=5000)
    b, c = _stats(base_rows), _stats(rows)
    dec = evaluate_challenger(
        PerformanceSnapshot(b["avg"], b["sum_net"], b["win"] * 100, b["pf"], 0.0),
        PerformanceSnapshot(c["avg"], c["sum_net"], c["win"] * 100, c["pf"], 0.0),
    )
    return {"d_cohort": float(np.mean(dd)) if len(dd) else 0.0, "ci": [lo, hi], "decision": dec.decision}


def main() -> None:
    ep = ExecutionPolicy()
    exit_harness._POLICY = load_production_selection_policy(version=4)
    cal = _calendar()
    idx = {d: i for i, d in enumerate(cal)}
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    windows = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    all_dates = sorted({d for _w, ds, _g in windows for d in ds})
    exit_harness.bars_dates = {d: cal[idx[d] + 1: idx[d] + 1 + 9] for d in all_dates if d in idx}
    mi = _main_industries_from_db(all_dates)
    for _w, _d, g in windows:
        _attach_main_industries(g, mi)
    with SessionLocal() as s:
        iv20 = load_ivol20_pct_map(s, all_dates)
        ivdn = load_ivol_dn20_pct_map(s, all_dates)

    v4 = load_production_selection_policy(version=4)
    base_picks = {w: _select(v4, g, iv20, ivdn) for w, _d, g in windows}
    codes = sorted({c for w in base_picks for _d, c in base_picks[w]})
    bars, closes = {}, {}
    for w, _d, g in windows:
        need = {x for dt in {d for d, _c in base_picks[w]} for x in exit_harness.bars_dates[dt]}
        bars.update(_bars_for(sorted({c for _d, c in base_picks[w]}), need))
        closes.update(_closes_for(sorted({c for _d, c in base_picks[w]}), {d for d, _c in base_picks[w]}))

    base_rows = {}
    for w, dates, groups in windows:
        rows = _sim_window(w, groups, base_picks[w], ep, bars, closes)
        base_rows[w] = rows
        st = _stats(rows)
        exp_n, _exp = EXPECTED_BASELINE[w]
        if st["n"] != exp_n:
            raise SystemExit(f"[{w}] 基线漂移 n={st['n']} 期望 {exp_n}")
        print(f"[{w}] n{st['n']} 胜{st['win']*100:.1f}% 均{st['avg']:+.4f} PF{st['pf']:.3f} sum{st['sum_net']:+.2f}", flush=True)

    print("\n=== regime×情绪 单元格（三窗）===")
    cells = {}
    for w in ("W1", "W2", "W3"):
        df = pd.DataFrame(base_rows[w])
        if not len(df):
            continue
        for (reg, se), gp in df.groupby(["regime", "sent"]):
            cells.setdefault((reg, se), {})[w] = _stats(gp.to_dict("records"))
    bad = []
    for key, byw in sorted(cells.items()):
        line = f"{key[0]}·{key[1]:2s} " + " | ".join(
            f"{w} n{byw.get(w,{}).get('n',0):>4} 胜{byw.get(w,{}).get('win',0)*100:4.1f}% 均{byw.get(w,{}).get('avg',0):+.3f}"
            for w in ("W1", "W2", "W3"))
        print(line)
        if all(w in byw and byw[w]["n"] >= 20 and byw[w]["avg"] < 0 for w in ("W1", "W2", "W3")):
            bad.append(key)
    print(f"\n三窗均负且每窗 n≥20 的格子：{bad if bad else '（无）'}")

    results = {}
    for key in bad:
        label = f"{key[0]}·{key[1]}"
        for line_value in (76.0, 85.0):
            ov = dict(v4.regime_sentiment_line_overrides)
            ov[label] = line_value
            pol = dataclasses.replace(v4, regime_sentiment_line_overrides=tuple(ov.items()))
            picks = {w: _select(pol, g, iv20, ivdn) for w, _d, g in windows}
            per_window = {}
            for w, dates, groups in windows:
                rows = _sim_window(w, groups, picks[w], ep, bars, closes)
                per_window[w] = {**_stats(rows), **_gate(rows, base_rows[w], dates)}
            results[f"{label}→{line_value:g}"] = per_window
            print(f"\n[{label} 提线到 {line_value:g}] " + " | ".join(
                f"{w} n{per_window[w]['n']} 胜{per_window[w]['win']*100:.1f}% 均{per_window[w]['avg']:+.3f} "
                f"Δ{per_window[w]['d_cohort']:+.4f} CI[{per_window[w]['ci'][0]:+.3f},{per_window[w]['ci'][1]:+.3f}] "
                f"闸门{per_window[w]['decision']}" for w in ("W1", "W2", "W3")), flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "regime_cell_canonical.json").write_text(
        json.dumps({"cells": {f"{k[0]}·{k[1]}": v for k, v in cells.items()},
                    "bad_cells": [list(k) for k in bad], "candidates": results},
                   ensure_ascii=False, indent=2, default=str))
    print(f"\nsaved {OUT / 'regime_cell_canonical.json'}")


if __name__ == "__main__":
    main()
