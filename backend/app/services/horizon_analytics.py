"""[中长线] 只读分析：交易明细平铺、诊断指标汇总、每日运行记录。

数据来源与「按批次复盘」完全相同（picks/*.json + midterm_service._load_batch 实时按日K结算），
这里只做平铺和聚合，不另存一份结果，口径因此与复盘页一致：
- 只有 status=closed 的笔计入胜率/盈亏比（已扣 0.3% 成本）；持有中为浮动收益，不计入。
- 胜：ret>0；盈亏比 = 平均盈利 / |平均亏损|；PF = 盈利总和 / |亏损总和|。
- 来源：origin=replay 为历史回放重建；无 origin 且 status/ 有当天运行记录的为实盘（LIVE）；
  两者都不是的旧文件记为 legacy，单列，不混进实盘。
"""
from __future__ import annotations

import datetime as _dt
import json
from statistics import median
from typing import Any, Iterable

from app.services import midterm_service as ms

_PICK_FIELDS = (
    "code", "name", "status", "entry_date", "entry_price", "exit_date", "exit_price", "mark_date", "mark_price",
    "ret", "days", "exit_delayed_days", "cancel_code", "cancel_reason", "selection_rank", "rule_score",
    "rule_score_label", "selection_reason", "ret_includes_cost",
)
TOP_K = (1, 3, 5, 10)
ORIGINS = ("live", "late", "replay", "legacy")


def _live_runs() -> set[tuple[str, str]]:
    """(信号日, 策略) 在 status/ 里有盘后运行记录的组合——只有真实盘后流程（17:35 / 手动出票）会写 status。"""
    sd = ms._status_dir()
    out: set[tuple[str, str]] = set()
    if sd.exists():
        for f in sd.glob("*.json"):
            try:
                out.update((f.stem, sid) for sid in json.loads(f.read_text()))
            except Exception:  # noqa: BLE001
                continue
    return out


def origin_of(batch: dict[str, Any], live_runs: set[tuple[str, str]]) -> str:
    """replay=历史回放重建；live=盘后流程真实出票，且在入场日 09:15 前生成；
    late=有运行记录但生成时间晚于入场日 09:15（开盘后补跑），不算实盘；
    legacy=无来源标记、也没有运行记录的旧文件（不当实盘算）。
    没有生成时间封签的旧文件（2026-10-27 之前）沿用旧规则，不改判。"""
    from app.services.midterm_live_seal import is_late

    if batch.get("origin") == "replay":
        return "replay"
    if (batch["signal_date"], batch["strategy_id"]) not in live_runs:
        return "legacy"
    return "late" if is_late(batch) else "live"


def trades(db, horizon: str, *, origin: str | None = None, strategy_id: str | None = None) -> list[dict[str, Any]]:
    """所有批次的个股平铺成一行一笔，按信号日新→旧、批内排名升序。"""
    live_runs = _live_runs()
    rows: list[dict[str, Any]] = []
    for b in ms._load_batches(db, ms._pick_files(strategy_id, horizon)):
        o = origin_of(b, live_runs)
        if origin and origin != "all" and o != origin:
            continue
        for p in b["picks"]:
            row = {k: p.get(k) for k in _PICK_FIELDS}
            row.update(strategy_id=b["strategy_id"], strategy_name=b["strategy_name"], signal_date=b["signal_date"],
                       hold_days=b["hold_days"], origin=o)
            rows.append(row)
    return rows


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def stats(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    closed = [r for r in rows if r.get("status") == "closed" and r.get("ret") is not None]
    rets = [float(r["ret"]) for r in closed]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x < 0]
    n = len(rets)
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    days = [r["days"] for r in closed if r.get("days") is not None]
    return {
        "n": n,
        "win_rate": _r(len(wins) / n * 100, 1) if n else None,
        "avg_ret": _r(sum(rets) / n) if n else None,
        "median_ret": _r(median(rets)) if n else None,
        "total_ret": _r(sum(rets), 1) if n else None,
        "pl_ratio": _r(avg_win / abs(avg_loss)) if avg_win is not None and avg_loss else None,
        "profit_factor": _r(sum(wins) / abs(sum(losses))) if wins and losses else None,
        "avg_win": _r(avg_win), "avg_loss": _r(avg_loss),
        "max_gain": _r(max(rets)) if n else None,
        "max_loss": _r(min(rets)) if n else None,
        "avg_days": _r(sum(days) / len(days), 1) if days else None,
        "delayed_exit": sum(1 for r in closed if (r.get("exit_delayed_days") or 0) > 0),
        "holding": sum(1 for r in rows if r.get("status") == "holding"),
        "pending": sum(1 for r in rows if r.get("status") == "pending"),
        "skipped": sum(1 for r in rows if r.get("status") == "skipped"),
        "total_picks": len(rows),
    }


def metrics(db, horizon: str, *, origin: str = "all", window_days: int | None = None,
            today: _dt.date | None = None, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """诊断页：总览 KPI + 按策略 + 批内排名 Top-K + 按信号月 + 实盘/回放对照。window_days 按信号日（自然日）截取。"""
    all_rows = rows if rows is not None else trades(db, horizon)
    start = None
    if window_days:
        start = ((today or _dt.date.today()) - _dt.timedelta(days=window_days)).isoformat()
        all_rows = [r for r in all_rows if r["signal_date"] >= start]
    by_origin = {o: stats(r for r in all_rows if r["origin"] == o) for o in ORIGINS}
    sel = all_rows if origin in (None, "all") else [r for r in all_rows if r["origin"] == origin]

    by_strategy = []
    for sid, meta in ms.STRATEGIES.items():
        if meta.get("horizon") != horizon:
            continue
        sr = [r for r in sel if r["strategy_id"] == sid]
        by_strategy.append({"strategy_id": sid, "strategy_name": meta.get("name", sid), "hold_days": ms._hold(sid),
                            "first_signal": min((r["signal_date"] for r in sr), default=None),
                            "last_signal": max((r["signal_date"] for r in sr), default=None), **stats(sr)})

    top_k = [{"k": k, **stats(r for r in sel if (r.get("selection_rank") or 999) <= k)} for k in TOP_K]

    months: dict[str, list[dict[str, Any]]] = {}
    for r in sel:
        months.setdefault(r["signal_date"][:7], []).append(r)
    by_month = [{"month": m, **stats(v)} for m, v in sorted(months.items(), reverse=True)]

    dates = [r["signal_date"] for r in sel]
    return {
        "horizon": horizon, "origin": origin or "all", "window_days": window_days, "window_start": start,
        "date_range": {"first_signal": min(dates, default=None), "last_signal": max(dates, default=None)},
        "overview": stats(sel), "by_origin": by_origin, "by_strategy": by_strategy, "top_k": top_k,
        "by_month": by_month,
    }


def runs(horizon: str, limit: int = 60) -> list[dict[str, Any]]:
    """每日运行记录：status/{date}.json 里属于该周期的策略，按日期新→旧。"""
    sd = ms._status_dir()
    if not sd.exists():
        return []
    sids = [sid for sid, m in ms.STRATEGIES.items() if m.get("horizon") == horizon]
    out = []
    for f in sorted(sd.glob("*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            continue
        items = []
        for sid in sids:
            row = data.get(sid)
            if not row:
                continue
            raw = row.get("raw")
            items.append({
                "strategy_id": sid, "strategy_name": ms.STRATEGIES[sid].get("name", sid),
                "status": row.get("status"), "recorded_at": row.get("recorded_at"),
                "picks": raw if isinstance(raw, int) and not isinstance(raw, bool) else None,
                "detail": (raw.get("skipped") or raw.get("error")) if isinstance(raw, dict) else
                          (raw if isinstance(raw, str) else None),
            })
        if items:
            out.append({"date": f.stem, "items": items})
    return out
