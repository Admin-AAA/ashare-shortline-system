"""Stage 5 离线评分层研究 harness：在入场面板上重跑真实 evaluate_selection_policy。

面板每张卡都带 net_by_code（按生产执行策略单独结算的净收益），所以任意「选哪几张」
都能离线结算，口径与 ablation harness 的 metrics() 一致（mdd 基于逐日均值累计）。
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from functools import lru_cache

import numpy as np

sys.path.insert(0, ".")
from app.engine.production_policy import (  # noqa: E402
    DEFAULT_PRODUCTION_SELECTION_POLICY,
    SelectionPolicyContext,
    evaluate_selection_policy,
)
from app.services.backtest.challenger_decision import PerformanceSnapshot, evaluate_challenger  # noqa: E402

SCORE_LINE = 72.0
OUTPUT_COUNT = 6


@lru_cache(maxsize=4)
def load(path: str) -> dict:
    return json.load(open(path))


def run_policy(day: dict, *, output_count: int = OUTPUT_COUNT, score_line: float = SCORE_LINE,
               cards: list[dict] | None = None, policy=DEFAULT_PRODUCTION_SELECTION_POLICY) -> list[str]:
    res = evaluate_selection_policy(
        [dict(c) for c in (cards if cards is not None else day["cards"])],
        SelectionPolicyContext(
            score_line=score_line,
            output_count=output_count,
            market_snapshot=dict(day["market_snapshot"]),
            risk_template="balanced",
            same_day_industry_counts=day["same_day_industry_counts"],
            same_day_recommendation_count=day["same_day_recommendation_count"],
        ),
        policy,
    )
    return [str(c.get("code")) for c in res.recommendations]


def metrics(rows: list[tuple[str, float]]) -> dict | None:
    """rows = [(signal_date, net)]，与 ablation harness metrics() 同口径。"""
    rows = [(d, n) for d, n in rows if n is not None and not math.isnan(n)]
    if not rows:
        return None
    net = np.array([n for _, n in rows])
    by_day: dict[str, list[float]] = defaultdict(list)
    for d, n in rows:
        by_day[d].append(n)
    daily = np.array([np.mean(by_day[d]) for d in sorted(by_day)])
    cum = daily.cumsum()
    wins, losses = net[net > 0].sum(), -net[net < 0].sum()
    return {
        "n": int(len(net)),
        "wr": float((net > 0).mean()),
        "avg": float(net.mean()),
        "sum": float(net.sum()),
        "pf": float(wins / max(losses, 1e-9)),
        "mdd": float((cum - np.maximum.accumulate(cum)).min()),
        "days": len(by_day),
    }


def snap(m: dict) -> PerformanceSnapshot:
    return PerformanceSnapshot(m["avg"], m["sum"], m["wr"] * 100, m["pf"], m["mdd"])


def decide(champion: dict, challenger: dict):
    return evaluate_challenger(snap(champion), snap(challenger))


def fmt(m: dict | None) -> str:
    if not m:
        return "n=0"
    return (f"n{m['n']:4d} wr{m['wr']*100:5.1f} avg{m['avg']:+.3f} pf{m['pf']:.3f} "
            f"sum{m['sum']:+8.1f} mdd{m['mdd']:+6.1f}")


def rows_for(days: list[dict], picks: dict[str, list[str]]) -> list[tuple[str, float]]:
    out = []
    for day in days:
        nets = day.get("net_by_code") or {}
        for code in picks.get(day["signal_date"], []):
            out.append((day["signal_date"], nets.get(code)))
    return out
