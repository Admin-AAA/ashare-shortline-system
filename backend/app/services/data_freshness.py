"""每日数据新鲜度巡检（只读）：滞后 + 近 30 个交易日行数异常 + 关键列全 0/NULL。

由 Prefect 定时任务 data_freshness_check（每天 18:00，只读，可在日更窗口内跑）调用，
发现问题走 emit_alert("data_freshness", ...)；同时包含 daily_basic 停更告警。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

WINDOW = 30
DROP_RATIO = 0.7          # 当日行数 < 近 30 日中位数 * 0.7 视为断崖
DEAD_COL_RATIO = 0.05     # 关键列非 0 非 NULL 占比 < 5% 视为整列失效
ROUND_COUNTS = (1000, 2000, 3000, 4000, 5000, 6000, 8000, 10000)

# table, date_col, allowed_lag_trade_days, key_cols, per_day_checks
DAILY_TABLES: list[tuple[str, str, int, tuple[str, ...], bool]] = [
    ("daily_kbars", "trade_date", 0, ("close", "adj_factor", "turnover_rate", "ma5", "ma60", "macd_dif", "kdj_k",
                                       "boll_mid", "cci", "rsi_6", "chip_cost_50", "chip_winner_rate", "ivol20"), True),
    ("daily_basic", "trade_date", 0, ("total_mv", "circ_mv", "turnover_rate"), True),
    ("capital_flow", "trade_date", 0, ("main_net_inflow_yi",), True),
    ("stk_limits", "trade_date", 0, ("up_limit", "down_limit"), True),
    ("margin_detail", "trade_date", 1, ("rzye",), True),
    ("auction_data", "trade_date", 0, ("price",), True),
    ("stock_quotes", "trade_date", 0, ("price", "total_mv_yi"), True),
    ("index_daily", "trade_date", 0, ("close",), True),
    ("limit_up_board", "trade_date", 0, ("pct_chg",), False),
    ("dragon_tiger", "trade_date", 0, ("net_amount_yi",), False),
    ("concept_sector", "trade_date", 0, (), False),
    ("concept_sector_stock", "trade_date", 0, (), False),
    ("hsgt_flows", "trade_date", 0, ("north_money",), False),
    ("hsgt_top10", "trade_date", 0, ("amount",), False),
    ("minute_kbars", "trade_date", 0, (), False),
]
# 事件/低频表：按自然日阈值判断停更
EVENT_TABLES: list[tuple[str, str, int]] = [
    ("share_floats", "created_at", 10),
    # news_announcement 不在此查：线上真源是公告事件快照（见 SNAPSHOT_MAX_AGE_DAYS），同名 MySQL 表
    # 只由逐股补数写入、不日更，按它判停更是误报（10-20 报「4 天未更新」时快照已写到当天）。
    ("institutional_surveys", "surv_date", 14),
    ("stock_fina_indicator", "ann_date", 30),
    ("holder_numbers", "ann_date", 45),
    ("pledge_stats", "end_date", 30),
    ("margin_data", "trade_date", 5),
    ("hk_holdings", "trade_date", 100),
]

# 事件类数据的线上真源是 auxiliary_snapshot_batches（block_trades/holder_trades 旧表已不再写入，设计如此），
# 按数据集看最近一次 complete 批次的年龄
SNAPSHOT_MAX_AGE_DAYS = {"block_trade": 4, "holder_trade": 4, "share_float": 4, "news_announcement": 4}
# 注意：以上按 fetched_at（抓取时间）计龄；回补旧分区也会刷新它，但日更若当日失败会被下面的 24h 全失败规则抓到。


def _norm(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v)[:10].replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else None


def _today() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


def recent_trade_dates(db, n: int = WINDOW) -> list[str]:
    rows = db.execute(text("SELECT DISTINCT trade_date FROM daily_kbars ORDER BY trade_date DESC LIMIT :n"),
                      {"n": n}).scalars().all()
    return sorted(str(r) for r in rows)


def check_daily_table(db, table: str, col: str, allowed_lag: int, keys: tuple[str, ...], per_day: bool,
                      dates: list[str]) -> dict[str, Any]:
    latest = dates[-1]
    mx = _norm(db.execute(text(f"SELECT MAX(`{col}`) FROM `{table}`")).scalar())
    lag = sum(1 for d in dates if mx is None or d > mx)
    issues: list[str] = []
    if lag > allowed_lag:
        issues.append(f"滞后 {lag} 个交易日（最新 {mx}，最近交易日 {latest}）")
    out: dict[str, Any] = {"table": table, "latest": mx, "lag_trade_days": lag}
    if per_day:
        sel = ", ".join(f"SUM(`{k}` IS NOT NULL AND `{k}` <> 0)" for k in keys)
        rows = db.execute(text(
            f"SELECT `{col}`, COUNT(*){', ' + sel if sel else ''} FROM `{table}` "
            f"WHERE `{col}` >= :s GROUP BY `{col}` ORDER BY `{col}`"), {"s": dates[0]}).all()
        counts = {str(r[0]): int(r[1]) for r in rows}
        out["counts"] = counts
        med = median(counts.values()) if counts else 0
        for r in rows:
            d, n = str(r[0]), int(r[1])
            if med and n < DROP_RATIO * med:
                issues.append(f"{d} 行数 {n} 断崖（中位 {int(med)}）")
            if n in ROUND_COUNTS:
                issues.append(f"{d} 行数恰为 {n}（疑似单次上限截断）")
            dead = [k for k, v in zip(keys, r[2:]) if n and int(v or 0) < DEAD_COL_RATIO * n]
            if dead:
                issues.append(f"{d} 关键列全 0/NULL: {','.join(dead)}")
    out["issues"] = issues
    return out


def check_event_table(db, table: str, col: str, max_age_days: int) -> dict[str, Any]:
    mx = _norm(db.execute(text(f"SELECT MAX(`{col}`) FROM `{table}`")).scalar())
    age = (_today() - date.fromisoformat(mx)).days if mx else None
    issues = [] if age is not None and age <= max_age_days else [f"最新 {mx}，已 {age} 天未更新（阈值 {max_age_days} 天）"]
    return {"table": table, "latest": mx, "age_days": age, "issues": issues}


def check_snapshot_datasets(db) -> list[dict[str, Any]]:
    rows = db.execute(text(
        "SELECT dataset, MAX(CASE WHEN status='complete' THEN fetched_at END), "
        "SUM(status<>'complete' AND fetched_at >= NOW() - INTERVAL 3 DAY), "
        "MAX(CASE WHEN status<>'complete' THEN failure_code END), "
        "SUM(status<>'complete' AND fetched_at >= UTC_TIMESTAMP() - INTERVAL 1 DAY), "
        "SUM(status='complete' AND fetched_at >= UTC_TIMESTAMP() - INTERVAL 1 DAY) "
        "FROM auxiliary_snapshot_batches GROUP BY dataset")).all()
    out = []
    now = datetime.now()
    for ds, last_ok, recent_fail, code, fail_1d, ok_1d in rows:
        limit = SNAPSHOT_MAX_AGE_DAYS.get(str(ds), 4)
        age = (now - last_ok).days if last_ok else None
        issues = []
        if age is None or age > limit:
            issues.append(f"最近成功快照 {last_ok}（{age} 天前），近 3 天失败 {int(recent_fail or 0)} 次，失败码 {code}")
        elif int(fail_1d or 0) and not int(ok_1d or 0):
            # 2026-10-19：holder_trade 10-12 起每天 unknown_schema 0 行，但 10-09 的成功批次让年龄阈值晚几天才报；
            # 近 24h 只有失败、没有任何成功批次即告警，不等年龄超阈值。
            issues.append(f"近 24 小时快照全部失败 {int(fail_1d)} 次（0 次成功），失败码 {code}")
        out.append({"table": f"snapshot:{ds}", "latest": str(last_ok)[:19] if last_ok else None,
                    "age_days": age, "issues": issues})
    return out


_KBAR_BASE_COLUMNS = {"id", "code", "trade_date", "source", "updated_at", "open", "high", "low", "close",
                      "volume", "amount", "pct_chg"}
# 已确认无定义、无写方、无线上读方的列（2026-10-19 审计）；仍列出在结果里但不告警
KNOWN_UNIMPLEMENTED_COLUMNS = {"profit_efficiency"}


def check_indicator_columns(db, trade_date: str) -> dict[str, Any]:
    """daily_kbars 所有指标列：最近交易日非 NULL 占比 < 5% 视为整列为空。"""
    cols = [r[0] for r in db.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = DATABASE() "
        "AND table_name = 'daily_kbars' ORDER BY ordinal_position")) if r[0] not in _KBAR_BASE_COLUMNS]
    sel = ", ".join(f"SUM(`{c}` IS NOT NULL)" for c in cols)
    row = db.execute(text(f"SELECT COUNT(*), {sel} FROM daily_kbars WHERE trade_date = :d"), {"d": trade_date}).one()
    n = int(row[0] or 0)
    empty = [c for c, v in zip(cols, row[1:]) if n and int(v or 0) < DEAD_COL_RATIO * n]
    alerting = [c for c in empty if c not in KNOWN_UNIMPLEMENTED_COLUMNS]
    issues = [f"{trade_date} 指标列整列为空: {','.join(alerting)}"] if alerting else []
    return {"table": "daily_kbars:indicators", "latest": trade_date, "columns_checked": len(cols),
            "empty_columns": empty, "issues": issues}


CHIP_FROZEN_RATIO = 0.9


def chip_frozen_issue(trade_date: str, prev_date: str, compared: int, identical: int) -> str | None:
    if compared and identical > CHIP_FROZEN_RATIO * compared:
        return (f"{trade_date} 筹码列与前一交易日 {prev_date} 完全相同 {identical}/{compared}"
                f"（>{int(CHIP_FROZEN_RATIO * 100)}%，疑似把前一日筹码写成当日）")
    return None


def check_chip_frozen(db, dates: list[str]) -> dict[str, Any]:
    """chip_cost_15/50/85 + chip_weight_avg 与前一交易日完全相同的股票占比 > 90% 告警。"""
    issues: list[str] = []
    for prev, d in zip(dates, dates[1:]):
        n, same = db.execute(text(
            "SELECT COUNT(*), SUM(a.chip_cost_50 = b.chip_cost_50 AND a.chip_cost_15 <=> b.chip_cost_15 "
            "AND a.chip_cost_85 <=> b.chip_cost_85 AND a.chip_weight_avg <=> b.chip_weight_avg) "
            "FROM daily_kbars a JOIN daily_kbars b ON b.code = a.code AND b.trade_date = :p "
            "WHERE a.trade_date = :d AND a.chip_cost_50 IS NOT NULL"), {"p": prev, "d": d}).one()
        msg = chip_frozen_issue(d, prev, int(n or 0), int(same or 0))
        if msg:
            issues.append(msg)
    return {"table": "daily_kbars:chip_frozen", "latest": dates[-1] if dates else None, "issues": issues}


def chip_history_dates(db, since: str, until: str | None = None) -> list[str]:
    """Trade dates in [since, until] plus the trade date just before `since` (comparison baseline)."""
    sql = ("SELECT DISTINCT trade_date FROM daily_kbars WHERE trade_date >= "
           "COALESCE((SELECT MAX(trade_date) FROM daily_kbars WHERE trade_date < :s), :s)")
    params: dict[str, Any] = {"s": since}
    if until:
        sql += " AND trade_date <= :u"
        params["u"] = until
    return [str(r[0]) for r in db.execute(text(sql + " ORDER BY trade_date"), params)]


def check_chip_frozen_since(db, since: str, until: str | None = None) -> dict[str, Any]:
    """历史版冻结巡检：覆盖 since..until 全部交易日（默认巡检只看近 30 个交易日）。只读。

    run_freshness_check(chip_frozen_since=...) 或 scripts/check_chip_frozen.py --since 2025-01-01 调用。
    """
    out = check_chip_frozen(db, chip_history_dates(db, since, until))
    out["table"] = "daily_kbars:chip_frozen_history"
    out["since"], out["until"] = since, until
    out["frozen_dates"] = [m.split(" ", 1)[0] for m in out["issues"]]
    return out


def check_repurchase_events() -> dict[str, Any]:
    from app.services.data_live_sources import repurchase_events_latest

    n, mx = repurchase_events_latest()
    age = (_today() - date.fromisoformat(mx)).days if mx else None
    issues = [] if age is not None and age <= 10 else [f"回购事件 parquet 最新 {mx}（{age} 天前）"]
    return {"table": "events:repurchase", "latest": mx, "age_days": age, "rows": n, "issues": issues}


def run_freshness_check(db, *, alert: bool = True, chip_frozen_since: str | None = None) -> dict[str, Any]:
    from app.core.alerts import emit_alert
    from app.services.daily_basic_ingest import check_daily_basic_staleness

    dates = recent_trade_dates(db)
    results: list[dict[str, Any]] = []
    for spec in DAILY_TABLES:
        try:
            results.append(check_daily_table(db, *spec, dates=dates))
        except Exception as exc:  # noqa: BLE001 - one broken table must not hide the rest
            db.rollback()
            results.append({"table": spec[0], "issues": [f"检查失败 {type(exc).__name__}: {str(exc)[:120]}"]})
    for spec in EVENT_TABLES:
        try:
            results.append(check_event_table(db, *spec))
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            results.append({"table": spec[0], "issues": [f"检查失败 {type(exc).__name__}: {str(exc)[:120]}"]})
    # chip_frozen_since 给定时，筹码冻结巡检覆盖该日起的全部历史交易日（如 2025-01-01），否则只看近 30 日
    chip_check = ((lambda: check_chip_frozen_since(db, chip_frozen_since)) if chip_frozen_since
                  else (lambda: check_chip_frozen(db, dates)))
    for fn in (lambda: check_indicator_columns(db, dates[-1]), chip_check, check_repurchase_events):
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            results.append({"table": "extra", "issues": [f"检查失败 {type(exc).__name__}: {str(exc)[:120]}"]})
    try:
        results.extend(check_snapshot_datasets(db))
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        results.append({"table": "snapshot:*", "issues": [f"检查失败 {type(exc).__name__}: {str(exc)[:120]}"]})
    problems = {r["table"]: r["issues"] for r in results if r["issues"]}
    if alert:
        for table, issues in problems.items():
            emit_alert("data_freshness", f"{table}:{dates[-1]}", f"{table}: " + "；".join(issues[:5]))
    daily_basic = check_daily_basic_staleness(db, latest=dates[-1]) if alert else None
    return {"latest_trade_date": dates[-1], "problems": problems, "tables": results, "daily_basic": daily_basic,
            "ok": not problems}
