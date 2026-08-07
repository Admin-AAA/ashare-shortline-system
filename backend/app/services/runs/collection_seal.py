"""Immutable recommendation-collection seals and recovery verification."""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tables import Recommendation, RecommendationCollectionSeal
from app.services.runs.contracts import RunErrorCode


COLLECTION_SEAL_SCHEMA_VERSION = 1
_COLLECTION_COLUMNS = (
    Recommendation.recommendation_id,
    Recommendation.run_id,
    Recommendation.trade_date,
    Recommendation.recommended_at,
    Recommendation.code,
    Recommendation.name,
    Recommendation.industry,
    Recommendation.recommend_price,
    Recommendation.bucket,
    Recommendation.final_score,
    Recommendation.consensus_score,
    Recommendation.setup_type,
    Recommendation.entry_rule,
    Recommendation.invalid_rule,
    Recommendation.rationale,
    Recommendation.risk_tags_json,
    Recommendation.info_tags_json,
    Recommendation.recent_repeat,
    Recommendation.feature_snapshot_json,
    Recommendation.market_context_json,
    Recommendation.skill_breakdown_json,
    Recommendation.agent_summary_json,
)
_COLLECTION_FIELD_NAMES = tuple(column.key for column in _COLLECTION_COLUMNS)


class RecommendationCollectionSealError(RuntimeError):
    """A conflicting persisted collection cannot be sealed or recovered."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RecommendationCollectionSnapshot:
    row_count: int
    collection_digest: str


@dataclass(frozen=True, slots=True)
class CollectionSealVerification:
    valid: bool
    block_code: str | None
    block_reason: str | None
    observed_row_count: int | None = None
    observed_collection_digest: str | None = None
    seal: RecommendationCollectionSeal | None = None


def collection_side_effect_key(run_id: str, collection_digest: str) -> str:
    normalized_run_id = str(run_id).strip()
    normalized_digest = str(collection_digest).strip().lower()
    if not normalized_run_id or len(normalized_digest) != 64:
        raise ValueError("collection side-effect key requires run_id and SHA-256 digest")
    return (
        f"recommendation-seal:v{COLLECTION_SEAL_SCHEMA_VERSION}:"
        f"{normalized_run_id}:{normalized_digest}"
    )


def recommendation_collection_snapshot(
    db: Session,
    run_id: str,
    *,
    lock: bool = False,
) -> RecommendationCollectionSnapshot:
    """Hash the explicit V1 persistence-owned fields in recommendation-ID order."""
    statement = (
        select(*_COLLECTION_COLUMNS)
        .where(Recommendation.run_id == run_id)
        .order_by(Recommendation.recommendation_id.asc())
    )
    if lock:
        statement = statement.with_for_update()
    rows = db.execute(statement).all()
    payload = {
        "schema_version": COLLECTION_SEAL_SCHEMA_VERSION,
        "run_id": str(run_id),
        "recommendations": [
            {
                field_name: _canonical_value(value)
                for field_name, value in zip(_COLLECTION_FIELD_NAMES, row, strict=True)
            }
            for row in rows
        ],
    }
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RecommendationCollectionSealError(
            RunErrorCode.RESUME_SEAL_DIGEST_MISMATCH.value,
            "recommendation collection contains a non-canonical value",
        ) from exc
    return RecommendationCollectionSnapshot(
        row_count=len(rows),
        collection_digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def seal_recommendation_collection(
    db: Session,
    run_id: str,
    *,
    sealed_at: datetime | None = None,
) -> RecommendationCollectionSeal:
    """Insert one immutable seal, accepting only an identical pre-existing fact."""
    snapshot = recommendation_collection_snapshot(db, run_id, lock=True)
    side_effect_key = collection_side_effect_key(run_id, snapshot.collection_digest)
    existing = db.get(RecommendationCollectionSeal, run_id)
    if existing is not None:
        _require_matching_seal(existing, snapshot, side_effect_key)
        return existing

    row = RecommendationCollectionSeal(
        run_id=run_id,
        schema_version=COLLECTION_SEAL_SCHEMA_VERSION,
        recommendation_row_count=snapshot.row_count,
        collection_digest=snapshot.collection_digest,
        checkpoint_side_effect_key=side_effect_key,
        sealed_at=sealed_at or datetime.now(timezone.utc).replace(tzinfo=None),
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        db.expire_all()
        concurrent = db.get(RecommendationCollectionSeal, run_id)
        if concurrent is None:
            raise
        _require_matching_seal(concurrent, snapshot, side_effect_key)
        return concurrent
    return row


def verify_recommendation_collection_seal(
    db: Session,
    run_id: str,
    *,
    expected_side_effect_key: str | None,
    lock: bool = False,
) -> CollectionSealVerification:
    """Recompute the durable collection and fail closed on every mismatch."""
    seal_statement = select(RecommendationCollectionSeal).where(
        RecommendationCollectionSeal.run_id == run_id
    )
    if lock:
        seal_statement = seal_statement.with_for_update()
    seal = db.scalar(seal_statement)
    if seal is None:
        return _blocked(
            RunErrorCode.RESUME_SEAL_NOT_FOUND,
            "运行没有 recommendation collection seal，不能恢复",
        )
    if seal.schema_version != COLLECTION_SEAL_SCHEMA_VERSION:
        return _blocked(
            RunErrorCode.RESUME_SEAL_SCHEMA_UNSUPPORTED,
            "recommendation collection seal schema 不受支持",
            seal=seal,
        )
    if not expected_side_effect_key or not hmac.compare_digest(
        str(seal.checkpoint_side_effect_key),
        str(expected_side_effect_key),
    ):
        return _blocked(
            RunErrorCode.RESUME_SEAL_KEY_MISMATCH,
            "persistence checkpoint 与 recommendation collection seal 不一致",
            seal=seal,
        )

    snapshot = recommendation_collection_snapshot(db, run_id, lock=lock)
    if snapshot.row_count != int(seal.recommendation_row_count):
        return _blocked(
            RunErrorCode.RESUME_SEAL_ROW_COUNT_MISMATCH,
            "recommendation collection 行数与 seal 不一致",
            snapshot=snapshot,
            seal=seal,
        )
    if not hmac.compare_digest(
        snapshot.collection_digest,
        str(seal.collection_digest).lower(),
    ):
        return _blocked(
            RunErrorCode.RESUME_SEAL_DIGEST_MISMATCH,
            "recommendation collection 摘要与 seal 不一致",
            snapshot=snapshot,
            seal=seal,
        )
    return CollectionSealVerification(
        valid=True,
        block_code=None,
        block_reason=None,
        observed_row_count=snapshot.row_count,
        observed_collection_digest=snapshot.collection_digest,
        seal=seal,
    )


def _require_matching_seal(
    seal: RecommendationCollectionSeal,
    snapshot: RecommendationCollectionSnapshot,
    side_effect_key: str,
) -> None:
    if seal.schema_version != COLLECTION_SEAL_SCHEMA_VERSION:
        raise RecommendationCollectionSealError(
            RunErrorCode.RESUME_SEAL_SCHEMA_UNSUPPORTED.value,
            "existing recommendation collection seal schema is unsupported",
        )
    if int(seal.recommendation_row_count) != snapshot.row_count:
        raise RecommendationCollectionSealError(
            RunErrorCode.RESUME_SEAL_ROW_COUNT_MISMATCH.value,
            "existing recommendation collection seal has a different row count",
        )
    if not hmac.compare_digest(str(seal.collection_digest).lower(), snapshot.collection_digest):
        raise RecommendationCollectionSealError(
            RunErrorCode.RESUME_SEAL_DIGEST_MISMATCH.value,
            "existing recommendation collection seal has a different digest",
        )
    if not hmac.compare_digest(str(seal.checkpoint_side_effect_key), side_effect_key):
        raise RecommendationCollectionSealError(
            RunErrorCode.RESUME_SEAL_KEY_MISMATCH.value,
            "existing recommendation collection seal has a different side-effect key",
        )


def _blocked(
    code: RunErrorCode,
    reason: str,
    *,
    snapshot: RecommendationCollectionSnapshot | None = None,
    seal: RecommendationCollectionSeal | None = None,
) -> CollectionSealVerification:
    return CollectionSealVerification(
        valid=False,
        block_code=code.value,
        block_reason=reason,
        observed_row_count=snapshot.row_count if snapshot is not None else None,
        observed_collection_digest=(
            snapshot.collection_digest if snapshot is not None else None
        ),
        seal=seal,
    )


def _canonical_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(timespec="microseconds")
    return value


__all__ = [
    "COLLECTION_SEAL_SCHEMA_VERSION",
    "CollectionSealVerification",
    "RecommendationCollectionSealError",
    "RecommendationCollectionSnapshot",
    "collection_side_effect_key",
    "recommendation_collection_snapshot",
    "seal_recommendation_collection",
    "verify_recommendation_collection_seal",
]
