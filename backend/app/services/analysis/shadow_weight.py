"""Bayesian strategy-weight suggestions that never enter the active pipeline.

The service deliberately has two seams:

* :func:`calculate_strategy_weight_shadow` is a pure calculator.  It accepts
  recommendation-time attribution rows and is therefore easy to test without a
  database.
* :func:`create_strategy_weight_shadow` is the read-only production adapter.
  It selects only complete ``formal``/``LIVE_FORWARD`` runs and writes an
  immutable ``strategy_weight_shadow`` governance object.  It does not import
  the active strategy loader, aggregator, preset registry, or calibrator.

A shadow result is evidence, not a configuration update.  The effective
multiplier is intentionally reported separately from ``suggested_weight`` and
is never consumed by recommendation execution.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.governance.envelope import ImmutableEnvelope
from app.governance.store import GovernanceObjectStore
from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun
from app.services.evidence.provenance_writer import resolve_code_revision
from app.services.evidence.selectors import live_forward_run_conditions

# The policy is intentionally constants, rather than settings.  A shadow result
# must be reproducible and cannot be changed by an active calibration setting.
ALPHA = 15.0
BETA = 15.0
MIN_SAMPLES = 30
MIN_MULTIPLIER = 0.83
MAX_MULTIPLIER = 1.20
CREDIBLE_LEVEL = 0.95
SHADOW_SCHEMA_VERSION = 1
SHADOW_KIND = "strategy_weight_shadow"
_EVALUATED_LABELS = frozenset({"success", "failed"})


class ShadowWeightError(ValueError):
    """Stable, fail-closed error for malformed shadow inputs."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _finite_positive(value: Any, *, default: float | None = None) -> float | None:
    if value is None and default is not None:
        value = default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _normalise_label(value: Any) -> str:
    label = str(value or "").strip().lower()
    # ``risk_hit`` is a legacy spelling of a failed current projection.  No
    # other label is promoted to an evaluated observation.
    return "failed" if label == "risk_hit" else label


def _date_key(value: Any) -> str:
    value = str(value or "").strip()
    # A recommendation trade date is server-owned.  Do not infer one from a
    # mutable timestamp or from an arbitrary string.
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return ""
    return value


def _month_key(value: str) -> str:
    return value[:7] if len(value) == 10 else ""


def _beta_log_normalizer(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Evaluate the continued fraction used by the regularized beta CDF."""
    # Numerical Recipes' betacf, with a bounded iteration count.  The
    # posterior parameters here are >= 15, so convergence is very fast.
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    tiny = 1.0e-300
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3.0e-14:
            break
    return h


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_bt = a * math.log(x) + b * math.log1p(-x) - _beta_log_normalizer(a, b)
    bt = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _beta_continued_fraction(a, b, x) / a
    return 1.0 - bt * _beta_continued_fraction(b, a, 1.0 - x) / b


def beta_quantile(probability: float, a: float, b: float) -> float:
    """Return an equal-tail Beta quantile without an optional scipy dependency."""
    if not 0.0 <= probability <= 1.0:
        raise ShadowWeightError("invalid_probability", "probability must be in [0, 1]")
    if a <= 0 or b <= 0:
        raise ShadowWeightError("invalid_beta_parameters", "beta parameters must be positive")
    if probability == 0.0:
        return 0.0
    if probability == 1.0:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(90):
        middle = (low + high) / 2.0
        if _regularized_beta(middle, a, b) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _credible_interval(success: int, evaluated: int) -> tuple[float, float]:
    posterior_a = success + ALPHA
    posterior_b = evaluated - success + BETA
    tail = (1.0 - CREDIBLE_LEVEL) / 2.0
    return (
        beta_quantile(tail, posterior_a, posterior_b),
        beta_quantile(1.0 - tail, posterior_a, posterior_b),
    )


def _evidence_strength(evaluated: int) -> str:
    if evaluated < MIN_SAMPLES:
        return "insufficient"
    if evaluated < 100:
        return "weak"
    if evaluated < 300:
        return "moderate"
    return "strong"


def _attribution_rows(
    rows: Iterable[Any],
) -> tuple[list[dict[str, Any]], dict[str, set[str]], dict[str, list[float]], int]:
    """Normalise pure input rows and collect exposure/base diagnostics.

    Rows represent one recommendation and may contain one or more attribution
    records in ``strategy_attribution``/``skill_breakdown``.  A recommendation
    is counted at most once for each strategy, avoiding multi-signal duplication.
    Invalid strategy IDs/dates are rejected rather than guessed.
    """
    observations: list[dict[str, Any]] = []
    exposures: dict[str, set[str]] = defaultdict(set)
    bases: dict[str, list[float]] = defaultdict(list)
    invalid = 0
    seen: set[tuple[str, str]] = set()

    for row in rows:
        recommendation_id = str(_value(row, "recommendation_id", "") or "").strip()
        strategy_id = str(_value(row, "strategy_id", "") or "").strip()
        trade_date = _date_key(_value(row, "trade_date"))
        if not recommendation_id or not strategy_id or not trade_date:
            invalid += 1
            continue
        dedupe_key = (recommendation_id, strategy_id)
        if dedupe_key in seen:
            # Duplicate attribution rows for the same recommendation are not
            # independent evidence.  Ignore exact duplicates; conflicting
            # observations are rejected below by keeping only the first row.
            continue
        seen.add(dedupe_key)
        base_weight = _finite_positive(
            _value(row, "base_weight", _value(row, "weight", 1.0)),
            default=1.0,
        )
        if base_weight is None:
            invalid += 1
            continue
        exposures[strategy_id].add(recommendation_id)
        bases[strategy_id].append(base_weight)
        label = _normalise_label(_value(row, "outcome_label", _value(row, "outcome")))
        if label not in _EVALUATED_LABELS:
            continue
        observations.append(
            {
                "recommendation_id": recommendation_id,
                "strategy_id": strategy_id,
                "outcome_label": label,
                "trade_date": trade_date,
                "recommended_at": str(_value(row, "recommended_at", "") or ""),
                "base_weight": base_weight,
            }
        )
    return observations, exposures, bases, invalid


def calculate_strategy_weight_shadow(
    rows: Iterable[Any],
    *,
    eligible_recommendation_count: int | None = None,
    exposure_by_strategy: Mapping[str, int | set[str]] | None = None,
    base_weight_by_strategy: Mapping[str, float] | None = None,
    invalid_input_count: int = 0,
) -> dict[str, Any]:
    """Calculate a deterministic shadow payload from recommendation evidence.

    ``rows`` must already be restricted to complete formal LIVE_FORWARD current
    outcomes by the caller when used outside :func:`create_strategy_weight_shadow`.
    Each row is one recommendation/strategy attribution pair.  Pending,
    provisional, missed and malformed outcomes are deliberately not evaluated.
    """
    rows_list = list(rows)
    observations, derived_exposures, derived_bases, invalid = _attribution_rows(rows_list)
    exposures: dict[str, int] = {}
    for strategy_id, values in derived_exposures.items():
        exposures[strategy_id] = len(values)
    if exposure_by_strategy is not None:
        for strategy_id, value in exposure_by_strategy.items():
            if isinstance(value, set):
                exposures[str(strategy_id)] = len(value)
            else:
                try:
                    exposures[str(strategy_id)] = max(0, int(value))
                except (TypeError, ValueError):
                    raise ShadowWeightError(
                        "invalid_exposure_diagnostic",
                        f"exposure count for {strategy_id!r} is not an integer",
                    ) from None
    bases: dict[str, float] = {
        strategy_id: values[-1]
        for strategy_id, values in derived_bases.items()
        if values
    }
    if base_weight_by_strategy is not None:
        for strategy_id, value in base_weight_by_strategy.items():
            parsed = _finite_positive(value)
            if parsed is None:
                raise ShadowWeightError(
                    "invalid_base_weight",
                    f"base weight for {strategy_id!r} must be finite and positive",
                )
            bases[str(strategy_id)] = parsed

    strategy_ids = sorted(set(exposures) | set(bases) | {str(_value(r, "strategy_id", "")) for r in observations})
    eligible_count = (
        max(0, int(eligible_recommendation_count))
        if eligible_recommendation_count is not None
        else len({str(_value(r, "recommendation_id", "")) for r in rows_list if _value(r, "recommendation_id")})
    )
    results: list[dict[str, Any]] = []
    total_evaluated = 0
    total_success = 0
    for strategy_id in strategy_ids:
        strategy_rows = [r for r in observations if str(_value(r, "strategy_id", "")) == strategy_id]
        # A second defensive dedupe protects callers that provide rows with
        # different ordering or bypass the normalizer's exact duplicate path.
        unique: dict[str, Any] = {}
        for row in strategy_rows:
            rid = str(_value(row, "recommendation_id", "") or "")
            if rid and rid not in unique:
                unique[rid] = row
        evaluated_rows = list(unique.values())
        success = sum(1 for row in evaluated_rows if _normalise_label(_value(row, "outcome_label", _value(row, "outcome"))) == "success")
        evaluated = len(evaluated_rows)
        posterior_mean = (success + ALPHA) / (evaluated + ALPHA + BETA)
        interval_low, interval_high = _credible_interval(success, evaluated)
        raw_multiplier = posterior_mean / 0.5
        effective_multiplier = (
            1.0
            if evaluated < MIN_SAMPLES
            else min(MAX_MULTIPLIER, max(MIN_MULTIPLIER, raw_multiplier))
        )
        base_weight = bases.get(strategy_id, 1.0)
        exposure_count = exposures.get(strategy_id, len(unique))
        distinct_days = sorted({str(_value(row, "trade_date", "")) for row in evaluated_rows if _value(row, "trade_date")})
        distinct_months = sorted({_month_key(day) for day in distinct_days if _month_key(day)})
        diagnostics = {
            "eligible_recommendation_count": eligible_count,
            "exposure_count": exposure_count,
            "exposure_rate": (exposure_count / eligible_count) if eligible_count else 0.0,
            "evaluated_exposure_rate": (evaluated / exposure_count) if exposure_count else 0.0,
            "success_count": success,
            "failed_count": evaluated - success,
            "pending_or_unresolved_count": max(0, exposure_count - evaluated),
        }
        results.append(
            {
                "strategy_id": strategy_id,
                "base_weight": base_weight,
                "suggested_weight": base_weight * effective_multiplier,
                "raw_multiplier": raw_multiplier,
                "effective_multiplier": effective_multiplier,
                "posterior_mean": posterior_mean,
                "posterior": posterior_mean,
                "credible_interval": {
                    "level": CREDIBLE_LEVEL,
                    "lower": interval_low,
                    "upper": interval_high,
                },
                "credible_interval_95": [interval_low, interval_high],
                "credible_interval_lower": interval_low,
                "credible_interval_upper": interval_high,
                "evidence_strength": _evidence_strength(evaluated),
                "status": "insufficient_evidence" if evaluated < MIN_SAMPLES else "sufficient_evidence",
                "success_count": success,
                "evaluated_count": evaluated,
                "distinct_days": len(distinct_days),
                "distinct_months": len(distinct_months),
                "exposure_diagnostics": diagnostics,
                "active_config_unchanged": True,
            }
        )
        total_evaluated += evaluated
        total_success += success

    return {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "object_type": SHADOW_KIND,
        "policy": {
            "alpha": ALPHA,
            "beta": BETA,
            "prior_mean": ALPHA / (ALPHA + BETA),
            "min_samples": MIN_SAMPLES,
            "clamp": {"min": MIN_MULTIPLIER, "max": MAX_MULTIPLIER},
            "credible_level": CREDIBLE_LEVEL,
            "formula": "posterior=(success+alpha)/(evaluated+alpha+beta); raw=posterior/0.5",
            "activation": "shadow_only",
        },
        "source_scope": {
            "run_mode": "formal",
            "evidence_class": "LIVE_FORWARD",
            "evidence_class_value": "live_forward",
            "provenance": "complete",
            "outcome_projection": "recommendation_outcomes_current",
            "attribution": "recommendations.skill_breakdown_json_at_recommendation_time",
        },
        "diagnostics": {
            "eligible_recommendation_count": eligible_count,
            "evaluated_count": total_evaluated,
            "success_count": total_success,
            "invalid_input_count": int(invalid_input_count) + invalid,
            "strategy_count": len(results),
            "active_configuration_modified": False,
        },
        "strategies": results,
    }


def _decode_skill_breakdown(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ShadowWeightError("invalid_strategy_attribution", "skill_breakdown_json is invalid JSON") from exc
    if value is None:
        return []
    if not isinstance(value, list):
        raise ShadowWeightError("invalid_strategy_attribution", "skill_breakdown_json must be a list")
    return [item for item in value if isinstance(item, Mapping)]


def _load_shadow_inputs(db: Session) -> tuple[list[dict[str, Any]], dict[str, set[str]], dict[str, list[float]], int, int]:
    """Read only canonical formal LIVE_FORWARD current recommendation outcomes."""
    from app.services.evidence.validation import canonical_live_forward_run_ids

    live_run_ids = canonical_live_forward_run_ids(db)
    if not live_run_ids:
        return [], {}, {}, 0, 0
    stmt = (
        select(Recommendation, RecommendationOutcome)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .outerjoin(
            RecommendationOutcome,
            RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
        )
        .where(
            *_official_live_conditions(),
            RecommendationRun.run_id.in_(live_run_ids),
        )
        .order_by(Recommendation.recommended_at.asc(), Recommendation.recommendation_id.asc())
    )
    rows = db.execute(stmt).all()
    observations: list[dict[str, Any]] = []
    exposures: dict[str, set[str]] = defaultdict(set)
    bases: dict[str, list[float]] = defaultdict(list)
    invalid = 0
    for rec, outcome in rows:
        rec_id = str(rec.recommendation_id or "")
        trade_date = _date_key(rec.trade_date)
        if not rec_id or not trade_date:
            invalid += 1
            continue
        try:
            signals = _decode_skill_breakdown(rec.skill_breakdown_json)
        except ShadowWeightError:
            invalid += 1
            continue
        # One recommendation can carry multiple strategies, but an accidental
        # duplicate strategy signal is not extra evidence.
        seen_for_rec: set[str] = set()
        for signal in signals:
            strategy_id = str(signal.get("strategy_id") or "").strip()
            if not strategy_id or strategy_id in seen_for_rec:
                if not strategy_id:
                    invalid += 1
                continue
            seen_for_rec.add(strategy_id)
            base_weight = _finite_positive(signal.get("weight"), default=1.0)
            if base_weight is None:
                invalid += 1
                continue
            exposures[strategy_id].add(rec_id)
            bases[strategy_id].append(base_weight)
            observations.append(
                {
                    "recommendation_id": rec_id,
                    "strategy_id": strategy_id,
                    "outcome_label": _normalise_label(getattr(outcome, "outcome_label", "")),
                    "trade_date": trade_date,
                    "recommended_at": str(getattr(rec, "recommended_at", "") or ""),
                    "base_weight": base_weight,
                }
            )
    return observations, exposures, bases, len(rows), invalid


def _official_live_conditions() -> tuple:
    # Keep the run-mode/status/recommendation identity requirements local to
    # this read adapter.  The provenance core itself remains the canonical
    # selector shared across all official metrics.
    from app.services.evidence.selectors import OFFICIAL_RECOMMENDATION_PREFIX

    return (
        RecommendationRun.run_mode == "formal",
        RecommendationRun.status == "completed",
        *live_forward_run_conditions(),
        Recommendation.recommendation_id.like(f"{OFFICIAL_RECOMMENDATION_PREFIX}%"),
    )


def build_strategy_weight_shadow_payload(db: Session) -> dict[str, Any]:
    observations, exposures, bases, eligible_count, invalid = _load_shadow_inputs(db)
    # The DB adapter has already parsed recommendation-time attribution, so pass
    # normalised rows with explicit exposure/base diagnostics to the pure core.
    return calculate_strategy_weight_shadow(
        observations,
        eligible_recommendation_count=eligible_count,
        exposure_by_strategy=exposures,
        base_weight_by_strategy={key: values[-1] for key, values in bases.items() if values},
        invalid_input_count=invalid,
    )


def _put_idempotent(
    store: GovernanceObjectStore,
    *,
    payload: Mapping[str, Any],
    created_by: str,
    code_revision: str,
) -> ImmutableEnvelope:
    # ``created_at`` is intentionally not part of the semantic key.  Re-running
    # against an unchanged current projection returns the existing immutable
    # object instead of creating an ever-growing set of identical snapshots.
    for existing in store.iter_kind(SHADOW_KIND):
        if (
            existing.payload == payload
            and existing.created_by == created_by
            and existing.code_revision == code_revision
            and existing.schema_version == SHADOW_SCHEMA_VERSION
        ):
            return existing
    return store.put(
        kind=SHADOW_KIND,
        payload=payload,
        created_at=datetime.now(timezone.utc),
        created_by=created_by,
        code_revision=code_revision,
        schema_version=SHADOW_SCHEMA_VERSION,
    )


def create_strategy_weight_shadow(
    db: Session,
    *,
    store: GovernanceObjectStore | None = None,
    created_by: str = "strategy_weight_shadow_service",
    code_revision: str | None = None,
) -> ImmutableEnvelope:
    """Create or return the immutable shadow object; never activate its advice."""
    if not isinstance(created_by, str) or not created_by.strip():
        raise ShadowWeightError("invalid_actor", "created_by is required")
    revision = str(code_revision or resolve_code_revision() or "").strip().lower()
    if not revision or revision == "unknown":
        raise ShadowWeightError("missing_code_revision", "cannot persist shadow evidence without code revision")
    payload = build_strategy_weight_shadow_payload(db)
    return _put_idempotent(
        store or GovernanceObjectStore(),
        payload=payload,
        created_by=created_by,
        code_revision=revision,
    )


__all__ = [
    "ALPHA",
    "BETA",
    "CREDIBLE_LEVEL",
    "MAX_MULTIPLIER",
    "MIN_MULTIPLIER",
    "MIN_SAMPLES",
    "SHADOW_KIND",
    "ShadowWeightError",
    "beta_quantile",
    "build_strategy_weight_shadow_payload",
    "calculate_strategy_weight_shadow",
    "create_strategy_weight_shadow",
]
