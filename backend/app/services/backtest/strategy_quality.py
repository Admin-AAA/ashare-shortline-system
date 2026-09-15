"""P0-P4 strategy quality gate and shadow portfolio evaluation.

This module is deliberately independent from SQLAlchemy and production
selection.  It consumes canonical ``Trade`` values plus signal-day evidence,
then returns a deterministic shadow result.  The canonical profile remains
the champion baseline until an OOS/canary release explicitly promotes the
quality policy.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from app.engine.backtest import Trade, canonical_fingerprint
from app.engine.simulation import compute_metrics


QUALITY_SPEC_VERSION = "strategy-quality:v1"
QUALITY_MODE = "shadow"
QUALITY_SCORE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0-69", 0.0, 69.999999),
    ("70-79", 70.0, 79.999999),
    ("80-84", 80.0, 84.999999),
    ("85+", 85.0, 100.0),
)
QUALITY_MIN_EXECUTED_TRADES = 100
QUALITY_MIN_SIGNAL_DAYS = 20


@dataclass(frozen=True, slots=True)
class StrategyQualityConfig:
    strategy_id: str
    min_score: float | None = None
    allowed_regimes: tuple[str, ...] = ()
    require_main_theme: bool = False
    required_setup_keywords: tuple[str, ...] = ()
    max_entry_gap_pct: float | None = 6.0
    single_name_cap: float = 0.35
    strategy_cap: float = 0.60
    gross_exposure_cap: float = 0.80
    industry_cap: float = 0.40
    theme_cap: float = 0.40
    max_positions_per_signal_day: int = 4
    min_executed_trades: int = QUALITY_MIN_EXECUTED_TRADES
    min_signal_days: int = QUALITY_MIN_SIGNAL_DAYS
    enabled: bool = True

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "min_score": self.min_score,
            "allowed_regimes": list(self.allowed_regimes),
            "require_main_theme": self.require_main_theme,
            "required_setup_keywords": list(self.required_setup_keywords),
            "max_entry_gap_pct": self.max_entry_gap_pct,
            "single_name_cap": self.single_name_cap,
            "strategy_cap": self.strategy_cap,
            "gross_exposure_cap": self.gross_exposure_cap,
            "industry_cap": self.industry_cap,
            "theme_cap": self.theme_cap,
            "max_positions_per_signal_day": self.max_positions_per_signal_day,
            "min_executed_trades": self.min_executed_trades,
            "min_signal_days": self.min_signal_days,
            "enabled": self.enabled,
        }


@dataclass(frozen=True, slots=True)
class QualityCostPolicy:
    """User brokerage cost projection for the shadow layer.

    ``account_capital_cny`` is optional because the commission minimum cannot
    be converted to a return percentage without an order notional.  When it is
    absent, the exact percentage rate is still applied and the report marks
    the minimum-commission component as unpriced.
    """

    commission_rate_per_side: float = 0.000085
    stamp_tax_rate_on_exit: float = 0.0005
    minimum_commission_cny: float = 1.0
    account_capital_cny: float | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "commission_rate_per_side": self.commission_rate_per_side,
            "stamp_tax_rate_on_exit": self.stamp_tax_rate_on_exit,
            "minimum_commission_cny": self.minimum_commission_cny,
            "account_capital_cny": self.account_capital_cny,
        }

    def cost_pct(self, position_size: float) -> float:
        if self.account_capital_cny is None:
            return (self.commission_rate_per_side * 2 + self.stamp_tax_rate_on_exit) * 100
        notional = max(0.0, float(self.account_capital_cny) * max(0.0, float(position_size)))
        if notional <= 0:
            return 0.0
        commission = max(
            self.minimum_commission_cny,
            notional * self.commission_rate_per_side,
        ) * 2
        stamp = notional * self.stamp_tax_rate_on_exit
        return (commission + stamp) / notional * 100


DEFAULT_QUALITY_COST_POLICY = QualityCostPolicy()


@dataclass(frozen=True, slots=True)
class StrategyTradeEvidence:
    trade: Trade
    score: float = 0.0
    regime: str = "unknown"
    sentiment_phase: str = "unknown"
    setup: str = ""
    evidence_text: tuple[str, ...] = ()
    is_main_theme: bool = False
    industry: str = ""
    theme: str = ""
    signal_price: float | None = None

    @property
    def industry_group(self) -> str:
        return industry_group(self.industry)

    @property
    def theme_key(self) -> str:
        return self.theme.strip() or self.industry_group or "unknown"

    @property
    def entry_gap_pct(self) -> float | None:
        if self.signal_price is None or self.signal_price <= 0:
            return None
        if self.trade.entry_price is None or self.trade.entry_price <= 0:
            return None
        return (self.trade.entry_price / self.signal_price - 1.0) * 100.0


@dataclass(frozen=True, slots=True)
class QualityGateResult:
    passed: tuple[StrategyTradeEvidence, ...]
    selected: tuple[StrategyTradeEvidence, ...]
    selected_trades: tuple[Trade, ...]
    rejected_reasons: Mapping[str, int]
    portfolio_rejected: int


# The first two are the only current candidates for a future formal gate.
# The third is intentionally kept shadow-only because the observed sample is
# too small to satisfy the release gates.
QUALITY_CONFIGS: Mapping[str, StrategyQualityConfig] = {
    "trend-pullback-continuation": StrategyQualityConfig(
        strategy_id="trend-pullback-continuation",
        min_score=85.0,
        allowed_regimes=("强势", "结构性强势"),
        required_setup_keywords=("缩量回踩", "趋势"),
    ),
    "limit-up-reseal-relay": StrategyQualityConfig(
        strategy_id="limit-up-reseal-relay",
        min_score=85.0,
        allowed_regimes=("震荡",),
        required_setup_keywords=("回封", "涨停"),
    ),
}


def quality_config_for(strategy_id: str) -> StrategyQualityConfig:
    return QUALITY_CONFIGS.get(
        strategy_id,
        StrategyQualityConfig(strategy_id=strategy_id, enabled=False),
    )


def industry_group(industry: str) -> str:
    """Conservative correlation grouping shared with portfolio de-dup logic."""

    value = str(industry or "").strip()
    groups = {
        **{item: "科技硬件" for item in ("元器件", "半导体", "通信设备", "消费电子", "电子制造")},
        **{item: "新能源" for item in ("电池", "新能源车", "汽车零部件", "光伏")},
        **{item: "金融" for item in ("证券", "保险", "银行")},
    }
    return groups.get(value, value)


def gate_rejection_reason(
    evidence: StrategyTradeEvidence,
    config: StrategyQualityConfig,
) -> str | None:
    if not evidence.trade.executed:
        return "not_executed"
    if not config.enabled:
        return "research_only"
    if config.min_score is not None and evidence.score < config.min_score:
        return "score_below_minimum"
    if config.allowed_regimes and evidence.regime not in config.allowed_regimes:
        return "regime_not_allowed"
    if config.require_main_theme and not evidence.is_main_theme:
        return "main_theme_required"
    searchable_setup = " ".join(
        (evidence.setup, *evidence.evidence_text)
    ).lower()
    if config.required_setup_keywords and not any(
        keyword.lower() in searchable_setup
        for keyword in config.required_setup_keywords
    ):
        return "setup_required"
    if (
        evidence.entry_gap_pct is not None
        and config.max_entry_gap_pct is not None
        and evidence.entry_gap_pct > config.max_entry_gap_pct
    ):
        return "entry_gap_too_high"
    return None


def apply_quality_gate(
    evidences: Iterable[StrategyTradeEvidence],
    config: StrategyQualityConfig,
) -> QualityGateResult:
    """Apply P0/P1/P2, then P3 overlap-aware portfolio caps."""

    ordered = tuple(
        sorted(
            evidences,
            key=lambda item: (
                item.trade.entry_date or item.trade.signal_date,
                -item.score,
                item.trade.code,
            ),
        )
    )
    reason_counts: Counter[str] = Counter()
    passed: list[StrategyTradeEvidence] = []
    for item in ordered:
        reason = gate_rejection_reason(item, config)
        if reason is not None:
            reason_counts[reason] += 1
            continue
        passed.append(item)

    active: list[tuple[StrategyTradeEvidence, float]] = []
    selected: list[StrategyTradeEvidence] = []
    selected_trades: list[Trade] = []
    selected_by_signal_date: Counter[str] = Counter()

    for item in passed:
        if not item.trade.executed:
            continue
        entry_date = item.trade.entry_date or item.trade.signal_date
        active = [
            (previous, allocation)
            for previous, allocation in active
            if previous.trade.exit_date is None or previous.trade.exit_date >= entry_date
        ]
        if selected_by_signal_date[item.trade.signal_date] >= config.max_positions_per_signal_day:
            reason_counts["portfolio_daily_position_cap"] += 1
            continue

        def active_sum(predicate: Any) -> float:
            return sum(
                allocation
                for previous, allocation in active
                if predicate(previous)
            )

        current = max(0.0, float(item.trade.position_size))
        remaining = min(
            current,
            max(0.0, config.single_name_cap - active_sum(lambda x: x.trade.code == item.trade.code)),
            max(0.0, config.strategy_cap - active_sum(lambda _x: True)),
            max(0.0, config.gross_exposure_cap - active_sum(lambda _x: True)),
            max(0.0, config.industry_cap - active_sum(lambda x: x.industry_group == item.industry_group)),
            max(0.0, config.theme_cap - active_sum(lambda x: x.theme_key == item.theme_key)),
        )
        if remaining <= 0:
            reason_counts["portfolio_exposure_cap"] += 1
            continue
        normalized_trade = replace(item.trade, position_size=round(remaining, 6))
        normalized_item = replace(item, trade=normalized_trade)
        selected.append(normalized_item)
        selected_trades.append(normalized_trade)
        selected_by_signal_date[item.trade.signal_date] += 1
        active.append((normalized_item, remaining))

    return QualityGateResult(
        passed=tuple(passed),
        selected=tuple(selected),
        selected_trades=tuple(selected_trades),
        rejected_reasons=dict(sorted(reason_counts.items())),
        portfolio_rejected=reason_counts.get("portfolio_exposure_cap", 0)
        + reason_counts.get("portfolio_daily_position_cap", 0),
    )


def compute_quality_profile(
    evidences: Iterable[StrategyTradeEvidence],
    config: StrategyQualityConfig,
    cost_policy: QualityCostPolicy = DEFAULT_QUALITY_COST_POLICY,
) -> dict[str, Any]:
    """Return a complete, JSON-safe P0-P4 shadow report."""

    evidence_tuple = reprice_quality_evidences(evidences, cost_policy)
    return _quality_profile_payload(evidence_tuple, config, cost_policy)


def reprice_quality_evidences(
    evidences: Iterable[StrategyTradeEvidence],
    cost_policy: QualityCostPolicy = DEFAULT_QUALITY_COST_POLICY,
) -> tuple[StrategyTradeEvidence, ...]:
    return tuple(
        replace(
            item,
            trade=_reprice_trade(item.trade, cost_policy),
        )
        for item in evidences
    )


def _quality_profile_payload(
    evidence_tuple: tuple[StrategyTradeEvidence, ...],
    config: StrategyQualityConfig,
    cost_policy: QualityCostPolicy,
) -> dict[str, Any]:
    gate = apply_quality_gate(evidence_tuple, config)
    raw_trades = tuple(item.trade for item in evidence_tuple)
    raw_executed = tuple(item for item in evidence_tuple if item.trade.executed)
    quality_fingerprint = canonical_fingerprint(
        {
            "spec_version": QUALITY_SPEC_VERSION,
            "config": config.canonical_payload(),
            "cost_policy": cost_policy.canonical_payload(),
        }
    )
    return {
        "spec_version": QUALITY_SPEC_VERSION,
        "mode": QUALITY_MODE,
        "quality_fingerprint": quality_fingerprint,
        "config": config.canonical_payload(),
        "cost_model": {
            **cost_policy.canonical_payload(),
            "round_trip_pct_without_minimum": round(
                (cost_policy.commission_rate_per_side * 2 + cost_policy.stamp_tax_rate_on_exit) * 100,
                6,
            ),
            "minimum_commission_priced": cost_policy.account_capital_cny is not None,
            "note": (
                "已按账户资金和单笔仓位计入单边最低佣金"
                if cost_policy.account_capital_cny is not None
                else "已按万0.85+印花税计入比例成本；最低1元需提供账户资金"
            ),
        },
        # A rolling shadow window is descriptive evidence only.  It may show
        # that a candidate is worth freezing, but it cannot consume an OOS
        # holdout or authorize production by itself.
        "release_ready": False,
        "descriptive_candidate_ready": _release_ready(gate.selected_trades, config),
        "release_blocker": "frozen_oos_and_forward_canary_required",
        "gate": {
            "candidate_count": len(evidence_tuple),
            "passed_count": len(gate.passed),
            "selected_count": len(gate.selected_trades),
            "raw_executed_count": len(raw_executed),
            "selected_signal_days": len({item.trade.signal_date for item in gate.selected}),
            "rejected_reasons": dict(gate.rejected_reasons),
            "portfolio_rejected": gate.portfolio_rejected,
        },
        "raw_metrics": _metrics_payload(raw_trades),
        "gated_metrics": _metrics_payload(gate.selected_trades),
        "regime_metrics": _grouped_metrics(evidence_tuple, lambda item: item.regime),
        "sentiment_metrics": _grouped_metrics(evidence_tuple, lambda item: item.sentiment_phase),
        "score_calibration": _score_calibration(evidence_tuple),
        "stop_gap_diagnostics": _stop_gap_diagnostics(raw_executed),
        "portfolio_caps": {
            "single_name_cap": config.single_name_cap,
            "strategy_cap": config.strategy_cap,
            "gross_exposure_cap": config.gross_exposure_cap,
            "industry_cap": config.industry_cap,
            "theme_cap": config.theme_cap,
            "theme_proxy_rule": "industry_group_when_theme_missing",
        },
    }


def _reprice_trade(trade: Trade, cost_policy: QualityCostPolicy) -> Trade:
    if not trade.executed:
        return trade
    return replace(
        trade,
        net_return_pct=round(
            trade.gross_return_pct - cost_policy.cost_pct(trade.position_size),
            10,
        ),
    )


def _release_ready(trades: tuple[Trade, ...], config: StrategyQualityConfig) -> bool:
    metrics = compute_metrics(trades)
    profit_factor_ready = (
        metrics.profit_loss_ratio > 1.10
        if metrics.profit_loss_ratio is not None
        else metrics.win_count > 0 and metrics.loss_count == 0
    )
    return (
        len(trades) >= config.min_executed_trades
        and len({trade.signal_date for trade in trades}) >= config.min_signal_days
        and bool(trades)
        and metrics.average_net_return_pct > 0
        and profit_factor_ready
    )


def _metrics_payload(trades: Iterable[Trade]) -> dict[str, Any]:
    normalized_trades = tuple(trades)
    metric = compute_metrics(normalized_trades)
    return {
        "sample_count": metric.executed_trade_count,
        "signal_days": len({trade.signal_date for trade in normalized_trades if trade.executed}),
        "win_count": metric.win_count,
        "loss_count": metric.loss_count,
        "hit_rate": round(metric.win_rate_pct / 100, 6) if metric.executed_trade_count else None,
        "avg_return": round(metric.average_net_return_pct, 4) if metric.executed_trade_count else None,
        "profit_loss_ratio": round(metric.profit_loss_ratio, 4) if metric.profit_loss_ratio is not None else None,
        "total_return_pct": round(metric.total_net_return_pct, 4),
        "max_drawdown_pct": round(metric.max_drawdown_pct, 4),
    }


def _grouped_metrics(
    evidences: tuple[StrategyTradeEvidence, ...],
    key_fn: Any,
) -> dict[str, Any]:
    groups: dict[str, list[Trade]] = defaultdict(list)
    for item in evidences:
        groups[str(key_fn(item) or "unknown")].append(item.trade)
    return {key: _metrics_payload(value) for key, value in sorted(groups.items())}


def _score_calibration(evidences: tuple[StrategyTradeEvidence, ...]) -> dict[str, Any]:
    buckets: dict[str, list[Trade]] = {name: [] for name, _low, _high in QUALITY_SCORE_BUCKETS}
    for item in evidences:
        for name, low, high in QUALITY_SCORE_BUCKETS:
            if low <= item.score <= high:
                buckets[name].append(item.trade)
                break
    rows = {
        name: {
            **_metrics_payload(trades),
            "score_low": low,
            "score_high": high,
            "reliable": (
                len([trade for trade in trades if trade.executed]) >= QUALITY_MIN_EXECUTED_TRADES
                and len({trade.signal_date for trade in trades if trade.executed}) >= QUALITY_MIN_SIGNAL_DAYS
            ),
        }
        for (name, low, high), trades in zip(QUALITY_SCORE_BUCKETS, (buckets[item[0]] for item in QUALITY_SCORE_BUCKETS))
    }
    reliable_returns = [
        rows[name]["avg_return"]
        for name, _low, _high in QUALITY_SCORE_BUCKETS
        if rows[name]["reliable"] and rows[name]["avg_return"] is not None
    ]
    return {
        "buckets": rows,
        "reliable_bucket_count": len(reliable_returns),
        "monotonic_avg_return": (
            all(left <= right for left, right in zip(reliable_returns, reliable_returns[1:]))
            if len(reliable_returns) >= 2
            else None
        ),
    }


def _stop_gap_diagnostics(trades: tuple[StrategyTradeEvidence, ...]) -> dict[str, Any]:
    gap_returns = [
        item.trade.net_return_pct
        for item in trades
        if item.trade.executed and str(item.trade.exit_reason or "").startswith("stop_loss_gap")
    ]
    caps = (5.5, 6.5, 7.5)
    sensitivity = {}
    for cap in caps:
        clipped = [max(value, -cap) for value in gap_returns]
        sensitivity[str(cap)] = {
            "observed_loss_count": len(gap_returns),
            "observed_avg_return": round(sum(gap_returns) / len(gap_returns), 4) if gap_returns else None,
            "clipped_avg_return_proxy": round(sum(clipped) / len(clipped), 4) if clipped else None,
            "clipped_total_return_proxy": round(sum(clipped), 4),
            "interpretation": "observed-loss clip proxy; requires bar-level rerun before adoption",
        }
    return {
        "stop_loss_gap_count": len(gap_returns),
        "stop_loss_gap_avg_return": round(sum(gap_returns) / len(gap_returns), 4) if gap_returns else None,
        "observed_loss_clip_sensitivity": sensitivity,
    }


__all__ = [
    "QUALITY_CONFIGS",
    "DEFAULT_QUALITY_COST_POLICY",
    "QUALITY_MODE",
    "QUALITY_SPEC_VERSION",
    "StrategyQualityConfig",
    "StrategyTradeEvidence",
    "QualityGateResult",
    "QualityCostPolicy",
    "apply_quality_gate",
    "compute_quality_profile",
    "gate_rejection_reason",
    "industry_group",
    "quality_config_for",
    "reprice_quality_evidences",
]
