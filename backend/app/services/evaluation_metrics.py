"""
统一评估指标框架（9-KPI 体系）。

从 overnight_holding_service 提取的核心指标计算逻辑，供所有策略共享。
为 backtest_service / outcome_service 提供标准化的指标计算入口。

指标定义：
  recommendation_accuracy — 净收益 > 0 的推荐数 / 总推荐数
  average_net_return     — 平均每笔净收益（扣除滑点/费用）
  median_net_return      — 中位数净收益
  profit_loss_ratio      — 平均盈利 / 平均亏损的绝对值
  max_drawdown           — 组合净值曲线的最大回撤
  positive_month_ratio   — 月度收益 > 0 的月份占比
  benchmark_excess_return — 策略累计收益 - 基准累计收益
  sample_confidence      — "low" (< 30 笔) / "normal"
  false_positive_notes   — 误判原因统计分析

使用方式：
  1. 将交易记录转换为 TradeRecord 格式
  2. 调用 compute_full_metrics(trades) 获取 9-KPI
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any, Protocol

# ── 常量 ───────────────────────────────────────────────────────────────────

MIN_SIGNAL_SAMPLE = 30
"""低于此样本量标记为 low confidence。"""

DEFAULT_BENCHMARK_NAME = "沪深300"
"""默认基准指数名称。"""

MAX_PORTFOLIO_TRADES_FOR_SEQUENTIAL = 200
"""超过此笔数时，顺序投资组合计算不再有意义（回测序列过长），改用等权分组法。"""


# ── TradeRecord 协议 ──────────────────────────────────────────────────────


class TradeRecord(Protocol):
    """单笔交易的最小协议。

    任何包含以下字段的 dict 或对象均可传入 compute_full_metrics。
    缺失的字段将使用安全默认值（通常是 0 或 False）。
    """
    def get(self, key: str, default: Any = None) -> Any: ...


# ── 核心指标函数 ──────────────────────────────────────────────────────────


def compute_full_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """从交易记录列表计算完整 9-KPI 指标。

    Parameters
    ----------
    trades : list[dict]
        每笔交易需包含以下字段（缺省用默认值）：
        - resolved: bool  是否已结算（默认 True）
        - net_return_pct: float  扣费后净收益
        - gross_return_pct: float  毛收益
        - achievable_return_pct: float  可实现收益
        - morning_drawdown_pct: float  盘中最大回撤
        - holding_days: float  持仓天数
        - benchmark_return_pct: float  基准同期收益
        - accurate: bool  是否准确（净收益 > 0）
        - trade_date: str  交易日期 YYYY-MM-DD
        - code: str  股票代码
        - false_positive_reason: str | None
    """
    resolved = [item for item in trades if item.get("resolved", True)]

    if not resolved:
        return _empty_metrics()

    # ── 基础字段 ──
    returns = [float(item.get("net_return_pct", 0)) for item in resolved]
    gross_returns = [float(item.get("gross_return_pct", 0)) for item in resolved]
    achievable_returns = [float(item.get("achievable_return_pct", 0) or item.get("gross_return_pct", 0)) for item in resolved]
    trade_drawdowns = [float(item.get("morning_drawdown_pct", 0)) for item in resolved]
    holding = [float(item.get("holding_days", 1)) for item in resolved]
    benchmark_returns = [float(item.get("benchmark_return_pct", 0)) for item in resolved]
    accurate_count = sum(1 for item in resolved if item.get("accurate", False))
    trade_count = len(resolved)

    # ── 收益统计 ──
    avg_return = round(sum(returns) / trade_count, 3) if trade_count else 0.0
    median_return = round(statistics.median(returns), 3) if returns else 0.0

    # ── 盈亏分离 ──
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    profit_loss_ratio = round(avg_win / max(abs(avg_loss), 0.001), 3) if wins else 0.0
    profit_factor = round(
        sum(wins) / max(abs(sum(losses)), 0.001), 3
    ) if wins else 0.0

    # ── 月度/回撤/基准 ──
    month_ratio = _positive_month_ratio(resolved)

    # 大量交易（>200笔）按日分组，等权计算组合净值曲线，避免天文数字
    if trade_count > MAX_PORTFOLIO_TRADES_FOR_SEQUENTIAL:
        daily_returns, daily_bench_returns = _grouped_portfolio_returns(resolved)
        portfolio_dd = _equity_max_drawdown(daily_returns)
        strategy_cum = _compound_return(daily_returns)
        benchmark_cum = _compound_return(daily_bench_returns) if daily_bench_returns else 0.0
    else:
        portfolio_dd = _equity_max_drawdown(returns)
        strategy_cum = _compound_return(returns)
        benchmark_cum = _compound_return(benchmark_returns)

    # ── 置信度 ──
    accuracy = round(accurate_count / trade_count, 3) if trade_count else 0.0
    confidence = _sample_confidence(trade_count)

    return {
        # 9-KPI 标准字段
        "trade_count": trade_count,
        "recommendation_accuracy": accuracy,
        "average_net_return": avg_return,
        "median_net_return": median_return,
        "profit_loss_ratio": profit_loss_ratio,
        "max_drawdown": round(portfolio_dd, 3),
        "positive_month_ratio": month_ratio,
        "benchmark_excess_return": round(strategy_cum - benchmark_cum, 3),
        "sample_confidence": confidence,

        # 兼容字段（向下兼容已有消费方）
        "total_recommendations": trade_count,
        "triggered_count": trade_count,
        "labeled_count": trade_count,
        "trigger_rate": round(1.0 if trade_count else 0.0, 3),
        "success_count": accurate_count,
        "hit_rate": accuracy,
        "avg_return": avg_return,
        "avg_executed_return": avg_return,
        "avg_weighted_return": avg_return,
        "avg_close_return": round(sum(gross_returns) / trade_count, 3) if trade_count else 0.0,
        "avg_max_return": round(sum(achievable_returns) / trade_count, 3) if trade_count else 0.0,
        "avg_achievable_return": round(sum(achievable_returns) / trade_count, 3) if trade_count else 0.0,
        "avg_max_drawdown": round(sum(trade_drawdowns) / trade_count, 3) if trade_count else 0.0,
        "avg_holding_days": round(sum(holding) / trade_count, 3) if trade_count else 0.0,
        "trade_positive_count": len(wins),
        "trade_negative_count": len(losses),
        "profit_factor": profit_factor,
        "win_loss_ratio": round(len(wins) / max(len(losses), 1), 3) if wins else 0.0,
        "strategy_compound_return": round(strategy_cum, 3),
        "benchmark_compound_return": round(benchmark_cum, 3),
        "false_positive_notes": _false_positive_notes(resolved),
        "low_confidence": confidence == "low",
    }


def _empty_metrics() -> dict[str, Any]:
    return {
        "trade_count": 0,
        "recommendation_accuracy": 0.0,
        "average_net_return": 0.0,
        "median_net_return": 0.0,
        "profit_loss_ratio": 0.0,
        "max_drawdown": 0.0,
        "positive_month_ratio": 0.0,
        "benchmark_excess_return": 0.0,
        "sample_confidence": "low",
        "total_recommendations": 0,
        "triggered_count": 0,
        "labeled_count": 0,
        "trigger_rate": 0.0,
        "success_count": 0,
        "hit_rate": 0.0,
        "avg_return": 0.0,
        "avg_executed_return": 0.0,
        "avg_weighted_return": 0.0,
        "avg_close_return": 0.0,
        "avg_max_return": 0.0,
        "avg_achievable_return": 0.0,
        "avg_max_drawdown": 0.0,
        "avg_holding_days": 0.0,
        "trade_positive_count": 0,
        "trade_negative_count": 0,
        "profit_factor": 0.0,
        "win_loss_ratio": 0.0,
        "strategy_compound_return": 0.0,
        "benchmark_compound_return": 0.0,
        "false_positive_notes": [],
        "low_confidence": True,
    }


# ── 辅助函数 ──────────────────────────────────────────────────────────────


def _positive_month_ratio(trades: list[dict[str, Any]]) -> float:
    """统计月度正向收益占比。"""
    by_month: dict[str, float] = defaultdict(float)
    for item in trades:
        month = str(item.get("trade_date") or "")[:7]
        if month:
            by_month[month] += item.get("net_return_pct", 0.0)
    if not by_month:
        return 0.0
    positive = sum(1 for value in by_month.values() if value > 0)
    return round(positive / len(by_month), 3)


def _grouped_portfolio_returns(trades: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    """将大量交易按交易日等权分组，返回每日组合收益率序列。

    适用于大样本回测：同一天的多笔推荐构成当日等权组合，
    按日期顺序排列形成净值曲线，避免 13K 笔交易顺序复利导致的"天文数字"问题。
    """
    by_date: dict[str, list[float]] = defaultdict(list)
    bench_by_date: dict[str, list[float]] = defaultdict(list)
    for item in trades:
        date = str(item.get("trade_date") or "")
        if date:
            by_date[date].append(float(item.get("net_return_pct", 0)))
            bench_by_date[date].append(float(item.get("benchmark_return_pct", 0)))

    daily_returns = [sum(vs) / len(vs) for vs in by_date.values()]
    daily_bench_returns = [sum(vs) / len(vs) for vs in bench_by_date.values()]
    return daily_returns, daily_bench_returns


def _compound_return(returns: list[float]) -> float:
    """计算累计复利收益（%）。"""
    equity = 1.0
    for value in returns:
        equity *= 1 + value / 100.0
    return (equity - 1) * 100.0


def _equity_max_drawdown(returns: list[float]) -> float:
    """计算组合净值曲线的最大回撤（%）。"""
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1 + value / 100.0
        peak = max(peak, equity)
        if peak <= 0:
            continue
        dd = (equity - peak) / peak * 100.0
        max_drawdown = min(max_drawdown, dd)
    return max_drawdown


def _false_positive_notes(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """聚合误判原因统计。"""
    counter: Counter[str] = Counter()
    example_map: dict[str, list[str]] = defaultdict(list)
    for item in trades:
        if item.get("accurate", False):
            continue
        reason = str(item.get("false_positive_reason") or "") or "成本后净收益不为正"
        counter[reason] += 1
        if len(example_map[reason]) < 3:
            date = item.get("trade_date", "?")
            code = item.get("code", "?")
            ret = item.get("net_return_pct", 0)
            example_map[reason].append(f"{date} {code} {ret:.2f}%")
    return [
        {"reason": reason, "count": count, "examples": example_map.get(reason, [])}
        for reason, count in counter.most_common(5)
    ]


def _sample_confidence(trade_count: int) -> str:
    return "low" if trade_count < MIN_SIGNAL_SAMPLE else "normal"
