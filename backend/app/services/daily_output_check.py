"""盘后出票是否成功：某个交易日的正式推荐有没有按时、按实盘口径产出。

「出票成功」= 两条都满足：
1. 短线：每个现役单策略（standalone_strategy_ids）当天都有一个 completed、evaluation_scope=live_forward 的 formal run，
   且生成时间在入场日 09:15 之前。0 只推荐也算成功（策略规则否决是正常结果），只有"没跑出来"才算失败。
   安全网等带 trade_date_override 的补跑：入场日开盘前生成的同样记为 live_forward、算出票成功；开盘后才补的记 historical_replay，不算。
2. 中长线：status/{date}.json 里现役策略没有 data_error / data_not_ready，也没有缺记录。
   出票前数据就绪闸门（strategy_data_readiness）拦下的策略，短线不建 run、中长线记 data_not_ready，都算当天没出票，
   原因（缺哪份数据、最新到哪天、应到哪天）单独写进 reason，健康分的「盘后出票」项因此能看到。
   例外：晚发布数据的策略（LATE_DATA_STRATEGIES，大宗交易约 21:00 发布）在次日 08:30 之前没有结果只记「待数据、未到点」，
   不算失败；次日 08:50 复核时仍没有才算失败。

什么时候判：某个交易日要过了当天 OUTPUT_DEADLINE 才判它（output_due / output_target_date）；
没到点时巡检、健康快照判上一个交易日，自愈也不归档、不补跑当天，缺票只代表「还没跑完」。

只读：查 recommendation_runs、skill_registry 和 status 文件，不写库、不调上游。
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

CN_TZ = ZoneInfo("Asia/Shanghai")
# 出票截止：过了当天这个点还没出票，才算当天出票失败。取 22:50 晚间数据质量验收的时点（scheduler_pure.DATA_QUALITY_CHECKS），
# 排在全部自动出票路径之后：21:00 晚间补数（闸门通过即 late_repick）→ 21:30 盘后出票流水线
# → 22:15 daily_run_watchdog 缺票时自动补跑一次。流水线单次实测 16–22 分钟（11-25 21:30 那次 982 秒，
# 11-25 watchdog 补跑 1300 秒），22:15 补跑约 22:37 结束。22:50 验收与 22:55 健康快照都要判当天，截止不能晚于它们；
# test_output_deadline_follows_schedule 校验这条顺序。
# 旧值 18:35（17:35 日更 + 1 小时）在 2026-11-24 出票挪到 21:30 后没跟着改，18:35–21:30 会把当天误报为出票失败。
OUTPUT_DEADLINE = _dt.time(22, 50)
# 晚发布数据的策略：大宗交易约 21:00 才发布（21:30 出票时可能还没有，次日 08:03 盘前备份再补一次）。
# 当晚 22:50 验收 / 22:55 快照时没有结果只记「待数据、未到点」；到次日 LATE_DATA_READY_TIME 仍没有才算失败（08:50 巡检）。
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
    missing, errors, not_ready, pending = [], [], [], []
    for sid in strategy_ids:
        row = stored.get(sid)
        bad = (not row) or row.get("status") in ("data_error", "data_not_ready")
        if not bad:
            continue
        ready = late_data_ready_at(sid, trade_date)
        if ready is not None and now < ready:
            pending.append(sid)
        elif not row:
            missing.append(sid)
        elif row.get("status") == "data_not_ready":
            not_ready.append(sid)
        else:
            errors.append(sid)
    return {"missing": missing, "data_error": errors, "data_not_ready": not_ready, "pending": pending}


def daily_output_status(db, trade_date: str, now: _dt.datetime | None = None) -> dict[str, Any]:
    """now：北京时间 naive，默认当前。决定晚发布数据的策略是「待数据」还是「失败」。"""
    from app.services import midterm_service as ms

    from app.services.strategy_data_readiness import blocked_on

    now = now or _dt.datetime.now(CN_TZ).replace(tzinfo=None)
    expected = expected_short_strategies(db)
    done = _live_short_runs(db, trade_date)
    short_missing = sorted(set(expected) - set(done))
    mid = classify_midlong(_midlong_status(trade_date)["stored"], trade_date, now, list(ms.STRATEGIES))
    # 出票前数据就绪闸门拦下的策略（output/strategy_readiness/<日期>.json 里仍是未就绪）：同样算没出票，原因单列
    blocked = blocked_on(trade_date)
    short_not_ready = [sid for sid in short_missing if sid in blocked]
    short_missing_other = [sid for sid in short_missing if sid not in blocked]
    ok = not short_missing and not mid["missing"] and not mid["data_error"] and not mid["data_not_ready"]
    reasons = []
    if short_missing_other:
        reasons.append(f"短线缺实盘出票：{','.join(short_missing_other)}")
    not_ready = short_not_ready + mid["data_not_ready"]
    if not_ready:
        reasons.append("数据未就绪、当天没出票：" + "；".join(f"{sid}（{blocked.get(sid) or '缺数据'}）" for sid in not_ready))
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
        "midlong_data_not_ready": mid["data_not_ready"],
        "short_data_not_ready": short_not_ready,
        "data_not_ready_reasons": {sid: blocked.get(sid, "") for sid in not_ready},
        "midlong_pending": mid["pending"],
        "reason": "；".join(reasons) or "按时出票",
    }


def _cn_naive(now: _dt.datetime) -> _dt.datetime:
    return now.astimezone(CN_TZ).replace(tzinfo=None) if now.tzinfo is not None else now


def output_due(trade_date: str, now: _dt.datetime) -> bool:
    """trade_date 是否已过出票截止（当天 OUTPUT_DEADLINE）。now 为北京时间 naive；带时区的先换成北京时间。"""
    return _cn_naive(now) >= _dt.datetime.combine(_dt.date.fromisoformat(trade_date), OUTPUT_DEADLINE)


def output_target_date(open_days: list[str], now: _dt.datetime) -> str | None:
    """已过出票截止的最近一个交易日（当天过了 OUTPUT_DEADLINE 才轮到当天）。open_days 为升序 YYYY-MM-DD。"""
    due = [d for d in open_days if output_due(d, now)]
    return due[-1] if due else None
