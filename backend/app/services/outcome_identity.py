"""Deterministic identity inputs for canonical outcome settlement.

Only business inputs that can change the canonical simulation belong here.  ORM
primary keys, writer actors and write timestamps are deliberately excluded.
"""
from __future__ import annotations

import json
from bisect import bisect_left
from datetime import date, datetime
from typing import Any, Iterable

from app.engine.canonical import canonical_fingerprint
from app.engine.trading_costs import ADDITIONAL_COST_PCT


OUTCOME_LABEL_POLICY_VERSION = "outcome-label-v2-net-cost"


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _datetime_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def recommendation_identity_payload(rec: Any) -> dict[str, Any]:
    """Return the recommendation facts consumed by outcome simulation."""
    return {
        "code": str(getattr(rec, "code", "") or ""),
        "feature_snapshot": _json_value(getattr(rec, "feature_snapshot_json", None), {}),
        "final_score": float(getattr(rec, "final_score", 0.0) or 0.0),
        "recommend_price": float(getattr(rec, "recommend_price", 0.0) or 0.0),
        "recommended_at": _datetime_value(getattr(rec, "recommended_at", None)),
        "recommendation_id": str(getattr(rec, "recommendation_id", "") or ""),
        "trade_date": str(getattr(rec, "trade_date", "") or ""),
    }


def recommendation_identity_hash(rec: Any, execution_state: Any | None = None) -> str:
    return canonical_fingerprint(
        {
            "execution_state": execution_state_identity_payload(execution_state),
            "recommendation": recommendation_identity_payload(rec),
        }
    )


def execution_state_identity_payload(state: Any | None) -> dict[str, Any]:
    """Return only fields read by ``apply_execution_outcome_policy``."""
    if state is None:
        return {
            "cancelled_before_entry": False,
            "ever_buy_confirmed": False,
            "latest_decision_code": "",
        }
    return {
        "cancelled_before_entry": bool(getattr(state, "cancelled_before_entry", False)),
        "ever_buy_confirmed": bool(getattr(state, "ever_buy_confirmed", False)),
        "latest_decision_code": str(getattr(state, "latest_decision_code", "") or ""),
    }


def bounded_outcome_bars(
    rec: Any,
    bars: Iterable[Any],
    horizon_days: int,
    *,
    entry_lookahead_days: int = 2,
) -> list[Any]:
    """Return the exact finite bar window consumed by outcome simulation."""
    if int(horizon_days) < 1:
        raise ValueError("horizon_days must be positive")
    ordered = sorted(
        (bar for bar in bars if str(getattr(bar, "code", "")) == str(getattr(rec, "code", ""))),
        key=lambda bar: str(getattr(bar, "trade_date", "")),
    )
    signal_date = str(getattr(rec, "trade_date", "") or "")
    dates = [str(getattr(bar, "trade_date", "")) for bar in ordered]
    signal_index = bisect_left(dates, signal_date)
    has_signal_bar = signal_index < len(ordered) and dates[signal_index] == signal_date
    window_start = signal_index if has_signal_bar else signal_index
    future_start = signal_index + 1 if has_signal_bar else signal_index
    lookahead = max(1, min(int(entry_lookahead_days), int(horizon_days)))
    exclusive_end = min(len(ordered), future_start + lookahead + int(horizon_days) - 1)
    return ordered[window_start:exclusive_end]


def outcome_market_artifact_payload(
    rec: Any,
    bars: Iterable[Any],
    horizon_days: int,
    *,
    entry_lookahead_days: int = 2,
) -> dict[str, Any]:
    """Build the exact bounded market input used by ``_simulate_outcome``."""
    bounded = bounded_outcome_bars(
        rec,
        bars,
        horizon_days,
        entry_lookahead_days=entry_lookahead_days,
    )
    signal_date = str(getattr(rec, "trade_date", "") or "")
    def bar_payload(bar: Any) -> dict[str, Any]:
        return {
            "amount": float(getattr(bar, "amount", 0.0) or 0.0),
            "close": float(getattr(bar, "close", 0.0) or 0.0),
            "code": str(getattr(bar, "code", "") or ""),
            "high": float(getattr(bar, "high", 0.0) or 0.0),
            "low": float(getattr(bar, "low", 0.0) or 0.0),
            "open": float(getattr(bar, "open", 0.0) or 0.0),
            "pct_chg": float(getattr(bar, "pct_chg", 0.0) or 0.0),
            "trade_date": str(getattr(bar, "trade_date", "") or ""),
            "volume": float(getattr(bar, "volume", 0.0) or 0.0),
        }

    return {
        "code": str(getattr(rec, "code", "") or ""),
        "horizon_days": int(horizon_days),
        "bars": [bar_payload(bar) for bar in bounded],
        "signal_date": signal_date,
    }


def outcome_market_artifact_hash(payload: dict[str, Any]) -> str:
    return canonical_fingerprint(payload)


def label_policy_fingerprint() -> str:
    return canonical_fingerprint(
        {
            "additional_cost_pct": ADDITIONAL_COST_PCT,
            "policy_version": OUTCOME_LABEL_POLICY_VERSION,
            "success_rule": "net_return_pct > 0 and not hit_invalid",
            "provisional_label": "horizon_close_t1_locked",
        }
    )


__all__ = [
    "OUTCOME_LABEL_POLICY_VERSION",
    "bounded_outcome_bars",
    "execution_state_identity_payload",
    "label_policy_fingerprint",
    "outcome_market_artifact_hash",
    "outcome_market_artifact_payload",
    "recommendation_identity_hash",
    "recommendation_identity_payload",
]
