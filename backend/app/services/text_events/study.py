"""标签超额收益。只做研究，不写选股、不改回测结算。

买入日是事件上已经对齐好的 entry_trade_date（收盘出信号的下一交易日）。
T+k = 买入日开盘持有到第 k 个交易日收盘 / 买入开盘 - 1。
基准是同一买入日、同一市值五分位里其他股票的等权收益。没有市值时退化为传入样本的等权，
调用方必须在结果里看 ``benchmark`` 字段，不能把这种退化当成市值分位结论。
"""
from __future__ import annotations

from collections import defaultdict
import statistics
from typing import Mapping, Sequence

HORIZONS = (1, 3, 5, 20)
DISCLAIMER = "仅供参考，不参与选股"


def holding_return(
    bars: Mapping[str, Mapping[str, Mapping[str, float]]],
    sessions: Sequence[str],
    code: str,
    entry: str,
    horizon: int,
) -> float | None:
    ordered = list(sessions)
    try:
        start = ordered.index(entry)
    except ValueError:
        return None
    stop = start + horizon - 1
    if stop >= len(ordered) or stop < start:
        return None
    entry_bar = bars.get(code, {}).get(entry)
    exit_bar = bars.get(code, {}).get(ordered[stop])
    if not entry_bar or not exit_bar:
        return None
    opened = float(entry_bar.get("open") or 0)
    closed = float(exit_bar.get("close") or 0)
    if opened <= 0 or closed <= 0:
        return None
    return closed / opened - 1.0


def assign_quintiles(caps: Mapping[str, float]) -> dict[str, int]:
    """caps: code -> 市值。空或非正的不进分位。返回 1..5，1 最小。"""
    ranked = sorted((code for code, value in caps.items() if value and value > 0), key=lambda code: caps[code])
    if not ranked:
        return {}
    out: dict[str, int] = {}
    size = len(ranked)
    for index, code in enumerate(ranked):
        bucket = min(5, index * 5 // size + 1)
        out[code] = bucket
    return out


def excess_study(
    events: Sequence[Mapping[str, object]],
    bars: Mapping[str, Mapping[str, Mapping[str, float]]],
    sessions: Sequence[str],
    caps_by_entry: Mapping[str, Mapping[str, float]] | None = None,
    horizons: Sequence[int] = HORIZONS,
) -> dict[str, object]:
    """``caps_by_entry[entry_date][code] = total_mv``。None 表示没有市值，基准退化为样本等权。"""
    benchmark_mode = "size_quintile" if caps_by_entry is not None else "sample_equal_weight"
    grouped: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    used = 0
    skipped = 0
    for event in events:
        label = str(event.get("label") or "")
        code = str(event.get("code") or "")
        entry = str(event.get("entry_trade_date") or "")
        if label not in {"利好", "利空", "中性"} or not code or not entry:
            skipped += 1
            continue
        buckets = assign_quintiles(caps_by_entry.get(entry, {})) if caps_by_entry is not None else {}
        bucket = buckets.get(code) if caps_by_entry is not None else None
        if caps_by_entry is not None and bucket is None:
            skipped += 1
            continue
        hit = False
        for horizon in horizons:
            stock = holding_return(bars, sessions, code, entry, horizon)
            if stock is None:
                continue
            bench = _benchmark(
                bars, sessions, entry, horizon, bucket, buckets, code,
                sample_mode=caps_by_entry is None,
            )
            if bench is None:
                continue
            grouped[label][horizon].append(stock - bench)
            hit = True
        if hit:
            used += 1
        else:
            skipped += 1
    by_label = {}
    for label, horizon_map in grouped.items():
        by_label[label] = {
            f"T+{horizon}": _stats(values)
            for horizon, values in sorted(horizon_map.items())
        }
    return {
        "disclaimer": DISCLAIMER,
        "participates_in_selection": False,
        "benchmark": benchmark_mode,
        "execution": "信号日收盘之后，下一交易日开盘买入，持有到第 k 个交易日收盘",
        "events_used": used,
        "events_skipped": skipped,
        "by_label": by_label,
    }


def _benchmark(
    bars: Mapping[str, Mapping[str, Mapping[str, float]]],
    sessions: Sequence[str],
    entry: str,
    horizon: int,
    bucket: int | None,
    buckets: Mapping[str, int],
    code: str,
    *,
    sample_mode: bool,
) -> float | None:
    peers: list[float] = []
    for other in bars:
        if other == code:
            continue
        if not sample_mode and buckets.get(other) != bucket:
            continue
        value = holding_return(bars, sessions, other, entry, horizon)
        if value is not None:
            peers.append(value)
    if len(peers) < 5:
        return None
    return statistics.fmean(peers)


def _stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "mean_excess": None, "median_excess": None}
    return {
        "n": len(values),
        "mean_excess": round(statistics.fmean(values), 6),
        "median_excess": round(statistics.median(values), 6),
    }
