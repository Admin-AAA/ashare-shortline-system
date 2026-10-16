"""单股票全 Skill 历史回测：逐日运行所有 Skill 规则评分，模拟交易，统计 per-skill 表现。"""

from __future__ import annotations

import logging
import time
from bisect import bisect_left
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.skill_loader import StrategySkill, load_skills
from app.engine.strategy_runner import run_strategy
from app.models.tables import DailyKbar, Stock
from app.engine.trading_calendar import normalize_trade_date
from app.services.outcome_service import (
    ENTRY_SLIPPAGE_BPS,
    EXIT_SLIPPAGE_BPS,
    ENTRY_LOOKAHEAD_DAYS,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    MAX_STOP_LOSS_PCT,
    _as_float,
    _loads,
)
from app.engine.board_rules import (
    is_one_word_limit_down_bar,
)
from app.services.execution_constraints import (
    exit_execution_block_reason_from_bar,
)

logger = logging.getLogger(__name__)


def run_stock_backtest(
    db: Session,
    code: str,
    start_date: str,
    end_date: str,
    *,
    horizon_days: int = 5,
    skill_ids: list[str] | None = None,
) -> dict[str, Any]:
    """对单只股票的历史区间运行全部 Skill 回测。

    每个交易日：加载该股 bars → 运行全部 Skill 规则评分 → 模拟入场/出场 → 记录交易。
    最终聚合每个 Skill 的胜率、平均收益、盈亏比。
    """
    t0 = time.perf_counter()
    settings = get_settings()

    # ── 加载 Skill ──
    all_skills = load_skills(settings.skills_dir)
    active_skills = [s for s in all_skills if s.enabled and s.executable]
    if skill_ids:
        id_set = set(skill_ids)
        active_skills = [s for s in active_skills if s.skill_id in id_set]
    if not active_skills:
        return {"status": "error", "message": "无可用的 Skill"}

    # ── 加载股票 ──
    stock = db.scalar(select(Stock).where(Stock.code == code))
    if not stock:
        return {"status": "error", "message": f"股票 {code} 不存在"}

    # ── 加载区间内所有 bars ──
    all_bars = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code == code, DailyKbar.trade_date >= start_date, DailyKbar.trade_date <= end_date)
        .order_by(DailyKbar.trade_date.asc())
    ).all()

    if len(all_bars) < 3:
        return {"status": "error", "message": f"股票 {code} 在 {start_date}~{end_date} 数据不足（{len(all_bars)} 条）"}

    # 需要历史 bars 用于指标计算（取 start_date 前 120 天）
    min_date = str(all_bars[0].trade_date)
    history_bars = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code == code, DailyKbar.trade_date < min_date)
        .order_by(DailyKbar.trade_date.desc())
        .limit(120)
    ).all()
    history_bars = list(reversed(history_bars))  # 升序

    # 合并历史 + 区间 bars
    combined_bars = list(history_bars) + list(all_bars)
    trade_dates = [str(bar.trade_date) for bar in all_bars]

    # ── per-skill 交易记录 ──
    trades_by_skill: dict[str, list[dict[str, Any]]] = defaultdict(list)
    signals_by_skill: dict[str, int] = defaultdict(int)

    for bar in all_bars:
        trade_date = str(bar.trade_date)
        # 构建截至当日的 bars（含历史）
        bar_index_in_combined = bisect_left([str(b.trade_date) for b in combined_bars], trade_date)
        bars_to_date = combined_bars[:bar_index_in_combined + 1]
        if len(bars_to_date) < 10:
            continue

        # 构建 stock dict
        stock_dict = _build_stock_dict_for_date(code, stock, bars_to_date, trade_date)

        # 对每个 Skill 运行规则评分
        for skill in active_skills:
            try:
                signal = run_strategy(skill, stock_dict, [], skip_universe_check=True)
            except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
                logger.debug("run_stock_backtest: best-effort fallback after %r", _ble_exc)
                continue

            score = float(signal.get("score", 0))
            if score <= 0 or signal.get("hard_veto"):
                continue

            signals_by_skill[skill.skill_id] += 1

            # 模拟交易
            future_bars = all_bars[bisect_left(trade_dates, trade_date) + 1:]
            if not future_bars:
                continue

            trade = _simulate_trade(signal, bar, future_bars, horizon_days, code)
            if trade:
                trades_by_skill[skill.skill_id].append(trade)

    # ── 聚合 per-skill 指标 ──
    per_skill_performance = []
    for skill in active_skills:
        trades = trades_by_skill.get(skill.skill_id, [])
        signal_count = signals_by_skill.get(skill.skill_id, 0)
        perf = _compute_skill_performance(skill, signal_count, trades)
        per_skill_performance.append(perf)

    # ── 总体指标 ──
    all_trades = [t for trades in trades_by_skill.values() for t in trades]
    total_wins = sum(1 for t in all_trades if t["net_return"] > 0)
    total_returns = [t["net_return"] for t in all_trades]

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    return {
        "status": "completed",
        "code": code,
        "name": stock.name,
        "start_date": start_date,
        "end_date": end_date,
        "dates_scanned": len(all_bars),
        "horizon_days": horizon_days,
        "per_skill_performance": per_skill_performance,
        "aggregate_metrics": {
            "total_signals": sum(signals_by_skill.values()),
            "total_trades": len(all_trades),
            "overall_win_rate": round(total_wins / max(1, len(all_trades)), 4),
            "overall_avg_return": round(sum(total_returns) / max(1, len(total_returns)), 4),
            "best_skill_id": max(per_skill_performance, key=lambda p: p.get("avg_return", 0)).get("skill_id", ""),
            "worst_skill_id": min(per_skill_performance, key=lambda p: p.get("avg_return", 0)).get("skill_id", ""),
        },
        "elapsed_ms": elapsed_ms,
    }


def _simulate_trade(
    signal: dict[str, Any],
    signal_bar: DailyKbar,
    future_bars: list[DailyKbar],
    horizon_days: int,
    code: str,
) -> dict[str, Any] | None:
    """从 signal dict 模拟一笔交易（入场 → 持仓 → 出场）。"""
    feature_snapshot = signal.get("feature_snapshot") or {}

    # 入场价 = signal_bar 的收盘价 + 滑点
    entry_price = _as_float(signal_bar.close)
    if entry_price <= 0:
        return None
    executed_entry = round(entry_price * (1 + ENTRY_SLIPPAGE_BPS / 10000.0), 4)

    # 止损/止盈
    stop_loss = _resolve_stop(entry_price, feature_snapshot)
    take_profit = _resolve_take_profit(entry_price, feature_snapshot)

    # 持仓 bars
    holding = list(future_bars[:max(1, horizon_days)])
    if not holding:
        return None

    # 出场
    exit_price, exit_date, exit_reason = _resolve_exit(holding, stop_loss, take_profit, code)
    executed_exit = round(exit_price * (1 - EXIT_SLIPPAGE_BPS / 10000.0), 4)
    net_return = round(((executed_exit - executed_entry) / executed_entry) * 100, 2)

    # 除权检测
    if abs(net_return) > 20:
        is_star = code.startswith("68")
        limit_pct = 20 if is_star else 10
        max_daily = max(abs(_as_float(b.pct_chg)) for b in holding) if holding else 0
        if max_daily < limit_pct * 0.6:
            cum = 1.0
            for b in holding:
                cum *= (1 + _as_float(b.pct_chg) / 100.0)
            net_return = round((cum - 1) * 100, 2)

    return {
        "trade_date": str(signal_bar.trade_date),
        "entry_date": str(signal_bar.trade_date),
        "entry_price": round(executed_entry, 2),
        "exit_date": exit_date,
        "exit_price": round(executed_exit, 2),
        "net_return": net_return,
        "exit_reason": exit_reason,
        "max_return": round(max((_as_float(b.high) - entry_price) / entry_price * 100 for b in holding), 2) if holding else 0,
        "max_drawdown": round(min((_as_float(b.low) - entry_price) / entry_price * 100 for b in holding), 2) if holding else 0,
    }


def _resolve_stop(entry_price: float, snapshot: dict[str, Any]) -> float:
    raw = _as_float(snapshot.get("stop_loss"))
    max_stop = entry_price * (1 + DEFAULT_STOP_LOSS_PCT / 100.0)
    if raw <= 0 or raw >= entry_price:
        return round(max_stop, 2)
    min_stop = entry_price * (1 + MAX_STOP_LOSS_PCT / 100.0)
    return round(max(min(raw, max_stop), min_stop), 2)


def _resolve_take_profit(entry_price: float, snapshot: dict[str, Any]) -> float:
    raw = _as_float(snapshot.get("take_profit"))
    fallback = entry_price * (1 + DEFAULT_TAKE_PROFIT_PCT / 100.0)
    if raw <= entry_price:
        return round(fallback, 2)
    return round(min(raw, entry_price * 1.2), 2)


def _resolve_exit(
    bars: list[DailyKbar],
    stop_loss: float,
    take_profit: float,
    code: str,
) -> tuple[float, str, str]:
    for bar in bars:
        if exit_execution_block_reason_from_bar(code, bar):
            continue
        open_p = _as_float(bar.open)
        high = _as_float(bar.high)
        low = _as_float(bar.low)
        if open_p > 0 and low > 0:
            if open_p <= stop_loss:
                return open_p, str(bar.trade_date), "stop_loss_gap"
            if open_p >= take_profit:
                return open_p, str(bar.trade_date), "take_profit_gap"
        if low <= stop_loss:
            return stop_loss, str(bar.trade_date), "stop_loss_hit"
        if high >= take_profit:
            return take_profit, str(bar.trade_date), "take_profit_hit"
    last = bars[-1]
    return _as_float(last.close), str(last.trade_date), "horizon_close"


def _build_stock_dict_for_date(
    code: str, stock: Any, bars: list[DailyKbar], trade_date: str
) -> dict[str, Any]:
    """为指定日期构建 stock dict（用于 run_strategy）。"""
    last_bar = bars[-1]
    bar_dicts = []
    for bar in bars[-60:]:
        bar_dicts.append({
            "trade_date": str(bar.trade_date),
            "open": float(bar.open or 0),
            "high": float(bar.high or 0),
            "low": float(bar.low or 0),
            "close": float(bar.close or 0),
            "volume": float(bar.volume or 0),
            "amount": float(bar.amount or 0),
            "pct_chg": float(bar.pct_chg or 0),
            "turnover_rate": float(bar.turnover_rate or 0),
            "ma5": bar.ma5,
            "ma10": bar.ma10,
            "ma20": bar.ma20,
            "ma60": bar.ma60,
        })
    # 历史 ma 列可能全 NULL；读时现算，与生产一致
    from app.services.indicator_computer import fill_missing_local_indicators
    bar_dicts = fill_missing_local_indicators(bar_dicts)

    from app.services.market_data import _build_stock_from_bars
    quote = {
        "code": code,
        "name": stock.name,
        "industry": stock.industry or "",
        "trade_date": trade_date,
        "price": float(last_bar.close or 0),
        "pct": float(last_bar.pct_chg or 0),
        "volume": float(last_bar.volume or 0),
        "amount_yi": round(float(last_bar.amount or 0) / 1e8, 2),
        "turnover_rate": float(last_bar.turnover_rate or 0),
        "is_st": bool(stock.is_st),
        "exchange": "SH" if code.startswith(("6", "9")) else "SZ",
        "main_net_inflow_yi": 0.0,
    }
    return _build_stock_from_bars(quote, bar_dicts, quote_source="backtest", kline_source="backtest")


def _compute_skill_performance(
    skill: StrategySkill,
    signal_count: int,
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    if not trades:
        return {
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "signal_count": signal_count,
            "triggered_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "trades": [],
        }

    returns = [t["net_return"] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    avg_win = sum(wins) / max(1, len(wins))
    avg_loss = abs(sum(losses) / max(1, len(losses)))

    return {
        "skill_id": skill.skill_id,
        "skill_name": skill.name,
        "signal_count": signal_count,
        "triggered_count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / max(1, len(trades)), 4),
        "avg_return": round(sum(returns) / max(1, len(returns)), 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(-avg_loss, 4),
        "profit_factor": round(avg_win / max(0.01, avg_loss), 4),
        "max_drawdown": round(min(t.get("max_drawdown", 0) for t in trades), 4) if trades else 0.0,
        "trades": trades,
    }
