"""canonical 生产选股政策网格 + 归因（治理口径，无 amount bug）。

走真实 evaluate_selection_policy + app.engine.simulation.simulate（因果止损止盈）。
重点：weak_regime_score_cap 网格（冠军=60），并把"选中交易"按 regime×情绪归因，
定位收益改善/恶化来自哪些格子。只读。
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
from collections import defaultdict

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.engine.backtest import ExecutionPolicy  # noqa: E402
from app.engine.production_policy import DEFAULT_PRODUCTION_SELECTION_POLICY as CHAMP  # noqa: E402
from app.engine.aggregator import _market_base, _market_sentiment  # noqa: E402
from app.services.backtest.policy_experiment import (  # noqa: E402
    CanonicalPolicyExperimentSpec,
    ProductionPolicyBacktestRunner,
    ScoredCandidateSnapshot,
    _purged_split,
)

SNAP = BACKEND / "output" / "frozen_snapshots" / "holdout_244d.json"
OUT = BACKEND / "output" / "research" / "canonical_weakcap_grid.json"
WINDOWS = ("train", "validation", "out_of_sample")


def make_spec(snapshot, exec_policy):
    spec = CanonicalPolicyExperimentSpec.__new__(CanonicalPolicyExperimentSpec)
    object.__setattr__(spec, "candidate_snapshot", snapshot)
    object.__setattr__(spec, "score_line", 72.0)
    object.__setattr__(spec, "output_count", int(CHAMP.daily_output_limit))
    object.__setattr__(spec, "execution_policy", exec_policy)
    return spec


def stats_from_frame(f: pd.DataFrame, date_set: set | None = None):
    """逐笔等权均值 + 日度cohort等权均值 + 胜率 + 盈亏比（选股系统正确口径）。"""
    if date_set is not None:
        f = f[f.date.isin(date_set)]
    if len(f) == 0:
        return {"n": 0, "avg": None, "day_cohort": None, "wr": None, "pl": None, "pick_days": 0}
    wins, losses = f.net[f.net > 0].sum(), abs(f.net[f.net < 0].sum())
    daily = f.groupby("date").net.mean()
    return {
        "n": int(len(f)),
        "avg": round(f.net.mean(), 4),                 # 逐笔等权
        "day_cohort": round(daily.mean(), 4),          # 每日等权再跨日平均
        "day_pos_pct": round((daily > 0).mean() * 100, 1),
        "wr": round((f.net > 0).mean() * 100, 2),
        "pl": round(wins / losses, 3) if losses > 0 else None,
        "pick_days": int(daily.shape[0]),
    }


def evaluate(runner, spec, policy, split):
    _public, records = runner._run_arm(spec, policy, split)
    f = selected_trades_frame(spec.candidate_snapshot, records)
    rows = {"all": stats_from_frame(f)}
    for w in WINDOWS:
        rows[w] = stats_from_frame(f, set(split[w]))
    return rows, f


def selected_trades_frame(snapshot, records):
    """选中(recommended)且成交的交易，附 regime×情绪。"""
    sel = records["selected_keys"]; outcomes = records["outcomes"]
    date_mbse = {d.signal_date: (_market_base(d.market_snapshot), _market_sentiment(d.market_snapshot))
                 for d in snapshot.days}
    rows = []
    for key in sel:
        date, code = key.split(":", 1)
        oc = outcomes.get(key) or {}
        tr = oc.get("trade")
        if tr is None or not getattr(tr, "executed", False):
            continue
        mb, se = date_mbse.get(date, ("?", "?"))
        rows.append({"date": date, "code": code, "mb": mb, "se": se,
                     "net": float(tr.net_return_pct)})
    return pd.DataFrame(rows)


def main():
    payload = json.loads(SNAP.read_text(encoding="utf-8"))
    snapshot = ScoredCandidateSnapshot.from_payload(payload)
    ep = ExecutionPolicy()
    split = _purged_split(tuple(d.signal_date for d in snapshot.days),
                          purge_days=ep.entry_lookahead_days + ep.horizon_days)
    runner = ProductionPolicyBacktestRunner(repository=None)
    spec = make_spec(snapshot, ep)

    grid = [("champion_cap60", CHAMP)]
    for cap in (64, 66, 68, 70, 72):
        grid.append((f"weakcap_{cap}", CHAMP.with_rule_patch("weak_regime_score_cap", float(cap))))
    grid.append(("weakcap_None", CHAMP.with_rule_patch("weak_regime_score_cap", None)))

    summary = {}
    frames = {}
    for name, pol in grid:
        rows, f = evaluate(runner, spec, pol, split)
        summary[name] = rows
        frames[name] = f
        a, t, v, o = rows["all"], rows["train"], rows["validation"], rows["out_of_sample"]
        print(f"{name:14s} | ALL n={a['n']:4d} 逐笔{a['avg']:>7} 日cohort{a['day_cohort']:>7} wr{a['wr']:>5} | "
              f"TRAIN n={t['n']:3d} {t['day_cohort']:>7} | VAL n={v['n']:3d} {v['day_cohort']:>7} | "
              f"OOS n={o['n']:3d} 逐笔{o['avg']:>7} 日cohort{o['day_cohort']:>7} wr{o['wr']:>5} 正日{o['day_pos_pct']}%")

    # 归因：champion vs weakcap_None，逐 regime×情绪 的选中笔数/avg
    print("\n=== 归因：选中交易逐 regime×情绪（avg逐笔净收益% / n）===")
    attr = {}
    for name in ("champion_cap60", "weakcap_72"):
        f = frames[name]
        g = f.groupby(["mb", "se"]).agg(n=("net", "size"), avg=("net", "mean"),
                                        daycohort=("net", lambda x: 0)).round(3)
        dc = f.groupby(["mb", "se", "date"]).net.mean().groupby(["mb", "se"]).mean().round(3)
        g["daycohort"] = dc
        attr[name] = g.reset_index().to_dict("records")
        print(f"\n## {name}")
        print(g.to_string())
    c = frames["champion_cap60"].groupby(["mb", "se"]).size()
    e = frames["weakcap_72"].groupby(["mb", "se"]).size()
    print("\n## 笔数变化 weakcap_72 - champion（负数=被新门槛剔除）")
    print((e.reindex(c.index, fill_value=0) - c).fillna(0).astype(int).to_string())

    OUT.write_text(json.dumps({"summary": summary, "attribution": attr},
                              ensure_ascii=False, indent=2, default=str))
    print("\nsaved", OUT)


if __name__ == "__main__":
    main()
