"""盘中锁利影子监控服务(只记录不交易)。

设计原则(见 memory self-improve-log 2026-07-30 第N+9轮):
- 影子链路只记录信号,不改任何实际交易/outcome——用真实盘中数据验证锁利价值,
  解决 bar 级回算"5min bar 内时序不可知"导致的成交价不确定问题。
- 盘中轮询精选持仓的 stk_mins 5min 数据,触及锁利目标记录信号(含触发时真实价),
  盘后回填触发后走势并与生产实际出场对比,量化 lock_delta。

三个入口:
- create_monitors_for_trade_date: 为当日已入场精选持仓建监控任务
- poll_once: 盘中轮询一次(每5min调一次),检测触及锁利目标→记录信号
- settle_signals: 盘后回填信号的 post_trigger 走势 + 实际出场 + lock_delta
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.tables import IntradayLockMonitor, IntradayLockSignal

# 锁利目标/比例的默认扫描网格(影子阶段同时监控多档,便于事后找最优)
DEFAULT_TARGET_PCTS: tuple[float, ...] = (4.0, 5.0, 6.0)
DEFAULT_LOCK_RATIO = 0.5
_POLL_FREQ = "5min"


def _ts_code(code: str) -> str:
    return code + (".SH" if code.startswith("6") else ".SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _fetch_positions_for_monitor(db: Session, trade_date: str) -> list[dict[str, Any]]:
    """取该交易日已入场的精选持仓(formal推荐 + outcome已入场未结算或持有中)。"""
    rows = db.execute(
        text(
            """
            SELECT r.recommendation_id, r.code, o.entry_date, o.entry_price, o.exit_date
            FROM recommendations r
            JOIN recommendation_runs run ON r.run_id = run.run_id
            JOIN recommendation_outcomes o ON o.recommendation_id = r.recommendation_id
            WHERE run.run_mode = 'formal'
              AND r.trade_date = :td
              AND o.entry_triggered = 1
              AND o.entry_price IS NOT NULL AND o.entry_price > 0
            """
        ),
        {"td": trade_date},
    ).fetchall()
    return [
        {
            "recommendation_id": str(r[0]),
            "code": str(r[1]),
            "entry_date": str(r[2]),
            "entry_price": float(r[3]),
            "exit_date": str(r[4]) if r[4] else None,
        }
        for r in rows
    ]


def create_monitors_for_trade_date(
    db: Session,
    trade_date: str,
    target_pcts: tuple[float, ...] = DEFAULT_TARGET_PCTS,
    lock_ratio: float = DEFAULT_LOCK_RATIO,
) -> int:
    """为 trade_date 已入场的精选持仓建监控任务(每持仓×每档目标一条)。幂等。"""
    positions = _fetch_positions_for_monitor(db, trade_date)
    created = 0
    now = datetime.utcnow()
    for pos in positions:
        for target in target_pcts:
            # 幂等: 同 recommendation_id + target_pct 已存在则跳过
            exists = db.execute(
                text(
                    "SELECT 1 FROM intraday_lock_monitors "
                    "WHERE recommendation_id = :rid AND target_pct = :tp LIMIT 1"
                ),
                {"rid": pos["recommendation_id"], "tp": target},
            ).fetchone()
            if exists:
                continue
            monitor = IntradayLockMonitor(
                monitor_id=_new_id("ilm"),
                recommendation_id=pos["recommendation_id"],
                code=pos["code"],
                trade_date=trade_date,
                entry_date=pos["entry_date"],
                entry_price=pos["entry_price"],
                target_pct=target,
                lock_ratio=lock_ratio,
                status="monitoring",
                created_at=now,
                updated_at=now,
            )
            db.add(monitor)
            created += 1
    db.commit()
    return created


def _fetch_minute_highs(pro, code: str, start_dt: str, end_dt: str) -> list[tuple[str, float, float]]:
    """拉 code 的 5min K线,返回 [(trade_time, high, low)] 按时间升序。"""
    df = pro.query(
        "stk_mins",
        ts_code=_ts_code(code),
        freq=_POLL_FREQ,
        start_date=start_dt,
        end_date=end_dt,
    )
    if df is None or len(df) == 0:
        return []
    df = df.sort_values("trade_time")
    return [
        (str(row["trade_time"]), float(row["high"]), float(row["low"]))
        for _, row in df.iterrows()
    ]


def _first_hit_bar(bars: list[tuple[str, float, float]], target_price: float) -> tuple[str, float] | None:
    """在按时间升序的 5min bars [(trade_time, high, low)] 中找首个 high >= target_price 的 bar。

    纯函数,便于单测。返回 (trigger_time, target_price) 或 None。
    """
    for (tt, high, low) in bars:
        if high >= target_price:
            return (tt, target_price)
    return None


def _lock_delta_pct(trigger_price: float, actual_exit_price: float, entry_price: float) -> float:
    """锁利价相对实际出场价的差额(相对entry的百分比)。正=锁利更优,负=锁利吃亏。"""
    return ((trigger_price - actual_exit_price) / entry_price) * 100


def poll_once(db: Session, pro, now_str: str | None = None) -> dict[str, int]:
    """盘中轮询一次: 对所有 monitoring 状态的监控任务,检测是否触及锁利目标。

    返回统计 {checked, triggered, errors}。now_str 用于测试注入;生产用当前时间。
    """
    monitors = db.execute(
        text("SELECT monitor_id, code, entry_price, target_pct, lock_ratio, entry_date "
             "FROM intraday_lock_monitors WHERE status = 'monitoring'")
    ).fetchall()
    stats = {"checked": 0, "triggered": 0, "errors": 0}
    now = datetime.utcnow()
    for m in monitors:
        monitor_id, code, entry_price, target_pct, lock_ratio, entry_date = (
            str(m[0]), str(m[1]), float(m[2]), float(m[3]), float(m[4]), str(m[5])
        )
        stats["checked"] += 1
        target_price = entry_price * (1 + target_pct / 100)
        # 拉入场以来至今的 5min(监控当日内)
        day = now.strftime("%Y-%m-%d")
        start_dt = f"{entry_date} 09:30:00"
        end_dt = now_str or f"{day} 15:00:00"
        try:
            bars = _fetch_minute_highs(pro, code, start_dt, end_dt)
        except Exception:
            stats["errors"] += 1
            continue
        # 找首个 high >= target_price 的 bar
        hit = _first_hit_bar(bars, target_price)
        if hit is None:
            continue
        trigger_time, trigger_price = hit
        # 记录信号(幂等: 同 monitor 不重复记)
        exists = db.execute(
            text("SELECT 1 FROM intraday_lock_signals WHERE monitor_id = :mid LIMIT 1"),
            {"mid": monitor_id},
        ).fetchone()
        if exists:
            db.execute(
                text("UPDATE intraday_lock_monitors SET status='triggered', "
                     "triggered_at=:t, updated_at=:t WHERE monitor_id=:mid"),
                {"t": now, "mid": monitor_id},
            )
            db.commit()
            continue
        signal = IntradayLockSignal(
            signal_id=_new_id("ils"),
            monitor_id=monitor_id,
            code=code,
            trade_date=day,
            target_pct=target_pct,
            lock_ratio=lock_ratio,
            trigger_time=trigger_time,
            trigger_price=trigger_price,
            status="open",
            created_at=now,
            updated_at=now,
        )
        db.add(signal)
        db.execute(
            text("UPDATE intraday_lock_monitors SET status='triggered', "
                 "triggered_at=:t, updated_at=:t WHERE monitor_id=:mid"),
            {"t": now, "mid": monitor_id},
        )
        db.commit()
        stats["triggered"] += 1
    return stats


def settle_signals(db: Session, pro) -> dict[str, int]:
    """盘后回填 open 信号: post_trigger 走势 + 生产实际出场 + lock_delta。

    lock_delta_pct = trigger_price 相对 actual_exit_price 的差(锁利价 - 实际出场价)/entry*100,
    正值=锁利优于实际出场,负值=锁利吃亏(趋势走强单)。
    """
    open_signals = db.execute(
        text("SELECT signal_id, monitor_id, code, trigger_time, trigger_price "
             "FROM intraday_lock_signals WHERE status = 'open'")
    ).fetchall()
    stats = {"settled": 0, "errors": 0, "skipped": 0}
    now = datetime.utcnow()
    for s in open_signals:
        signal_id, monitor_id, code, trigger_time, trigger_price = (
            str(s[0]), str(s[1]), str(s[2]), str(s[3]), float(s[4])
        )
        # 拿监控的 entry_price / recommendation_id / entry_date
        mon = db.execute(
            text("SELECT entry_price, recommendation_id, entry_date FROM intraday_lock_monitors WHERE monitor_id = :mid"),
            {"mid": monitor_id},
        ).fetchone()
        if not mon:
            stats["skipped"] += 1
            continue
        entry_price, recommendation_id = float(mon[0]), str(mon[1])
        # 生产实际出场(outcome)
        oc = db.execute(
            text("SELECT exit_price, executed_return, exit_date FROM recommendation_outcomes "
                 "WHERE recommendation_id = :rid ORDER BY horizon_days LIMIT 1"),
            {"rid": recommendation_id},
        ).fetchone()
        if not oc or oc[0] is None:
            # 实际出场还没结算,留到下次
            stats["skipped"] += 1
            continue
        actual_exit_price = float(oc[0])
        actual_exit_return = float(oc[1]) if oc[1] is not None else None
        exit_date = str(oc[2]) if oc[2] else None
        # post_trigger 走势: 从 trigger_time 到 exit_date 的 5min max/min
        try:
            end_dt = f"{exit_date} 15:00:00" if exit_date else trigger_time[:10] + " 15:00:00"
            bars = _fetch_minute_highs(pro, code, trigger_time, end_dt)
            highs = [b[1] for b in bars]
            lows = [b[2] for b in bars]
            post_max = ((max(highs) / entry_price - 1) * 100) if highs else None
            post_min = ((min(lows) / entry_price - 1) * 100) if lows else None
        except Exception:
            stats["errors"] += 1
            continue
        lock_delta = _lock_delta_pct(trigger_price, actual_exit_price, entry_price)
        db.execute(
            text(
                "UPDATE intraday_lock_signals SET post_trigger_max_pct=:pmax, "
                "post_trigger_min_pct=:pmin, actual_exit_price=:axp, actual_exit_return=:axr, "
                "lock_delta_pct=:ld, status='closed', updated_at=:t WHERE signal_id=:sid"
            ),
            {
                "pmax": post_max, "pmin": post_min, "axp": actual_exit_price,
                "axr": actual_exit_return, "ld": lock_delta, "t": now, "sid": signal_id,
            },
        )
        db.commit()
        stats["settled"] += 1
    return stats
