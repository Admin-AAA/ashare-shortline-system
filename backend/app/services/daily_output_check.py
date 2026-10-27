"""盘后出票是否成功：某个交易日的正式推荐有没有按时、按实盘口径产出。

「出票成功」= 两条都满足：
1. 短线：每个现役单策略（standalone_strategy_ids）当天都有一个 completed、evaluation_scope=live_forward 的 formal run，
   且生成时间在入场日 09:15 之前。0 只推荐也算成功（策略规则否决是正常结果），只有"没跑出来"才算失败。
   安全网等带 trade_date_override 的补跑：入场日开盘前生成的同样记为 live_forward、算出票成功；开盘后才补的记 historical_replay，不算。
2. 中长线：status/{date}.json 里现役策略没有 data_error，也没有缺记录。
   例外：晚发布数据的策略（LATE_DATA_STRATEGIES，大宗交易约 21:00 发布）在次日 08:30 之前没有结果只记「待数据、未到点」，
   不算失败；次日 08:50 复核时仍没有才算失败。

只读：查 recommendation_runs、skill_registry 和 status 文件，不写库、不调上游。
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

CN_TZ = ZoneInfo("Asia/Shanghai")
OUTPUT_DEADLINE = _dt.time(18, 35)  # 17:35 盘后日更 + 1 小时：过了这个点还没出票，就算当天出票失败
# 晚发布数据的策略：大宗交易约 21:00 才发布（21:00 晚间补数出票，次日 08:03 盘前备份再补一次）。
# 17:35 / 18:40 时没有结果只记「待数据、未到点」；到次日 LATE_DATA_READY_TIME 仍没有才算失败（08:50 复核）。
LATE_DATA_STRATEGIES: dict[str, str] = {"mid-block-trade-premium": "大宗交易约 21:00 发布"}
LATE_DATA_READY_TIME = _dt.time(8, 30)


def expected_short_strategies(db) -> list[str]:
    from app.models.strategy import SkillRegistry
    from app.services.standalone_strategies import standalone_strategy_ids

    return standalone_strategy_ids(db.scalars(select(SkillRegistry)).all())


def _live_short_runs(db, trade_date: str) -> dict[str, dict[str, Any]]:
    from app.services.midterm_live_seal import entry_deadline
    from app.services.standalone_strategies import run_skill_ids

    deadline_utc = entry_deadline(trade_date).astimezone(_dt.timezone.utc).replace(tzinfo=None)
    rows = db.execute(text("""
        SELECT run_id, selected_skills_json, recommended_count, actual_generated_at, finished_at
        FROM recommendation_runs
        WHERE trade_date = :d AND run_mode = 'formal' AND status = 'completed'
          AND evaluation_scope = 'live_forward'
    """), {"d": trade_date}).all()
    out: dict[str, dict[str, Any]] = {}
    for run_id, sel, cnt, generated_at, finished_at in rows:
        ids = run_skill_ids(sel)
        if len(ids) != 1:
            continue
        stamp = generated_at or finished_at  # 库内为 UTC naive
        if stamp is not None and stamp > deadline_utc:
            continue  # 次日开盘后才生成，不算实盘出票
        out.setdefault(ids[0], {"run_id": run_id, "recommended_count": int(cnt or 0)})
    return out


def _midlong_status(trade_date: str) -> dict[str, Any]:
    from app.services import midterm_service as ms

    f = ms._status_dir() / f"{trade_date}.json"
    stored: dict[str, Any] = {}
    if f.exists():
        try:
            stored = json.loads(f.read_text())
        except Exception:  # noqa: BLE001 - 读不出当作缺记录
            stored = {}
    return {"status_file": f.exists(), "stored": stored}


def late_data_ready_at(sid: str, trade_date: str) -> _dt.datetime | None:
    """晚发布数据的策略：到这个时刻之前没出票只算「待数据、未到点」。不在名单里返回 None。"""
    if sid not in LATE_DATA_STRATEGIES:
        return None
    from app.services.midterm_live_seal import next_open_date

    return _dt.datetime.combine(next_open_date(trade_date), LATE_DATA_READY_TIME)


def classify_midlong(stored: dict[str, Any], trade_date: str, now: _dt.datetime,
                     strategy_ids: list[str]) -> dict[str, list[str]]:
    """把各现役中长线策略分成 失败（缺记录/数据异常）与 待数据（晚发布数据未到点）。now 为北京时间 naive。"""
    missing, errors, pending = [], [], []
    for sid in strategy_ids:
        row = stored.get(sid)
        bad = (not row) or row.get("status") == "data_error"
        if not bad:
            continue
        ready = late_data_ready_at(sid, trade_date)
        if ready is not None and now < ready:
            pending.append(sid)
        elif not row:
            missing.append(sid)
        else:
            errors.append(sid)
    return {"missing": missing, "data_error": errors, "pending": pending}


def daily_output_status(db, trade_date: str, now: _dt.datetime | None = None) -> dict[str, Any]:
    """now：北京时间 naive，默认当前。决定晚发布数据的策略是「待数据」还是「失败」。"""
    from app.services import midterm_service as ms

    now = now or _dt.datetime.now(CN_TZ).replace(tzinfo=None)
    expected = expected_short_strategies(db)
    done = _live_short_runs(db, trade_date)
    short_missing = sorted(set(expected) - set(done))
    mid = classify_midlong(_midlong_status(trade_date)["stored"], trade_date, now, list(ms.STRATEGIES))
    ok = not short_missing and not mid["missing"] and not mid["data_error"]
    reasons = []
    if short_missing:
        reasons.append(f"短线缺实盘出票：{','.join(short_missing)}")
    if mid["missing"]:
        reasons.append(f"中长线缺运行记录：{','.join(mid['missing'])}")
    if mid["data_error"]:
        reasons.append(f"中长线数据异常：{','.join(mid['data_error'])}")
    if mid["pending"]:
        reasons.append(f"待数据、未到点（次日 {LATE_DATA_READY_TIME:%H:%M} 前不算失败）：{','.join(mid['pending'])}")
    return {
        "trade_date": trade_date,
        "checked_at": now.isoformat(timespec="seconds"),
        "ok": ok,
        "status": "failed" if not ok else ("pending" if mid["pending"] else "ok"),
        "short_expected": len(expected),
        "short_done": len(expected) - len(short_missing),
        "short_missing": short_missing,
        "short_recommended": sum(v["recommended_count"] for v in done.values()),
        "midlong_missing": mid["missing"],
        "midlong_data_error": mid["data_error"],
        "midlong_pending": mid["pending"],
        "reason": "；".join(reasons) or "按时出票",
    }


def output_target_date(open_days: list[str], now: _dt.datetime) -> str | None:
    """已过出票截止（当天 18:35）的最近一个交易日。open_days 为升序 YYYY-MM-DD。"""
    today = now.date().isoformat()
    past = [d for d in open_days if d < today]
    if today in open_days and now.time() >= OUTPUT_DEADLINE:
        return today
    return past[-1] if past else None
