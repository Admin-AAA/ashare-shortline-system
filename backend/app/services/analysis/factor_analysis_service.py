from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from app.domain.calculation import CalculationIdentity
from app.domain.factor_analysis import (
    CrossSectionalObservation,
    FactorAnalysisRequest,
    FactorAnalysisResult,
    evaluate_factor_cross_section,
)
from app.engine.canonical import canonicalize
from app.governance import (
    GovernanceEventLog,
    GovernanceObjectStore,
    ImmutableEnvelope,
)
from app.governance.locking import file_lock


FACTOR_ANALYSIS_KIND = "factor_analysis"
FACTOR_ANALYSIS_COMPLETED_EVENT = "factor_analysis_completed"


class FactorAnalysisServiceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FactorAnalysisArtifact:
    calculation_identity: CalculationIdentity
    result: FactorAnalysisResult
    object_id: str
    event_object_id: str
    evidence_uri: str
    reused: bool


class FactorAnalysisService:
    def __init__(self, store: GovernanceObjectStore) -> None:
        self._store = store
        self._events = GovernanceEventLog(store)

    def analyze(
        self,
        request: FactorAnalysisRequest,
        observations: Sequence[CrossSectionalObservation],
        *,
        actor: str,
        code_revision: str,
        recorded_at: datetime,
    ) -> FactorAnalysisArtifact:
        normalized = tuple(observations)
        result = evaluate_factor_cross_section(request, normalized)
        identity = CalculationIdentity(
            calculation_kind=FACTOR_ANALYSIS_KIND,
            schema_version=result.schema_version,
            request_fingerprint=result.request_fingerprint,
            input_artifact_fingerprints=(result.observation_fingerprint,),
            code_revision=code_revision,
        )
        payload = {
            "calculation_fingerprint": identity.calculation_fingerprint,
            "calculation_identity": identity.canonical_payload(),
            "request": request.canonical_payload(),
            "result": result.canonical_payload(),
            "source_artifact_ids": sorted(
                {
                    source_id
                    for observation in normalized
                    for source_id in observation.source_artifact_ids
                }
            ),
        }

        with file_lock(self._store.root / ".factor_analysis.lock"):
            envelope, reused = self._put_or_reuse(
                payload=payload,
                actor=actor,
                code_revision=code_revision,
                recorded_at=recorded_at,
                calculation_fingerprint=identity.calculation_fingerprint,
                result_hash=result.result_hash,
            )
            event = self._append_or_reuse_event(
                envelope=envelope,
                identity=identity,
                result=result,
                actor=actor,
                code_revision=code_revision,
                recorded_at=recorded_at,
            )

        return FactorAnalysisArtifact(
            calculation_identity=identity,
            result=result,
            object_id=envelope.object_id,
            event_object_id=event.object_id,
            evidence_uri=self._store.evidence_uri_for(envelope.object_id),
            reused=reused,
        )

    def _put_or_reuse(
        self,
        *,
        payload: dict[str, object],
        actor: str,
        code_revision: str,
        recorded_at: datetime,
        calculation_fingerprint: str,
        result_hash: str,
    ) -> tuple[ImmutableEnvelope, bool]:
        matches = [
            item
            for item in self._store.iter_kind(FACTOR_ANALYSIS_KIND)
            if item.payload.get("calculation_fingerprint") == calculation_fingerprint
        ]
        if len(matches) > 1:
            raise FactorAnalysisServiceError(
                "multiple factor analysis objects share one calculation identity"
            )
        if matches:
            existing = matches[0]
            stored_result = existing.payload.get("result")
            if not isinstance(stored_result, dict):
                raise FactorAnalysisServiceError(
                    "stored factor analysis result payload is malformed"
                )
            if stored_result.get("result_hash") != result_hash:
                raise FactorAnalysisServiceError(
                    "factor analysis calculation identity conflicts with stored result"
                )
            if canonicalize(existing.payload) != canonicalize(payload):
                raise FactorAnalysisServiceError(
                    "factor analysis calculation identity conflicts with stored payload"
                )
            return existing, True
        return (
            self._store.put(
                kind=FACTOR_ANALYSIS_KIND,
                payload=payload,
                created_at=recorded_at,
                created_by=actor,
                code_revision=code_revision,
            ),
            False,
        )

    def _append_or_reuse_event(
        self,
        *,
        envelope: ImmutableEnvelope,
        identity: CalculationIdentity,
        result: FactorAnalysisResult,
        actor: str,
        code_revision: str,
        recorded_at: datetime,
    ) -> ImmutableEnvelope:
        matches = []
        for event in self._events.iter_events():
            if event.payload.get("event_type") != FACTOR_ANALYSIS_COMPLETED_EVENT:
                continue
            event_payload = event.payload.get("payload")
            if (
                isinstance(event_payload, dict)
                and event_payload.get("calculation_fingerprint")
                == identity.calculation_fingerprint
            ):
                matches.append(event)
        if len(matches) > 1:
            raise FactorAnalysisServiceError(
                "multiple completion events share one factor analysis identity"
            )
        if matches:
            existing = matches[0]
            event_payload = existing.payload.get("payload")
            if not isinstance(event_payload, dict) or (
                event_payload.get("object_id") != envelope.object_id
                or event_payload.get("result_hash") != result.result_hash
            ):
                raise FactorAnalysisServiceError(
                    "factor analysis completion event conflicts with stored object"
                )
            return existing
        return self._events.append(
            event_type=FACTOR_ANALYSIS_COMPLETED_EVENT,
            actor=actor,
            code_revision=code_revision,
            recorded_at=recorded_at,
            payload={
                "calculation_fingerprint": identity.calculation_fingerprint,
                "evidence_status": result.diagnostics.evidence_status,
                "object_id": envelope.object_id,
                "observation_count": result.diagnostics.observation_count,
                "result_hash": result.result_hash,
            },
            referenced_object_ids=(envelope.object_id,),
        )


__all__ = [
    "FACTOR_ANALYSIS_COMPLETED_EVENT",
    "FACTOR_ANALYSIS_KIND",
    "FactorAnalysisArtifact",
    "FactorAnalysisService",
    "FactorAnalysisServiceError",
]
