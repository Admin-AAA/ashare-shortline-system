"""
陈小群龙头战法 — 独立回测脚本。

流程：
  1. 从 daily_kbars 获取交易日历
  2. 每天调用 feature_builder._compute_chen_xiaoqun_signals() 获取龙头候选
  3. 检测当日情绪周期 → 确定可开仓位
  4. 对通过龙头识别的候选，次日开盘买入
  5. 持续监控止损条件，满足则次日开盘卖出
  6. 输出回测指标

用法：
  cd backend && source .venv/bin/activate && python scripts/backtest_chen_xiaoqun.py
"""

from __future__ import annotations

import logging
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

sys.path.insert(0, ".")

# ── 配置日志 ──
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── 回测参数 ──
START_DATE = "2026-01-01"
END_DATE = "2026-05-25"

# 交易成本（单边千一滑点 + 佣金）
BUY_COST_RATIO = 0.0013
SELL_COST_RATIO = 0.0018

# 退潮期强平前，最多跟踪多少个交易日后仍无机会就退出
MAX_HOLDING_DAYS = 20

# 候选池预过滤参数
MIN_PCT = 3.0           # 最小涨幅
MAX_PCT = 10.0          # 最大涨幅（涨停也可以）
MIN_AMOUNT_YI = 2.0     # 最小成交额（亿）
MAX_TURNOVER = 20.0     # 最大换手率
MIN_TURNOVER = 1.5      # 最小换手率
INITIAL_CAPITAL = 100000.0  # 初始本金（¥）

# 次日开盘强度过滤统计
_open_filter_stats = {"before": 0, "after": 0}


def load_dragon_tiger_map(db: Session, start_date: str, end_date: str) -> dict[str, dict[str, dict]]:
    """从 dragon_tiger 表预加载龙虎榜数据。

    Returns:
        {trade_date: {code: {"net_amount_yi": ..., "buy_amount_yi": ..., "sell_amount_yi": ...}}}
    """
    from app.models.tables import DragonTiger

    rows = db.scalars(
        select(DragonTiger).where(
            DragonTiger.trade_date >= start_date,
            DragonTiger.trade_date <= end_date,
        )
    ).all()

    result: dict[str, dict[str, dict]] = {}
    for row in rows:
        td = str(row.trade_date)
        code = str(row.code)
        if td not in result:
            result[td] = {}
        result[td][code] = {
            "net_amount_yi": float(row.net_amount_yi) if row.net_amount_yi else 0.0,
            "buy_amount_yi": float(row.buy_amount_yi) if row.buy_amount_yi else 0.0,
            "sell_amount_yi": float(row.sell_amount_yi) if row.sell_amount_yi else 0.0,
        }

    logger.info("龙虎榜数据: %d 个交易日有数据, 共 %d 条", len(result), len(rows))
    return result


def main() -> None:
    import os

    from app.models.tables import DailyKbar

    db_url = os.environ.get("DATABASE_URL", "mysql+pymysql://aowang:1279990918@139.196.242.53:4407/ashare_workbench?charset=utf8mb4")
    engine = create_engine(db_url, connect_args={"charset": "utf8mb4"}, pool_pre_ping=True)

    with Session(engine) as db:
        _run_backtest(db)

    engine.dispose()


def _run_backtest(db: Session) -> dict[str, Any]:
    from app.db.session import Base
    from app.models.tables import DailyKbar, LimitUpBoard, Stock

    # ── 1. 获取交易日历 ──
    trade_dates = list(
        db.scalars(
            select(DailyKbar.trade_date)
            .distinct()
            .where(
                DailyKbar.trade_date >= START_DATE,
                DailyKbar.trade_date <= END_DATE,
            )
            .order_by(DailyKbar.trade_date.asc())
        ).all()
    )
    logger.info("交易日历: %s ~ %s, 共 %d 天", trade_dates[0] if trade_dates else "?", trade_dates[-1] if trade_dates else "?", len(trade_dates))

    if len(trade_dates) < 5:
        logger.warning("交易日不足 5 天，无法运行回测")
        return

    # ── 2. 预加载全量数据 ──
    logger.info("预加载 daily_kbars ...")
    all_bars: list[DailyKbar] = db.scalars(
        select(DailyKbar).where(
            DailyKbar.trade_date >= trade_dates[0],
            DailyKbar.trade_date <= trade_dates[-1],
        ).order_by(DailyKbar.trade_date.asc())
    ).all()

    # 按日期分组
    bars_by_date: dict[str, list[DailyKbar]] = defaultdict(list)
    for bar in all_bars:
        bars_by_date[str(bar.trade_date)].append(bar)

    # 按 (code, date) 索引
    bar_map: dict[tuple[str, str], DailyKbar] = {}
    for bar in all_bars:
        bar_map[(str(bar.code), str(bar.trade_date))] = bar

    # 预加载 Stock 元信息
    all_codes = {str(bar.code) for bar in all_bars}
    stock_meta_list: list[Stock] = db.scalars(
        select(Stock).where(Stock.code.in_(all_codes))
    ).all()
    stock_meta: dict[str, dict[str, Any]] = {
        str(s.code): {"name": str(s.name or s.code), "industry": s.industry or "未分类", "is_st": bool(s.is_st)}
        for s in stock_meta_list
    }

    # 预加载 limit_up_board
    limit_up_rows = db.scalars(
        select(LimitUpBoard).where(
            LimitUpBoard.trade_date >= trade_dates[0],
            LimitUpBoard.trade_date <= trade_dates[-1],
        )
    ).all()
    limit_up_by_date: dict[str, list[LimitUpBoard]] = defaultdict(list)
    for row in limit_up_rows:
        limit_up_by_date[str(row.trade_date)].append(row)

    # 涨停代码集合（用于情绪周期检测）
    limit_up_codes_by_date: dict[str, set[str]] = {}
    for td, rows in limit_up_by_date.items():
        limit_up_codes_by_date[td] = {str(r.code) for r in rows if r.limit_type == "U"}

    # 预加载龙虎榜
    dt_map = load_dragon_tiger_map(db, trade_dates[0], trade_dates[-1])
    dt_trade_dates = set(dt_map.keys())
    overlap_dates = dt_trade_dates & set(trade_dates)
    if len(overlap_dates) < len(trade_dates) * 0.3:
        logger.warning("龙虎榜数据覆盖不足 (%d/%d 个交易日, %.0f%%)，仅有龙虎榜数据的日期会使用真实数据",
                       len(overlap_dates), len(trade_dates), len(overlap_dates) / max(1, len(trade_dates)) * 100)
    else:
        logger.info("龙虎榜数据覆盖 %d/%d 个交易日", len(overlap_dates), len(trade_dates))

    # ── 3. 回测主循环 ──
    from app.services.chen_xiaoqun_service import EmotionCycle, detect_emotion_cycle, get_position_pct

    global _open_filter_stats
    _open_filter_stats = {"before": 0, "after": 0}

    trades: list[dict[str, Any]] = []
    cycle_distribution: dict[str, int] = {"启动": 0, "发酵": 0, "高潮": 0, "退潮": 0}
    stop_loss_stats: dict[str, int] = {"stop_loss": 0, "half": 0, "exit": 0, "hold": 0}
    active_positions: dict[str, dict[str, Any]] = {}  # code -> position info
    balance = INITIAL_CAPITAL         # 当前总权益
    portfolio_values = [1.0]          # 归一化净值序列（从 1.0 开始）

    date_set = set(trade_dates)

    for idx, td in enumerate(trade_dates):
        if idx + 1 >= len(trade_dates):
            break  # 需要下一个交易日来操作

        next_td = trade_dates[idx + 1]

        # ── 3a. 检测情绪周期 ──
        try:
            cycle = detect_emotion_cycle(td, db)
        except Exception as exc:
            logger.warning("情绪周期检测失败 %s: %s", td, exc)
            cycle = EmotionCycle.STARTUP

        cycle_distribution[cycle.value] += 1

        # ── 3b. 获取龙头候选 ──
        leader_candidates = _find_leader_candidates(
            db=db,
            trade_date=td,
            bars=bars_by_date.get(td, []),
            stock_meta=stock_meta,
            dt_map=dt_map.get(td),
            bar_map=bar_map,
            next_td=next_td,
        )

        # ── 3c. 退潮期不开新仓 ──
        if cycle == EmotionCycle.RETREAT:
            # 强制平所有仓位
            for code, pos in list(active_positions.items()):
                pnl = _close_position(
                    db=db,
                    pos=pos,
                    trades=trades,
                    active_positions=active_positions,
                    bar_map=bar_map,
                    exit_date=next_td,
                    exit_reason="退潮期强制平仓",
                )
                balance += pnl
            portfolio_values.append(balance / INITIAL_CAPITAL)
            continue

        # ── 3d. 管理已有持仓（止损检查）──
        pnl_closed = _manage_positions(
            db=db,
            active_positions=active_positions,
            trades=trades,
            bar_map=bar_map,
            current_date=td,
            trade_dates_set=date_set,
            trade_dates=trade_dates,
            current_idx=idx,
            stop_loss_stats=stop_loss_stats,
        )
        balance += pnl_closed

        # ── 3e. 开新仓 ──
        position_pct = get_position_pct(cycle)

        # 过滤出龙头候选
        candidates = [c for c in leader_candidates if c.get("_is_leader_candidate")]

        if candidates and position_pct > 0:
            # 等分仓位
            per_stake = position_pct / len(candidates)

            for cand in candidates:
                code = cand["code"]
                if code in active_positions:
                    continue  # 已有持仓不重复开

                # 查看次日是否有数据（可买入）
                next_bar = bar_map.get((code, next_td))
                if next_bar is None:
                    continue
                if _num(next_bar.pct_chg) >= 9.8:
                    # 次日一字涨停买不到，跳过
                    continue

                entry_price = _num(next_bar.open)
                if entry_price <= 0:
                    entry_price = _num(next_bar.close)
                if entry_price <= 0:
                    continue

                position_value = balance * per_stake
                position = {
                    "code": code,
                    "name": cand.get("name", code),
                    "entry_date": next_td,
                    "entry_price": entry_price,
                    "entry_date_idx": trade_dates.index(next_td),
                    "position_value": position_value,
                    "day_held": 0,
                    "already_halved": False,
                    "signal_date": td,
                    "signal_day_volume": _num(cand.get("daily_volume", cand.get("volume"))),
                    "industry": cand.get("industry", "未分类"),
                    "cycle": cycle.value,
                }
                active_positions[code] = position

        portfolio_values.append(balance / INITIAL_CAPITAL)

    # ── 4. 计算指标 ──
    return _output_results(trades, cycle_distribution, stop_loss_stats, trade_dates, portfolio_values, INITIAL_CAPITAL)


def _find_leader_candidates(
    db: Session,
    trade_date: str,
    bars: list[DailyKbar],
    stock_meta: dict[str, dict[str, Any]],
    dt_map: dict[str, dict] | None = None,
    bar_map: dict[tuple[str, str], DailyKbar] | None = None,
    next_td: str | None = None,
) -> list[dict[str, Any]]:
    """从当日日K线中筛选龙头候选（模拟 _compute_chen_xiaoqun_signals 逻辑）。

    当无法获取实时行情辅助数据时，使用价量回退推断。

    Args:
        dt_map: 当日龙虎榜数据 {code: {net_amount_yi, ...}}, None 表示无数据
    """
    candidates = []
    for bar in bars:
        code = str(bar.code)
        meta = stock_meta.get(code) or {}
        if meta.get("is_st"):
            continue

        pct_val = _num(bar.pct_chg)
        turnover = _num(bar.turnover_rate)
        amount_yi = _num(bar.amount) / 1e8
        close = _num(bar.close)

        # 基础过滤（类似陈小群的候选条件）
        if pct_val < MIN_PCT or pct_val > MAX_PCT:
            continue
        if turnover < MIN_TURNOVER or turnover > MAX_TURNOVER:
            continue
        if amount_yi < MIN_AMOUNT_YI:
            continue

        # 是否为涨停
        is_limit_up = pct_val >= 9.8

        # ── 龙虎榜增强信号 ──
        dt = (dt_map or {}).get(code)
        dt_available = dt is not None
        net_yi = 0.0
        buy_yi = 0.0
        dt_boost = False
        if dt_available:
            net_yi = float(dt.get("net_amount_yi", 0))
            buy_yi = float(dt.get("buy_amount_yi", 0))
            dt_boost = net_yi > 0 or buy_yi > 0.5

        # 回退龙头推断（无概念板块数据时用价量）
        leader_from_price = (
            pct_val > 5 and amount_yi >= 5 and 2 <= turnover <= 10
        )
        leader_breakout = (
            bool(bar.breakout20)
            and pct_val > 3 and amount_yi >= 4 and 3 <= turnover <= 10
        )
        is_leader = is_limit_up or leader_from_price or leader_breakout

        # 龙虎榜增强：上榜且净买入的候选优先确认
        if dt_boost and is_leader:
            is_leader = True

        candidate = {
            "code": code,
            "name": meta.get("name", code),
            "industry": meta.get("industry", "未分类"),
            "trade_date": trade_date,
            "pct": pct_val,
            "turnover_rate": turnover,
            "amount_yi": amount_yi,
            "close": close,
            "daily_volume": _num(bar.volume),
            "volume": _num(bar.volume),
            "_is_leader_candidate": is_leader,
        }

        # 龙虎榜标注
        if dt_available:
            candidate["_dragon_tiger_dispersion"] = "上榜"
            candidate["_dragon_tiger_net_amount_yi"] = round(float(dt.get("net_amount_yi", 0)), 4)
        else:
            candidate["_dragon_tiger_dispersion"] = "未上榜"
            candidate["_dragon_tiger_net_amount_yi"] = 0.0

        candidates.append(candidate)

    # ── 次日开盘强度过滤：开盘强度 < -1% 的非龙头候选 ──
    if bar_map is not None and next_td is not None:
        before = sum(1 for c in candidates if c.get("_is_leader_candidate"))
        for c in candidates:
            if not c.get("_is_leader_candidate"):
                continue
            nb = bar_map.get((c["code"], next_td))
            if nb is None:
                c["_is_leader_candidate"] = False
                continue
            op = _num(nb.open)
            if op <= 0:
                op = _num(nb.close)
            # 前收盘使用信号日（trade_date）的 close
            pc = c.get("close", 0)
            if pc > 0:
                strength = (op - pc) / pc * 100
            else:
                strength = 0.0
            if strength < -1.0:
                c["_is_leader_candidate"] = False
        after = sum(1 for c in candidates if c.get("_is_leader_candidate"))
        _open_filter_stats["before"] += before
        _open_filter_stats["after"] += after

    return candidates


def _manage_positions(
    db: Session,
    active_positions: dict[str, dict[str, Any]],
    trades: list[dict[str, Any]],
    bar_map: dict[tuple[str, str], DailyKbar],
    current_date: str,
    trade_dates_set: set[str],
    trade_dates: list[str],
    current_idx: int,
    stop_loss_stats: dict[str, int],
) -> float:
    """检查并处理所有活跃持仓的止损条件。返回该日止盈止损平仓盈亏总额。"""
    from app.services.chen_xiaoqun_service import apply_stop_loss

    total_pnl = 0.0
    for code, pos in list(active_positions.items()):
        bar = bar_map.get((code, current_date))
        if bar is None:
            continue

        current_price = _num(bar.close)
        days_held = pos.get("day_held", 0) + 1
        pos["day_held"] = days_held

        stop_check = apply_stop_loss(
            trade=pos,
            current_price=current_price,
            entry_price=pos["entry_price"],
            days_held=days_held,
        )

        action = stop_check["action"]
        stop_loss_stats[action] = stop_loss_stats.get(action, 0) + 1

        if action == "stop_loss":
            exit_date = _next_trade_date(trade_dates, current_idx)
            if not exit_date or exit_date not in trade_dates_set:
                continue
            pnl = _close_position(
                db=db, pos=pos, trades=trades, active_positions=active_positions,
                bar_map=bar_map, exit_date=exit_date,
                exit_reason=stop_check["reason"],
            )
            total_pnl += pnl
        elif action == "half":
            # 减半：卖出 half 仓位，剩余继续持有（每个持仓只减半一次）
            pos["position_value"] *= 0.5
            pos["already_halved"] = True
        elif action == "exit":
            exit_date = _next_trade_date(trade_dates, current_idx)
            if not exit_date or exit_date not in trade_dates_set:
                continue
            pnl = _close_position(
                db=db, pos=pos, trades=trades, active_positions=active_positions,
                bar_map=bar_map, exit_date=exit_date,
                exit_reason=stop_check["reason"],
            )
            total_pnl += pnl
    return total_pnl


def _close_position(
    db: Session,
    pos: dict[str, Any],
    trades: list[dict[str, Any]],
    active_positions: dict[str, dict[str, Any]],
    bar_map: dict[tuple[str, str], DailyKbar],
    exit_date: str,
    exit_reason: str,
) -> float:
    """平仓并记录交易。返回盈亏金额。"""
    code = pos["code"]
    entry_price = pos["entry_price"]
    exit_bar = bar_map.get((code, exit_date))
    if exit_bar is None:
        # 找不到退出日数据，用最后已知价
        exit_price = entry_price
    else:
        exit_price = _num(exit_bar.open) if _num(exit_bar.open) > 0 else _num(exit_bar.close)

    gross_return = (exit_price - entry_price) / entry_price * 100.0
    net_return = gross_return - (BUY_COST_RATIO + SELL_COST_RATIO) * 100.0
    pnl = pos.get("position_value", 0) * net_return / 100.0

    trade = {
        "code": code,
        "name": pos.get("name", code),
        "signal_date": pos.get("signal_date", ""),
        "entry_date": pos["entry_date"],
        "exit_date": exit_date,
        "entry_price": round(entry_price, 3),
        "exit_price": round(exit_price, 3),
        "gross_return_pct": round(gross_return, 3),
        "net_return_pct": round(net_return, 3),
        "pnl": round(pnl, 2),
        "holding_days": pos.get("day_held", 1),
        "accurate": net_return > 0,
        "resolved": True,
        "exit_reason": exit_reason,
        "cycle": pos.get("cycle", ""),
        "industry": pos.get("industry", "未分类"),
    }
    trades.append(trade)
    active_positions.pop(code, None)
    return pnl


def _output_results(
    trades: list[dict[str, Any]],
    cycle_distribution: dict[str, int],
    stop_loss_stats: dict[str, int],
    trade_dates: list[str],
    portfolio_values: list[float],
    initial_capital: float,
) -> dict[str, Any]:
    """输出回测结果。"""
    total_trades = len(trades)
    if total_trades == 0:
        print("\n========== 陈小群龙头战法回测结果 ==========")
        print("  无交易产生")
        print("  情绪周期分布:", dict(cycle_distribution))
        return {
            "total_trades": 0,
            "accuracy": 0.0,
            "average_net_return": 0.0,
            "median_net_return": 0.0,
            "profit_loss_ratio": 0.0,
            "max_dd": 0.0,
            "cumulative_return": 0.0,
            "open_filter_before": _open_filter_stats.get("before", 0),
            "open_filter_after": _open_filter_stats.get("after", 0),
        }

    from app.services.evaluation_metrics import compute_full_metrics

    metrics = compute_full_metrics(trades)
    accurate_count = sum(1 for t in trades if t.get("accurate", False))
    accuracy = accurate_count / total_trades * 100

    # 按情绪周期拆分
    by_cycle: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_cycle[t.get("cycle", "未知")].append(t)

    # 按止损类型统计
    total_stop_triggers = sum(1 for t in trades if not t.get("accurate", False))
    stop_loss_triggered = sum(1 for t in trades if "止损" in t.get("exit_reason", "")
                              or "跌" in t.get("exit_reason", "")
                              or "放量" in t.get("exit_reason", ""))

    # 从净值序列计算组合级指标
    cumulative_return = (portfolio_values[-1] - 1) * 100.0 if portfolio_values else 0.0

    peak = 1.0
    max_dd = 0.0
    for v in portfolio_values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100.0
            max_dd = max(max_dd, dd)

    print("\n========== 陈小群龙头战法回测结果 ==========")
    print(f"  回测区间: {trade_dates[0]} ~ {trade_dates[-1]}")
    print(f"  交易日数: {len(trade_dates)}")
    print(f"  总交易次数: {total_trades}")
    print(f"  胜率: {accuracy:.1f}%")
    print(f"  平均净收益: {metrics.get('average_net_return', 0):.2f}%")
    print(f"  中位数净收益: {metrics.get('median_net_return', 0):.2f}%")
    print(f"  盈亏比: {metrics.get('profit_loss_ratio', 0):.2f}")
    print(f"  最大回撤: {max_dd:.2f}%")
    print(f"  累计收益: {cumulative_return:.2f}%")
    print(f"  期末权益: ¥{initial_capital * portfolio_values[-1]:,.0f}")
    print(f"  样本置信度: {metrics.get('sample_confidence', 'low')}")

    print("\n--- 情绪周期分布 ---")
    for phase, count in sorted(cycle_distribution.items()):
        pct = count / max(1, len(trade_dates)) * 100
        print(f"  {phase}: {count} 天 ({pct:.1f}%)")

    print("\n--- 分周期表现 ---")
    for phase in ["启动", "发酵", "高潮", "退潮"]:
        phase_trades = by_cycle.get(phase, [])
        if not phase_trades:
            continue
        phase_accurate = sum(1 for t in phase_trades if t.get("accurate", False))
        phase_avg = sum(t.get("net_return_pct", 0) for t in phase_trades) / len(phase_trades)
        print(f"  {phase}: {len(phase_trades)} 笔, 胜率 {phase_accurate/max(1,len(phase_trades))*100:.1f}%, 平均收益 {phase_avg:.2f}%")

    print("\n--- 止损触发统计 ---")
    for action, count in sorted(stop_loss_stats.items()):
        print(f"  {action}: {count} 次")

    print("\n--- 开盘强度过滤 ---")
    print(f"  开盘过滤：前{_open_filter_stats['before']} 后{_open_filter_stats['after']}")

    print(f"\n--- 最近 10 笔交易 ---")
    for t in trades[-10:]:
        print(f"  {t['entry_date']} {t['code']} {t.get('name','')}: 持仓{t['holding_days']}天 "
              f"收益{t['net_return_pct']:.2f}% {'✓' if t['accurate'] else '✗'} [{t.get('exit_reason','')}]")

    return {
        "total_trades": total_trades,
        "accuracy": accuracy,
        "average_net_return": metrics.get("average_net_return", 0),
        "median_net_return": metrics.get("median_net_return", 0),
        "profit_loss_ratio": metrics.get("profit_loss_ratio", 0),
        "max_dd": max_dd,
        "cumulative_return": cumulative_return,
        "open_filter_before": _open_filter_stats.get("before", 0),
        "open_filter_after": _open_filter_stats.get("after", 0),
    }


def _next_trade_date(trade_dates: list[str], current_idx: int) -> str | None:
    """获取下一个交易日。"""
    if current_idx + 1 < len(trade_dates):
        return trade_dates[current_idx + 1]
    return None


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return float(default)


if __name__ == "__main__":
    main()
