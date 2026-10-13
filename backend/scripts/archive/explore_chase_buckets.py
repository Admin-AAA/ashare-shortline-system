#!/usr/bin/env python3
"""追高程度分层：验证"温和追高=主升浪(不该回避) vs 极端追高=反转(该回避)"。

把过线池按 chg20/pos60 分桶，看各桶 T+5 净收益。判断回避阈值应切在哪。
"""
from __future__ import annotations

import json
import math
import statistics

SNAPSHOT = "/Users/wangao/Desktop/ashare-shortline-system/backend/output/frozen_snapshots/holdout_244d.json"
SCORE_LINE = 65.0
HOLD_BARS = 5


def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _ret(code, ob):
    bars = ob.get(code)
    if not bars or not bars[0].get("tradable"):
        return None
    entry = _f(bars[0].get("open"))
    xp = _f(bars[min(HOLD_BARS - 1, len(bars) - 1)].get("close"))
    if not entry or not xp or entry <= 0:
        return None
    return xp / entry - 1.0


def bucket_analysis(key, edges):
    snap = json.load(open(SNAPSHOT))
    buckets = {i: [] for i in range(len(edges) + 1)}
    for day in snap["days"]:
        ob = day.get("outcome_bars") or {}
        for c in day.get("cards") or []:
            if (c.get("final_score") or 0) < SCORE_LINE:
                continue
            v = _f(c.get(key))
            if v is None:
                continue
            r = _ret(c.get("code"), ob)
            if r is None:
                continue
            bi = sum(1 for e in edges if v > e)
            buckets[bi].append(r)
    print(f"\n=== 按 {key} 分桶（过线>={SCORE_LINE}，T+1→T+{HOLD_BARS}）===")
    labels = []
    lo = "-inf"
    for i in range(len(edges) + 1):
        hi = edges[i] if i < len(edges) else "+inf"
        labels.append(f"({lo},{hi}]")
        lo = hi
    print(f"{'区间':<18}{'n':>6}{'胜率%':>7}{'票均%':>8}{'总收益%':>9}")
    for i in range(len(edges) + 1):
        rs = buckets[i]
        if not rs:
            continue
        wr = 100 * sum(1 for r in rs if r > 0) / len(rs)
        avg = 100 * statistics.mean(rs)
        tot = 100 * sum(rs)
        print(f"{labels[i]:<18}{len(rs):>6}{wr:>7.1f}{avg:>8.3f}{tot:>9.1f}")


bucket_analysis("chg20", [0, 5, 10, 15, 20, 30])
bucket_analysis("pos60", [50, 70, 80, 90, 95])
