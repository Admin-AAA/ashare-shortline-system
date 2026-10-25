"""策略综合分（rank_score，0~100）：只在同一周期（短 / 中 / 长）内比较，不跨周期。

纯函数、不连库、不联网，只读策略已有的回测指标（中长线 midterm_backtest_metrics.json 的
recent_12m / phases；短线 standalone_metrics.json 的 last_12m / recent，即“口径 A”）。
不使用历史重建回放（口径 B）的指标——其 benchmark_excess_return、max_drawdown 口径异常。

【按实际数值缩放（v2，取代旧的“周期内百分位”）】
每个指标先在“可排序”的策略里取中位数 med 与稳健尺度 scale = max(1.4826×MAD, floor)，
z = clip((x − med)/scale, −3, 3)，指标分 = 50 + z × 50/3（0~100，中位数策略 50 分）。
floor 是该指标的“最小有意义差距”，保证几个策略数值很接近时不会被放大成大分差，分差反映真实差距；
策略少（甚至只有 1~2 个）时也不会因为排名而拉开。综合分 = Σ 权重 × 指标分。

中线 / 长线（五项）：
  25%  均值  = R12 平均每笔收益 × n/(n+K)                     （K=30，样本少向 0 收缩；floor 1.0 个百分点）
  25%  收益/回撤 = R12 组合收益 tot_20 / max(|mdd_20|, 1)       （折算到 20 仓位口径后；floor 0.5）
  20%  盈亏比 = 收缩后 PF = 1 + (PF − 1) × n/(n+K)              （floor 0.25）
  15%  N3 均值 = 2024-01 至今平均每笔收益；无 N3 用 R12 均值      （floor 1.0 个百分点）
  15%  阶段稳定 = 历史阶段里平均每笔收益>0 的占比（不含 N3 汇总）    （floor 0.15）

仓位口径折算：tot 与 mdd 的“权益”按 k_slots 个等额仓位计，多数策略 20 仓位（每笔 1/20 名义）。
long-lowturn-lowvol / long-dividend-lowvol 按每笔 1/30 名义计，实际权益偏小：
    tot_20 = tot × (30/20)，mdd_20 = mdd × (30/20)      （一阶线性近似，忽略复利；已用 n×avg/30 对账）
折算后的 tot_20 / mdd_20 用于展示与“收益 / 回撤”排序；收益/回撤是比值，折算前后相同。

短线（口径 A，两个窗口各半；窗口缺失时另一窗口权重为 1），每个窗口内：
  30% 收缩后均值（floor 0.5）+ 30% 收缩后均值/max(|mdd|,1)（floor 0.02）+ 25% 收缩后 PF（floor 0.3）
  + 15% 收缩后胜率（floor 5 个百分点）
  收缩（w = n/(n+K)，K=30）：avg×w；PF → 1+(PF−1)×w；胜率 → 50+(胜率−50)×w

沉底（不进综合分、rank_score=None、排在可排序策略之后、彼此保持传入的原始顺序）：
  insufficient_sample  R12（短线为 12 个月窗口）笔数 n < 30
  missing_metrics      缺 recent_12m / last_12m 或其 n、avg、mdd、tot（中长线）、阶段（中长线）
  stale_data           指标窗口结束日距今 > STALE_DAYS（默认 21 天）
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Iterable

SHRINK_K = 30.0
MIN_SAMPLE = 30
STALE_DAYS = 21
MDD_FLOOR = 1.0  # |回撤|% 下限，避免除零

BASE_SLOTS = 20
# 权益按每笔 1/30 名义计的策略（见各自 metrics.note）；其余按 20 仓位
NOTIONAL_SLOTS: dict[str, int] = {"long-lowturn-lowvol": 30, "long-dividend-lowvol": 30}

# 指标权重与“最小有意义差距”floor（见模块文档）
HORIZON_WEIGHTS = {"avg": 0.25, "calmar": 0.25, "pf": 0.20, "n3": 0.15, "pos": 0.15}
HORIZON_FLOORS = {"avg": 1.0, "calmar": 0.5, "pf": 0.25, "n3": 1.0, "pos": 0.15}
SHORT_WINDOW_WEIGHTS = {"avg": 0.30, "avg_dd": 0.30, "pf": 0.25, "win": 0.15}
SHORT_FLOORS = {"avg": 0.5, "avg_dd": 0.02, "pf": 0.3, "win": 5.0}
Z_CLIP = 3.0
MAD_TO_SIGMA = 1.4826

FLAG_LABELS = {
    "insufficient_sample": "样本不足（n<30）",
    "missing_metrics": "缺指标",
    "stale_data": "数据过期",
}

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def shrink_weight(n: float, k: float = SHRINK_K) -> float:
    return n / (n + k) if n > 0 else 0.0


def _median(xs: list[float]) -> float:
    ys = sorted(xs)
    m = len(ys)
    return ys[m // 2] if m % 2 else (ys[m // 2 - 1] + ys[m // 2]) / 2.0


def robust_scores(values: list[float], floor: float) -> list[float]:
    """按实际数值缩放到 0~100：50 + 50/3 × clip((x−中位数)/max(1.4826×MAD, floor), ±3)。
    分差反映真实差距：scale 不会小于 floor，所以数值接近的策略分数也接近；空列表返回 []，单个值给 50。"""
    if not values:
        return []
    med = _median(values)
    mad = _median([abs(v - med) for v in values])
    scale = max(MAD_TO_SIGMA * mad, floor)
    out = []
    for v in values:
        z = max(-Z_CLIP, min(Z_CLIP, (v - med) / scale))
        out.append(50.0 + z * 50.0 / Z_CLIP)
    return out


def window_end(window: Any) -> _dt.date | None:
    """从 '2025-10-01~2026-10-13' 取结束日；取不到返回 None。"""
    if not isinstance(window, str):
        return None
    found = _DATE_RE.findall(window.split("~")[-1])
    if not found:
        return None
    try:
        return _dt.date.fromisoformat(found[-1])
    except ValueError:
        return None


def _is_stale(end: _dt.date | None, today: _dt.date) -> bool:
    return end is not None and (today - end).days > STALE_DAYS


def notional_scale(strategy_id: str) -> float:
    return NOTIONAL_SLOTS.get(strategy_id, BASE_SLOTS) / BASE_SLOTS


def _finish(items: list[dict[str, Any]], flags_of: list[list[str]], scores: dict[int, float],
            comps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把分数写回并按“可排序先、综合分降序、同分/沉底保持原序”重排。"""
    for i, it in enumerate(items):
        it["rank_flags"] = flags_of[i]
        it["rank_flag_labels"] = [FLAG_LABELS[f] for f in flags_of[i]]
        it["rank_components"] = comps[i]
        it["rank_score"] = round(scores[i], 1) if i in scores else None
    order = sorted(range(len(items)),
                   key=lambda i: (0, -scores[i], i) if i in scores else (1, 0.0, i))
    ranked_ids = [i for i in order if i in scores]
    pos = {i: r + 1 for r, i in enumerate(ranked_ids)}
    for i, it in enumerate(items):
        it["rank"] = pos.get(i)
    return [items[i] for i in order]


def rank_horizon_strategies(items: Iterable[dict[str, Any]], today: _dt.date | None = None) -> list[dict[str, Any]]:
    """给 /api/horizons/{mid|long}/strategies 的每个策略加 rank_score / rank / rank_flags / rank_components。
    返回新列表：默认综合分降序，沉底的在后、保持传入顺序；不修改传入对象。"""
    today = today or _dt.date.today()
    items = [dict(it) for it in items]
    comps: list[dict[str, Any]] = []
    flags_of: list[list[str]] = []
    for it in items:
        sid = str(it.get("id"))
        m = it.get("metrics") or {}
        r12 = m.get("recent_12m") or {}
        phases = m.get("phases") or {}
        n, avg, tot, mdd = (_num(r12.get(k)) for k in ("n", "avg", "tot", "mdd"))
        scale = notional_scale(sid)
        flags: list[str] = []
        c: dict[str, Any] = {"n": n, "avg": avg, "win": _num(r12.get("win")), "pf": _num(r12.get("pf")),
                             "mdd": mdd, "tot": tot, "notional_scale": scale,
                             "tot_20": None if tot is None else round(tot * scale, 2),
                             "mdd_20": None if mdd is None else round(mdd * scale, 2)}
        non_n3 = {k: v for k, v in phases.items() if not str(k).startswith("N3")}
        n3 = next((v for k, v in phases.items() if str(k).startswith("N3")), None)
        miss = n is None or avg is None or tot is None or mdd is None or not non_n3
        if miss:
            flags.append("missing_metrics")
        else:
            if n < MIN_SAMPLE:
                flags.append("insufficient_sample")
            wt = shrink_weight(n)
            pf = _num(r12.get("pf"))
            c["avg_shrunk"] = avg * wt
            c["calmar"] = (tot * scale) / max(abs(mdd * scale), MDD_FLOOR)
            c["pf_shrunk"] = 1.0 + ((pf if pf is not None else 1.0) - 1.0) * wt
            n3_avg = _num((n3 or {}).get("avg"))
            c["n3_avg"] = n3_avg if n3_avg is not None else avg
            c["n3_source"] = "N3" if n3_avg is not None else "R12"
            pos_cnt = sum(1 for v in non_n3.values() if (_num((v or {}).get("avg")) or 0) > 0)
            c["pos_ratio"] = pos_cnt / len(non_n3)
        if _is_stale(window_end(m.get("window")), today):
            flags.append("stale_data")
        if scale != 1:
            c["note"] = f"仓位口径折算 ×{scale:g}（按每笔 1/{NOTIONAL_SLOTS[sid]} 名义 → 1/{BASE_SLOTS}）"
        comps.append(c)
        flags_of.append(flags)
    eligible = [i for i, f in enumerate(flags_of) if not f]
    scores: dict[int, float] = {}
    src = {"avg": "avg_shrunk", "calmar": "calmar", "pf": "pf_shrunk", "n3": "n3_avg", "pos": "pos_ratio"}
    sc = {key: robust_scores([comps[i][src[key]] for i in eligible], HORIZON_FLOORS[key]) for key in HORIZON_WEIGHTS}
    for j, i in enumerate(eligible):
        comps[i]["metric_scores"] = {key: round(sc[key][j], 1) for key in HORIZON_WEIGHTS}
        scores[i] = sum(HORIZON_WEIGHTS[key] * sc[key][j] for key in HORIZON_WEIGHTS)
    return _finish(items, flags_of, scores, comps)


def _short_window(w: Any) -> dict[str, float | None]:
    w = w or {}
    n = _num(w.get("n"))
    avg = _num(w.get("avg_return_pct"))
    win = _num(w.get("win_rate"))
    pf = _num(w.get("pf"))
    mdd = _num(w.get("max_drawdown_pct"))
    out: dict[str, float | None] = {"n": n, "avg_raw": avg, "win_raw": win, "pf_raw": pf, "mdd": mdd,
                                    "avg": None, "avg_dd": None, "pf": None, "win": None}
    if n is None or n <= 0 or avg is None or mdd is None:
        return out
    wt = shrink_weight(n)
    out["avg"] = avg * wt
    out["avg_dd"] = (avg * wt) / max(abs(mdd), MDD_FLOOR)
    out["pf"] = 1.0 + ((pf if pf is not None else 1.0) - 1.0) * wt
    out["win"] = 50.0 + ((win if win is not None else 50.0) - 50.0) * wt
    return out


def rank_short_strategies(rows: Iterable[dict[str, Any]], window_end_date: Any = None,
                          today: _dt.date | None = None) -> list[dict[str, Any]]:
    """给 /api/strategies/standalone-metrics 的 strategies 行加综合分（口径 A）。
    window_end_date：返回体 window.end（YYYY-MM-DD），用于过期判定。"""
    today = today or _dt.date.today()
    rows = [dict(r) for r in rows]
    end = window_end(f"~{window_end_date}") if window_end_date else None
    stale = _is_stale(end, today)
    comps: list[dict[str, Any]] = []
    flags_of: list[list[str]] = []
    for r in rows:
        a = _short_window(r.get("last_12m"))
        b = _short_window(r.get("recent"))
        flags: list[str] = []
        if a["avg"] is None or r.get("status") in ("pending", "no_data"):
            flags.append("missing_metrics")
        elif (a["n"] or 0) < MIN_SAMPLE:
            flags.append("insufficient_sample")
        if stale:
            flags.append("stale_data")
        comps.append({"last_12m": a, "recent": b,
                      "recent_used": b["avg"] is not None,
                      "n": a["n"], "avg": a["avg_raw"], "win": a["win_raw"], "mdd": a["mdd"],
                      "recent_avg": b["avg_raw"]})
        flags_of.append(flags)
    eligible = [i for i, f in enumerate(flags_of) if not f]

    def _window_scores(which: str, idxs: list[int]) -> dict[int, float]:
        idxs = [i for i in idxs if comps[i][which]["avg"] is not None]
        res: dict[int, float] = {i: 0.0 for i in idxs}
        for key, wgt in SHORT_WINDOW_WEIGHTS.items():
            pr = robust_scores([comps[i][which][key] for i in idxs], SHORT_FLOORS[key])
            for j, i in enumerate(idxs):
                res[i] += wgt * pr[j]
        return res

    s_a = _window_scores("last_12m", eligible)
    s_b = _window_scores("recent", eligible)
    scores: dict[int, float] = {}
    for i in eligible:
        if i in s_b:
            scores[i] = 0.5 * s_a[i] + 0.5 * s_b[i]
        else:
            scores[i] = s_a[i]
        comps[i]["window_scores"] = {"last_12m": round(s_a[i], 1),
                                     "recent": round(s_b[i], 1) if i in s_b else None}
    return _finish(rows, flags_of, scores, comps)
