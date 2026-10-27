"""Canonical, side-effect-free single-trade execution core.

Adapters may translate DTO/ORM objects into these immutable values, but entry,
T+1, exit ordering, gap, trailing, slippage, fee, and result invariants live
only here.  This module imports no ORM, service, configuration, or I/O code.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Mapping

from app.engine.canonical import CanonicalValue, canonical_fingerprint
from app.engine.execution_policy import (
    ExecutionPolicy,
    GapStopFillMode,
    GapTakeProfitFillMode,
    IntradayExitPriority,
    TrailingMode,
)


class CanonicalExecutionError(ValueError):
    """Stable error raised for invalid canonical execution inputs."""


class ExecutionStatus(StrEnum):
    NOT_EXECUTED = "not_executed"
    OPEN_T1_LOCKED = "open_t1_locked"
    CLOSED = "closed"


class IntradayFirstHit(StrEnum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    AMBIGUOUS = "ambiguous"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class IntradayExitEvidence(CanonicalValue):
    first_hit: IntradayFirstHit
    same_bar_stop_take_profit_hit: bool
    applied_priority: IntradayExitPriority | None

    def __post_init__(self) -> None:
        try:
            first_hit = (
                self.first_hit
                if isinstance(self.first_hit, IntradayFirstHit)
                else IntradayFirstHit(str(self.first_hit))
            )
        except ValueError as exc:
            raise CanonicalExecutionError("invalid intraday first_hit") from exc
        if not isinstance(self.same_bar_stop_take_profit_hit, bool):
            raise CanonicalExecutionError(
                "same_bar_stop_take_profit_hit must be boolean"
            )
        priority = self.applied_priority
        if priority is not None:
            try:
                priority = (
                    priority
                    if isinstance(priority, IntradayExitPriority)
                    else IntradayExitPriority(str(priority))
                )
            except ValueError as exc:
                raise CanonicalExecutionError(
                    "invalid applied intraday exit priority"
                ) from exc
        is_ambiguous = first_hit is IntradayFirstHit.AMBIGUOUS
        if self.same_bar_stop_take_profit_hit is not is_ambiguous:
            raise CanonicalExecutionError(
                "same-bar stop/target flag must agree with first_hit"
            )
        if (priority is not None) is not is_ambiguous:
            raise CanonicalExecutionError(
                "applied_priority is required only for ambiguous evidence"
            )
        object.__setattr__(self, "first_hit", first_hit)
        object.__setattr__(self, "applied_priority", priority)


@dataclass(frozen=True, slots=True)
class ExecutionBar(CanonicalValue):
    code: str
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    tradable: bool = True
    sellable: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code:
            raise CanonicalExecutionError("bar code must be non-empty")
        if not isinstance(self.trade_date, str) or not self.trade_date:
            raise CanonicalExecutionError("bar trade_date must be non-empty")
        for name in ("open", "high", "low", "close"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CanonicalExecutionError(f"bar {name} must be numeric")
            object.__setattr__(self, name, float(value))
        if not isinstance(self.tradable, bool) or not isinstance(self.sellable, bool):
            raise CanonicalExecutionError("bar tradable/sellable flags must be boolean")


@dataclass(frozen=True, slots=True)
class ExecutionIntent(CanonicalValue):
    code: str
    signal_date: str
    signal_price: float
    recommendation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code:
            raise CanonicalExecutionError("intent code must be non-empty")
        if not isinstance(self.signal_date, str) or not self.signal_date:
            raise CanonicalExecutionError("intent signal_date must be non-empty")
        if (
            isinstance(self.signal_price, bool)
            or not isinstance(self.signal_price, (int, float))
            or self.signal_price <= 0
        ):
            raise CanonicalExecutionError("intent signal_price must be positive")
        object.__setattr__(self, "signal_price", float(self.signal_price))


@dataclass(frozen=True, slots=True)
class ExecutionRequest(CanonicalValue):
    intent: ExecutionIntent
    bars: tuple[ExecutionBar, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.intent, ExecutionIntent):
            raise CanonicalExecutionError("request intent must be ExecutionIntent")
        if not isinstance(self.bars, tuple) or not all(
            isinstance(bar, ExecutionBar) for bar in self.bars
        ):
            raise CanonicalExecutionError("request bars must be ExecutionBar tuple")


@dataclass(frozen=True, slots=True)
class EntryDecision(CanonicalValue):
    triggered: bool
    bar_index: int | None
    trade_date: str | None
    raw_price: float | None
    executed_price: float | None
    reason: str
    observed_gap_pct: float | None = None


@dataclass(frozen=True, slots=True)
class CausalEntryBar(CanonicalValue):
    code: str
    trade_date: str
    open: float
    low: float
    close: float
    block_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.code or not self.trade_date:
            raise CanonicalExecutionError("entry bar identity must be non-empty")
        for name in ("open", "low", "close"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CanonicalExecutionError(f"entry bar {name} must be numeric")
            object.__setattr__(self, name, float(value))


@dataclass(frozen=True, slots=True)
class CausalEntryConfig(CanonicalValue):
    policy_id: str
    price_source: str
    signal_date: str
    recommendation_price: float
    intraday: bool
    target_prices: tuple[float, ...]
    max_entry_trade_days: int
    horizon_days: int
    allow_target_touch: bool
    fallback_to_open_when_targets_miss: bool
    entry_slippage_bps: float
    max_entry_gap_pct: float | None = None

    def __post_init__(self) -> None:
        if self.price_source not in {
            "open",
            "open_window",
            "intraday_snapshot_or_postclose_target",
        }:
            raise CanonicalExecutionError(
                f"unsupported canonical entry price_source: {self.price_source}"
            )
        if not self.policy_id or not self.signal_date:
            raise CanonicalExecutionError(
                "entry policy_id and signal_date must be non-empty"
            )
        if self.max_entry_trade_days < 1 or self.horizon_days < 1:
            raise CanonicalExecutionError("entry windows must be positive")
        if self.recommendation_price < 0:
            raise CanonicalExecutionError("recommendation_price cannot be negative")
        if self.max_entry_gap_pct is not None and self.max_entry_gap_pct < 0:
            raise CanonicalExecutionError("max_entry_gap_pct cannot be negative")
        targets = tuple(float(value) for value in self.target_prices if value > 0)
        object.__setattr__(self, "target_prices", targets)


@dataclass(frozen=True, slots=True)
class ExitDecision(CanonicalValue):
    trade_date: str
    raw_price: float
    executed_price: float
    reason: str
    holding_days: int
    intraday_exit_evidence: IntradayExitEvidence


@dataclass(frozen=True, slots=True)
class ExecutionResult(CanonicalValue):
    status: ExecutionStatus
    entry: EntryDecision
    exit: ExitDecision | None
    gross_return_pct: float
    fee_pct: float
    net_return_pct: float
    position_size: float
    policy_fingerprint: str
    input_fingerprint: str
    t_plus_one: bool

    def __post_init__(self) -> None:
        status = (
            self.status
            if isinstance(self.status, ExecutionStatus)
            else ExecutionStatus(str(self.status))
        )
        object.__setattr__(self, "status", status)
        if status is ExecutionStatus.NOT_EXECUTED:
            if self.entry.triggered or self.exit is not None:
                raise CanonicalExecutionError(
                    "not-executed result cannot carry execution"
                )
        elif status is ExecutionStatus.OPEN_T1_LOCKED:
            if not self.entry.triggered or self.exit is not None or not self.t_plus_one:
                raise CanonicalExecutionError(
                    "open T+1 result has inconsistent fields"
                )
        else:
            if not self.entry.triggered or self.exit is None:
                raise CanonicalExecutionError(
                    "closed result requires entry and exit"
                )
            if (
                self.t_plus_one
                and self.entry.trade_date is not None
                and self.exit.trade_date <= self.entry.trade_date
            ):
                raise CanonicalExecutionError(
                    "T+1 closed result requires exit_date after entry_date"
                )


@dataclass(frozen=True, slots=True)
class PositionSizingDecision(CanonicalValue):
    requested_size: float
    approved_size: float
    applicable_caps: tuple[tuple[str, float], ...]
    binding_caps: tuple[str, ...]


def compose_position_caps(
    requested_size: float,
    applicable_caps: Mapping[str, float | None],
) -> PositionSizingDecision:
    """Return the minimum of requested size and every applicable cap.

    ``None`` means not applicable. Zero is an explicit reject cap. Invalid,
    non-finite, duplicate-after-normalization, or out-of-range caps fail closed
    instead of being silently skipped by an adapter.
    """
    import math

    if (
        isinstance(requested_size, bool)
        or not isinstance(requested_size, (int, float))
        or not math.isfinite(float(requested_size))
        or not 0 <= float(requested_size) <= 1
    ):
        raise CanonicalExecutionError(
            "requested position size must be in [0, 1]"
        )
    normalized: dict[str, float] = {}
    for raw_name, raw_value in applicable_caps.items():
        if raw_value is None:
            continue
        name = str(raw_name).strip()
        if not name or name in normalized:
            raise CanonicalExecutionError(
                "position cap names must be unique and non-empty"
            )
        if (
            isinstance(raw_value, bool)
            or not isinstance(raw_value, (int, float))
            or not math.isfinite(float(raw_value))
            or not 0 <= float(raw_value) <= 1
        ):
            raise CanonicalExecutionError(
                f"position cap {name!r} must be in [0, 1]"
            )
        normalized[name] = float(raw_value)
    approved = min([float(requested_size), *normalized.values()])
    binding = tuple(
        sorted(name for name, value in normalized.items() if value == approved)
    )
    if approved == float(requested_size):
        binding = ("requested_size", *binding)
    return PositionSizingDecision(
        requested_size=float(requested_size),
        approved_size=approved,
        applicable_caps=tuple(sorted(normalized.items())),
        binding_caps=binding,
    )


@dataclass(frozen=True, slots=True)
class CausalExitConfig(CanonicalValue):
    stop_loss_price: float
    take_profit_price: float
    entry_price: float
    t_plus_one: bool = True
    dynamic_trailing: bool = True
    trailing_mode: str = "profit_floor"
    profit_lock_ratio: float = 0.5
    activation_gain_pct: float = 2.0
    callback_pct: float = 0.8
    minimum_lock_gain_pct: float = 0.3
    stop_gap_loss_cap_pct: float = 15.0
    stop_gap_fill_mode: GapStopFillMode = GapStopFillMode.CAPPED_OPEN
    take_profit_gap_fill_mode: GapTakeProfitFillMode = GapTakeProfitFillMode.OPEN
    intraday_exit_priority: IntradayExitPriority = IntradayExitPriority.STOP_LOSS

    def __post_init__(self) -> None:
        if self.dynamic_trailing and self.trailing_mode not in {
            "peak_callback",
            "callback",
            "profit_floor",
        }:
            raise CanonicalExecutionError(
                f"unknown trailing_mode: {self.trailing_mode}"
            )
        if (
            self.dynamic_trailing
            and self.trailing_mode == "profit_floor"
            and not 0 < self.profit_lock_ratio <= 1
        ):
            raise CanonicalExecutionError(
                "profit_lock_ratio must be in (0, 1]"
            )
        object.__setattr__(
            self,
            "stop_gap_fill_mode",
            self.stop_gap_fill_mode
            if isinstance(self.stop_gap_fill_mode, GapStopFillMode)
            else GapStopFillMode(str(self.stop_gap_fill_mode)),
        )
        object.__setattr__(
            self,
            "take_profit_gap_fill_mode",
            self.take_profit_gap_fill_mode
            if isinstance(self.take_profit_gap_fill_mode, GapTakeProfitFillMode)
            else GapTakeProfitFillMode(str(self.take_profit_gap_fill_mode)),
        )
        object.__setattr__(
            self,
            "intraday_exit_priority",
            self.intraday_exit_priority
            if isinstance(self.intraday_exit_priority, IntradayExitPriority)
            else IntradayExitPriority(str(self.intraday_exit_priority)),
        )


@dataclass(frozen=True, slots=True)
class CausalExitResult(CanonicalValue):
    raw_price: float
    trade_date: str
    reason: str
    holding_days: int
    intraday_exit_evidence: IntradayExitEvidence


def _intraday_exit_evidence(
    first_hit: IntradayFirstHit,
    *,
    applied_priority: IntradayExitPriority | None = None,
) -> IntradayExitEvidence:
    ambiguous = first_hit is IntradayFirstHit.AMBIGUOUS
    return IntradayExitEvidence(
        first_hit=first_hit,
        same_bar_stop_take_profit_hit=ambiguous,
        applied_priority=applied_priority,
    )


def _ordered_bars(request: ExecutionRequest) -> tuple[ExecutionBar, ...]:
    bars = tuple(
        bar
        for bar in request.bars
        if bar.code == request.intent.code
        and bar.trade_date > request.intent.signal_date
    )
    dates = tuple(bar.trade_date for bar in bars)
    if len(set(dates)) != len(dates):
        raise CanonicalExecutionError(
            "bars must not contain duplicate dates for the intent code"
        )
    return tuple(sorted(bars, key=lambda bar: bar.trade_date))


def _entry_not_triggered_reason(
    blocked_reasons: list[str], *, has_targets: bool
) -> str:
    unique_reasons = list(dict.fromkeys(blocked_reasons))
    if unique_reasons:
        return "；".join(unique_reasons[:2])
    if has_targets:
        return "观察期内未触发目标买点，未建仓"
    return "观察期内无可成交行情，未建仓"


def resolve_causal_entry(
    candidate_bars: Iterable[CausalEntryBar],
    config: CausalEntryConfig,
) -> EntryDecision:
    """Resolve the canonical entry decision for formal outcome projection."""
    bars = tuple(candidate_bars)
    if any(bar.code != bars[0].code for bar in bars[1:]):
        raise CanonicalExecutionError("entry bars must belong to one code")

    def decision(index: int, trade_date: str, raw_price: float, reason: str) -> EntryDecision:
        if config.recommendation_price > 0 and config.max_entry_gap_pct is not None:
            gap_pct = (
                (raw_price - config.recommendation_price)
                / config.recommendation_price
                * 100
            )
            if gap_pct > config.max_entry_gap_pct:
                return EntryDecision(
                    False,
                    None,
                    None,
                    None,
                    None,
                    "gap_up_blocked",
                    gap_pct,
                )
        executed_price = round(
            raw_price * (1 + config.entry_slippage_bps / 10_000), 10
        )
        return EntryDecision(
            True,
            index,
            trade_date,
            raw_price,
            executed_price,
            reason,
        )

    if config.price_source == "open":
        if not bars:
            return EntryDecision(
                False, None, None, None, None,
                "下一交易日行情缺失，无法确认成交",
            )
        bar = bars[0]
        if bar.block_reason:
            return EntryDecision(False, None, None, None, None, bar.block_reason)
        raw_price = bar.open
        if raw_price <= 0:
            return EntryDecision(
                False, None, None, None, None,
                "下一交易日开盘价无效，无法真实成交",
            )
        return decision(0, bar.trade_date, raw_price, "next_trading_day_open_entry")

    if config.price_source == "open_window" and not config.intraday:
        # 与回测路径 _resolve_entry 同一规则：窗口内跳过不可成交日与开盘跳空超限日，第一个可买日开盘买入
        window = bars[: max(1, min(config.max_entry_trade_days, config.horizon_days, len(bars)))]
        blocked: list[str] = []
        last_gap: float | None = None
        for index, bar in enumerate(window):
            if bar.block_reason:
                blocked.append(bar.block_reason)
                continue
            if bar.open <= 0:
                blocked.append("入场日开盘价无效，无法真实成交")
                continue
            if config.recommendation_price > 0 and config.max_entry_gap_pct is not None:
                gap_pct = (bar.open - config.recommendation_price) / config.recommendation_price * 100
                if gap_pct > config.max_entry_gap_pct:
                    last_gap = gap_pct
                    blocked.append("gap_up_blocked")
                    continue
            return decision(index, bar.trade_date, bar.open, "backtest_open_window_entry")
        if blocked and all(reason == "gap_up_blocked" for reason in blocked):
            return EntryDecision(False, None, None, None, None, "gap_up_blocked", last_gap)
        return EntryDecision(
            False, None, None, None, None,
            _entry_not_triggered_reason(
                [reason for reason in blocked if reason != "gap_up_blocked"] or blocked,
                has_targets=False,
            ),
        )

    if config.intraday:
        if config.recommendation_price <= 0:
            return EntryDecision(
                False, None, None, None, None,
                "盘中推荐价无效，无法确认成交",
            )
        return decision(
            0,
            config.signal_date,
            config.recommendation_price,
            "intraday_recommend_price_entry",
        )

    lookahead = max(
        1,
        min(config.max_entry_trade_days, config.horizon_days, len(bars)),
    )
    entry_window = bars[:lookahead]
    blocked_reasons: list[str] = []
    if config.allow_target_touch:
        for index, bar in enumerate(entry_window):
            for target in config.target_prices:
                if bar.low <= target:
                    if bar.block_reason:
                        blocked_reasons.append(bar.block_reason)
                        continue
                    raw_price = min(bar.open, target) if bar.open > 0 else target
                    return decision(index, bar.trade_date, raw_price, "buy_target_touched")

    has_targets = bool(config.target_prices)
    if has_targets and not config.fallback_to_open_when_targets_miss:
        return EntryDecision(
            False,
            None,
            None,
            None,
            None,
            _entry_not_triggered_reason(blocked_reasons, has_targets=True),
        )

    for index, bar in enumerate(entry_window):
        if bar.block_reason:
            blocked_reasons.append(bar.block_reason)
            continue
        raw_price = bar.open or bar.close
        if raw_price <= 0:
            blocked_reasons.append("入场日价格无效，无法真实成交")
            continue
        reason = "next_open_fallback_entry（买点未命中，市价入场）" if has_targets else "next_open_entry"
        return decision(index, bar.trade_date, raw_price, reason)

    return EntryDecision(
        False,
        None,
        None,
        None,
        None,
        _entry_not_triggered_reason(blocked_reasons, has_targets=has_targets),
    )


def _resolve_entry(
    intent: ExecutionIntent,
    bars: tuple[ExecutionBar, ...],
    policy: ExecutionPolicy,
) -> EntryDecision:
    for index, bar in enumerate(bars[: policy.entry_lookahead_days]):
        if not bar.tradable:
            if policy.skip_untradable_entry_bars:
                continue
            return EntryDecision(False, None, None, None, None, "entry_rejected_untradable")
        if policy.max_entry_gap_pct is not None:
            gap_pct = (bar.open / intent.signal_price - 1) * 100
            if gap_pct > policy.max_entry_gap_pct:
                continue
        raw_price = bar.open
        executed_price = round(
            raw_price * (1 + policy.entry_slippage_bps / 10_000), 10
        )
        return EntryDecision(
            True,
            index,
            bar.trade_date,
            raw_price,
            executed_price,
            "next_open_entry",
        )
    return EntryDecision(False, None, None, None, None, "entry_not_filled")


def _trailing_stop_return(
    observed_high_return: float,
    config: CausalExitConfig,
) -> float:
    if config.trailing_mode == "profit_floor":
        return max(
            config.minimum_lock_gain_pct,
            observed_high_return * config.profit_lock_ratio,
        )
    return observed_high_return - config.callback_pct


def resolve_causal_exit(
    holding_bars: Iterable[ExecutionBar],
    config: CausalExitConfig,
) -> CausalExitResult | None:
    """Resolve one causal exit; an entry bar is never scanned under T+1.

    Trailing protection uses information known through the previous close.
    A high formed during a bar can only update the following bar's protection.
    """
    bars = tuple(holding_bars)
    if not bars:
        return None
    if config.t_plus_one and len(bars) <= 1:
        return None

    entry_bar = bars[0]
    scan_bars = bars[1:] if config.t_plus_one else bars
    hard_stop = config.stop_loss_price
    take_profit = config.take_profit_price
    trailing_active = False
    observed_high_return = 0.0
    next_trailing_stop = 0.0

    if config.dynamic_trailing and config.t_plus_one and config.entry_price > 0:
        entry_close = entry_bar.close
        if entry_close > 0:
            entry_close_return = (entry_close / config.entry_price - 1) * 100
            observed_high_return = max(0.0, entry_close_return)
            if entry_close_return >= config.activation_gain_pct:
                trailing_active = True
                stop_return = _trailing_stop_return(entry_close_return, config)
                next_trailing_stop = max(
                    config.entry_price * (1 + stop_return / 100), hard_stop
                )

    locked_bars = 0
    for offset, bar in enumerate(scan_bars):
        if not bar.sellable:
            locked_bars += 1
        bar_open = bar.open
        high = bar.high
        low = bar.low
        has_ohlc = bar_open > 0 and low > 0 and high > 0
        holding_days = (
            offset + 2 if config.t_plus_one else offset + 1
        )

        if bar.sellable and has_ohlc:
            if bar_open <= hard_stop:
                if config.stop_gap_fill_mode is GapStopFillMode.CAPPED_OPEN:
                    minimum_exit = config.entry_price * (
                        1 - config.stop_gap_loss_cap_pct / 100
                    )
                    price = max(bar_open, minimum_exit)
                else:
                    price = bar_open
                return CausalExitResult(
                    price,
                    bar.trade_date,
                    "stop_loss_gap",
                    holding_days,
                    _intraday_exit_evidence(IntradayFirstHit.NOT_APPLICABLE),
                )
            if (
                config.dynamic_trailing
                and trailing_active
                and next_trailing_stop > 0
                and bar_open <= next_trailing_stop
            ):
                return CausalExitResult(
                    bar_open,
                    bar.trade_date,
                    "trailing_stop_gap",
                    holding_days,
                    _intraday_exit_evidence(IntradayFirstHit.NOT_APPLICABLE),
                )
            if bar_open >= take_profit:
                price = (
                    bar_open
                    if config.take_profit_gap_fill_mode
                    is GapTakeProfitFillMode.OPEN
                    else take_profit
                )
                return CausalExitResult(
                    price,
                    bar.trade_date,
                    "take_profit_gap",
                    holding_days,
                    _intraday_exit_evidence(IntradayFirstHit.NOT_APPLICABLE),
                )
            stop_hit = low <= hard_stop
            take_profit_hit = high >= take_profit
            if stop_hit and take_profit_hit:
                if (
                    config.intraday_exit_priority
                    is IntradayExitPriority.TAKE_PROFIT
                ):
                    return CausalExitResult(
                        take_profit,
                        bar.trade_date,
                        "take_profit_intraday_both_hit",
                        holding_days,
                        _intraday_exit_evidence(
                            IntradayFirstHit.AMBIGUOUS,
                            applied_priority=config.intraday_exit_priority,
                        ),
                    )
                return CausalExitResult(
                    hard_stop,
                    bar.trade_date,
                    "stop_loss_intraday_both_hit",
                    holding_days,
                    _intraday_exit_evidence(
                        IntradayFirstHit.AMBIGUOUS,
                        applied_priority=config.intraday_exit_priority,
                    ),
                )
            if stop_hit:
                return CausalExitResult(
                    hard_stop,
                    bar.trade_date,
                    "stop_loss_hit",
                    holding_days,
                    _intraday_exit_evidence(IntradayFirstHit.STOP_LOSS),
                )
            if (
                config.dynamic_trailing
                and trailing_active
                and next_trailing_stop > 0
                and low <= next_trailing_stop
            ):
                return CausalExitResult(
                    next_trailing_stop,
                    bar.trade_date,
                    "trailing_stop_hit",
                    holding_days,
                    _intraday_exit_evidence(IntradayFirstHit.TRAILING_STOP),
                )
            if take_profit_hit:
                return CausalExitResult(
                    take_profit,
                    bar.trade_date,
                    "take_profit_hit",
                    holding_days,
                    _intraday_exit_evidence(IntradayFirstHit.TAKE_PROFIT),
                )

        if config.dynamic_trailing and config.entry_price > 0 and high > 0:
            bar_high_return = (high / config.entry_price - 1) * 100
            observed_high_return = max(observed_high_return, bar_high_return)
            if (
                not trailing_active
                and observed_high_return >= config.activation_gain_pct
            ):
                trailing_active = True
            if trailing_active:
                stop_return = _trailing_stop_return(observed_high_return, config)
                protection = max(
                    config.entry_price * (1 + stop_return / 100), hard_stop
                )
                next_trailing_stop = max(next_trailing_stop, protection)

    last_bar = bars[-1]
    if locked_bars and locked_bars == len(scan_bars):
        reason = "limit_down_locked_horizon_mark"
    elif locked_bars:
        reason = "horizon_close_after_locked_limit_down"
    else:
        reason = "horizon_close"
    return CausalExitResult(
        last_bar.close,
        last_bar.trade_date,
        reason,
        len(bars),
        _intraday_exit_evidence(IntradayFirstHit.NOT_APPLICABLE),
    )


def execute_trade(
    request: ExecutionRequest,
    policy: ExecutionPolicy,
) -> ExecutionResult:
    """Execute one next-open trade under the canonical policy."""
    if not isinstance(request, ExecutionRequest):
        raise CanonicalExecutionError("request must be ExecutionRequest")
    if not isinstance(policy, ExecutionPolicy):
        raise CanonicalExecutionError("policy must be ExecutionPolicy")
    ordered = _ordered_bars(request)
    normalized_request = ExecutionRequest(intent=request.intent, bars=ordered)
    input_fingerprint = canonical_fingerprint(
        {"request": normalized_request, "policy": policy}
    )
    entry = _resolve_entry(request.intent, ordered, policy)
    if not entry.triggered:
        return ExecutionResult(
            ExecutionStatus.NOT_EXECUTED,
            entry,
            None,
            0.0,
            policy.fee_pct,
            0.0,
            0.0,
            policy.fingerprint(),
            input_fingerprint,
            policy.t_plus_one,
        )

    assert entry.bar_index is not None
    assert entry.executed_price is not None
    sizing = compose_position_caps(
        policy.position_size,
        {
            name: getattr(policy.position, name)
            for name in policy.position._OPTIONAL_CAP_FIELDS
        },
    )
    holding_bars = ordered[
        entry.bar_index : entry.bar_index + policy.horizon_days
    ]
    stop_price = (
        entry.executed_price * (1 - policy.stop_loss_pct / 100)
        if policy.stop_loss_pct is not None
        else 0.0
    )
    take_profit_price = (
        entry.executed_price * (1 + policy.take_profit_pct / 100)
        if policy.take_profit_pct is not None
        else 1e308
    )
    exit_config = CausalExitConfig(
        stop_loss_price=stop_price,
        take_profit_price=take_profit_price,
        entry_price=entry.executed_price,
        t_plus_one=policy.t_plus_one,
        dynamic_trailing=policy.trailing.enabled,
        trailing_mode=(
            "profit_floor"
            if policy.trailing.mode is TrailingMode.PROFIT_FLOOR
            else "callback"
        ),
        profit_lock_ratio=policy.trailing.profit_lock_ratio,
        activation_gain_pct=policy.trailing.activation_gain_pct,
        callback_pct=policy.trailing.callback_pct,
        minimum_lock_gain_pct=policy.trailing.minimum_lock_gain_pct,
        stop_gap_loss_cap_pct=policy.gap_risk.stop_gap_loss_cap_pct,
        stop_gap_fill_mode=policy.gap_risk.stop_gap_fill_mode,
        take_profit_gap_fill_mode=policy.gap_risk.take_profit_gap_fill_mode,
        intraday_exit_priority=policy.intraday_exit_priority,
    )
    exit_result = resolve_causal_exit(holding_bars, exit_config)
    if exit_result is None:
        return ExecutionResult(
            ExecutionStatus.OPEN_T1_LOCKED,
            entry,
            None,
            0.0,
            policy.fee_pct,
            0.0,
            sizing.approved_size,
            policy.fingerprint(),
            input_fingerprint,
            policy.t_plus_one,
        )

    executed_exit = round(
        exit_result.raw_price * (1 - policy.exit_slippage_bps / 10_000), 10
    )
    gross_return = round(
        (executed_exit / entry.executed_price - 1) * 100, 10
    )
    net_return = round(gross_return - policy.fee_pct, 10)
    exit_decision = ExitDecision(
        exit_result.trade_date,
        exit_result.raw_price,
        executed_exit,
        exit_result.reason,
        exit_result.holding_days,
        exit_result.intraday_exit_evidence,
    )
    return ExecutionResult(
        ExecutionStatus.CLOSED,
        entry,
        exit_decision,
        gross_return,
        policy.fee_pct,
        net_return,
        sizing.approved_size,
        policy.fingerprint(),
        input_fingerprint,
        policy.t_plus_one,
    )


__all__ = [
    "CanonicalExecutionError",
    "CausalExitConfig",
    "CausalExitResult",
    "EntryDecision",
    "ExecutionBar",
    "ExecutionIntent",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStatus",
    "ExitDecision",
    "IntradayExitEvidence",
    "IntradayFirstHit",
    "PositionSizingDecision",
    "compose_position_caps",
    "execute_trade",
    "resolve_causal_exit",
]
