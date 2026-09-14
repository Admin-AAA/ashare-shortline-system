"""Canonical, immutable A-share execution policy and versioned artifact registry.

This module defines policy data only.  It intentionally contains no simulation,
ORM, database, or network behavior.  Backtest and formal adapters may project
legacy constants from one registered policy until the shared simulation core is
cut over in a later task.
"""
from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields, replace
from enum import Enum
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, TypeVar

from app.engine.canonical import CanonicalValue, canonical_json


EXECUTION_POLICY_SCHEMA_VERSION = 1
EXECUTION_POLICY_ARTIFACT_SCHEMA_VERSION = 1
EXECUTION_POLICY_REGISTRY_SCHEMA_VERSION = 1
DEFAULT_EXECUTION_POLICY_ID = "a-share-shortline"
DEFAULT_EXECUTION_POLICY_VERSION = "1.0.0"


class ExecutionPolicyError(ValueError):
    """Base error for invalid policies and policy artifacts."""


class ExecutionPolicyValidationError(ExecutionPolicyError):
    """A typed policy value violates the canonical schema."""


class ExecutionPolicyArtifactError(ExecutionPolicyError):
    """A repository-owned policy artifact or manifest is invalid."""


class ExecutionPolicyNotFoundError(ExecutionPolicyError):
    """A requested policy identity or fingerprint is not registered."""


class EntryPriceSource(str, Enum):
    INTRADAY_SNAPSHOT_OR_POSTCLOSE_TARGET = "intraday_snapshot_or_postclose_target"
    NEXT_TRADING_DAY_OPEN = "next_trading_day_open"
    TARGET_THEN_OPEN = "target_then_open"


class IntradayExitPriority(str, Enum):
    """Deterministic resolution when stop loss and take profit hit in one bar."""

    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


class UntradableEntryAction(str, Enum):
    SKIP = "skip"
    REJECT = "reject"


class UntradableExitAction(str, Enum):
    DEFER = "defer"
    MARK_AT_HORIZON = "mark_at_horizon"


class PositionCapComposition(str, Enum):
    """Versioned position-cap behavior; the legacy exception is audit-visible."""

    MIN_ALL_APPLICABLE = "min_all_applicable"
    LEGACY_WEAK_REGIME_BYPASS = "legacy_weak_regime_bypass"


class TrailingMode(str, Enum):
    PROFIT_FLOOR = "profit_floor"
    CALLBACK = "callback"


class GapStopFillMode(str, Enum):
    CAPPED_OPEN = "capped_open"
    OPEN = "open"


class GapTakeProfitFillMode(str, Enum):
    OPEN = "open"
    TARGET = "target"


def _non_empty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionPolicyValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _identifier(value: object, field_name: str) -> str:
    normalized = _non_empty(value, field_name)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", normalized):
        raise ExecutionPolicyValidationError(f"{field_name} has an invalid format")
    return normalized


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ExecutionPolicyValidationError(f"{field_name} must be a positive integer")
    return value


def _finite(
    value: object,
    field_name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    minimum_inclusive: bool = True,
    maximum_inclusive: bool = True,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ExecutionPolicyValidationError(f"{field_name} must be a finite number")
    normalized = float(value)
    if minimum is not None:
        invalid = normalized < minimum if minimum_inclusive else normalized <= minimum
        if invalid:
            operator = ">=" if minimum_inclusive else ">"
            raise ExecutionPolicyValidationError(f"{field_name} must be {operator} {minimum}")
    if maximum is not None:
        invalid = normalized > maximum if maximum_inclusive else normalized >= maximum
        if invalid:
            operator = "<=" if maximum_inclusive else "<"
            raise ExecutionPolicyValidationError(f"{field_name} must be {operator} {maximum}")
    return normalized


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ExecutionPolicyValidationError(f"{field_name} must be a boolean")
    return value


EnumT = TypeVar("EnumT", bound=Enum)


def _enum(value: object, enum_type: type[EnumT], field_name: str) -> EnumT:
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(str(item.value) for item in enum_type)
        raise ExecutionPolicyValidationError(f"{field_name} must be one of: {choices}") from exc


@dataclass(frozen=True, slots=True)
class EntryWindowPolicy(CanonicalValue):
    policy_id: str = "causal_strategy_entry_v1"
    price_source: EntryPriceSource = EntryPriceSource.INTRADAY_SNAPSHOT_OR_POSTCLOSE_TARGET
    max_entry_trade_days: int = 2
    allow_target_touch: bool = True
    fallback_to_open_when_targets_miss: bool = False
    skip_untradable_bars: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _identifier(self.policy_id, "entry.policy_id"))
        object.__setattr__(self, "price_source", _enum(self.price_source, EntryPriceSource, "entry.price_source"))
        _positive_int(self.max_entry_trade_days, "entry.max_entry_trade_days")
        for name in ("allow_target_touch", "fallback_to_open_when_targets_miss", "skip_untradable_bars"):
            _boolean(getattr(self, name), f"entry.{name}")


@dataclass(frozen=True, slots=True)
class FeePolicy(CanonicalValue):
    commission_rate_per_side: float = 0.0001
    stamp_tax_rate_on_exit: float = 0.0005

    def __post_init__(self) -> None:
        object.__setattr__(self, "commission_rate_per_side", _finite(
            self.commission_rate_per_side, "fees.commission_rate_per_side", minimum=0.0, maximum=0.01,
        ))
        object.__setattr__(self, "stamp_tax_rate_on_exit", _finite(
            self.stamp_tax_rate_on_exit, "fees.stamp_tax_rate_on_exit", minimum=0.0, maximum=0.01,
        ))

    @property
    def round_trip_pct(self) -> float:
        return (self.commission_rate_per_side * 2 + self.stamp_tax_rate_on_exit) * 100


@dataclass(frozen=True, slots=True)
class SlippagePolicy(CanonicalValue):
    entry_bps: float = 10.0
    exit_bps: float = 10.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_bps", _finite(self.entry_bps, "slippage.entry_bps", minimum=0.0, maximum=500.0))
        object.__setattr__(self, "exit_bps", _finite(self.exit_bps, "slippage.exit_bps", minimum=0.0, maximum=500.0))


@dataclass(frozen=True, slots=True)
class BoardLimitPolicy(CanonicalValue):
    main_board_pct: float = 10.0
    growth_board_pct: float = 20.0
    star_market_pct: float = 20.0
    beijing_exchange_pct: float = 30.0
    st_pct: float = 5.0
    limit_detection_tolerance_ratio: float = 0.005
    block_one_word_limit_up_entry: bool = True
    defer_one_word_limit_down_exit: bool = True

    def __post_init__(self) -> None:
        for name in ("main_board_pct", "growth_board_pct", "star_market_pct", "beijing_exchange_pct", "st_pct"):
            object.__setattr__(self, name, _finite(getattr(self, name), f"board_limits.{name}", minimum=0.0, maximum=100.0, minimum_inclusive=False))
        object.__setattr__(self, "limit_detection_tolerance_ratio", _finite(
            self.limit_detection_tolerance_ratio,
            "board_limits.limit_detection_tolerance_ratio",
            minimum=0.0,
            maximum=0.1,
        ))
        _boolean(self.block_one_word_limit_up_entry, "board_limits.block_one_word_limit_up_entry")
        _boolean(self.defer_one_word_limit_down_exit, "board_limits.defer_one_word_limit_down_exit")


@dataclass(frozen=True, slots=True)
class LiquidityPolicy(CanonicalValue):
    require_positive_open: bool = True
    require_positive_close: bool = True
    require_positive_volume: bool = True
    require_positive_amount: bool = True
    untradable_entry_action: UntradableEntryAction = UntradableEntryAction.SKIP
    untradable_exit_action: UntradableExitAction = UntradableExitAction.DEFER

    def __post_init__(self) -> None:
        for name in ("require_positive_open", "require_positive_close", "require_positive_volume", "require_positive_amount"):
            _boolean(getattr(self, name), f"liquidity.{name}")
        object.__setattr__(self, "untradable_entry_action", _enum(
            self.untradable_entry_action, UntradableEntryAction, "liquidity.untradable_entry_action",
        ))
        object.__setattr__(self, "untradable_exit_action", _enum(
            self.untradable_exit_action, UntradableExitAction, "liquidity.untradable_exit_action",
        ))


@dataclass(frozen=True, slots=True)
class PositionSizingPolicy(CanonicalValue):
    default_fraction: float = 1.0
    high_score_threshold: float = 85.0
    high_score_cap: float = 0.5
    market_regime_cap_enabled: bool = True
    cap_composition: PositionCapComposition = PositionCapComposition.LEGACY_WEAK_REGIME_BYPASS
    weak_regime_prefix: str = "弱势"
    # Optional exposure caps; None means "not applicable" and is omitted from
    # the canonical form so pre-cap (v1) payloads keep their original identity.
    single_name_cap: float | None = None
    strategy_cap: float | None = None
    gross_exposure_cap: float | None = None
    net_exposure_cap: float | None = None
    industry_cap: float | None = None
    theme_cap: float | None = None

    _OPTIONAL_CAP_FIELDS = (
        "single_name_cap",
        "strategy_cap",
        "gross_exposure_cap",
        "net_exposure_cap",
        "industry_cap",
        "theme_cap",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "default_fraction", _finite(
            self.default_fraction, "position.default_fraction", minimum=0.0, maximum=1.0, minimum_inclusive=False,
        ))
        object.__setattr__(self, "high_score_threshold", _finite(
            self.high_score_threshold, "position.high_score_threshold", minimum=0.0, maximum=100.0,
        ))
        object.__setattr__(self, "high_score_cap", _finite(
            self.high_score_cap, "position.high_score_cap", minimum=0.0, maximum=1.0, minimum_inclusive=False,
        ))
        _boolean(self.market_regime_cap_enabled, "position.market_regime_cap_enabled")
        object.__setattr__(self, "cap_composition", _enum(
            self.cap_composition, PositionCapComposition, "position.cap_composition",
        ))
        object.__setattr__(self, "weak_regime_prefix", _non_empty(self.weak_regime_prefix, "position.weak_regime_prefix"))
        for name in self._OPTIONAL_CAP_FIELDS:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite(
                    value, f"position.{name}", minimum=0.0, maximum=1.0, minimum_inclusive=False,
                ))
        if self.high_score_cap > self.default_fraction:
            raise ExecutionPolicyValidationError("position.high_score_cap cannot exceed default_fraction")

    def canonical_payload(self) -> dict[str, Any]:
        """Omit ``None`` (not-applicable) caps so legacy v1 payloads stay canonical."""

        # NOTE: explicit base call; zero-arg super() breaks on slots=True dataclasses.
        return {key: value for key, value in CanonicalValue.canonical_payload(self).items() if value is not None}


@dataclass(frozen=True, slots=True)
class StopLossPolicy(CanonicalValue):
    enabled: bool = True
    default_loss_pct: float = 4.5
    maximum_loss_pct: float = 4.7

    def __post_init__(self) -> None:
        _boolean(self.enabled, "stop_loss.enabled")
        object.__setattr__(self, "default_loss_pct", _finite(
            self.default_loss_pct, "stop_loss.default_loss_pct", minimum=0.0, maximum=100.0, minimum_inclusive=False, maximum_inclusive=False,
        ))
        object.__setattr__(self, "maximum_loss_pct", _finite(
            self.maximum_loss_pct, "stop_loss.maximum_loss_pct", minimum=0.0, maximum=100.0, minimum_inclusive=False, maximum_inclusive=False,
        ))
        if self.default_loss_pct > self.maximum_loss_pct:
            raise ExecutionPolicyValidationError("stop_loss.default_loss_pct cannot exceed maximum_loss_pct")


@dataclass(frozen=True, slots=True)
class TakeProfitPolicy(CanonicalValue):
    enabled: bool = True
    default_gain_pct: float = 6.0
    maximum_gain_pct: float = 15.0

    def __post_init__(self) -> None:
        _boolean(self.enabled, "take_profit.enabled")
        object.__setattr__(self, "default_gain_pct", _finite(
            self.default_gain_pct, "take_profit.default_gain_pct", minimum=0.0, maximum=100.0, minimum_inclusive=False, maximum_inclusive=False,
        ))
        object.__setattr__(self, "maximum_gain_pct", _finite(
            self.maximum_gain_pct, "take_profit.maximum_gain_pct", minimum=0.0, maximum=100.0, minimum_inclusive=False, maximum_inclusive=False,
        ))
        if self.default_gain_pct > self.maximum_gain_pct:
            raise ExecutionPolicyValidationError("take_profit.default_gain_pct cannot exceed maximum_gain_pct")


@dataclass(frozen=True, slots=True)
class TrailingPolicy(CanonicalValue):
    enabled: bool = True
    mode: TrailingMode = TrailingMode.PROFIT_FLOOR
    activation_gain_pct: float = 2.0
    callback_pct: float = 0.8
    minimum_lock_gain_pct: float = 0.3
    profit_lock_ratio: float = 0.5
    take_profit_precedence: bool = True

    def __post_init__(self) -> None:
        _boolean(self.enabled, "trailing.enabled")
        object.__setattr__(self, "mode", _enum(self.mode, TrailingMode, "trailing.mode"))
        for name in ("activation_gain_pct", "callback_pct", "minimum_lock_gain_pct"):
            object.__setattr__(self, name, _finite(
                getattr(self, name), f"trailing.{name}", minimum=0.0, maximum=100.0, maximum_inclusive=False,
            ))
        object.__setattr__(self, "profit_lock_ratio", _finite(
            self.profit_lock_ratio, "trailing.profit_lock_ratio", minimum=0.0, maximum=1.0,
        ))
        _boolean(self.take_profit_precedence, "trailing.take_profit_precedence")


@dataclass(frozen=True, slots=True)
class GapRiskPolicy(CanonicalValue):
    max_entry_gap_up_pct: float | None = 10.0
    stop_gap_loss_cap_pct: float = 15.0
    stop_gap_fill_mode: GapStopFillMode = GapStopFillMode.CAPPED_OPEN
    take_profit_gap_fill_mode: GapTakeProfitFillMode = GapTakeProfitFillMode.OPEN

    def __post_init__(self) -> None:
        if self.max_entry_gap_up_pct is not None:
            object.__setattr__(self, "max_entry_gap_up_pct", _finite(
                self.max_entry_gap_up_pct,
                "gap_risk.max_entry_gap_up_pct",
                minimum=0.0,
                maximum=100.0,
                maximum_inclusive=False,
            ))
        object.__setattr__(self, "stop_gap_loss_cap_pct", _finite(
            self.stop_gap_loss_cap_pct,
            "gap_risk.stop_gap_loss_cap_pct",
            minimum=0.0,
            maximum=100.0,
            minimum_inclusive=False,
            maximum_inclusive=False,
        ))
        object.__setattr__(self, "stop_gap_fill_mode", _enum(
            self.stop_gap_fill_mode, GapStopFillMode, "gap_risk.stop_gap_fill_mode",
        ))
        object.__setattr__(self, "take_profit_gap_fill_mode", _enum(
            self.take_profit_gap_fill_mode, GapTakeProfitFillMode, "gap_risk.take_profit_gap_fill_mode",
        ))


_UNSET = object()


@dataclass(frozen=True, slots=True, init=False)
class ExecutionPolicy(CanonicalValue):
    """Complete immutable execution contract used as the sole fingerprint input.

    Canonical fields are nested by concern.  The keyword-only flat arguments are
    a temporary adapter for existing research callers; they are projected into
    these canonical fields and never appear in canonical JSON.
    """

    schema_version: int = EXECUTION_POLICY_SCHEMA_VERSION
    policy_id: str = DEFAULT_EXECUTION_POLICY_ID
    version: str = DEFAULT_EXECUTION_POLICY_VERSION
    entry: EntryWindowPolicy = field(default_factory=EntryWindowPolicy)
    t_plus_one: bool = True
    horizon_days: int = 5
    fees: FeePolicy = field(default_factory=FeePolicy)
    slippage: SlippagePolicy = field(default_factory=SlippagePolicy)
    board_limits: BoardLimitPolicy = field(default_factory=BoardLimitPolicy)
    liquidity: LiquidityPolicy = field(default_factory=LiquidityPolicy)
    position: PositionSizingPolicy = field(default_factory=PositionSizingPolicy)
    stop_loss: StopLossPolicy = field(default_factory=StopLossPolicy)
    take_profit: TakeProfitPolicy = field(default_factory=TakeProfitPolicy)
    trailing: TrailingPolicy = field(default_factory=TrailingPolicy)
    gap_risk: GapRiskPolicy = field(default_factory=GapRiskPolicy)
    intraday_exit_priority: IntradayExitPriority = IntradayExitPriority.STOP_LOSS

    def __init__(
        self,
        *,
        schema_version: int = EXECUTION_POLICY_SCHEMA_VERSION,
        policy_id: str = DEFAULT_EXECUTION_POLICY_ID,
        version: str = DEFAULT_EXECUTION_POLICY_VERSION,
        entry: EntryWindowPolicy | None = None,
        t_plus_one: bool = True,
        horizon_days: int = 5,
        fees: FeePolicy | None = None,
        slippage: SlippagePolicy | None = None,
        board_limits: BoardLimitPolicy | None = None,
        liquidity: LiquidityPolicy | None = None,
        position: PositionSizingPolicy | None = None,
        stop_loss: StopLossPolicy | None = None,
        take_profit: TakeProfitPolicy | None = None,
        trailing: TrailingPolicy | None = None,
        gap_risk: GapRiskPolicy | None = None,
        intraday_exit_priority: IntradayExitPriority | str = IntradayExitPriority.STOP_LOSS,
        entry_lookahead_days: object = _UNSET,
        commission_rate: object = _UNSET,
        stamp_tax_rate: object = _UNSET,
        entry_slippage_bps: object = _UNSET,
        exit_slippage_bps: object = _UNSET,
        position_size: object = _UNSET,
        stop_loss_pct: object = _UNSET,
        take_profit_pct: object = _UNSET,
        max_entry_gap_pct: object = _UNSET,
        skip_untradable_entry_bars: object = _UNSET,
    ) -> None:
        resolved_entry = entry or EntryWindowPolicy()
        resolved_fees = fees or FeePolicy()
        resolved_slippage = slippage or SlippagePolicy()
        resolved_position = position or PositionSizingPolicy()
        # Bare construction must reproduce the registered v1 artifact field for
        # field; drifting local defaults used to give one policy identity two
        # different fingerprints.
        resolved_stop_loss = stop_loss or StopLossPolicy()
        resolved_take_profit = take_profit or TakeProfitPolicy()
        resolved_gap_risk = gap_risk or GapRiskPolicy()

        if entry_lookahead_days is not _UNSET:
            resolved_entry = replace(resolved_entry, max_entry_trade_days=entry_lookahead_days)
        if skip_untradable_entry_bars is not _UNSET:
            resolved_entry = replace(resolved_entry, skip_untradable_bars=skip_untradable_entry_bars)
        if commission_rate is not _UNSET:
            resolved_fees = replace(resolved_fees, commission_rate_per_side=commission_rate)
        if stamp_tax_rate is not _UNSET:
            resolved_fees = replace(resolved_fees, stamp_tax_rate_on_exit=stamp_tax_rate)
        if entry_slippage_bps is not _UNSET:
            resolved_slippage = replace(resolved_slippage, entry_bps=entry_slippage_bps)
        if exit_slippage_bps is not _UNSET:
            resolved_slippage = replace(resolved_slippage, exit_bps=exit_slippage_bps)
        if position_size is not _UNSET:
            default_fraction = _finite(
                position_size,
                "position_size",
                minimum=0.0,
                maximum=1.0,
                minimum_inclusive=False,
            )
            resolved_position = replace(
                resolved_position,
                default_fraction=default_fraction,
                high_score_cap=min(resolved_position.high_score_cap, default_fraction),
            )
        if stop_loss_pct is not _UNSET:
            if stop_loss_pct is None:
                resolved_stop_loss = replace(resolved_stop_loss, enabled=False)
            else:
                loss_pct = _finite(stop_loss_pct, "stop_loss_pct", minimum=0.0, maximum=100.0, minimum_inclusive=False, maximum_inclusive=False)
                # The maximum floor is the resolved policy's own maximum (the
                # registered default for bare construction), never a hardcoded
                # constant that silently lifts the ceiling above the artifact.
                resolved_stop_loss = replace(
                    resolved_stop_loss,
                    enabled=True,
                    default_loss_pct=loss_pct,
                    maximum_loss_pct=max(loss_pct, resolved_stop_loss.maximum_loss_pct),
                )
        if take_profit_pct is not _UNSET:
            if take_profit_pct is None:
                resolved_take_profit = replace(resolved_take_profit, enabled=False)
            else:
                gain_pct = _finite(take_profit_pct, "take_profit_pct", minimum=0.0, maximum=100.0, minimum_inclusive=False, maximum_inclusive=False)
                resolved_take_profit = replace(
                    resolved_take_profit,
                    enabled=True,
                    default_gain_pct=gain_pct,
                    maximum_gain_pct=max(gain_pct, resolved_take_profit.maximum_gain_pct),
                )
        if max_entry_gap_pct is not _UNSET:
            resolved_gap_risk = replace(resolved_gap_risk, max_entry_gap_up_pct=max_entry_gap_pct)

        values: dict[str, Any] = {
            "schema_version": schema_version,
            "policy_id": policy_id,
            "version": version,
            "entry": resolved_entry,
            "t_plus_one": t_plus_one,
            "horizon_days": horizon_days,
            "fees": resolved_fees,
            "slippage": resolved_slippage,
            "board_limits": board_limits or BoardLimitPolicy(),
            "liquidity": liquidity or LiquidityPolicy(),
            "position": resolved_position,
            "stop_loss": resolved_stop_loss,
            "take_profit": resolved_take_profit,
            "trailing": trailing or TrailingPolicy(),
            "gap_risk": resolved_gap_risk,
            "intraday_exit_priority": _enum(intraday_exit_priority, IntradayExitPriority, "intraday_exit_priority"),
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)
        self._validate()

    def _validate(self) -> None:
        if self.schema_version != EXECUTION_POLICY_SCHEMA_VERSION:
            raise ExecutionPolicyValidationError(
                f"unsupported execution policy schema_version: {self.schema_version}"
            )
        object.__setattr__(self, "policy_id", _identifier(self.policy_id, "policy_id"))
        object.__setattr__(self, "version", _identifier(self.version, "version"))
        _boolean(self.t_plus_one, "t_plus_one")
        _positive_int(self.horizon_days, "horizon_days")
        expected_types = {
            "entry": EntryWindowPolicy,
            "fees": FeePolicy,
            "slippage": SlippagePolicy,
            "board_limits": BoardLimitPolicy,
            "liquidity": LiquidityPolicy,
            "position": PositionSizingPolicy,
            "stop_loss": StopLossPolicy,
            "take_profit": TakeProfitPolicy,
            "trailing": TrailingPolicy,
            "gap_risk": GapRiskPolicy,
        }
        for name, expected_type in expected_types.items():
            if not isinstance(getattr(self, name), expected_type):
                raise ExecutionPolicyValidationError(f"{name} must be {expected_type.__name__}")

    def canonical_payload(self) -> dict[str, Any]:
        """Canonical form; the position payload drops ``None`` ("not applicable") caps.

        Omitting not-applicable caps keeps the canonical form of the registered
        v1 artifact byte-compatible, so bare construction and the v1 artifact
        share one fingerprint while v2 carries the registered cap values.
        """

        payload = CanonicalValue.canonical_payload(self)
        payload["position"] = self.position.canonical_payload()
        return payload

    # Read-only flat projections retained until adapters move to the shared core.
    @property
    def entry_lookahead_days(self) -> int:
        return self.entry.max_entry_trade_days

    @property
    def commission_rate(self) -> float:
        return self.fees.commission_rate_per_side

    @property
    def stamp_tax_rate(self) -> float:
        return self.fees.stamp_tax_rate_on_exit

    @property
    def entry_slippage_bps(self) -> float:
        return self.slippage.entry_bps

    @property
    def exit_slippage_bps(self) -> float:
        return self.slippage.exit_bps

    @property
    def position_size(self) -> float:
        return self.position.default_fraction

    @property
    def stop_loss_pct(self) -> float | None:
        return self.stop_loss.default_loss_pct if self.stop_loss.enabled else None

    @property
    def take_profit_pct(self) -> float | None:
        return self.take_profit.default_gain_pct if self.take_profit.enabled else None

    @property
    def max_entry_gap_pct(self) -> float | None:
        return self.gap_risk.max_entry_gap_up_pct

    @property
    def skip_untradable_entry_bars(self) -> bool:
        return self.entry.skip_untradable_bars

    @property
    def fee_pct(self) -> float:
        return self.fees.round_trip_pct

    def config_projection(self) -> Mapping[str, Any]:
        """Flat, immutable projection consumed by the existing research API schema."""

        return MappingProxyType({
            "schema_version": self.schema_version,
            "entry_lookahead_days": self.entry_lookahead_days,
            "horizon_days": self.horizon_days,
            "commission_rate": self.commission_rate,
            "stamp_tax_rate": self.stamp_tax_rate,
            "entry_slippage_bps": self.entry_slippage_bps,
            "exit_slippage_bps": self.exit_slippage_bps,
            "position_size": self.position_size,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "max_entry_gap_pct": self.max_entry_gap_pct,
            "skip_untradable_entry_bars": self.skip_untradable_entry_bars,
            "intraday_exit_priority": self.intraday_exit_priority.value,
        })

    def runtime_constant_projection(self) -> Mapping[str, Any]:
        """Immutable legacy-formal constants derived from this policy.

        2026-09-15：补入三个 enabled 开关。原因——这些常数会被
        `app/services/outcome_simulation.py`（实盘 outcome 结算路径）读取，
        而该路径原先只读数值、不读 enabled，导致"关掉止盈/止损/trailing"
        在实盘结算里被**静默忽略**（v3 出场策略发布时发现的真实缺口）。
        """

        return MappingProxyType({
            "ENTRY_LOOKAHEAD_DAYS": self.entry.max_entry_trade_days,
            "ENTRY_SLIPPAGE_BPS": self.slippage.entry_bps,
            "EXIT_SLIPPAGE_BPS": self.slippage.exit_bps,
            "STOP_LOSS_ENABLED": self.stop_loss.enabled,
            "DEFAULT_STOP_LOSS_PCT": -self.stop_loss.default_loss_pct,
            "MAX_STOP_LOSS_PCT": -self.stop_loss.maximum_loss_pct,
            "TAKE_PROFIT_ENABLED": self.take_profit.enabled,
            "DEFAULT_TAKE_PROFIT_PCT": self.take_profit.default_gain_pct,
            "MAX_TAKE_PROFIT_PCT": self.take_profit.maximum_gain_pct,
            "STOP_LOSS_GAP_CAP_PCT": -self.gap_risk.stop_gap_loss_cap_pct,
            "MAX_ENTRY_GAP_PCT": self.gap_risk.max_entry_gap_up_pct,
            "TRAILING_ENABLED": self.trailing.enabled,
            "TRAILING_STOP_ACTIVATE_PCT": self.trailing.activation_gain_pct,
            "TRAILING_STOP_CALLBACK_PCT": self.trailing.callback_pct,
            "TRAILING_STOP_MIN_LOCK_PCT": self.trailing.minimum_lock_gain_pct,
            "TRAILING_STOP_PROFIT_LOCK_RATIO": self.trailing.profit_lock_ratio,
        })


def render_execution_policy_markdown(policy: ExecutionPolicy) -> str:
    """Generate the human-readable execution-rule projection from policy data."""

    if not isinstance(policy, ExecutionPolicy):
        raise TypeError("policy must be an ExecutionPolicy")
    entry_gap = "disabled" if policy.gap_risk.max_entry_gap_up_pct is None else f"{policy.gap_risk.max_entry_gap_up_pct:g}%"
    # 2026-09-15：enabled=False 时必须显式写 disabled，否则文档会显示一个
    # 已经失效的阈值（v3 关掉止盈后曾出现"profit 6%"的误导性投影）。
    stop_txt = (
        f"{policy.stop_loss.default_loss_pct:g}% (maximum {policy.stop_loss.maximum_loss_pct:g}%)"
        if policy.stop_loss.enabled else "disabled"
    )
    profit_txt = (
        f"{policy.take_profit.default_gain_pct:g}% (maximum {policy.take_profit.maximum_gain_pct:g}%)"
        if policy.take_profit.enabled else "disabled"
    )
    trailing_txt = (
        f"`{policy.trailing.mode.value}`, activation {policy.trailing.activation_gain_pct:g}%, "
        f"callback {policy.trailing.callback_pct:g}%, minimum lock {policy.trailing.minimum_lock_gain_pct:g}%, "
        f"ratio {policy.trailing.profit_lock_ratio:g}"
        if policy.trailing.enabled else "disabled"
    )
    return "\n".join((
        f"# Execution Policy `{policy.policy_id}@{policy.version}`",
        "",
        f"- Schema: `{policy.schema_version}`; fingerprint: `{policy.fingerprint()}`",
        f"- Entry: `{policy.entry.policy_id}`, window {policy.entry.max_entry_trade_days} trading days, source `{policy.entry.price_source.value}`.",
        f"- Settlement/horizon: T+1 `{str(policy.t_plus_one).lower()}`, {policy.horizon_days} holding bars.",
        f"- Costs: commission {policy.fees.commission_rate_per_side:g} per side, exit stamp tax {policy.fees.stamp_tax_rate_on_exit:g}; slippage {policy.slippage.entry_bps:g}/{policy.slippage.exit_bps:g} bps.",
        f"- Board/liquidity: {policy.board_limits.main_board_pct:g}/{policy.board_limits.growth_board_pct:g}/{policy.board_limits.beijing_exchange_pct:g}% limits; one-word limit-up blocks entry and one-word limit-down defers exit.",
        f"- Position: default {policy.position.default_fraction:g}, high-score cap {policy.position.high_score_cap:g} from score {policy.position.high_score_threshold:g}, composition `{policy.position.cap_composition.value}`.",
        f"- Stops: loss {stop_txt}, profit {profit_txt}.",
        f"- Trailing: {trailing_txt}.",
        f"- Gap risk: max entry gap {entry_gap}, stop gap cap {policy.gap_risk.stop_gap_loss_cap_pct:g}%, same-bar priority `{policy.intraday_exit_priority.value}`.",
        "",
    ))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExecutionPolicyArtifactError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json_object(path: Path, context: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExecutionPolicyArtifactError(f"cannot read {context}: {path}") from exc
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExecutionPolicyArtifactError(f"{context} is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ExecutionPolicyArtifactError(f"{context} must be a JSON object")
    return payload


def _strict_keys(payload: Mapping[str, Any], expected: set[str], context: str) -> None:
    actual = set(payload)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        raise ExecutionPolicyArtifactError(f"{context} schema mismatch: {', '.join(details)}")


def _nested_value(cls: type[Any], payload: object, context: str) -> Any:
    if not isinstance(payload, dict):
        raise ExecutionPolicyArtifactError(f"{context} must be an object")
    field_map = {item.name: item for item in fields(cls)}
    unknown = sorted(set(payload) - set(field_map))
    if unknown:
        raise ExecutionPolicyArtifactError(f"{context} schema mismatch: unknown={unknown}")
    # Older artifacts may omit fields that later versions added with defaults
    # (e.g. the v2 position caps); required fields must still be present.
    missing = sorted(
        name
        for name, item in field_map.items()
        if name not in payload and item.default is MISSING and item.default_factory is MISSING
    )
    if missing:
        raise ExecutionPolicyArtifactError(f"{context} schema mismatch: missing={missing}")
    try:
        return cls(**payload)
    except (TypeError, ExecutionPolicyValidationError) as exc:
        raise ExecutionPolicyArtifactError(f"invalid {context}: {exc}") from exc


def execution_policy_from_payload(payload: Mapping[str, Any]) -> ExecutionPolicy:
    """Build and validate a policy from a complete canonical schema payload."""

    if not isinstance(payload, Mapping):
        raise ExecutionPolicyArtifactError("execution policy payload must be an object")
    expected = {item.name for item in fields(ExecutionPolicy)}
    _strict_keys(payload, expected, "execution policy")
    try:
        policy = ExecutionPolicy(
            schema_version=payload["schema_version"],
            policy_id=payload["policy_id"],
            version=payload["version"],
            entry=_nested_value(EntryWindowPolicy, payload["entry"], "entry"),
            t_plus_one=payload["t_plus_one"],
            horizon_days=payload["horizon_days"],
            fees=_nested_value(FeePolicy, payload["fees"], "fees"),
            slippage=_nested_value(SlippagePolicy, payload["slippage"], "slippage"),
            board_limits=_nested_value(BoardLimitPolicy, payload["board_limits"], "board_limits"),
            liquidity=_nested_value(LiquidityPolicy, payload["liquidity"], "liquidity"),
            position=_nested_value(PositionSizingPolicy, payload["position"], "position"),
            stop_loss=_nested_value(StopLossPolicy, payload["stop_loss"], "stop_loss"),
            take_profit=_nested_value(TakeProfitPolicy, payload["take_profit"], "take_profit"),
            trailing=_nested_value(TrailingPolicy, payload["trailing"], "trailing"),
            gap_risk=_nested_value(GapRiskPolicy, payload["gap_risk"], "gap_risk"),
            intraday_exit_priority=payload["intraday_exit_priority"],
        )
    except (TypeError, ExecutionPolicyValidationError) as exc:
        raise ExecutionPolicyArtifactError(f"invalid execution policy: {exc}") from exc
    if canonical_json(policy.canonical_payload()) != canonical_json(dict(payload)):
        raise ExecutionPolicyArtifactError("execution policy payload is not losslessly canonicalizable")
    return policy


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ExecutionPolicyArtifactError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def load_execution_policy_artifact(path: str | Path) -> ExecutionPolicy:
    """Load one versioned artifact and verify its declared canonical fingerprint."""

    artifact_path = Path(path)
    payload = _load_json_object(artifact_path, "execution policy artifact")
    _strict_keys(payload, {"artifact_schema_version", "policy_fingerprint", "policy"}, "execution policy artifact")
    if payload["artifact_schema_version"] != EXECUTION_POLICY_ARTIFACT_SCHEMA_VERSION:
        raise ExecutionPolicyArtifactError(
            f"unsupported artifact_schema_version: {payload['artifact_schema_version']}"
        )
    declared_fingerprint = _sha256(payload["policy_fingerprint"], "policy_fingerprint")
    policy_payload = payload["policy"]
    if not isinstance(policy_payload, dict):
        raise ExecutionPolicyArtifactError("policy must be an object")
    policy = execution_policy_from_payload(policy_payload)
    if policy.fingerprint() != declared_fingerprint:
        raise ExecutionPolicyArtifactError(
            f"policy fingerprint mismatch: declared={declared_fingerprint}, calculated={policy.fingerprint()}"
        )
    return policy


@dataclass(frozen=True, slots=True)
class ExecutionPolicyRegistry:
    """Immutable identity/fingerprint index loaded from an explicit manifest."""

    _by_identity: Mapping[tuple[str, str], ExecutionPolicy]
    _by_fingerprint: Mapping[str, ExecutionPolicy]
    _current_identity: tuple[str, str]

    def __post_init__(self) -> None:
        identity_copy = dict(self._by_identity)
        fingerprint_copy = dict(self._by_fingerprint)
        if not identity_copy:
            raise ExecutionPolicyArtifactError("execution policy registry cannot be empty")
        for identity, policy in identity_copy.items():
            if not isinstance(policy, ExecutionPolicy):
                raise ExecutionPolicyArtifactError(
                    "registry values must be ExecutionPolicy instances"
                )
            if identity != (policy.policy_id, policy.version):
                raise ExecutionPolicyArtifactError(
                    "execution policy identity index does not match policy content"
                )
        expected_fingerprints = {
            policy.fingerprint(): policy for policy in identity_copy.values()
        }
        if len(expected_fingerprints) != len(identity_copy):
            raise ExecutionPolicyArtifactError(
                "execution policy registry contains duplicate fingerprints"
            )
        if set(fingerprint_copy) != set(expected_fingerprints) or any(
            fingerprint_copy[fingerprint] != policy
            for fingerprint, policy in expected_fingerprints.items()
        ):
            raise ExecutionPolicyArtifactError(
                "execution policy fingerprint index does not match policy content"
            )
        if self._current_identity not in identity_copy:
            raise ExecutionPolicyArtifactError("current execution policy is not registered")
        object.__setattr__(self, "_by_identity", MappingProxyType(identity_copy))
        object.__setattr__(self, "_by_fingerprint", MappingProxyType(fingerprint_copy))

    @classmethod
    def build(
        cls,
        policies: tuple[ExecutionPolicy, ...] | list[ExecutionPolicy],
        *,
        current_policy_id: str,
        current_version: str,
    ) -> "ExecutionPolicyRegistry":
        by_identity: dict[tuple[str, str], ExecutionPolicy] = {}
        by_fingerprint: dict[str, ExecutionPolicy] = {}
        for policy in policies:
            if not isinstance(policy, ExecutionPolicy):
                raise ExecutionPolicyArtifactError("registry values must be ExecutionPolicy instances")
            identity = (policy.policy_id, policy.version)
            if identity in by_identity:
                raise ExecutionPolicyArtifactError(f"duplicate execution policy identity: {identity[0]}@{identity[1]}")
            policy_fingerprint = policy.fingerprint()
            if policy_fingerprint in by_fingerprint:
                other = by_fingerprint[policy_fingerprint]
                raise ExecutionPolicyArtifactError(
                    f"duplicate execution policy fingerprint: {other.policy_id}@{other.version} and {policy.policy_id}@{policy.version}"
                )
            by_identity[identity] = policy
            by_fingerprint[policy_fingerprint] = policy
        return cls(by_identity, by_fingerprint, (current_policy_id, current_version))

    @classmethod
    def from_manifest(cls, manifest_path: str | Path) -> "ExecutionPolicyRegistry":
        path = Path(manifest_path)
        payload = _load_json_object(path, "execution policy registry manifest")
        _strict_keys(payload, {"registry_schema_version", "current", "artifacts"}, "execution policy registry manifest")
        if payload["registry_schema_version"] != EXECUTION_POLICY_REGISTRY_SCHEMA_VERSION:
            raise ExecutionPolicyArtifactError(
                f"unsupported registry_schema_version: {payload['registry_schema_version']}"
            )
        current = payload["current"]
        if not isinstance(current, dict):
            raise ExecutionPolicyArtifactError("registry current must be an object")
        _strict_keys(current, {"policy_id", "version", "fingerprint"}, "registry current")
        current_policy_id = _identifier(current["policy_id"], "current.policy_id")
        current_version = _identifier(current["version"], "current.version")
        current_fingerprint = _sha256(current["fingerprint"], "current.fingerprint")

        artifact_specs = payload["artifacts"]
        if not isinstance(artifact_specs, list) or not artifact_specs:
            raise ExecutionPolicyArtifactError("registry artifacts must be a non-empty list")
        base = path.parent.resolve()
        policies: list[ExecutionPolicy] = []
        for index, spec in enumerate(artifact_specs):
            context = f"registry artifacts[{index}]"
            if not isinstance(spec, dict):
                raise ExecutionPolicyArtifactError(f"{context} must be an object")
            _strict_keys(spec, {"file", "policy_id", "version", "fingerprint"}, context)
            filename = _non_empty(spec["file"], f"{context}.file")
            candidate = (base / filename).resolve()
            if candidate == base or base not in candidate.parents:
                raise ExecutionPolicyArtifactError(f"{context}.file must stay inside the manifest directory")
            policy = load_execution_policy_artifact(candidate)
            expected_identity = (
                _identifier(spec["policy_id"], f"{context}.policy_id"),
                _identifier(spec["version"], f"{context}.version"),
            )
            expected_fingerprint = _sha256(spec["fingerprint"], f"{context}.fingerprint")
            if (policy.policy_id, policy.version) != expected_identity:
                raise ExecutionPolicyArtifactError(f"{context} identity does not match artifact")
            if policy.fingerprint() != expected_fingerprint:
                raise ExecutionPolicyArtifactError(f"{context} fingerprint does not match artifact")
            policies.append(policy)

        registry = cls.build(
            policies,
            current_policy_id=current_policy_id,
            current_version=current_version,
        )
        if registry.current.fingerprint() != current_fingerprint:
            raise ExecutionPolicyArtifactError("registry current fingerprint does not match current policy")
        return registry

    @property
    def current(self) -> ExecutionPolicy:
        return self._by_identity[self._current_identity]

    @property
    def current_identity(self) -> tuple[str, str]:
        return self._current_identity

    def get(self, policy_id: str, version: str) -> ExecutionPolicy:
        try:
            return self._by_identity[(policy_id, version)]
        except KeyError:
            raise ExecutionPolicyNotFoundError(f"unknown execution policy: {policy_id}@{version}") from None

    def get_by_fingerprint(self, policy_fingerprint: str) -> ExecutionPolicy:
        try:
            return self._by_fingerprint[policy_fingerprint]
        except KeyError:
            raise ExecutionPolicyNotFoundError(
                f"unknown execution policy fingerprint: {policy_fingerprint}"
            ) from None

    def identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._by_identity)

    def policies(self) -> tuple[ExecutionPolicy, ...]:
        return tuple(self._by_identity.values())

    def __len__(self) -> int:
        return len(self._by_identity)


_POLICY_ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "policies"
DEFAULT_EXECUTION_POLICY_REGISTRY = ExecutionPolicyRegistry.from_manifest(
    _POLICY_ARTIFACT_DIR / "execution_policy.registry.json"
)
DEFAULT_EXECUTION_POLICY = DEFAULT_EXECUTION_POLICY_REGISTRY.current


def execution_policy_from_config_projection(
    payload: Mapping[str, Any],
    *,
    base_policy: ExecutionPolicy = DEFAULT_EXECUTION_POLICY,
) -> ExecutionPolicy:
    """Resolve the legacy flat config adapter without losing policy identity.

    An unchanged projection resolves to the exact registered current object.
    Research-only overrides receive a deterministic derived identity/version
    bound to the base fingerprint and retain all nested semantics (including
    v2 cap composition).  This prevents flat adapters from silently relabeling
    changed content as the historical ``1.0.0`` policy.
    """
    if not isinstance(payload, Mapping):
        raise ExecutionPolicyValidationError(
            "execution policy config projection must be a mapping"
        )
    expected_keys = set(base_policy.config_projection())
    actual_keys = set(payload)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        unknown = sorted(actual_keys - expected_keys)
        raise ExecutionPolicyValidationError(
            "execution policy config projection schema mismatch: "
            f"missing={missing}, unknown={unknown}"
        )
    normalized = dict(payload)
    if canonical_json(normalized) == canonical_json(
        dict(base_policy.config_projection())
    ):
        return base_policy

    derivation_payload = {
        "base_fingerprint": base_policy.fingerprint(),
        "config_projection": normalized,
    }
    derivation_hash = sha256(
        canonical_json(derivation_payload).encode("utf-8")
    ).hexdigest()
    return ExecutionPolicy(
        schema_version=normalized["schema_version"],
        policy_id=f"{base_policy.policy_id}-derived",
        version=f"{base_policy.version}-cfg-{derivation_hash[:16]}",
        entry=base_policy.entry,
        t_plus_one=base_policy.t_plus_one,
        horizon_days=normalized["horizon_days"],
        fees=base_policy.fees,
        slippage=base_policy.slippage,
        board_limits=base_policy.board_limits,
        liquidity=base_policy.liquidity,
        position=base_policy.position,
        stop_loss=base_policy.stop_loss,
        take_profit=base_policy.take_profit,
        trailing=base_policy.trailing,
        gap_risk=base_policy.gap_risk,
        intraday_exit_priority=normalized["intraday_exit_priority"],
        entry_lookahead_days=normalized["entry_lookahead_days"],
        commission_rate=normalized["commission_rate"],
        stamp_tax_rate=normalized["stamp_tax_rate"],
        entry_slippage_bps=normalized["entry_slippage_bps"],
        exit_slippage_bps=normalized["exit_slippage_bps"],
        position_size=normalized["position_size"],
        stop_loss_pct=normalized["stop_loss_pct"],
        take_profit_pct=normalized["take_profit_pct"],
        max_entry_gap_pct=normalized["max_entry_gap_pct"],
        skip_untradable_entry_bars=normalized[
            "skip_untradable_entry_bars"
        ],
    )


__all__ = [
    "BoardLimitPolicy",
    "DEFAULT_EXECUTION_POLICY",
    "DEFAULT_EXECUTION_POLICY_ID",
    "DEFAULT_EXECUTION_POLICY_REGISTRY",
    "DEFAULT_EXECUTION_POLICY_VERSION",
    "EXECUTION_POLICY_ARTIFACT_SCHEMA_VERSION",
    "EXECUTION_POLICY_REGISTRY_SCHEMA_VERSION",
    "EXECUTION_POLICY_SCHEMA_VERSION",
    "EntryPriceSource",
    "EntryWindowPolicy",
    "ExecutionPolicy",
    "ExecutionPolicyArtifactError",
    "ExecutionPolicyError",
    "ExecutionPolicyNotFoundError",
    "ExecutionPolicyRegistry",
    "ExecutionPolicyValidationError",
    "FeePolicy",
    "GapRiskPolicy",
    "GapStopFillMode",
    "GapTakeProfitFillMode",
    "IntradayExitPriority",
    "LiquidityPolicy",
    "PositionCapComposition",
    "PositionSizingPolicy",
    "SlippagePolicy",
    "StopLossPolicy",
    "TakeProfitPolicy",
    "TrailingMode",
    "TrailingPolicy",
    "UntradableEntryAction",
    "UntradableExitAction",
    "execution_policy_from_config_projection",
    "execution_policy_from_payload",
    "load_execution_policy_artifact",
    "render_execution_policy_markdown",
]
