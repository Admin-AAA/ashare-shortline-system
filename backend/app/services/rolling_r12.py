"""现役策略「近12个月扣成本后每笔」每月滚动重算。

为什么需要：策略页的 R12（midterm_backtest_metrics.json / standalone_metrics.json）是上线时用研究脚本算的一次性快照，
窗口固定在 2025-10~2026-10。上线条件本身就要求 R12 每笔为正，不滚动重算，这个数永远是 100%，看不出策略变差。

口径（结果写清截止日 cutoff 与窗口）：
- 中线/长线：用现行生产选股代码（midterm_service.generate_picks，与实盘同一套，开每日新推荐上限）在独立 staging 目录里
  把 [窗口起点 − 预热, cutoff] 逐交易日重放一遍，不碰生产 picks 目录；收益用生产 _load_batch/_realize 按日K结算
  （次日开盘买、持有期满开盘卖、涨停不买、跌停顺延、扣 0.3%）。只算信号日在窗口内、且到 cutoff 已卖出的笔。
- 短线：读库里已有的已结算推荐——历史重建（historical_replay 正式队列）+ 实盘（live_forward），只算已成交的笔，
  用 avg_executed_return（净收益，没成交的不当 0% 摊）。不另跑回放；历史重建只覆盖到 replay_coverage_end，
  之后只有实盘票，结果里如实标出。
- 样本少于 30 笔标 insufficient_sample，不算"为正"也不算"为负"。

输出：backend/output/strategy_health/rolling_r12_latest.json（另存一份 rolling_r12_<cutoff>.json 归档）。
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = BACKEND_ROOT / "output" / "strategy_health"
LATEST = OUT_ROOT / "rolling_r12_latest.json"
MIN_TRADES = 30
WARMUP_DAYS = 100  # 自然日，约 70 个交易日，覆盖长线 60 日持有，让窗口开头的「已持有不重复买」与实盘一致
BLACKOUTS = ((_dt.time(9, 15), _dt.time(15, 5)), (_dt.time(16, 35), _dt.time(18, 35)))


def _now() -> _dt.datetime:
    from app.services.midterm_live_seal import now_cn

    return now_cn().replace(tzinfo=None)


def in_blackout(now: _dt.datetime) -> bool:
    t = now.time()
    return any(a <= t < b for a, b in BLACKOUTS)


def _latest_trade_date(db) -> str:
    return str(db.execute(text("SELECT MAX(trade_date) FROM daily_kbars WHERE code='000001'")).scalar())


def trade_stats(rets: list[float], open_count: int = 0) -> dict[str, Any]:
    n = len(rets)
    if not n:
        return {"n": 0, "avg": None, "median": None, "win_rate": None, "pf": None, "trim5_avg": None,
                "open": open_count, "verdict": "insufficient_sample"}
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    srt = sorted(rets)
    k = int(n * 0.05)
    core = srt[k:n - k] if n - 2 * k > 0 else srt
    avg = sum(rets) / n
    verdict = "insufficient_sample" if n < MIN_TRADES else ("positive" if avg > 0 else "negative")
    return {
        "n": n, "avg": round(avg, 2), "median": round(median(rets), 2),
        "win_rate": round(len(wins) / n * 100, 1),
        "pf": round(sum(wins) / abs(sum(losses)), 2) if wins and losses else None,
        "trim5_avg": round(sum(core) / len(core), 2), "open": open_count, "verdict": verdict,
    }


def midlong_window_stats(db, staging: Path, window_start: str, cutoff: str) -> dict[str, dict[str, Any]]:
    from app.services import midterm_service as ms

    rets: dict[str, list[float]] = {sid: [] for sid in ms.STRATEGIES}
    opened: dict[str, int] = {sid: 0 for sid in ms.STRATEGIES}
    for f in sorted(staging.glob("*.json")):
        b = ms._load_batch(db, f)
        sid = b["strategy_id"]
        if sid not in rets or not (window_start <= b["signal_date"] <= cutoff):
            continue
        for p in b["picks"]:
            if p.get("status") == "closed" and p.get("ret") is not None:
                rets[sid].append(float(p["ret"]))
            elif p.get("status") in ("holding", "pending"):
                opened[sid] += 1
    out = {}
    for sid, rr in rets.items():
        out[sid] = {"name": ms.STRATEGIES[sid].get("name", sid), "horizon": ms.STRATEGIES[sid].get("horizon"),
                    "hold_days": ms._hold(sid), **trade_stats(rr, opened[sid])}
    return out


def short_window_stats(db, window_start: str, cutoff: str) -> dict[str, Any]:
    from app.services.daily_output_check import expected_short_strategies
    from app.services.outcome_service import metrics_by_strategy

    expected = expected_short_strategies(db)
    parts: dict[str, dict[str, list[tuple[int, float]]]] = {}
    for scope in ("historical_replay", "live_forward"):
        for row in metrics_by_strategy(db, start_date=window_start, end_date=cutoff, sample_scope=scope):
            sid = row.get("strategy_id")
            n = int(row.get("triggered_count") or 0)
            if sid in expected and n:
                parts.setdefault(sid, {}).setdefault(scope, []).append((n, float(row.get("avg_return") or 0.0)))
    cover = db.execute(text(
        "SELECT MAX(trade_date) FROM recommendation_runs WHERE evidence_class='historical_reconstruction' AND status='completed'"
    )).scalar()
    out: dict[str, Any] = {}
    for sid in expected:
        by_scope = {}
        tot_n, tot_sum = 0, 0.0
        for scope, items in (parts.get(sid) or {}).items():
            n = sum(i[0] for i in items)
            s = sum(i[0] * i[1] for i in items)
            by_scope[scope] = {"n": n, "avg": round(s / n, 2) if n else None}
            tot_n += n
            tot_sum += s
        avg = tot_sum / tot_n if tot_n else None
        verdict = "insufficient_sample" if tot_n < MIN_TRADES else ("positive" if (avg or 0) > 0 else "negative")
        out[sid] = {"n": tot_n, "avg": round(avg, 2) if avg is not None else None, "verdict": verdict,
                    "by_scope": by_scope}
    return {"strategies": out, "replay_coverage_end": str(cover) if cover else None}


def _month_done(month: str) -> bool:
    try:
        d = json.loads(LATEST.read_text())
    except Exception:  # noqa: BLE001
        return False
    return d.get("month") == month and d.get("status") == "completed"


def run_monthly(db, *, force: bool = False, now: _dt.datetime | None = None,
                out_root: Path | None = None) -> dict[str, Any]:
    from app.services import midterm_service as ms

    now = now or _now()
    if in_blackout(now) and not force:
        return {"status": "skipped", "summary_text": "交易时段 / 盘后日更窗口内不跑滚动重算"}
    cutoff = _latest_trade_date(db)
    month = now.strftime("%Y-%m")
    root = out_root or OUT_ROOT
    if not force and out_root is None and _month_done(month):
        return {"status": "skipped", "summary_text": f"{month} 已算过（截止 {cutoff}），本月不再重算"}
    end = _dt.date.fromisoformat(cutoff)
    window_start = (end - _dt.timedelta(days=365)).isoformat()
    replay_start = (end - _dt.timedelta(days=365 + WARMUP_DAYS)).isoformat()
    staging = root / "rolling_r12" / f"{month}_{cutoff}" / "picks"

    from scripts.rebuild_replay_picks import replay_midlong

    replay = replay_midlong(db, replay_start, cutoff, list(ms.STRATEGIES), staging, stop_at=_dt.time(9, 0),
                            apply_daily_cap=True, retry_waits=(60,))
    if replay.get("stopped"):
        return {"status": "partial_failed", "replay": {k: replay.get(k) for k in ("days", "done_through", "seconds")},
                "summary_text": f"中长线重放到 {replay.get('done_through')} 时到了停止时刻，下个交易日接着跑"}
    midlong = midlong_window_stats(db, staging, window_start, cutoff)
    short = short_window_stats(db, window_start, cutoff)
    allv = [v["verdict"] for v in midlong.values()] + [v["verdict"] for v in short["strategies"].values()]
    judged = [v for v in allv if v != "insufficient_sample"]
    incomplete = {k: v for k, v in (replay.get("no_data") or {}).items() if v} | \
                 {k: v for k, v in (replay.get("errors") or {}).items() if v}
    doc = {
        "month": month,
        "cutoff": cutoff,
        "window": {"signal_start": window_start, "signal_end": cutoff, "replay_start": replay_start},
        "generated_at": now.isoformat(timespec="seconds"),
        "status": "completed",
        "positive_ratio": round(judged.count("positive") / len(judged), 3) if judged else None,
        "counts": {"positive": allv.count("positive"), "negative": allv.count("negative"),
                   "insufficient_sample": allv.count("insufficient_sample")},
        "midlong": midlong,
        "short": short,
        "replay": {"days": replay.get("days"), "seconds": replay.get("seconds"),
                   "incomplete_days": {k: len(v) for k, v in incomplete.items()}},
        "method": ("中长线：现行生产选股代码重放（开每日上限），只算窗口内信号、截止日前已卖出的笔，扣 0.3%；"
                   "短线：历史重建 + 实盘已成交推荐的净收益（未成交不摊 0%）；样本 < 30 笔不判正负"),
    }
    root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, ensure_ascii=False, indent=1)
    (root / f"rolling_r12_{cutoff}.json").write_text(payload, encoding="utf-8")
    if out_root is None:
        LATEST.write_text(payload, encoding="utf-8")
    neg = [sid for sid, v in {**midlong, **short["strategies"]}.items() if v["verdict"] == "negative"]
    if neg:
        try:
            from app.core.alerts import emit_alert

            emit_alert("rolling_r12_negative", cutoff, f"近12个月（截止 {cutoff}）每笔转负的现役策略：{','.join(neg)}")
        except Exception:  # noqa: BLE001
            logger.warning("rolling r12 alert failed", exc_info=True)
    return {
        "status": "partial_failed" if neg or incomplete else "completed",
        "cutoff": cutoff,
        "counts": doc["counts"],
        "positive_ratio": doc["positive_ratio"],
        "negative": neg,
        "summary_text": (f"近12个月滚动重算（截止 {cutoff}）：为正 {doc['counts']['positive']}、为负 {doc['counts']['negative']}、"
                         f"样本不足 {doc['counts']['insufficient_sample']}" + (f"；转负：{','.join(neg)}" if neg else "")
                         + (f"；有取数失败日：{doc['replay']['incomplete_days']}" if incomplete else "")),
    }


def load_latest() -> dict[str, Any] | None:
    try:
        return json.loads(LATEST.read_text())
    except Exception:  # noqa: BLE001
        return None
