"""长远目标三指标（2026-11-27 定，到 2027-03 验证）：

1. 去重口径近 12 个月滚动超额 > 0：同一信号日同一只票只算一份（账户买一次），净收益减同持有期全市场等权基准（EWALL.A）。
2. 逐月超额为正的月份 ≥ 8/12：按信号月算去重后的平均超额。
3. 每条现役策略的「独有信号」超额 > 0：同日同票没有被其他现役策略推的信号才算，这是该策略对账户的边际贡献（L-224/L-229）。

三个数每天由数据质量巡检算一次，达不到的项标黄。只统计已平仓且能算出基准的笔；持有中不计。
短线（recommendations）暂不并入：实盘按版本分段后样本太少（L-225），等现行版本回放重建完成后再接。
"""
from __future__ import annotations

import collections
import datetime as _dt
import statistics
from typing import Any, Iterable

GOAL_WINDOW_DAYS = 365
GOAL_POSITIVE_MONTHS = 8
GOAL_MIN_UNIQUE_TRADES = 30  # 独有信号少于此数的策略只报数、不判定


def _dedup(rows: Iterable[dict[str, Any]], start: str) -> list[dict[str, Any]]:
    by: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if r.get("status") != "closed" or r.get("ret") is None or r.get("bench_ret") is None:
            continue
        sd = str(r.get("signal_date") or "")
        if sd < start:
            continue
        key = (sd, str(r["code"]))
        cur = by.get(key)
        if cur is None:
            by[key] = {"signal_date": sd, "code": str(r["code"]), "net": float(r["ret"]), "bench": float(r["bench_ret"]),
                       "strategies": {str(r["strategy_id"])}}
        else:
            cur["strategies"].add(str(r["strategy_id"]))
    return list(by.values())


def compute(rows: Iterable[dict[str, Any]], active_strategy_ids: Iterable[str], *, today: _dt.date | None = None) -> dict[str, Any]:
    """rows：horizon_analytics.trades 的输出（已附 bench_ret）。返回三指标与判定。"""
    today = today or _dt.date.today()
    start = (today - _dt.timedelta(days=GOAL_WINDOW_DAYS)).isoformat()
    rows = list(rows)
    active = set(active_strategy_ids)
    dedup = _dedup((r for r in rows if str(r.get("strategy_id")) in active), start)
    excess = [d["net"] - d["bench"] for d in dedup]
    rolling = statistics.mean(excess) if excess else None

    by_month: dict[str, list[float]] = collections.defaultdict(list)
    for d in dedup:
        by_month[d["signal_date"][:7]].append(d["net"] - d["bench"])
    months = [{"month": m, "n": len(v), "excess": round(statistics.mean(v), 2)} for m, v in sorted(by_month.items())]
    positive_months = sum(1 for m in months if m["excess"] > 0)

    owners: dict[tuple[str, str], set[str]] = {(d["signal_date"], d["code"]): d["strategies"] for d in dedup}
    per: list[dict[str, Any]] = []
    for sid in sorted(active):
        uniq = [r for r in rows if str(r.get("strategy_id")) == sid and r.get("status") == "closed" and r.get("bench_ret") is not None
                and str(r.get("signal_date") or "") >= start and owners.get((str(r["signal_date"]), str(r["code"]))) == {sid}]
        ex = [float(r["ret"]) - float(r["bench_ret"]) for r in uniq]
        total = [r for r in rows if str(r.get("strategy_id")) == sid and r.get("status") == "closed" and r.get("bench_ret") is not None
                 and str(r.get("signal_date") or "") >= start]
        tex = [float(r["ret"]) - float(r["bench_ret"]) for r in total]
        verdict = "insufficient" if len(ex) < GOAL_MIN_UNIQUE_TRADES else ("pass" if statistics.mean(ex) > 0 else "fail")
        per.append({"strategy_id": sid, "n": len(total), "excess": round(statistics.mean(tex), 2) if tex else None,
                    "unique_n": len(ex), "unique_excess": round(statistics.mean(ex), 2) if ex else None, "verdict": verdict})

    return {
        "window_start": start, "window_end": today.isoformat(), "basis": "signal_date, dedup (signal_date, code), closed only",
        "dedup_n": len(dedup), "raw_n": sum(1 for r in rows if r.get("status") == "closed" and str(r.get("strategy_id")) in active
                                           and str(r.get("signal_date") or "") >= start and r.get("bench_ret") is not None),
        "rolling_excess": round(rolling, 2) if rolling is not None else None,
        "rolling_excess_pass": (rolling is not None and rolling > 0),
        "months": months, "positive_months": positive_months, "month_count": len(months),
        "positive_months_pass": positive_months >= GOAL_POSITIVE_MONTHS,
        "per_strategy": per,
        "strategies_failing": [p["strategy_id"] for p in per if p["verdict"] == "fail"],
        "strategies_insufficient": [p["strategy_id"] for p in per if p["verdict"] == "insufficient"],
    }


def summary_text(g: dict[str, Any]) -> str:
    parts = [
        f"去重近12月滚动超额 {g['rolling_excess']:+.2f}%" if g["rolling_excess"] is not None else "去重近12月滚动超额 无样本",
        f"逐月为正 {g['positive_months']}/{g['month_count']}（目标 ≥{GOAL_POSITIVE_MONTHS}）",
        f"独有信号超额为负的策略 {len(g['strategies_failing'])} 个" + (f"：{'、'.join(g['strategies_failing'])}" if g["strategies_failing"] else ""),
    ]
    if g["strategies_insufficient"]:
        parts.append(f"独有信号不足 {GOAL_MIN_UNIQUE_TRADES} 笔未判 {len(g['strategies_insufficient'])} 个")
    return "；".join(parts)
