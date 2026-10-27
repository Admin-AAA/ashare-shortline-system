"""Fail-closed validation of persisted recommendation evidence identities."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.models.tables import RecommendationRun
from app.services.evidence.identity import (
    EvidenceClass,
    has_canonical_persisted_identity,
)
from app.services.evidence.reconstruction_batch import (
    ReconstructionCohortView,
    latest_validated_reconstruction_cohorts,
    merge_reconstruction_cohorts,
)
from app.services.evidence.selectors import (
    live_forward_run_conditions,
)


_IDENTITY_COLUMNS = (
    RecommendationRun.run_id,
    RecommendationRun.evidence_class,
    RecommendationRun.actual_generated_at,
    RecommendationRun.trade_date,
    RecommendationRun.data_as_of,
    RecommendationRun.source_snapshot_id,
    RecommendationRun.evidence_identity_hash,
    RecommendationRun.strategy_policy_fingerprint,
    RecommendationRun.execution_policy_fingerprint,
    RecommendationRun.code_revision,
    RecommendationRun.reconstruction_batch_id,
    RecommendationRun.provenance_schema_version,
)


def canonical_historical_reconstruction_cohort(
    db: Session,
    *,
    trade_date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> ReconstructionCohortView | None:
    """Return the merged reconstruction cohort for Metrics.

    Same population as ``canonical_historical_reconstruction_run_ids``: the latest
    validated batch of every strategy (run_name / attempt-tag ignored), merged.
    A single strategy yields its own cohort unchanged (same seal).
    """

    return merge_reconstruction_cohorts(
        latest_validated_reconstruction_cohorts(
            db,
            trade_date=trade_date,
            date_from=date_from,
            date_to=date_to,
        )
    )


def canonical_historical_reconstruction_run_ids(
    db: Session,
    *,
    trade_date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[str, ...]:
    """Return runs published in the latest validated reconstruction cohort.

    Reconstruction evidence reaches business consumers (dashboard, history,
    bootstrap, single-stock snapshots) only after its batch manifest is
    complete, sealed and re-verified; an identity-canonical run without a
    published batch stays invisible.
    """

    if trade_date is not None and (date_from is not None or date_to is not None):
        raise ValueError("trade_date cannot be combined with a date range")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("date_from must not be later than date_to")
    # 短线回放按策略分批：每个策略（不看 run_name/attempt-tag）取最新已验证 cohort，再取并集。
    cohorts = latest_validated_reconstruction_cohorts(
        db,
        trade_date=trade_date,
        date_from=date_from,
        date_to=date_to,
    )
    if not cohorts:
        return ()
    # A validated cohort holds exactly one run per expected trade date, aligned
    # positionally with ``expected_trade_dates`` (``_validate_runs`` enforces
    # uniqueness and ordering).  Narrow the returned ids to the requested window.
    matched: list[str] = []
    for cohort in cohorts:
        for run_id, run_date in zip(
            cohort.run_ids,
            cohort.expected_trade_dates,
        ):
            if trade_date is not None:
                if run_date == trade_date:
                    matched.append(run_id)
                continue
            if date_from is not None and run_date < date_from:
                continue
            if date_to is not None and run_date > date_to:
                continue
            matched.append(run_id)
    return tuple(matched)



def canonical_live_forward_run_ids(
    db: Session,
    *,
    trade_date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[str, ...]:
    """Return completed LIVE runs whose persisted hash is canonical."""

    if trade_date is not None and (date_from is not None or date_to is not None):
        raise ValueError("trade_date cannot be combined with a date range")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("date_from must not be later than date_to")
    statement = select(RecommendationRun).where(
        RecommendationRun.run_mode == "formal",
        RecommendationRun.status == "completed",
        *live_forward_run_conditions(),
    ).options(load_only(*_IDENTITY_COLUMNS))
    if trade_date is not None:
        statement = statement.where(RecommendationRun.trade_date == trade_date)
    else:
        if date_from is not None:
            statement = statement.where(RecommendationRun.trade_date >= date_from)
        if date_to is not None:
            statement = statement.where(RecommendationRun.trade_date <= date_to)
    candidates = db.scalars(statement).all()
    return tuple(
        str(run.run_id)
        for run in candidates
        if has_canonical_persisted_identity(
            run,
            expected_class=EvidenceClass.LIVE_FORWARD,
        )
    )


def catchup_formal_run_ids(
    db: Session,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[str, ...]:
    """Formal runs generated after midnight but before the next session opens.

    A post-close run that is caught up early the next morning (e.g. 06:42 CST)
    fails the same-business-day LIVE_FORWARD check and is classified
    UNVERIFIED.  Its picks are still point-in-time valid for the next session,
    so the user-facing history page shows them; KPI/evidence selectors keep
    using ``canonical_live_forward_run_ids`` and are unaffected.
    """

    from datetime import date as _date, timedelta, timezone

    from app.services.midterm_live_seal import generated_before_entry_open

    statement = select(RecommendationRun).where(
        RecommendationRun.run_mode == "formal",
        RecommendationRun.status == "completed",
        RecommendationRun.evidence_class == "unverified",
        RecommendationRun.data_as_of == RecommendationRun.trade_date,
        RecommendationRun.source_snapshot_id.is_not(None),
        RecommendationRun.actual_generated_at.is_not(None),
    ).options(load_only(*_IDENTITY_COLUMNS))
    if date_from is not None:
        statement = statement.where(RecommendationRun.trade_date >= date_from)
    if date_to is not None:
        statement = statement.where(RecommendationRun.trade_date <= date_to)
    cst = timezone(timedelta(hours=8))
    matched: list[str] = []
    for run in db.scalars(statement).all():
        generated = run.actual_generated_at
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        local = generated.astimezone(cst)
        try:
            signal = _date.fromisoformat(str(run.trade_date))
        except ValueError:
            continue
        # 与实盘判定同一函数（入场日 09:15 前）；signal < 生成日：信号日当天生成的已是 LIVE，不在这里重复
        if local.date() > signal and generated_before_entry_open(str(run.trade_date), generated):
            matched.append(str(run.run_id))
    return tuple(matched)


def latest_canonical_live_forward_run(db: Session) -> RecommendationRun | None:
    """Return the newest completed formal LIVE run with an exact identity hash."""

    candidates = db.scalars(
        select(RecommendationRun)
        .where(
            RecommendationRun.run_mode == "formal",
            RecommendationRun.status == "completed",
            *live_forward_run_conditions(),
        )
        .options(load_only(*_IDENTITY_COLUMNS))
        .order_by(
            RecommendationRun.started_at.desc(),
            RecommendationRun.run_id.desc(),
        )
    ).all()
    return next(
        (
            run
            for run in candidates
            if has_canonical_persisted_identity(
                run,
                expected_class=EvidenceClass.LIVE_FORWARD,
            )
        ),
        None,
    )


__all__ = [
    "canonical_historical_reconstruction_cohort",
    "canonical_historical_reconstruction_run_ids",
    "canonical_live_forward_run_ids",
    "latest_canonical_live_forward_run",
]
