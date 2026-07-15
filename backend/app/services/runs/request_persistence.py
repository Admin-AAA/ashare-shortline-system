"""Durable RunRequest persistence, backfill, and worker cutover guards.

The request columns are an additive expand.  ``params_json`` remains a legacy
projection for historical queries, but it is never used to recover a worker once
that worker has crossed the canonical-request cutover.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isclose
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.tables import RecommendationRun
from app.services.runs.contracts import RunRequest, parse_run_request


RECOVERABLE_RUN_STATUSES = frozenset({"queued", "running", "interrupted"})
_KEY_COLUMNS = (
    "run_mode",
    "preset_id",
    "run_name",
    "market_scope",
    "market_scope_value",
    "risk_template",
    "score_line",
    "output_count",
)
_FLOAT_COLUMNS = frozenset({"score_line"})


class RunRequestPersistenceError(RuntimeError):
    """A canonical request cannot safely be persisted or used for recovery."""


class RunRequestCutoverError(RunRequestPersistenceError):
    """A row is not safe for the worker's canonical-request read path."""


@dataclass(frozen=True, slots=True)
class RunPartitionCounts:
    """Stable row and run-mode counts captured around the backfill/cutover."""

    total_rows: int
    by_run_mode: tuple[tuple[str, int], ...]

    @classmethod
    def from_counts(cls, total_rows: int, by_run_mode: Counter[str]) -> "RunPartitionCounts":
        return cls(
            total_rows=int(total_rows),
            by_run_mode=tuple(sorted((str(mode), int(count)) for mode, count in by_run_mode.items())),
        )

    @property
    def mode_counts(self) -> dict[str, int]:
        return dict(self.by_run_mode)


@dataclass(frozen=True, slots=True)
class RunRequestBackfillReport:
    """Auditable result of one or more bounded backfill batches."""

    scanned_rows: int
    backfilled_rows: int
    skipped_invalid_rows: int
    skipped_mismatch_rows: int
    invalid_rows: tuple[tuple[str, str], ...]
    mismatch_rows: tuple[tuple[str, tuple[str, ...]], ...]
    initial_counts: RunPartitionCounts
    final_counts: RunPartitionCounts
    remaining_unresolved_rows: int

    @property
    def counts_unchanged(self) -> bool:
        return self.initial_counts == self.final_counts

    @property
    def cutover_ready(self) -> bool:
        return self.remaining_unresolved_rows == 0 and not self.invalid_rows and not self.mismatch_rows


def capture_run_partition_counts(session: Session) -> RunPartitionCounts:
    """Capture total rows and every run-mode partition without mutating data."""
    total = int(session.scalar(select(func.count()).select_from(RecommendationRun)) or 0)
    grouped = session.execute(
        select(RecommendationRun.run_mode, func.count())
        .select_from(RecommendationRun)
        .group_by(RecommendationRun.run_mode)
    ).all()
    counts = Counter({str(mode): int(count) for mode, count in grouped})
    return RunPartitionCounts.from_counts(total, counts)


def verify_run_partition_counts(
    session: Session,
    expected: RunPartitionCounts,
) -> RunPartitionCounts:
    """Verify that backfill did not add/remove rows or alter mode partitions."""
    actual = capture_run_partition_counts(session)
    if actual != expected:
        raise RunRequestPersistenceError(
            "recommendation_runs row/mode counts changed during request backfill: "
            f"expected={expected} actual={actual}"
        )
    return actual


def request_column_mismatches(row: RecommendationRun, request: RunRequest) -> tuple[str, ...]:
    """Return independent indexed-column mismatches for a parsed request.

    These columns are intentionally checked before assigning ``request_schema_version``.
    A canonical JSON document that disagrees with an indexed value is not a safe
    worker input, even when its JSON syntax and RunRequest schema are valid.
    """
    expected: dict[str, Any] = {
        "run_mode": request.run_mode.value,
        "preset_id": request.preset_id,
        "run_name": request.run_name,
        "market_scope": request.market_scope,
        "market_scope_value": request.market_scope_value,
        "risk_template": request.risk_template,
        "score_line": request.score_line,
        "output_count": request.output_count,
    }
    mismatches: list[str] = []
    for column in _KEY_COLUMNS:
        actual = getattr(row, column, None)
        wanted = expected[column]
        if column in _FLOAT_COLUMNS:
            try:
                equal = isclose(float(actual), float(wanted), rel_tol=0.0, abs_tol=1e-9)
            except (TypeError, ValueError):
                equal = False
        else:
            equal = actual == wanted
        if not equal:
            mismatches.append(f"{column}: row={actual!r}, request={wanted!r}")
    return tuple(mismatches)


def assert_run_request_index_consistency(row: RecommendationRun, request: RunRequest) -> None:
    """Reject a write when indexed request projections drift from canonical JSON.

    New lifecycle writers invoke this immediately before flushing a row.  It makes
    ``run_mode`` (and the other independently queried request fields) an explicit
    write-time invariant instead of relying only on a later worker read to detect
    a partial or stale projection.
    """
    mismatches = request_column_mismatches(row, request)
    if mismatches:
        raise RunRequestPersistenceError(
            f"run {getattr(row, 'run_id', '<unknown>')} request/indexed-column mismatch before persistence: "
            + "; ".join(mismatches)
        )


def _parse_request_source(raw: Any, *, source: str) -> RunRequest:
    if raw is None or raw == "":
        raise RunRequestPersistenceError(f"{source} is empty")
    try:
        return parse_run_request(str(raw))
    except (TypeError, ValueError) as exc:
        raise RunRequestPersistenceError(f"{source} is not a valid RunRequest: {exc}") from exc


def _request_from_legacy_row(row: RecommendationRun) -> RunRequest:
    """Parse old params first, with a partial-write repair fallback."""
    errors: list[str] = []
    for source, raw in (("params_json", getattr(row, "params_json", None)), ("request_json", getattr(row, "request_json", None))):
        try:
            return _parse_request_source(raw, source=source)
        except RunRequestPersistenceError as exc:
            errors.append(str(exc))
    raise RunRequestPersistenceError("; ".join(errors) or "row has no request payload")


def load_canonical_run_request(row: RecommendationRun) -> RunRequest:
    """Load and validate the request used by a cutover worker.

    This function deliberately has no ``params_json`` fallback.  Rows that are not
    valid for the cutover must be repaired/backfilled or explicitly rejected before
    a worker is started.
    """
    raw = getattr(row, "request_json", None)
    schema_version = getattr(row, "request_schema_version", None)
    if raw in (None, "") or schema_version is None:
        raise RunRequestCutoverError(
            f"run {getattr(row, 'run_id', '<unknown>')} has no canonical request; backfill is required"
        )
    try:
        request = parse_run_request(str(raw))
    except (TypeError, ValueError) as exc:
        raise RunRequestCutoverError(
            f"run {getattr(row, 'run_id', '<unknown>')} canonical request is invalid: {exc}"
        ) from exc
    try:
        persisted_schema_version = int(schema_version)
    except (TypeError, ValueError) as exc:
        raise RunRequestCutoverError(
            f"run {getattr(row, 'run_id', '<unknown>')} request schema version is invalid"
        ) from exc
    if persisted_schema_version != request.schema_version:
        raise RunRequestCutoverError(
            f"run {getattr(row, 'run_id', '<unknown>')} request schema version mismatch"
        )
    mismatches = request_column_mismatches(row, request)
    if mismatches:
        raise RunRequestCutoverError(
            f"run {getattr(row, 'run_id', '<unknown>')} request/indexed-column mismatch: "
            + "; ".join(mismatches)
        )
    return request


def _unresolved_statement():
    return select(RecommendationRun).where(
        or_(
            RecommendationRun.request_schema_version.is_(None),
            RecommendationRun.request_json.is_(None),
        )
    )


def backfill_canonical_run_requests(
    session: Session,
    *,
    batch_size: int = 500,
    max_batches: int | None = None,
    commit_each_batch: bool = True,
) -> RunRequestBackfillReport:
    """Backfill legacy ``params_json`` in bounded, repeatable batches.

    Only rows whose payload parses as V1 and agrees with all independent key columns
    receive both ``request_json`` and ``request_schema_version``.  Invalid or
    mismatched rows are left untouched and returned in the report for remediation;
    ``params_json`` is never deleted or rewritten.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if max_batches is not None and max_batches < 1:
        raise ValueError("max_batches must be positive when provided")

    initial_counts = capture_run_partition_counts(session)
    scanned = backfilled = skipped_invalid = skipped_mismatch = 0
    invalid_rows: list[tuple[str, str]] = []
    mismatch_rows: list[tuple[str, tuple[str, ...]]] = []
    last_run_id: str | None = None
    batches = 0

    while max_batches is None or batches < max_batches:
        statement = _unresolved_statement()
        if last_run_id is not None:
            statement = statement.where(RecommendationRun.run_id > last_run_id)
        statement = statement.order_by(RecommendationRun.run_id).limit(batch_size)
        rows = list(session.scalars(statement).all())
        if not rows:
            break
        batches += 1
        scanned += len(rows)
        last_run_id = str(rows[-1].run_id)
        for row in rows:
            run_id = str(row.run_id)
            try:
                request = _request_from_legacy_row(row)
            except RunRequestPersistenceError as exc:
                skipped_invalid += 1
                invalid_rows.append((run_id, str(exc)))
                continue
            mismatches = request_column_mismatches(row, request)
            if mismatches:
                skipped_mismatch += 1
                mismatch_rows.append((run_id, mismatches))
                continue
            # Assign the pair together only after parse and consistency checks pass.
            row.request_json = request.to_canonical_json()
            row.request_schema_version = request.schema_version
            backfilled += 1
        try:
            if commit_each_batch:
                session.commit()
            else:
                session.flush()
        except Exception:
            session.rollback()
            raise

    final_counts = capture_run_partition_counts(session)
    remaining = int(
        session.scalar(
            select(func.count())
            .select_from(RecommendationRun)
            .where(
                or_(
                    RecommendationRun.request_schema_version.is_(None),
                    RecommendationRun.request_json.is_(None),
                )
            )
        )
        or 0
    )
    return RunRequestBackfillReport(
        scanned_rows=scanned,
        backfilled_rows=backfilled,
        skipped_invalid_rows=skipped_invalid,
        skipped_mismatch_rows=skipped_mismatch,
        invalid_rows=tuple(invalid_rows),
        mismatch_rows=tuple(mismatch_rows),
        initial_counts=initial_counts,
        final_counts=final_counts,
        remaining_unresolved_rows=remaining,
    )


def verify_run_request_cutover(
    session: Session,
    *,
    expected_counts: RunPartitionCounts | None = None,
) -> RunPartitionCounts:
    """Verify all queued/running/interrupted rows before worker cutover."""
    actual = capture_run_partition_counts(session)
    if expected_counts is not None and actual != expected_counts:
        raise RunRequestPersistenceError(
            "worker cutover row/mode counts changed: "
            f"expected={expected_counts} actual={actual}"
        )
    rows = session.scalars(
        select(RecommendationRun).where(RecommendationRun.status.in_(RECOVERABLE_RUN_STATUSES))
    ).all()
    failures: list[str] = []
    for row in rows:
        try:
            load_canonical_run_request(row)
        except RunRequestPersistenceError as exc:
            failures.append(str(exc))
    if failures:
        preview = "; ".join(failures[:8])
        suffix = " ..." if len(failures) > 8 else ""
        raise RunRequestPersistenceError(
            f"worker cutover blocked by {len(failures)} unrecoverable active run(s): {preview}{suffix}"
        )
    return actual


__all__ = [
    "RECOVERABLE_RUN_STATUSES",
    "RunPartitionCounts",
    "RunRequestBackfillReport",
    "RunRequestCutoverError",
    "RunRequestPersistenceError",
    "assert_run_request_index_consistency",
    "backfill_canonical_run_requests",
    "capture_run_partition_counts",
    "load_canonical_run_request",
    "request_column_mismatches",
    "verify_run_partition_counts",
    "verify_run_request_cutover",
]
