"""数据健康分每日快照：每个交易日 18:40 存一份，用来看近 20 个交易日的均值和出票成功率。

健康分本身是"请求那一刻"现算的，同一天上午 50、晚上 79 都可能，单看一次没法跟踪。
这里在固定时刻（17:35 日更 + 18:35 出票截止之后）先重跑一次统一巡检，再按页面同一公式算分，
连同红/黄项和当天盘后出票结果追加到 data/runtime/data_health_snapshots.jsonl（每个交易日一行，重跑覆盖同日）。
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


def take_snapshot(db, *, now: _dt.datetime | None = None, path: Path | None = None) -> dict[str, Any]:
    from app.services.daily_output_check import daily_output_status, output_target_date
    from app.services.data_quality_monitor import _now, load_open_days, run_data_quality_check

    now = now or _now()
    rep = run_data_quality_check(db, now=now)  # 先重跑巡检（会同步告警），再算分，保证快照里的红黄项是此刻的
    if rep.get("overall") == "error":
        rep = None  # 巡检失败时不按"0 扣分"算，退回最近一次落盘报告
    from app.services.data_quality_monitor import load_report

    rep = rep or load_report()
    health = combined_health(db, force_refresh=True, report=rep)
    open_days = load_open_days(db)
    trade_date = output_target_date(open_days, now.replace(tzinfo=None)) or now.date().isoformat()
    output = daily_output_status(db, trade_date)
    snap = {
        "trade_date": trade_date,
        "taken_at": now.isoformat(timespec="seconds"),
        "score": health.get("score"),
        "score_db": health.get("score_db"),
        "penalty": (health.get("score_detail") or {}).get("penalty"),
        "red": _items(rep, "red"),
        "yellow": _items(rep, "yellow"),
        "output_ok": bool(output["ok"]),
        "output": output,
    }
    _write(snap, path or SNAPSHOT_PATH)
    return snap


def _write(snap: dict[str, Any], path: Path) -> None:
    with _LOCK:
        rows = [r for r in load_snapshots(path) if r.get("trade_date") != snap["trade_date"]]
        rows.append(snap)
        rows.sort(key=lambda r: str(r.get("trade_date")))
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
    """近 N 个有快照的交易日：健康分均值、出票成功率、有红项的天数。缺快照的交易日不补、如实显示天数。"""
    rows = sorted(load_snapshots(path), key=lambda r: str(r.get("trade_date")))[-days:]
    scores = [float(r["score"]) for r in rows if isinstance(r.get("score"), (int, float))]
    ok = [bool(r.get("output_ok")) for r in rows]
    return {
        "days_requested": days,
        "days_with_snapshot": len(rows),
        "first_date": rows[0]["trade_date"] if rows else None,
        "last_date": rows[-1]["trade_date"] if rows else None,
        "avg_score": round(mean(scores), 1) if scores else None,
        "min_score": min(scores) if scores else None,
        "output_success_rate": round(sum(ok) / len(ok), 3) if ok else None,
        "output_failed_dates": [r["trade_date"] for r in rows if not r.get("output_ok")],
        "red_days": sum(1 for r in rows if r.get("red")),
        "snapshots": [{k: r.get(k) for k in ("trade_date", "taken_at", "score", "score_db", "penalty", "output_ok")}
                      | {"red": [i["key"] for i in r.get("red") or []], "yellow_count": len(r.get("yellow") or [])}
                      for r in rows],
    }
