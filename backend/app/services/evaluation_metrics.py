"""
统一评估指标框架（9-KPI 体系）。

从 overnight_holding_service 提取的核心指标计算逻辑，供所有策略共享。
为 backtest_service / outcome_service 提供标准化的指标计算入口。

指标定义：
  recommendation_accuracy — 扣费后净收益 > 0 的推荐数 / 总推荐数
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
from typing import Any, Mapping, Protocol

from app.engine.execution_core import (
    CanonicalExecutionError,
    IntradayExitEvidence,
    IntradayFirstHit,
)
from app.services.failure_attribution import classify_false_positive
from app.services.quality_outcome import OUTCOME_CALCULATION_SCHEMA_VERSION

# ── 常量 ───────────────────────────────────────────────────────────────────

MIN_SIGNAL_SAMPLE = 30
"""低于此样本量标记为 low confidence。"""

DEFAULT_BENCHMARK_NAME = "沪深300"
"""默认基准指数名称。"""

MAX_PORTFOLIO_TRADES_FOR_SEQUENTIAL = 200  # 已弃用：组合口径不再按笔数分叉，保留常量仅为兼容旧 import
PORTFOLIO_CALIBRE_DAILY = "daily_equal_weight"
PORTFOLIO_CALIBRE_SEQUENTIAL = "sequential_trades"
PORTFOLIO_CALIBRE_LABELS = {
    PORTFOLIO_CALIBRE_DAILY: "同日推荐等权成当日组合，按日复利（max_drawdown / strategy_compound_return / benchmark_* 同口径）",
    PORTFOLIO_CALIBRE_SEQUENTIAL: "逐笔顺序复利（仅在所有交易都缺 trade_date、无法按日分组时使用）",
}
"""超过此笔数时，顺序投资组合计算不再有意义（回测序列过长），改用等权分组法。"""

ACCURACY_REQUIRED_CONDITIONS = (
    "signal_valid_at_recommend_time",
    "executed_trade",
    "net_return_after_cost_gt_0",
)
"""推荐准确率的三道硬条件。"""


# ── TradeRecord 协议 ──────────────────────────────────────────────────────


class TradeRecord(Protocol):
    """单笔交易的最小协议。

    任何包含以下字段的 dict 或对象均可传入 compute_full_metrics。
    缺失的字段将使用安全默认值（通常是 0 或 False）。
    """
    def get(self, key: str, default: Any = None) -> Any: ...


class ExitAmbiguityContractError(ValueError):
    """新口径 outcome 声明 canonical evidence，但证据结构不合法。"""


def compute_exit_ambiguity_statistics(
    records: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """统计 canonical closed outcome 的同根止盈/止损二义性。

    旧 schema、未结算、missed/provisional 和 non-canonical 记录不会进入分母；
    当前 schema 一旦声明 canonical evidence，就必须提供完整 typed payload。
    """
    dimensions = {
        "strategy": defaultdict(list),
        "month": defaultdict(list),
        "evaluation_scope": defaultdict(list),
    }
    overall: list[IntradayExitEvidence] = []
    for record in records:
        evidence = _eligible_exit_evidence(record)
        if evidence is None:
            continue
        overall.append(evidence)
        strategy_values = record.get("strategy_ids")
        if isinstance(strategy_values, (list, tuple, set, frozenset)):
            strategies = sorted(
                {str(item).strip() for item in strategy_values if str(item).strip()}
            ) or ["unknown"]
        else:
            strategies = [
                str(record.get("strategy_id") or record.get("skill_id") or "unknown")
            ]
        for strategy in strategies:
            dimensions["strategy"][strategy].append(evidence)
        trade_date = str(
            record.get("exit_date")
            or record.get("trade_date")
            or record.get("signal_date")
            or ""
        )
        month = trade_date[:7] if len(trade_date) >= 7 else "unknown"
        evaluation_scope = str(record.get("evaluation_scope") or "unknown")
        dimensions["month"][month].append(evidence)
        dimensions["evaluation_scope"][evaluation_scope].append(evidence)
    return {
        "schema_version": OUTCOME_CALCULATION_SCHEMA_VERSION,
        "overall": _ambiguity_summary(overall),
        "by_strategy": {
            key: _ambiguity_summary(value)
            for key, value in sorted(dimensions["strategy"].items())
        },
        "by_month": {
            key: _ambiguity_summary(value)
            for key, value in sorted(dimensions["month"].items())
        },
        "by_evaluation_scope": {
            key: _ambiguity_summary(value)
            for key, value in sorted(dimensions["evaluation_scope"].items())
        },
    }


def _eligible_exit_evidence(
    record: Mapping[str, Any],
) -> IntradayExitEvidence | None:
    try:
        schema_version = int(record.get("calculation_schema_version") or 0)
    except (TypeError, ValueError):
        return None
    if schema_version != OUTCOME_CALCULATION_SCHEMA_VERSION:
        return None
    label = str(record.get("outcome_label") or record.get("label") or "")
    if label not in {"success", "failed"}:
        return None
    if record.get("canonical_exit_evidence") is not True:
        return None
    payload = record.get("intraday_exit_evidence")
    if not isinstance(payload, Mapping):
        raise ExitAmbiguityContractError(
            "canonical schema outcome requires intraday_exit_evidence mapping"
        )
    required = {
        "first_hit",
        "same_bar_stop_take_profit_hit",
        "applied_priority",
    }
    if set(payload) != required:
        raise ExitAmbiguityContractError(
            "intraday_exit_evidence fields do not match the canonical contract"
        )
    try:
        return IntradayExitEvidence(
            first_hit=payload["first_hit"],
            same_bar_stop_take_profit_hit=payload["same_bar_stop_take_profit_hit"],
            applied_priority=payload["applied_priority"],
        )
    except CanonicalExecutionError as exc:
        raise ExitAmbiguityContractError(
            "malformed canonical intraday_exit_evidence"
        ) from exc


def _ambiguity_summary(evidence: list[IntradayExitEvidence]) -> dict[str, Any]:
    ambiguous_count = sum(
        item.first_hit is IntradayFirstHit.AMBIGUOUS for item in evidence
    )
    eligible_count = len(evidence)
    return {
        "eligible_count": eligible_count,
        "ambiguous_count": ambiguous_count,
        "ambiguity_rate": (
            round(ambiguous_count / eligible_count, 6) if eligible_count else None
        ),
    }


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
    chronological = _sort_trades_chronologically(resolved)

    # ── 基础字段 ──
    executed = [item for item in chronological if _is_executed_record(item)]
    returns = [float(item.get("net_return_pct", 0)) for item in chronological]
    executed_returns = [float(item.get("net_return_pct", 0)) for item in executed]
    gross_returns = [float(item.get("gross_return_pct", 0)) for item in chronological]
    achievable_returns = [float(item.get("achievable_return_pct", 0) or item.get("gross_return_pct", 0)) for item in chronological]
    trade_drawdowns = [float(item.get("morning_drawdown_pct", 0)) for item in chronological]
    holding = [float(item.get("holding_days", 1)) for item in chronological]
    benchmark_returns = [float(item.get("benchmark_return_pct", 0)) for item in chronological]
    accurate_count = sum(
        1
        for item in chronological
        if _is_accurate_record(item)
    )
    trade_count = len(resolved)
    executed_count = len(executed)
    missed_count = max(0, trade_count - executed_count)

    # ── 收益统计 ──
    avg_return = round(sum(returns) / trade_count, 3) if trade_count else 0.0
    median_return = round(statistics.median(returns), 3) if returns else 0.0
    avg_executed_return = round(sum(executed_returns) / executed_count, 3) if executed_count else 0.0

    # ── 盈亏分离 ──
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    flat = [r for r in returns if r == 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    profit_loss_ratio = round(avg_win / abs(avg_loss), 3) if wins and losses else None
    profit_factor = round(
        sum(wins) / max(abs(sum(losses)), 0.001), 3
    ) if wins else 0.0

    # ── 月度/回撤/基准 ──
    month_ratio = _positive_month_ratio(chronological)

    # 组合口径统一：无论样本多少，一律「同一交易日的推荐等权成当日组合 → 按日复利」。
    # 旧实现 ≤200 笔走逐笔顺序复利、>200 笔走按日分组，同一策略跨过 200 笔门槛时回撤/累计收益/超额收益
    # 口径会突变（2026-10-25 审计）。仅当所有交易都缺 trade_date（无法分组）时才退回逐笔顺序并如实标注。
    daily_returns, daily_bench_returns = _grouped_portfolio_returns(chronological)
    if daily_returns:
        portfolio_calibre = PORTFOLIO_CALIBRE_DAILY
        portfolio_dd = _equity_max_drawdown(daily_returns)
        strategy_cum = _compound_return(daily_returns)
        benchmark_cum = _compound_return(daily_bench_returns) if daily_bench_returns else 0.0
    else:
        portfolio_calibre = PORTFOLIO_CALIBRE_SEQUENTIAL
        portfolio_dd = _equity_max_drawdown(returns)
        strategy_cum = _compound_return(returns)
        benchmark_cum = _compound_return(benchmark_returns)

    # ── 置信度 ──
    accuracy = round(accurate_count / trade_count, 3) if trade_count else 0.0
    trade_accuracy = round(accurate_count / executed_count, 3) if executed_count else 0.0
    confidence = _sample_confidence(trade_count)
    accuracy_policy = _accuracy_policy_audit(chronological)

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
        "triggered_count": executed_count,
        "executed_trade_count": executed_count,
        "unexecuted_count": missed_count,
        "missed_count": missed_count,
        "labeled_count": trade_count,
        "trigger_rate": round(executed_count / trade_count, 3) if trade_count else 0.0,
        "success_count": accurate_count,
        "accuracy_numerator": accurate_count,
        "accuracy_denominator": trade_count,
        "hit_rate": trade_accuracy,
        "trade_accuracy": trade_accuracy,
        "avg_return": avg_return,
        "avg_executed_return": avg_executed_return,
        "average_executed_return": avg_executed_return,
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
        "false_positive_notes": _false_positive_notes(chronological),
        "false_positive_category_counts": _false_positive_category_counts(chronological),
        "accuracy_policy": accuracy_policy,
        "accuracy_gate_summary": accuracy_policy["gate_counts"],
        "low_confidence": confidence == "low",
        "accuracy_basis": "labeled_recommendations",
        "flat_count": len(flat),
        "portfolio_calibre": portfolio_calibre,
        "portfolio_calibre_label": PORTFOLIO_CALIBRE_LABELS[portfolio_calibre],
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
        "executed_trade_count": 0,
        "unexecuted_count": 0,
        "missed_count": 0,
        "labeled_count": 0,
        "trigger_rate": 0.0,
        "success_count": 0,
        "accuracy_numerator": 0,
        "accuracy_denominator": 0,
        "hit_rate": 0.0,
        "trade_accuracy": 0.0,
        "avg_return": 0.0,
        "avg_executed_return": 0.0,
        "average_executed_return": 0.0,
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
        "false_positive_category_counts": [],
        "accuracy_policy": _empty_accuracy_policy(),
        "accuracy_gate_summary": _empty_accuracy_policy()["gate_counts"],
        "low_confidence": True,
        "accuracy_basis": "labeled_recommendations",
        "flat_count": 0,
        "portfolio_calibre": PORTFOLIO_CALIBRE_DAILY,
        "portfolio_calibre_label": PORTFOLIO_CALIBRE_LABELS[PORTFOLIO_CALIBRE_DAILY],
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


def _sort_trades_chronologically(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for _index, item in sorted(
            enumerate(trades),
            key=lambda pair: (
                _date_sort_value(pair[1]),
                str(pair[1].get("entry_date") or ""),
                str(pair[1].get("recommendation_id") or pair[1].get("code") or ""),
                pair[0],
            ),
        )
    ]


def _date_sort_value(item: dict[str, Any]) -> str:
    value = str(item.get("trade_date") or item.get("signal_date") or item.get("entry_date") or "")
    return value if value else "9999-99-99"


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

    sorted_dates = sorted(by_date)
    daily_returns = [sum(by_date[date]) / len(by_date[date]) for date in sorted_dates]
    daily_bench_returns = [sum(bench_by_date[date]) / len(bench_by_date[date]) for date in sorted_dates]
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
    attribution_map: dict[str, dict[str, str]] = {}
    for item in trades:
        if _is_accurate_record(item):
            continue
        reason = _false_positive_reason_for_item(item)
        attribution_map[reason] = classify_false_positive(
            reason=reason,
            executed=_is_executed_record(item),
            outcome_label=item.get("outcome_label"),
            exit_reason=item.get("exit_reason"),
            user_label=item.get("user_label"),
            net_return_pct=float(item.get("net_return_pct", 0)),
        )
        counter[reason] += 1
        if len(example_map[reason]) < 3:
            date = item.get("trade_date", "?")
            code = item.get("code", "?")
            ret = item.get("net_return_pct", 0)
            example_map[reason].append(f"{date} {code} {ret:.2f}%")
    return [
        {
            "reason": reason,
            "count": count,
            "examples": example_map.get(reason, []),
            **attribution_map.get(reason, classify_false_positive(reason=reason)),
        }
        for reason, count in counter.most_common(5)
    ]


def _false_positive_category_counts(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str, str]] = Counter()
    for item in trades:
        if _is_accurate_record(item):
            continue
        reason = _false_positive_reason_for_item(item)
        attribution = classify_false_positive(
            reason=reason,
            executed=_is_executed_record(item),
            outcome_label=item.get("outcome_label"),
            exit_reason=item.get("exit_reason"),
            user_label=item.get("user_label"),
            net_return_pct=float(item.get("net_return_pct", 0)),
        )
        counter[(attribution["category"], attribution["category_label"], attribution["stage"])] += 1
    return [
        {
            "category": category,
            "category_label": label,
            "stage": stage,
            "count": count,
        }
        for (category, label, stage), count in counter.most_common(5)
    ]


def _accuracy_policy_audit(trades: list[dict[str, Any]]) -> dict[str, Any]:
    trade_count = len(trades)
    if not trade_count:
        return _empty_accuracy_policy()

    signal_valid_records = [item for item in trades if _is_valid_signal_record(item)]
    signal_valid_count = len(signal_valid_records)
    signal_invalid_count = trade_count - signal_valid_count

    executable_records = [item for item in signal_valid_records if _is_executed_record(item)]
    executable_count = len(executable_records)
    unexecutable_count = signal_valid_count - executable_count

    net_positive_records = [
        item for item in executable_records if float(item.get("net_return_pct", 0)) > 0
    ]
    net_positive_count = len(net_positive_records)
    net_non_positive_count = executable_count - net_positive_count

    accurate_count = sum(1 for item in trades if _is_accurate_record(item))
    legacy_label_mismatch_count = sum(
        1
        for item in trades
        if "accurate" in item and _boolish(item.get("accurate"), False) != _is_accurate_record(item)
    )

    failure_counter: Counter[tuple[str, str]] = Counter()
    for item in trades:
        if _is_accurate_record(item):
            continue
        gate, label = _accuracy_failure_gate(item)
        failure_counter[(gate, label)] += 1

    return {
        "definition": "recommendation_accuracy = accurate_recommendations / total_recommendations",
        "required_conditions": list(ACCURACY_REQUIRED_CONDITIONS),
        "denominator": trade_count,
        "accurate_count": accurate_count,
        "strict_three_gate_accurate_count": net_positive_count,
        "reported_accuracy": round(accurate_count / trade_count, 3),
        "strict_three_gate_accuracy": round(net_positive_count / trade_count, 3),
        "matches_reported_accuracy": accurate_count == net_positive_count,
        "sample_confidence": _sample_confidence(trade_count),
        "gate_counts": {
            "total_recommendations": trade_count,
            "signal_valid_count": signal_valid_count,
            "signal_invalid_count": signal_invalid_count,
            "executable_count": executable_count,
            "unexecutable_count": unexecutable_count,
            "net_positive_count": net_positive_count,
            "net_non_positive_count": net_non_positive_count,
            "manual_override_count": legacy_label_mismatch_count,
            "legacy_accuracy_label_mismatch_count": legacy_label_mismatch_count,
        },
        "funnel": [
            _funnel_row("total_recommendations", "总推荐", trade_count, 0, trade_count),
            _funnel_row("signal_valid", "信号当时真实成立", signal_valid_count, signal_invalid_count, trade_count),
            _funnel_row("executed_trade", "可真实成交", executable_count, unexecutable_count, signal_valid_count),
            _funnel_row("net_return_positive", "扣费滑点后净收益 > 0", net_positive_count, net_non_positive_count, executable_count),
            _funnel_row("accurate_recommendations", "准确推荐", accurate_count, trade_count - accurate_count, trade_count),
        ],
        "failure_breakdown": [
            {"gate": gate, "label": label, "count": count}
            for (gate, label), count in failure_counter.most_common()
        ],
    }


def _empty_accuracy_policy() -> dict[str, Any]:
    return {
        "definition": "recommendation_accuracy = accurate_recommendations / total_recommendations",
        "required_conditions": list(ACCURACY_REQUIRED_CONDITIONS),
        "denominator": 0,
        "accurate_count": 0,
        "strict_three_gate_accurate_count": 0,
        "reported_accuracy": 0.0,
        "strict_three_gate_accuracy": 0.0,
        "matches_reported_accuracy": True,
        "sample_confidence": "low",
        "gate_counts": {
            "total_recommendations": 0,
            "signal_valid_count": 0,
            "signal_invalid_count": 0,
            "executable_count": 0,
            "unexecutable_count": 0,
            "net_positive_count": 0,
            "net_non_positive_count": 0,
            "manual_override_count": 0,
            "legacy_accuracy_label_mismatch_count": 0,
        },
        "funnel": [
            _funnel_row("total_recommendations", "总推荐", 0, 0, 0),
            _funnel_row("signal_valid", "信号当时真实成立", 0, 0, 0),
            _funnel_row("executed_trade", "可真实成交", 0, 0, 0),
            _funnel_row("net_return_positive", "扣费滑点后净收益 > 0", 0, 0, 0),
            _funnel_row("accurate_recommendations", "准确推荐", 0, 0, 0),
        ],
        "failure_breakdown": [],
    }


def _funnel_row(stage: str, label: str, count: int, failed_count: int, denominator: int) -> dict[str, Any]:
    return {
        "stage": stage,
        "label": label,
        "count": count,
        "failed_count": failed_count,
        "pass_rate": round(count / denominator, 3) if denominator else 0.0,
    }


def _accuracy_failure_gate(item: dict[str, Any]) -> tuple[str, str]:
    if not _is_valid_signal_record(item):
        return "signal_valid", "信号当时真实不成立"
    if not _is_executed_record(item):
        return "executed_trade", "未触发买点或不可真实成交"
    if float(item.get("net_return_pct", 0)) <= 0:
        return "net_return_positive", "成本后净收益不为正"
    return "explicit_accuracy_label", "显式准确标记为否"


def _is_executed_record(item: dict[str, Any]) -> bool:
    return _boolish(item.get("executed", item.get("entry_triggered", True)), True)


def _is_valid_signal_record(item: dict[str, Any]) -> bool:
    for key in (
        "signal_valid",
        "signal_true_at_time",
        "signal_valid_at_recommend_time",
        "setup_valid",
    ):
        if key in item:
            return _boolish(item.get(key), True)
    return True


def _is_accurate_record(item: dict[str, Any]) -> bool:
    # ``net_return_pct`` is already post-fee by the TradeRecord contract.
    return (
        _is_valid_signal_record(item)
        and _is_executed_record(item)
        and float(item.get("net_return_pct", 0)) > 0
    )


def _false_positive_reason_for_item(item: dict[str, Any]) -> str:
    explicit = str(item.get("false_positive_reason") or "").strip()
    if explicit:
        return explicit
    if not _is_valid_signal_record(item):
        return "信号当时真实不成立"
    if not _is_executed_record(item):
        return "未触发买点或不可真实成交"
    return "成本后净收益不为正"


def _boolish(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"false", "0", "no", "n", "off"}:
        return False
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    return bool(value)


def _sample_confidence(trade_count: int) -> str:
    return "low" if trade_count < MIN_SIGNAL_SAMPLE else "normal"
