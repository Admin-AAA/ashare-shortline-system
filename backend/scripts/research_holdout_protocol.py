"""Hold-out 评估协议（docs/holdout-protocol.md）的离线工具。

面板来自 research_strategy_ablation_fast.py --panel-dir（训练段与 hold-out 段分别导出）。
子命令：
  baseline   训练段 / hold-out 段基线指标
  drawdown   在训练段网格搜索回撤控制参数 → 选一个 → 在 hold-out 段只评估一次
  only       单策略口径：只保留某技能发出非否决信号的入选卡（新策略评估用）
判定统一用 evaluate_challenger（L-010）。
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.engine.drawdown_control import (  # noqa: E402
    DrawdownControlConfig, DrawdownControlState, apply_drawdown_control, record_day,
)
from app.services.backtest.challenger_decision import PerformanceSnapshot, evaluate_challenger  # noqa: E402

HOLDOUT_START = "2026-07-01"


def load(path: str) -> list[dict]:
    p = Path(path)
    if p.is_dir():
        p = next(iter(sorted(p.glob("entry_panel_*.json"))))
    return json.loads(p.read_text())["days"]


def metrics(rows):
    rows = [(d, n) for d, n in rows if n is not None and not math.isnan(n)]
    if not rows:
        return None
    net = np.array([n for _, n in rows])
    by_day = defaultdict(list)
    for d, n in rows:
        by_day[d].append(n)
    daily = np.array([np.mean(by_day[d]) for d in sorted(by_day)])
    cum = daily.cumsum()
    wins, losses = net[net > 0].sum(), -net[net < 0].sum()
    return {"n": int(len(net)), "wr": float((net > 0).mean()), "avg": float(net.mean()),
            "sum": float(net.sum()), "pf": float(wins / max(losses, 1e-9)),
            "mdd": float((cum - np.maximum.accumulate(cum)).min()), "days": len(by_day)}


def fmt(m):
    if not m:
        return "n=0"
    return (f"n={m['n']} 胜率{m['wr']*100:.1f}% 平均{m['avg']:+.2f}% PF{m['pf']:.2f} "
            f"累计{m['sum']:+.1f} 回撤{m['mdd']:+.1f}")


def decide(a, b):
    s = lambda m: PerformanceSnapshot(m["avg"], m["sum"], m["wr"] * 100, m["pf"], m["mdd"])  # noqa: E731
    return evaluate_challenger(s(a), s(b))


def _score(card):
    return float(card.get("final_score") or 0)


def ordered_selected(day):
    cards = {str(c.get("code")): c for c in day.get("cards") or []}
    return sorted((str(c) for c in day.get("selected") or []), key=lambda c: -_score(cards.get(c, {})))


def regime(day):
    return ((day.get("market_snapshot") or {}).get("breadth") or {}).get("regime")


def run(days, cfg=None, only_skill=None):
    state = DrawdownControlState()
    rows = []
    for i, day in enumerate(sorted(days, key=lambda d: d["signal_date"])):
        picks = ordered_selected(day)
        if only_skill:
            cards = {str(c.get("code")): c for c in day.get("cards") or []}
            picks = [c for c in picks if _has_signal(cards.get(c, {}), only_skill)]
        if cfg is not None:
            picks, _ = apply_drawdown_control(picks, regime=regime(day), day_index=i, state=state, cfg=cfg)
        nets = day.get("net_by_code") or {}
        day_nets = [nets.get(c) for c in picks]
        record_day(state, i, day_nets)
        rows += [(day["signal_date"], n) for n in day_nets]
    return metrics(rows)


def _has_signal(card, sid):
    for sig in card.get("strategy_signals") or []:
        if str(sig.get("strategy_id") or sig.get("skill_id")) == sid and not sig.get("hard_veto") and not sig.get("vetoed"):
            return True
    return False


def grid():
    for weak, thr, lb, pause in itertools.product(
        (None, 0, 1, 2, 3), (None, -3.0, -5.0, -8.0, -12.0), (5, 10), (2, 5)
    ):
        if thr is None and (lb, pause) != (5, 2):
            continue
        if weak is None and thr is None:
            continue
        yield DrawdownControlConfig(enabled=True, weak_max_tickets=weak, breaker_threshold=thr,
                                    lookback_days=lb, pause_days=pause)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("baseline", "drawdown", "only"))
    ap.add_argument("--train", required=True)
    ap.add_argument("--holdout", required=True)
    ap.add_argument("--skill", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    train, hold = load(a.train), load(a.holdout)
    assert all(d["signal_date"] < HOLDOUT_START for d in train), "训练面板混入 hold-out 日期"
    assert all(d["signal_date"] >= HOLDOUT_START for d in hold), "hold-out 面板混入训练日期"
    out = {}
    bt, bh = run(train), run(hold)
    print("基线 训练段 ", fmt(bt)); print("基线 hold-out", fmt(bh))
    out["baseline"] = {"train": bt, "holdout": bh}
    if a.cmd == "drawdown":
        results = []
        for cfg in grid():
            m = run(train, cfg)
            if not m:
                continue
            d = decide(bt, m)
            results.append((cfg, m, getattr(d, "decision", str(d))))
        passed = [r for r in results if "PRODUCTION" in str(r[2])]
        print(f"训练段网格 {len(results)} 组，PRODUCTION {len(passed)} 组")
        pool = passed or results
        best = max(pool, key=lambda r: r[1]["sum"] / max(abs(r[1]["mdd"]), 1.0))
        cfg, m, d = best
        print("训练段选中:", cfg, fmt(m), d)
        mh = run(hold, cfg)
        dh = decide(bh, mh)
        print("hold-out 评估一次:", fmt(mh), getattr(dh, "decision", str(dh)))
        out["drawdown"] = {"chosen": cfg.__dict__, "train": m, "train_decision": str(d),
                           "holdout": mh, "holdout_decision": str(getattr(dh, "decision", dh)),
                           "grid_size": len(results), "grid_production": len(passed)}
    if a.cmd == "only":
        mt, mh = run(train, only_skill=a.skill), run(hold, only_skill=a.skill)
        print(f"{a.skill} 入选卡 训练段", fmt(mt)); print(f"{a.skill} 入选卡 hold-out", fmt(mh))
        out["only"] = {"skill": a.skill, "train": mt, "holdout": mh}
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
