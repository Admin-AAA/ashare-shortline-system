"""数据健康分每日快照：每个交易日 18:40 存一份原始记录，用来看近 20 个交易日的均值和出票成功率。

健康分本身是"请求那一刻"现算的，同一天上午 50、晚上 79 都可能，单看一次没法跟踪。这里固定时刻取样：

- 原始记录（original）：只由 18:40 定时那次写入（当天、18:40 之后、还没有原始记录），写入后不可覆盖。
  先重跑一次统一巡检，再按页面同一公式算分，连同红/黄项和当天盘后出票结果一起存。
- 复核（rechecks）：之后的任何重跑（次日 08:55 复核、手动触发）只追加到 rechecks，带时间，不改原始记录。
  大宗交易这类 21:00 才发布的数据，18:40 时只记「待数据、未到点」，次日复核的结果写在这里。
- 巡检本身失败：这一次标 quality_ok=false、score 为空，不拿旧报告凑分；不计入均值，单独计数显示。

均值和出票成功率只用原始记录。文件：data/runtime/data_health_snapshots.jsonl（每个交易日一行）。
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
from pathlib import Path
from statistics import mean
from typing import Any

from app.services.data_quality_monitor import DATA_DIR

SNAPSHOT_PATH = DATA_DIR / "runtime" / "data_health_snapshots.jsonl"
ORIGINAL_TIME = _dt.time(18, 40)
MODE_ORIGINAL = "original"
MODE_RECHECK = "recheck"
_LOCK = threading.Lock()


def combined_health(db, *, force_refresh: bool = False, report: dict[str, Any] | None = None) -> dict[str, Any]:
    """页面健康分的唯一公式：库表评分 score_db − 统一巡检扣分（红/黄项，见 quality_score_penalty）。"""
    from app.services.data_health_service import diagnose_all
    from app.services.data_quality_monitor import load_report, quality_score_penalty

    result = diagnose_all(db, force_refresh=force_refresh)
    rep = report if report is not None else load_report()
    pen = quality_score_penalty(rep)
    base = result.get("score")
    extra: dict[str, Any] = {"score_db": base, "score_detail": {**pen, "score_db": base}}
    if isinstance(base, (int, float)) and pen["penalty"]:
        extra["score"] = max(0, int(base) - int(pen["penalty"]))
    if rep:
        extra["data_quality"] = {
            "overall": rep.get("overall"), "summary": rep.get("summary"), "generated_at": rep.get("generated_at"),
            "expected_trade_date": rep.get("expected_trade_date"),
            "red": [i["key"] for i in rep.get("items", []) if i.get("severity") == "red"],
        }
    return {**result, **extra}


def _items(rep: dict[str, Any] | None, severity: str) -> list[dict[str, str]]:
    return [{"key": i["key"], "detail": str(i.get("detail") or "")[:200]}
            for i in (rep or {}).get("items", []) if i.get("severity") == severity and not i.get("retired")]


def measure(db, *, now: _dt.datetime, trade_date: str) -> dict[str, Any]:
    """取一次样：重跑巡检 → 算分 → 核对 trade_date 的盘后出票。巡检失败时不算分（不用旧报告）。"""
    from app.services.daily_output_check import daily_output_status
    from app.services.data_quality_monitor import run_data_quality_check

    rep = run_data_quality_check(db, now=now)
    quality_ok = rep.get("overall") != "error"
    sample: dict[str, Any] = {"taken_at": now.isoformat(timespec="seconds"), "quality_ok": quality_ok}
    if quality_ok:
        health = combined_health(db, force_refresh=True, report=rep)
        sample.update(score=health.get("score"), score_db=health.get("score_db"),
                      penalty=(health.get("score_detail") or {}).get("penalty"),
                      red=_items(rep, "red"), yellow=_items(rep, "yellow"))
    else:
        sample.update(score=None, score_db=None, penalty=None, red=[], yellow=[],
                      quality_error=str(rep.get("error") or "巡检失败")[:300])
    output = daily_output_status(db, trade_date, now=now.replace(tzinfo=None))
    sample.update(output_status=output["status"], output_ok=bool(output["ok"]), output=output)
    return sample


def is_original_slot(trade_date: str, now: _dt.datetime) -> bool:
    return now.date().isoformat() == trade_date and now.time().replace(tzinfo=None) >= ORIGINAL_TIME


def take_snapshot(db, *, mode: str = MODE_ORIGINAL, now: _dt.datetime | None = None,
                  path: Path | None = None) -> dict[str, Any]:
    """mode=original（18:40 定时）：当天还没有原始记录且已过 18:40 才写原始记录，否则记为复核。
    mode=recheck（次日 08:55 / 手动）：只追加复核。返回 {"trade_date", "written": original|recheck, "sample"}。"""
    from app.services.daily_output_check import output_target_date
    from app.services.data_quality_monitor import _now, load_open_days

    now = now or _now()
    open_days = load_open_days(db)
    trade_date = output_target_date(open_days, now.replace(tzinfo=None)) or now.date().isoformat()
    sample = measure(db, now=now, trade_date=trade_date)
    p = path or SNAPSHOT_PATH
    with _LOCK:
        rows = {str(r.get("trade_date")): r for r in load_snapshots(p)}
        rec = rows.get(trade_date) or {"trade_date": trade_date, "original": None, "rechecks": []}
        if mode == MODE_ORIGINAL and rec.get("original") is None and is_original_slot(trade_date, now):
            rec["original"] = sample
            written = "original"
        else:
            rec.setdefault("rechecks", []).append({**sample, "kind": mode})
            written = "recheck"
        rows[trade_date] = rec
        _write_all([rows[k] for k in sorted(rows)], p)
    return {"trade_date": trade_date, "written": written, "sample": sample}


def _write_all(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)


def load_snapshots(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or SNAPSHOT_PATH
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def summarize(days: int = 20, path: Path | None = None) -> dict[str, Any]:
    """近 N 个交易日（按有记录的交易日计）的原始记录：健康分均值、出票成功率、红项天数。

    - 均值只用巡检成功的原始记录；巡检失败的天数单独计数（quality_failed_days）。
    - 出票成功率 = 原始记录里出票状态不是 failed 的占比（「待数据、未到点」不算失败）；
      次日复核仍失败的日期单独列在 recheck_failed_dates。
    - 没有原始记录（18:40 没跑上）的天数单独计数，不补。"""
    rows = sorted(load_snapshots(path), key=lambda r: str(r.get("trade_date")))[-days:]
    originals = [r for r in rows if r.get("original")]
    good = [r["original"] for r in originals if r["original"].get("quality_ok")
            and isinstance(r["original"].get("score"), (int, float))]
    scores = [float(o["score"]) for o in good]
    out_ok = [r["original"].get("output_status") != "failed" for r in originals]

    def last_recheck(r: dict[str, Any]) -> dict[str, Any] | None:
        rc = r.get("rechecks") or []
        return rc[-1] if rc else None

    return {
        "days_requested": days,
        "days_with_record": len(rows),
        "days_with_original": len(originals),
        "days_missing_original": len(rows) - len(originals),
        "quality_failed_days": sum(1 for r in originals if not r["original"].get("quality_ok")),
        "first_date": rows[0]["trade_date"] if rows else None,
        "last_date": rows[-1]["trade_date"] if rows else None,
        "avg_score": round(mean(scores), 1) if scores else None,
        "min_score": min(scores) if scores else None,
        "output_success_rate": round(sum(out_ok) / len(out_ok), 3) if out_ok else None,
        "output_failed_dates": [r["trade_date"] for r in originals if r["original"].get("output_status") == "failed"],
        "output_pending_dates": [r["trade_date"] for r in originals if r["original"].get("output_status") == "pending"],
        "recheck_failed_dates": [r["trade_date"] for r in rows
                                 if (last_recheck(r) or {}).get("output_status") == "failed"],
        "red_days": sum(1 for o in good if o.get("red")),
        "snapshots": [
            {"trade_date": r["trade_date"],
             "original": None if not r.get("original") else {
                 k: r["original"].get(k) for k in ("taken_at", "quality_ok", "score", "score_db", "penalty",
                                                   "output_status")} | {
                 "red": [i["key"] for i in r["original"].get("red") or []],
                 "yellow_count": len(r["original"].get("yellow") or [])},
             "rechecks": [{k: c.get(k) for k in ("taken_at", "kind", "quality_ok", "score", "output_status")}
                          for c in r.get("rechecks") or []]}
            for r in rows],
    }
