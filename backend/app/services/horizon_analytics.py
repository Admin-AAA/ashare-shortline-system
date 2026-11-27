"""[中长线] 只读分析：交易明细平铺、诊断指标汇总、每日运行记录。

数据来源与「按批次复盘」完全相同（picks/*.json + midterm_service._load_batch 实时按日K结算），
这里只做平铺和聚合，不另存一份结果，口径因此与复盘页一致：
- 只有 status=closed 的笔计入胜率/盈亏比（已扣 0.27% 成本：税费 0.07% + 双边滑点 0.20%，与短线执行政策同源）；持有中为浮动收益，不计入。
- 胜：ret>0；盈亏比 = 平均盈利 / |平均亏损|；PF = 盈利总和 / |亏损总和|。
- 基准：bench_ret/excess_ret 为同信号日、同持有期、同成本的全市场等权基准（index_daily 的 EWALL.A）与超额；
  stats 汇总为 bench_avg_ret / excess_avg_ret / bench_n。基准缺日时该笔不计入超额。
- 来源：origin=replay 为历史回放重建；无 origin 且 status/ 有当天运行记录的为实盘（LIVE）；
  两者都不是的旧文件记为 legacy，单列，不混进实盘。
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
import time
from statistics import median
from typing import Any, Iterable

from app.services import midterm_service as ms
from app.services.return_quality import quality_metrics

_PICK_FIELDS = (
    "code", "name", "status", "entry_date", "entry_price", "exit_date", "exit_price", "mark_date", "mark_price",
    "ret", "days", "exit_delayed_days", "cancel_code", "cancel_reason", "selection_rank", "rule_score",
    "rule_score_label", "selection_reason", "ret_includes_cost",
)
TOP_K = (1, 3, 5, 10)
# 同日同票被几个策略推：1 / 2 / 3-4 / ≥5。
OVERLAP_BUCKETS = (("1 个策略", 1, 1), ("2 个策略", 2, 2), ("3-4 个策略", 3, 4), ("≥5 个策略", 5, 10**6))
ORIGINS = ("live", "late", "replay", "legacy")
# 策略画像「回放已平仓」：信号日 >= 锚点往前 365 个自然日（含锚点当天）。
# 与 rolling_r12.month_manifest 的 window_start = cutoff - 365 天同一长度，按信号日而不是平仓日。
# 研究回测 quality.R12 是上线快照里的固定阶段，样本和名额不同，数字不必相等。
PORTRAIT_CLOSED_WINDOW_DAYS = 365

# 平铺结果缓存：见 trades() 的说明。30s 与复盘页 benchmark 的「空闲 31 秒让缓存过期」口径一致。
_TRADES_CACHE_TTL_SECONDS = 30.0
# key 里带 OUT_DIR：单测会把 ms.OUT_DIR 指到 tmp 目录，不同夹具之间不能互相复用缓存。
_trades_cache: dict[tuple[str, str | None, str], tuple[float, list[dict[str, Any]]]] = {}
_trades_cache_lock = threading.Lock()


def _live_runs() -> set[tuple[str, str]]:
    """(信号日, 策略) 在 status/ 里有盘后运行记录的组合——只有真实盘后流程（21:30 盘后主流程 / 手动出票）会写 status。"""
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
    """所有批次的个股平铺成一行一笔，按信号日新→旧、批内排名升序。

    结果按 (horizon, strategy_id) 缓存 ``_TRADES_CACHE_TTL_SECONDS``：复盘页会同时打
    /trades 和 /metrics，后者内部又调一次 trades；1728 个 pick 文件全量解析 + 结算
    （实测 ~180ms）不该在一次页面加载里做两遍。结算依赖的日K每天只更新一次，
    30s 的缓存不会让「持有中」的日期标签明显滞后（实时价另有 45s 缓存）。
    """
    key = (horizon, strategy_id, str(ms.OUT_DIR))
    now = time.monotonic()
    with _trades_cache_lock:
        hit = _trades_cache.get(key)
    if hit is None or now - hit[0] > _TRADES_CACHE_TTL_SECONDS:
        rows = _trades_uncached(db, horizon, strategy_id)
        with _trades_cache_lock:
            _trades_cache[key] = (now, rows)
        hit = (now, rows)
    rows = hit[1]
    if origin and origin != "all":
        return [row for row in rows if row["origin"] == origin]
    return rows


def clear_trades_cache() -> None:
    """清空中长线平铺缓存（新批次写入 / 结算口径变更后调用）。"""
    with _trades_cache_lock:
        _trades_cache.clear()


def _trades_uncached(db, horizon: str, strategy_id: str | None) -> list[dict[str, Any]]:
    live_runs = _live_runs()
    rows: list[dict[str, Any]] = []
    for b in ms._load_batches(db, ms._pick_files(strategy_id, horizon)):
        o = origin_of(b, live_runs)
        for p in b["picks"]:
            row = {k: p.get(k) for k in _PICK_FIELDS}
            row.update(strategy_id=b["strategy_id"], strategy_name=b["strategy_name"], signal_date=b["signal_date"],
                       hold_days=b["hold_days"], origin=o, bench_ret=None, excess_ret=None)
            rows.append(row)
    _attach_benchmark(db, rows)
    # 同一信号日同一只票被几个策略推（本次加载范围内）。≥5 个策略同推的票是组合里最大的集中度来源：
    # 2026-10 这一桶 97 笔净均 -20.7%，2026-03 同一桶 +17.9%，是高 beta 而不是毒区（L-224）。
    overlap: dict[tuple[str, str], int] = {}
    for r in rows:
        k = (str(r["signal_date"]), str(r["code"]))
        overlap[k] = overlap.get(k, 0) + 1
    for r in rows:
        r["overlap_count"] = overlap[(str(r["signal_date"]), str(r["code"]))]
    return rows


def _attach_benchmark(db, rows: list[dict[str, Any]]) -> None:
    """给已平仓的笔附上同期等权基准收益（EWALL.A，同持有期、同一往返成本）与超额。

    基准曲线缺该信号日或持有期未走完时留 None。db 为 None（纯文件口径的单测）时不附。"""
    if db is None:
        return
    from app.services.market_benchmark import equal_weight_curve, holding_benchmark_return

    dates, pct = equal_weight_curve(db)
    if not dates:
        return
    for r in rows:
        if r.get("status") != "closed" or r.get("ret") is None:
            continue
        b = holding_benchmark_return(dates, pct, str(r["signal_date"]), int(r.get("hold_days") or ms.HOLD_DAYS))
        if b is None:
            continue
        r["bench_ret"] = b
        r["excess_ret"] = round(float(r["ret"]) - b, 2)


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
    out = {
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
    # 同期全市场等权基准与超额（L-224）：只统计能算出基准的已平仓笔；净均值为负但超额为正，说明是市场在跌。
    benched = [r for r in closed if r.get("bench_ret") is not None]
    out["bench_n"] = len(benched)
    out["bench_avg_ret"] = _r(sum(float(r["bench_ret"]) for r in benched) / len(benched)) if benched else None
    out["excess_avg_ret"] = _r(sum(float(r["ret"]) - float(r["bench_ret"]) for r in benched) / len(benched)) if benched else None
    # 收益质量（只增字段，不改上面任何既有字段）：识别「去掉极端笔后不赚钱」的策略，口径见 return_quality。
    # 注：quality_metrics 也返回 median_ret，与本函数上面的定义同源同值，覆盖无副作用。
    # 账户层面的组合收益与回撤不在 stats 里按切片算：中线看 metrics() 的 overlap_portfolios（交易日历固定槽位，
    # 走完全部持有期，L-203 2026-11-27 修订 / L-227）。原 portfolio_* 字段以信号日为时间轴、止于最后一个信号日，已删除。
    out.update(quality_metrics(rets))
    return out


def portrait_realized_12m(rows: Iterable[dict[str, Any]], horizon: str, *, today: _dt.date) -> dict[str, Any]:
    """画像条用的近 12 个月已平仓。按信号日截取，不受调用方 window_days 影响。

    回放格用 by_strategy（窗口内全部来源）。窗口内已有真实前瞻平仓时，前端改读 by_strategy_live，避免把回放算进「前瞻已结算」。
    已平仓才进入 n/avg/median（见 stats）。前端直接读本块，不再自己算窗口。"""
    start = (today - _dt.timedelta(days=PORTRAIT_CLOSED_WINDOW_DAYS)).isoformat()
    windowed = [r for r in rows if str(r.get("signal_date") or "") >= start]
    live_rows = [r for r in windowed if r.get("origin") == "live"]

    def _by_strategy(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for sid, meta in ms.STRATEGIES.items():
            if meta.get("horizon") != horizon:
                continue
            sr = [r for r in pool if r["strategy_id"] == sid]
            out.append({"strategy_id": sid, "strategy_name": meta.get("name", sid), "hold_days": ms._hold(sid),
                        "first_signal": min((r["signal_date"] for r in sr), default=None),
                        "last_signal": max((r["signal_date"] for r in sr), default=None), **stats(sr)})
        return out

    return {
        "window_days": PORTRAIT_CLOSED_WINDOW_DAYS,
        "window_basis": "signal_date",
        "window_start": start,
        "window_end": today.isoformat(),
        "note": (
            "策略画像「回放已平仓」用 by_strategy：信号日落在锚点往前 365 个自然日内（含锚点当天）的已平仓，含回放/旧文件。"
            "该窗口内 by_origin.live.n>0 时，画像改读 by_strategy_live（只含实盘）。"
            "锚点是本次请求的 today（缺省为当天自然日）。与 rolling_r12 的 window_start=cutoff-365 天同一长度，按信号日。"
            "研究回测 quality.R12 是上线时的固定阶段快照，引擎名额和重建路径不同，数字不必相等。"
        ),
        "by_origin": {o: stats(r for r in windowed if r["origin"] == o) for o in ORIGINS},
        "by_strategy": _by_strategy(windowed),
        "by_strategy_live": _by_strategy(live_rows),
    }


def metrics(db, horizon: str, *, origin: str = "all", window_days: int | None = None,
            today: _dt.date | None = None, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """诊断页：总览 KPI + 按策略 + 批内排名 Top-K + 按信号月 + 实盘/回放对照。window_days 按信号日（自然日）截取。

    portrait_realized_12m 始终按信号日近 365 天另算一份，给策略画像条用，不随 window_days 缩小。"""
    raw_rows = list(rows) if rows is not None else trades(db, horizon)
    anchor = today or _dt.date.today()
    portrait = portrait_realized_12m(raw_rows, horizon, today=anchor)
    all_rows = raw_rows
    start = None
    if window_days:
        start = (anchor - _dt.timedelta(days=window_days)).isoformat()
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

    by_overlap = [{"bucket": label, **stats(r for r in sel if lo <= (r.get("overlap_count") or 1) <= hi)}
                  for label, lo, hi in OVERLAP_BUCKETS]
    distinct = {(r["signal_date"], r["code"]) for r in sel}

    dates = [r["signal_date"] for r in sel]
    # 中线组合视图（A 跟全部策略买 / B 去重每票一份）：按 EWALL.A 交易日历走完持有期，见 overlap_portfolio。
    overlap_portfolios = None
    if horizon == "mid" and db is not None:
        from app.services import overlap_portfolio
        from app.services.market_benchmark import equal_weight_curve

        calendar, day_pct = equal_weight_curve(db)
        overlap_portfolios = overlap_portfolio.build(sel, calendar, day_pct) if calendar else None
    return {
        "horizon": horizon, "origin": origin or "all", "window_days": window_days, "window_start": start,
        "date_range": {"first_signal": min(dates, default=None), "last_signal": max(dates, default=None)},
        "overview": stats(sel), "by_origin": by_origin, "by_strategy": by_strategy, "top_k": top_k,
        "by_month": by_month, "by_overlap": by_overlap, "distinct_signals": len(distinct),
        "overlap_portfolios": overlap_portfolios,
        "portrait_realized_12m": portrait,
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
