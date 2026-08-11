#!/usr/bin/env python3
"""回避追高 counterfactual（探索性，非发布依据）。

模拟生产 top-N 选择：每日从过线池按 final_score 降序取 top-N。
- champion：不过滤，直接 top-N
- challenger：先剔除"追高"票（chg20/pos60 超阈值），再按 final_score 取 top-N（不足 N 用被剔票补足，保证出票数一致）

比较 T+1 open → T+5 close 净收益、胜率、票均。关键约束：challenger 出票数 ~= champion（不降出票率）。
"""
from __future__ import annotations

import json
import math
import statistics

SNAPSHOT = "/Users/wangao/Desktop/ashare-shortline-system/backend/output/frozen_snapshots/holdout_244d.json"
SCORE_LINE = 65.0
TOP_N = 6
HOLD_BARS = 5


def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _ret(code, ob):
    bars = ob.get(code)
    if not bars:
        return None
    e = bars[0]
    if not e.get("tradable"):
        return None
    entry = _f(e.get("open"))
    xp = _f(bars[min(HOLD_BARS - 1, len(bars) - 1)].get("close"))
    if not entry or not xp or entry <= 0:
        return None
    return xp / entry - 1.0


def select(day, avoid_key=None, avoid_th=None):
    """返回 [(code, ret)]。avoid_key 设时先剔除追高再补位保证出票数。"""
    ob = day.get("outcome_bars") or {}
    pool = [c for c in (day.get("cards") or []) if (c.get("final_score") or 0) >= SCORE_LINE]
    pool.sort(key=lambda c: (c.get("final_score") or 0), reverse=True)
    if avoid_key is None:
        chosen = pool[:TOP_N]
    else:
        low = [c for c in pool if (c.get(avoid_key) or 0) <= avoid_th]
        high = [c for c in pool if (c.get(avoid_key) or 0) > avoid_th]
        chosen = low[:TOP_N]
        # 补足出票数（若低票不足 N，用高票补，保持出票率不降）
        if len(chosen) < TOP_N:
            chosen = chosen + high[: TOP_N - len(chosen)]
    out = []
    for c in chosen:
        r = _ret(c.get("code"), ob)
        if r is not None:
            out.append((c.get("code"), r))
    return out


def stats(trades):
    if not trades:
        return dict(n=0, wr=0, avg=0, total=0)
    rets = [r for _, r in trades]
    wins = sum(1 for r in rets if r > 0)
    return dict(
        n=len(rets),
        wr=round(100 * wins / len(rets), 1),
        avg=round(100 * statistics.mean(rets), 3),
        total=round(100 * sum(rets), 1),
    )


def main():
    snap = json.load(open(SNAPSHOT))
    days = snap["days"]

    configs = [
        ("champion(不过滤)", None, None),
        ("回避 chg20>15", "chg20", 15),
        ("回避 chg20>20", "chg20", 20),
        ("回避 pos60>80", "pos60", 80),
        ("回避 pos60>90", "pos60", 90),
    ]
    print(f"口径: 过线>={SCORE_LINE} 按 final_score 取 top-{TOP_N}，T+1 open→T+{HOLD_BARS} close")
    print(f"{'配置':<22}{'笔数':>6}{'胜率%':>7}{'票均%':>8}{'总收益%':>9}")
    print("-" * 56)
    for name, key, th in configs:
        trades = []
        for day in days:
            trades.extend(select(day, key, th))
        s = stats(trades)
        print(f"{name:<22}{s['n']:>6}{s['wr']:>7}{s['avg']:>8}{s['total']:>9}")


if __name__ == "__main__":
    main()
