"""分策略消融的【配对日差 bootstrap CI + 逐月同号】终审（生产同构全管线重放，只读）。

research_strategy_exclude_canonical 只打印聚合，本脚本补落地门槛所需的推断量：
对每个目标策略，A=全部、B=排除，按交易日对齐 cohort（空日记0），delta=B-A，
bootstrap 4000 次(seed7)求日差均值 95% CI，并算逐月同号占比、逐笔差。
三窗顺序跑，结果写 output/research/per_strategy/ablate_ci.json。
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import numpy as np
import pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.db.session import SessionLocal
from app.engine.backtest import ExecutionPolicy
from app.engine.production_policy import load_production_selection_policy
from scripts.research_strategy_exclude_canonical import build, arm_trades, enabled_skill_ids

WINDOWS = {
    "frozen": ("2025-01-15", "2026-01-16"),
    "forward": ("2026-02-09", "2026-08-05"),
    "run3": ("2026-08-07", "2026-09-02"),
}
TARGETS = ["small-cap-thematic-momentum", "sector-relative-strength-leader"]


def daily_series(df: pd.DataFrame, days) -> pd.Series:
    s = df.groupby("dt").net.mean()
    return s.reindex(days).fillna(0.0)


def boot_ci(delta: np.ndarray, draws: int = 4000, seed: int = 7):
    rng = np.random.default_rng(seed)
    n = len(delta)
    means = np.empty(draws)
    for i in range(draws):
        means[i] = delta[rng.integers(0, n, n)].mean()
    return float(np.mean(delta)), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def monthly_sign(delta_by_day: pd.Series) -> tuple[float, dict]:
    m = delta_by_day.groupby(delta_by_day.index.str[:7]).mean()
    pos = int((m > 0).sum()); neg = int((m < 0).sum())
    ratio = pos / max(pos + neg, 1)
    return ratio, {k: round(float(v), 4) for k, v in m.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", nargs="*", default=TARGETS)
    args = ap.parse_args()
    ep = ExecutionPolicy(); pol = load_production_selection_policy()  # 当前生产默认版本（v4）
    out = {}
    db = SessionLocal()
    try:
        for win, (s, e) in WINDOWS.items():
            print(f"\n===== {win} {s}~{e} =====", flush=True)
            sa = build(db, s, e, None, ep, 72.0, 10)
            ta = arm_trades(sa, pol, ep); del sa
            days = sorted(set(ta.dt))
            da = daily_series(ta, days)
            all_ids = enabled_skill_ids(db)
            out[win] = {"A_n": int(len(ta)), "A_trade_avg": float(ta.net.mean()),
                        "A_cohort": float(da.mean())}
            for t in args.targets:
                keep = [x for x in all_ids if x != t]
                sb = build(db, s, e, keep, ep, 72.0, 10)
                tb = arm_trades(sb, pol, ep); del sb
                db_ = daily_series(tb, sorted(set(da.index) | set(tb.dt)))
                da_ = da.reindex(db_.index).fillna(0.0)
                dlt = (db_ - da_)
                md, lo, hi = boot_ci(dlt.values)
                ms_ratio, ms_map = monthly_sign(dlt)
                ka = set(zip(ta.dt, ta.code)); kb = set(zip(tb.dt, tb.code))
                oa = ta[[(d, c) in ka - kb for d, c in zip(ta.dt, ta.code)]]
                ob = tb[[(d, c) in kb - ka for d, c in zip(tb.dt, tb.code)]]
                rec = {
                    "B_n": int(len(tb)),
                    "cohort_delta": md, "ci_lo": lo, "ci_hi": hi,
                    "days_pos": int((dlt > 0).sum()), "days_neg": int((dlt < 0).sum()),
                    "monthly_pos_ratio": ms_ratio, "monthly": ms_map,
                    "unique_A_cards": int(len(oa)), "unique_A_avg": float(oa.net.mean()) if len(oa) else None,
                    "repl_B_cards": int(len(ob)), "repl_B_avg": float(ob.net.mean()) if len(ob) else None,
                    "B_trade_avg": float(tb.net.mean()),
                }
                out[win][t] = rec
                print(f"  {t}: Δ{md:+.4f} CI[{lo:+.4f},{hi:+.4f}] 日+{rec['days_pos']}/-{rec['days_neg']} "
                      f"月同号{ms_ratio:.2f} 独持{rec['unique_A_cards']}张均{rec['unique_A_avg']} "
                      f"递补{rec['repl_B_cards']}张均{rec['repl_B_avg']}", flush=True)
    finally:
        db.close()
    p = B / "output/research/per_strategy/ablate_ci.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print("\nsaved", p)


if __name__ == "__main__":
    main()
