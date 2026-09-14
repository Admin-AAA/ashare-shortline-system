"""候选出场配置的稳健性检查（逐月同号 + 邻域平台），避免在同一三窗上挑最大值过拟合。

背景：`research_exit_sweep_three_window.py` 已评估约 370 个配置，取"三窗 ΣΔsum 最大"。
按 L-058/L-009，这种"在固定三窗上取最大"必须再验：
  1. **逐月同号**：相对基线（默认 v3）的月度 cohort Δ，正号月份占比是否达标（≥0.8 为强，
     0.6~0.8 记为"方向一致但不够硬"）；
  2. **邻域平台**：候选参数在邻近取值是否同样改善（刀尖最优＝可疑）。

报告口径：逐窗 Δavg / Δsum / ΔWR / ΔPF / ΔMDD，以及合并月度同号率与月数。

只读。
用法：
  cd backend && .venv/bin/python scripts/research_exit_robustness_check.py \
      --mge 6 --min-lock 2.5 --neighbors
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import numpy as np
import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.db.session import SessionLocal  # noqa: E402
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY  # noqa: E402
from app.services.backtest.policy_experiment import (  # noqa: E402
    ScoredCandidateDay,
    _thaw_json,
    _simulate_card,
)
from app.services.risk_features import load_ivol20_pct_map, load_ivol_dn20_pct_map  # noqa: E402
from scripts.research_riskveto_canonical import frozen_groups, canon_groups  # noqa: E402
from scripts.research_off_main_theme_penalty import (  # noqa: E402
    _main_industries_from_db,
    _attach_main_industries,
)
from scripts.research_exit_policy_canonical import (  # noqa: E402
    _bars_for,
    _calendar,
    _closes_for,
    _selected,
)
import scripts.research_exit_policy_canonical as exit_harness  # noqa: E402

BAR_HORIZON = 9


def _variant(mge: float, min_lock: float):
    base = DEFAULT_EXECUTION_POLICY
    return dataclasses.replace(
        base,
        gap_risk=dataclasses.replace(base.gap_risk, max_entry_gap_up_pct=mge),
        trailing=dataclasses.replace(base.trailing, minimum_lock_gain_pct=min_lock),
    )


def _series(policy, days, windows):
    out = {}
    for w, dates, _g in windows:
        rows = []
        for dt, day in days[w].items():
            for card in day.cards:
                tr = (_simulate_card(day, card, policy) or {}).get("trade")
                if tr is not None and tr.executed:
                    rows.append({"dt": dt, "net": float(tr.net_return_pct)})
        df = pd.DataFrame(rows)
        g = df.groupby("dt").net.mean().reindex(sorted(dates)).fillna(0.0)
        out[w] = {"g": g, "n": len(df), "win": float((df.net > 0).mean()),
                  "avg": float(df.net.mean()), "sum": float(df.net.sum())}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mge", type=float, default=6.0)
    ap.add_argument("--min-lock", type=float, default=2.5)
    ap.add_argument("--neighbors", action="store_true")
    args = ap.parse_args()

    exit_harness._POLICY = __import__(
        "app.engine.production_policy", fromlist=["load_production_selection_policy"]
    ).load_production_selection_policy(version=4)
    cal = _calendar()
    idx = {d: i for i, d in enumerate(cal)}
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    windows = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    all_dates = sorted({d for _w, ds, _g in windows for d in ds})
    mi = _main_industries_from_db(all_dates)
    for _w, _d, g in windows:
        _attach_main_industries(g, mi)
    with SessionLocal() as s:
        iv20 = load_ivol20_pct_map(s, all_dates)
        ivdn = load_ivol_dn20_pct_map(s, all_dates)
    picks = {w: _selected(g, iv20, ivdn) for w, _d, g in windows}
    bars, closes = {}, {}
    for w, _d, _g in windows:
        codes = sorted({c for _dt, c in picks[w]})
        need = {d for dt in {x[0] for x in picks[w]} for d in cal[idx[dt] + 1: idx[dt] + 1 + BAR_HORIZON]}
        bars.update(_bars_for(codes, need))
        closes.update(_closes_for(codes, {d for d, _c in picks[w]}))
    days = {}
    for w, _dates, groups in windows:
        per_day = {}
        for dt, _cards, _nm, day in groups:
            sel = [(c, picks[w][(dt, c)]) for (d, c) in picks[w] if d == dt]
            if not sel:
                continue
            built = {code: tuple(bars[(code, x)] for x in cal[idx[dt] + 1: idx[dt] + 1 + BAR_HORIZON]
                                 if (code, x) in bars) for code, _card in sel}
            built = {k: v for k, v in built.items() if v}
            cards = []
            for code, card in sel:
                cc = dict(card)
                if not cc.get("recommend_price"):
                    cc["recommend_price"] = closes.get((code, dt))
                cards.append(cc)
            per_day[dt] = ScoredCandidateDay(signal_date=dt,
                                             market_snapshot=_thaw_json(day.market_snapshot),
                                             cards=tuple(cards), outcome_bars=built)
        days[w] = per_day

    base = _series(DEFAULT_EXECUTION_POLICY, days, windows)
    variants = [(f"mge{args.mge:g}_ml{args.min_lock:g}", _variant(args.mge, args.min_lock))]
    if args.neighbors:
        for mge in (5.0, 6.0, 7.0):
            for ml in (1.5, 2.5, 3.5):
                variants.append((f"mge{mge:g}_ml{ml:g}", _variant(mge, ml)))
    print(f"基线 = 已发布 {DEFAULT_EXECUTION_POLICY.version}；候选 = {len(variants)} 个\n")
    print(f"{'variant':16s} {'W1 Δavg/Δsum/ΔWR':>26s} {'W2':>26s} {'W3':>26s} {'月同号':>8s} {'月数':>5s}")
    for name, pol in variants:
        got = _series(pol, days, windows)
        cells, months_all = [], {}
        for w in ("W1", "W2", "W3"):
            b, g = base[w], got[w]
            cells.append(f"{g['avg']-b['avg']:+.3f}/{g['sum']-b['sum']:+.0f}/"
                         f"{(g['win']-b['win'])*100:+.1f}pp")
            dd = (g["g"] - b["g"]).dropna()
            for k, v in dd.groupby(dd.index.str[:7]).mean().items():
                months_all.setdefault(k, []).append(v)
        pooled = np.concatenate([np.array(v) for v in months_all.values()]) if months_all else np.array([])
        pos = float((pooled > 0).mean()) if pooled.size else float("nan")
        print(f"{name:16s} {cells[0]:>26s} {cells[1]:>26s} {cells[2]:>26s} {pos:>8.2f} {len(months_all):>5d}")


if __name__ == "__main__":
    main()
