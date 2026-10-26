"""盘后出票是否成功：某个交易日的正式推荐有没有按时、按实盘口径产出。

「出票成功」= 两条都满足：
1. 短线：每个现役单策略（standalone_strategy_ids）当天都有一个 completed、evaluation_scope=live_forward 的 formal run，
   且生成时间在次日 09:25 之前。0 只推荐也算成功（策略规则否决是正常结果），只有"没跑出来"才算失败。
   带 trade_date_override 的补跑会被记成 historical_replay，不算实盘，因此不算成功。
2. 中长线：status/{date}.json 里现役策略没有 data_error，也没有缺记录。

只读：查 recommendation_runs、skill_registry 和 status 文件，不写库、不调上游。
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from sqlalchemy import select, text

OUTPUT_DEADLINE = _dt.time(18, 35)  # 17:35 盘后日更 + 1 小时：过了这个点还没出票，就算当天出票失败


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
    missing, errors = [], []
    for sid in ms.STRATEGIES:
        row = stored.get(sid)
        if not row:
            missing.append(sid)
        elif row.get("status") == "data_error":
            errors.append(sid)
    return {"status_file": f.exists(), "missing": missing, "data_error": errors}


def daily_output_status(db, trade_date: str) -> dict[str, Any]:
    expected = expected_short_strategies(db)
    done = _live_short_runs(db, trade_date)
    short_missing = sorted(set(expected) - set(done))
    mid = _midlong_status(trade_date)
    ok = not short_missing and not mid["missing"] and not mid["data_error"]
    reasons = []
    if short_missing:
        reasons.append(f"短线缺实盘出票：{','.join(short_missing)}")
    if mid["missing"]:
        reasons.append(f"中长线缺运行记录：{','.join(mid['missing'])}")
    if mid["data_error"]:
        reasons.append(f"中长线数据异常：{','.join(mid['data_error'])}")
    return {
        "trade_date": trade_date,
        "ok": ok,
        "short_expected": len(expected),
        "short_done": len(expected) - len(short_missing),
        "short_missing": short_missing,
        "short_recommended": sum(v["recommended_count"] for v in done.values()),
        "midlong_missing": mid["missing"],
        "midlong_data_error": mid["data_error"],
        "reason": "；".join(reasons) or "按时出票",
    }


def output_target_date(open_days: list[str], now: _dt.datetime) -> str | None:
    """已过出票截止（当天 18:35）的最近一个交易日。open_days 为升序 YYYY-MM-DD。"""
    today = now.date().isoformat()
    past = [d for d in open_days if d < today]
    if today in open_days and now.time() >= OUTPUT_DEADLINE:
        return today
    return past[-1] if past else None
