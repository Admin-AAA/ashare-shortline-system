"""长远目标三指标（2026-11-27 定，到 2027-03 验证）：

1. 去重口径近 12 个月滚动超额 > 0：同一信号日同一只票只算一份（账户买一次），净收益减同持有期全市场等权基准（EWALL.A）。
2. 逐月超额为正的月份 ≥ 8/12：按信号月算去重后的平均超额。
3. 每条现役策略「独有信号」全期扣费净收益合计 > 0（L-238）：同日同票没有被其他现役策略推的信号才算，这是该策略对账户的
   边际贡献（L-224/L-229）。全期 = 2022-04 起研究回测逐笔（`data/midterm/fullhist_research_trades.parquet`，由
   `output/research/fullhist-retire-20261127/build.py` 导出，静态）+ 2025-10 起生产结算（replay/live，不含 historical_replay 以免与
   研究段重叠）。合计为正即去掉会少赚，不退役；超额为负只列观察项（收益主要来自 beta）。单看近 12 个月不算数。

前两项每天由数据质量巡检算一次，达不到的项标黄；第三项为负的策略标黄。只统计已平仓且能算出基准的笔；持有中不计。
短线（compute_short）：每条现役短线策略 = 现行指��回放段 + 同指纹实盘段（rolling_r12.short_trade_rows，L-225），
近 12 个月去重滚动超额、逐月为正月份、每策略全期（回放+实盘）扣费净合计；短线策略互相很少同日同票，不做独有信号判定。
"""
from __future__ import annotations

import collections
import datetime as _dt
import statistics
from pathlib import Path
from typing import Any, Iterable

GOAL_WINDOW_DAYS = 365
GOAL_POSITIVE_MONTHS = 8
GOAL_MIN_UNIQUE_TRADES = 30  # 独有信号少于此数的策略只报数、不判定
FULLHIST_START = "2022-04-01"
PROD_START = "2025-10-01"  # 研究逐笔到 2025-09-30，生产结算从这天起
RESEARCH_TRADES = Path(__file__).resolve().parents[2] / "data" / "midterm" / "fullhist_research_trades.parquet"


def load_research_trades(path: Path | None = None) -> list[dict[str, Any]]:
    """2022-04～2025-09 研究回测逐笔（strategy_id, signal_date, code, net, bench；net 已扣 0.07%）。文件不存在返回空表。"""
    import pandas as pd

    p = path or RESEARCH_TRADES
    if not p.exists():
        return []
    df = pd.read_parquet(p)
    return [{"strategy_id": str(a), "signal_date": str(b), "code": str(c), "net": float(d), "bench": float(e)}
            for a, b, c, d, e in zip(df["strategy_id"], df["signal_date"], df["code"], df["net"], df["bench"])]


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


def compute(rows: Iterable[dict[str, Any]], active_strategy_ids: Iterable[str], *, today: _dt.date | None = None,
            research_rows: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """rows：horizon_analytics.trades 的输出（已附 bench_ret、origin）；research_rows：load_research_trades()。返回三指标与判定。"""
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

    # 第三项：全期独有信号。研究段 + 生产段（去掉 historical_replay，与研究段重叠）拼成一张表，
    # 同日同票归属 = 推过它的全部现役策略；只被一条推的才是该策略的独有信号。
    full: list[dict[str, Any]] = [dict(r) for r in (research_rows or []) if str(r.get("strategy_id")) in active
                                  and str(r.get("signal_date") or "") >= FULLHIST_START]
    for r in rows:
        if (str(r.get("strategy_id")) not in active or r.get("status") != "closed" or r.get("ret") is None
                or r.get("bench_ret") is None or str(r.get("origin") or "") == "historical_replay"):
            continue
        sd = str(r.get("signal_date") or "")
        if sd < PROD_START:
            continue
        full.append({"strategy_id": str(r["strategy_id"]), "signal_date": sd, "code": str(r["code"]),
                     "net": float(r["ret"]), "bench": float(r["bench_ret"])})
    owners: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for r in full:
        owners[(r["signal_date"], r["code"])].add(r["strategy_id"])
    per: list[dict[str, Any]] = []
    for sid in sorted(active):
        mine = [r for r in full if r["strategy_id"] == sid]
        uniq = [r for r in mine if owners[(r["signal_date"], r["code"])] == {sid}]
        net_sum = sum(r["net"] for r in uniq)
        ex_sum = sum(r["net"] - r["bench"] for r in uniq)
        n_hist = sum(1 for r in uniq if r["signal_date"] < PROD_START)
        if len(uniq) < GOAL_MIN_UNIQUE_TRADES:
            verdict = "insufficient"
        elif net_sum > 0:
            verdict = "pass" if ex_sum > 0 else "watch"
        else:
            verdict = "fail"
        per.append({"strategy_id": sid, "n": len(mine), "unique_n": len(uniq), "unique_hist_n": n_hist,
                    "unique_prod_n": len(uniq) - n_hist,
                    "unique_net_sum": round(net_sum, 1), "unique_excess_sum": round(ex_sum, 1),
                    "unique_net_avg": round(net_sum / len(uniq), 2) if uniq else None,
                    "unique_excess_avg": round(ex_sum / len(uniq), 2) if uniq else None, "verdict": verdict})

    return {
        "window_start": start, "window_end": today.isoformat(), "basis": "signal_date, dedup (signal_date, code), closed only",
        "dedup_n": len(dedup), "raw_n": sum(1 for r in rows if r.get("status") == "closed" and str(r.get("strategy_id")) in active
                                           and str(r.get("signal_date") or "") >= start and r.get("bench_ret") is not None),
        "rolling_excess": round(rolling, 2) if rolling is not None else None,
        "rolling_excess_pass": (rolling is not None and rolling > 0),
        "months": months, "positive_months": positive_months, "month_count": len(months),
        "positive_months_pass": positive_months >= GOAL_POSITIVE_MONTHS,
        "per_strategy": per,
        "fullhist_start": FULLHIST_START, "fullhist_n": len(full),
        "strategies_failing": [p["strategy_id"] for p in per if p["verdict"] == "fail"],
        "strategies_watch": [p["strategy_id"] for p in per if p["verdict"] == "watch"],
        "strategies_insufficient": [p["strategy_id"] for p in per if p["verdict"] == "insufficient"],
    }


def summary_text(g: dict[str, Any]) -> str:
    parts = [
        f"去重近12月滚动超额 {g['rolling_excess']:+.2f}%" if g["rolling_excess"] is not None else "去重近12月滚动超额 无样本",
        f"逐月为正 {g['positive_months']}/{g['month_count']}（目标 ≥{GOAL_POSITIVE_MONTHS}）",
        f"全期独有信号扣费净合计为负的策略 {len(g['strategies_failing'])} 个" + (f"：{'、'.join(g['strategies_failing'])}" if g["strategies_failing"] else ""),
    ]
    if g.get("strategies_watch"):
        parts.append(f"净为正但超额为负（收益主要是 beta）的观察项 {len(g['strategies_watch'])} 个：{'、'.join(g['strategies_watch'])}")
    if g["strategies_insufficient"]:
        parts.append(f"独有信号不足 {GOAL_MIN_UNIQUE_TRADES} 笔未判 {len(g['strategies_insufficient'])} 个")
    return "；".join(parts)


def compute_short(short_rows: Iterable[dict[str, Any]], *, today: _dt.date | None = None) -> dict[str, Any]:
    """short_rows：rolling_r12.short_trade_rows 的输出（已含 net/bench/scope）。
    近 12 个月：同日同票去重后的滚动超额与逐月为正；每策略：全期笔数、净合计、超额合计、回放/实盘分段。"""
    today = today or _dt.date.today()
    start = (today - _dt.timedelta(days=GOAL_WINDOW_DAYS)).isoformat()
    rows = list(short_rows)
    recent: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if r["signal_date"] >= start:
            recent.setdefault((r["signal_date"], r["code"]), r)
    excess = [r["net"] - r["bench"] for r in recent.values()]
    by_month: dict[str, list[float]] = collections.defaultdict(list)
    for r in recent.values():
        by_month[r["signal_date"][:7]].append(r["net"] - r["bench"])
    months = [{"month": m, "n": len(v), "excess": round(statistics.mean(v), 2)} for m, v in sorted(by_month.items())]
    per: list[dict[str, Any]] = []
    for sid in sorted({r["strategy_id"] for r in rows}):
        mine = [r for r in rows if r["strategy_id"] == sid]
        rep = [r for r in mine if r["scope"] == "historical_replay"]
        live = [r for r in mine if r["scope"] == "live_forward"]
        net_sum = sum(r["net"] for r in mine); ex_sum = sum(r["net"] - r["bench"] for r in mine)
        if len(mine) < GOAL_MIN_UNIQUE_TRADES:
            verdict = "insufficient"
        elif net_sum > 0:
            verdict = "pass" if ex_sum > 0 else "watch"
        else:
            verdict = "fail"
        per.append({"strategy_id": sid, "n": len(mine), "replay_n": len(rep), "live_n": len(live),
                    "net_sum": round(net_sum, 1), "excess_sum": round(ex_sum, 1),
                    "net_avg": round(net_sum / len(mine), 2) if mine else None,
                    "excess_avg": round(ex_sum / len(mine), 2) if mine else None,
                    "live_excess_avg": round(statistics.mean(r["net"] - r["bench"] for r in live), 2) if live else None,
                    "signal_days": len({r["signal_date"] for r in mine}), "verdict": verdict})
    rolling = statistics.mean(excess) if excess else None
    positive_months = sum(1 for m in months if m["excess"] > 0)
    return {
        "window_start": start, "window_end": today.isoformat(), "basis": "current-fingerprint replay + same-fingerprint live (L-225)",
        "dedup_n": len(recent), "rolling_excess": round(rolling, 2) if rolling is not None else None,
        "rolling_excess_pass": (rolling is not None and rolling > 0),
        "months": months, "positive_months": positive_months, "month_count": len(months),
        "positive_months_pass": positive_months >= GOAL_POSITIVE_MONTHS,
        "per_strategy": per,
        "strategies_failing": [p["strategy_id"] for p in per if p["verdict"] == "fail"],
        "strategies_watch": [p["strategy_id"] for p in per if p["verdict"] == "watch"],
        "strategies_insufficient": [p["strategy_id"] for p in per if p["verdict"] == "insufficient"],
    }


def short_summary_text(g: dict[str, Any]) -> str:
    parts = [
        f"短线去重近12月滚动超额 {g['rolling_excess']:+.2f}%" if g["rolling_excess"] is not None else "短线去重近12月滚动超额 无样本",
        f"逐月为正 {g['positive_months']}/{g['month_count']}",
        f"全期扣费净合计为负的策略 {len(g['strategies_failing'])} 个" + (f"：{'、'.join(g['strategies_failing'])}" if g["strategies_failing"] else ""),
    ]
    if g.get("strategies_watch"):
        parts.append(f"净正超额负观察项 {len(g['strategies_watch'])} 个：{'、'.join(g['strategies_watch'])}")
    if g["strategies_insufficient"]:
        parts.append(f"不足 {GOAL_MIN_UNIQUE_TRADES} 笔未判 {len(g['strategies_insufficient'])} 个：{'、'.join(g['strategies_insufficient'])}")
    return "；".join(parts)
