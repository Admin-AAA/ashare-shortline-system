from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.factor_analysis import (
    CrossSectionalObservation,
    FactorAnalysisRequest,
)
from app.engine.evaluator import ALLOWED_NAMES
from app.models.tables import (
    Recommendation,
    RecommendationOutcome,
    RecommendationOutcomeVersion,
    RecommendationRun,
)
from app.services.evidence.validation import canonical_live_forward_run_ids
from app.services.official_sample_scope import official_sample_conditions
from app.services.quality_outcome import OutcomeVersionRecord


_CLOSED_OUTCOME_LABELS = frozenset({"success", "failed"})


class FactorObservationRepositoryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FactorObservationRecord:
    trade_date: str
    security_code: str
    recommendation_id: str
    feature_snapshot_json: str
    run_evidence_identity_hash: str | None
    outcome_label: str | None
    outcome_horizon_days: int | None
    calculation_version_id: str | None
    current_calculation_fingerprint: str | None
    projection_revision: int | None
    projected_at_present: bool
    version_id: str | None
    version_recommendation_id: str | None
    version_horizon_days: int | None
    result_json: str | None
    calculation_fingerprint: str | None
    result_hash: str | None
    recommendation_identity_hash: str | None
    market_snapshot_id: str | None
    market_artifact_hash: str | None
    execution_policy_fingerprint: str | None
    calculation_code_revision: str | None
    label_policy_fingerprint: str | None
    calculation_schema_version: int | None
    validity_status: str | None


class FactorObservationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def load(self, request: FactorAnalysisRequest) -> tuple[CrossSectionalObservation, ...]:
        if request.universe_scope != "selected":
            raise FactorObservationRepositoryError(
                "database factor repository supports only selected recommendations"
            )
        if request.evaluation_scope != "live_forward":
            raise FactorObservationRepositoryError(
                "database factor repository supports only live_forward evidence"
            )

        live_run_ids = canonical_live_forward_run_ids(self._session)
        if not live_run_ids:
            return ()
        statement = (
            select(
                Recommendation,
                RecommendationOutcome,
                RecommendationOutcomeVersion,
                RecommendationRun,
            )
            .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
            .outerjoin(
                RecommendationOutcome,
                RecommendationOutcome.recommendation_id
                == Recommendation.recommendation_id,
            )
            .outerjoin(
                RecommendationOutcomeVersion,
                RecommendationOutcomeVersion.version_id
                == RecommendationOutcome.calculation_version_id,
            )
            .where(
                *official_sample_conditions(),
                RecommendationRun.run_id.in_(live_run_ids),
                Recommendation.trade_date >= request.start_date,
                Recommendation.trade_date <= request.end_date,
            )
            .order_by(
                Recommendation.trade_date,
                Recommendation.code,
                Recommendation.recommendation_id,
            )
        )
        observations: list[CrossSectionalObservation] = []
        seen_recommendations: set[str] = set()
        for recommendation, outcome, version, run in self._session.execute(statement):
            recommendation_id = str(recommendation.recommendation_id)
            if recommendation_id in seen_recommendations:
                raise FactorObservationRepositoryError(
                    f"duplicate selected recommendation row: {recommendation_id}"
                )
            seen_recommendations.add(recommendation_id)
            record = _record_from_models(recommendation, outcome, version, run)
            observation = map_factor_observation(request, record)
            if observation is not None:
                observations.append(observation)
        return tuple(observations)


def map_factor_observation(
    request: FactorAnalysisRequest,
    record: FactorObservationRecord,
) -> CrossSectionalObservation | None:
    if request.factor_name not in ALLOWED_NAMES:
        raise FactorObservationRepositoryError(
            f"factor field is not allowlisted: {request.factor_name}"
        )
    if record.outcome_label not in _CLOSED_OUTCOME_LABELS:
        return None
    if record.outcome_horizon_days != request.horizon_days:
        raise FactorObservationRepositoryError(
            "closed outcome horizon does not match factor analysis request"
        )
    if not _has_complete_projection_identity(record):
        return None
    _validate_projection_consistency(record)

    version = OutcomeVersionRecord(
        version_id=record.version_id or "",
        recommendation_id=record.version_recommendation_id or "",
        calculation_fingerprint=record.calculation_fingerprint,
        result_hash=record.result_hash,
        result_json=record.result_json or "",
        recommendation_identity_hash=record.recommendation_identity_hash,
        market_snapshot_id=record.market_snapshot_id,
        market_artifact_hash=record.market_artifact_hash,
        execution_policy_fingerprint=record.execution_policy_fingerprint,
        calculation_code_revision=record.calculation_code_revision,
        label_policy_fingerprint=record.label_policy_fingerprint,
        calculation_schema_version=record.calculation_schema_version,
        validity_status=record.validity_status or "",
    )
    if not version.is_complete_valid:
        raise FactorObservationRepositoryError(
            "closed outcome version result hash or calculation identity conflicts"
        )
    if record.version_recommendation_id != record.recommendation_id:
        raise FactorObservationRepositoryError(
            "outcome version recommendation identity does not match selected recommendation"
        )
    if record.version_horizon_days != request.horizon_days:
        raise FactorObservationRepositoryError(
            "outcome version horizon does not match factor analysis request"
        )

    result = _json_object(record.result_json, "outcome result")
    if result.get("outcome_label") != record.outcome_label:
        raise FactorObservationRepositoryError(
            "outcome result label conflicts with current projection"
        )
    if "net_return_pct" not in result:
        raise FactorObservationRepositoryError(
            "outcome result must explicitly contain canonical net_return_pct"
        )
    future_return = _finite_number(result["net_return_pct"], "net_return_pct")

    feature_snapshot = _json_object(record.feature_snapshot_json, "feature snapshot")
    factor_value: float | None = None
    if request.factor_name in feature_snapshot and feature_snapshot[request.factor_name] is not None:
        factor_value = _finite_number(
            feature_snapshot[request.factor_name],
            f"factor {request.factor_name}",
        )

    source_artifact_ids = tuple(
        item
        for item in (
            record.run_evidence_identity_hash,
            record.calculation_fingerprint,
            record.result_hash,
        )
        if item
    )
    if len(source_artifact_ids) != 3:
        raise FactorObservationRepositoryError(
            "factor observation provenance fingerprints are incomplete"
        )
    return CrossSectionalObservation(
        trade_date=record.trade_date,
        security_code=record.security_code,
        factor_name=request.factor_name,
        factor_value=factor_value,
        future_return_pct=future_return,
        universe_scope="selected",
        evaluation_scope="live_forward",
        recommendation_id=record.recommendation_id,
        outcome_identity=record.calculation_fingerprint,
        source_artifact_ids=source_artifact_ids,
    )


def _record_from_models(
    recommendation: Recommendation,
    outcome: RecommendationOutcome | None,
    version: RecommendationOutcomeVersion | None,
    run: RecommendationRun,
) -> FactorObservationRecord:
    return FactorObservationRecord(
        trade_date=str(recommendation.trade_date),
        security_code=str(recommendation.code),
        recommendation_id=str(recommendation.recommendation_id),
        feature_snapshot_json=str(recommendation.feature_snapshot_json or ""),
        run_evidence_identity_hash=run.evidence_identity_hash,
        outcome_label=outcome.outcome_label if outcome is not None else None,
        outcome_horizon_days=outcome.horizon_days if outcome is not None else None,
        calculation_version_id=(
            outcome.calculation_version_id if outcome is not None else None
        ),
        current_calculation_fingerprint=(
            outcome.current_calculation_fingerprint if outcome is not None else None
        ),
        projection_revision=outcome.projection_revision if outcome is not None else None,
        projected_at_present=bool(outcome is not None and outcome.projected_at is not None),
        version_id=version.version_id if version is not None else None,
        version_recommendation_id=(
            version.recommendation_id if version is not None else None
        ),
        version_horizon_days=version.horizon_days if version is not None else None,
        result_json=version.result_json if version is not None else None,
        calculation_fingerprint=(
            version.calculation_fingerprint if version is not None else None
        ),
        result_hash=version.result_hash if version is not None else None,
        recommendation_identity_hash=(
            version.recommendation_identity_hash if version is not None else None
        ),
        market_snapshot_id=version.market_snapshot_id if version is not None else None,
        market_artifact_hash=(
            version.market_artifact_hash if version is not None else None
        ),
        execution_policy_fingerprint=(
            version.execution_policy_fingerprint if version is not None else None
        ),
        calculation_code_revision=(
            version.calculation_code_revision if version is not None else None
        ),
        label_policy_fingerprint=(
            version.label_policy_fingerprint if version is not None else None
        ),
        calculation_schema_version=(
            version.calculation_schema_version if version is not None else None
        ),
        validity_status=version.validity_status if version is not None else None,
    )


def _has_complete_projection_identity(record: FactorObservationRecord) -> bool:
    required = (
        record.calculation_version_id,
        record.current_calculation_fingerprint,
        record.version_id,
        record.version_recommendation_id,
        record.result_json,
        record.calculation_fingerprint,
        record.result_hash,
        record.recommendation_identity_hash,
        record.market_snapshot_id,
        record.market_artifact_hash,
        record.execution_policy_fingerprint,
        record.calculation_code_revision,
        record.label_policy_fingerprint,
        record.calculation_schema_version,
        record.validity_status,
    )
    return bool(
        all(value is not None and value != "" for value in required)
        and not isinstance(record.projection_revision, bool)
        and record.projection_revision is not None
        and record.projection_revision >= 1
        and record.projected_at_present
        and record.validity_status == "VALID"
    )


def _validate_projection_consistency(record: FactorObservationRecord) -> None:
    if (
        record.version_id != record.calculation_version_id
        or record.current_calculation_fingerprint != record.calculation_fingerprint
    ):
        raise FactorObservationRepositoryError(
            "closed outcome current projection conflicts with version identity"
        )


def _json_object(raw: str | None, field_name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw or "")
    except (TypeError, json.JSONDecodeError) as exc:
        raise FactorObservationRepositoryError(f"{field_name} is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise FactorObservationRepositoryError(f"{field_name} must be a JSON object")
    return value


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FactorObservationRepositoryError(f"{field_name} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise FactorObservationRepositoryError(f"{field_name} must be finite")
    return normalized


__all__ = [
    "FactorObservationRecord",
    "FactorObservationRepository",
    "FactorObservationRepositoryError",
    "map_factor_observation",
]
