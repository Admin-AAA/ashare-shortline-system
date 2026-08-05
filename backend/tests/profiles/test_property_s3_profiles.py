"""S3 Layer-1 profile, ledger, package, and champion properties.

All fixtures are pure in-memory domain values.  They never import a production
session, Prefect, network provider, or mutable projection.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from hypothesis import given, settings, strategies as st

from app.services.evidence.identity import (
    EvidenceClass,
    RecommendationEvidenceIdentity,
    RunSource,
)
from app.services.release_governance import (
    ActiveStrategy,
    CanonicalProfileContext,
    CanonicalProfileIdentity,
    ChampionReason,
    ChampionRecord,
    ChampionState,
    CompleteProvenance,
    ConfidenceStatus,
    EvidenceObservation,
    LedgerRecordType,
    PopulationScope,
    ProfileStatus,
    ReleaseGovernanceError,
    TrueForwardLedger,
    build_canonical_profiles,
    build_evidence_package,
    evaluate_champion,
)


_POLICY = "a" * 64
_EXECUTION = "b" * 64
_REVISION = "c" * 40
_SIGNAL = date(2026, 7, 1)


def _context(*, revision: str = _REVISION) -> CanonicalProfileContext:
    return CanonicalProfileContext(
        source_snapshot_id="snapshot-s3",
        snapshot_schema_version="market-snapshot-v1",
        execution_policy_fingerprint=_EXECUTION,
        window_start="2026-07-01",
        window_end="2026-07-31",
        actual_signal_dates=tuple(
            (_SIGNAL + timedelta(days=offset)).isoformat() for offset in range(10)
        ),
        code_revision=revision,
    )


def _strategy(strategy_id: str = "strategy-0") -> ActiveStrategy:
    return ActiveStrategy(strategy_id, "v1", _POLICY)


def _profile_identity(
    strategy_id: str = "strategy-0", *, revision: str = _REVISION
) -> CanonicalProfileIdentity:
    return CanonicalProfileIdentity.from_strategy(
        _strategy(strategy_id), _context(revision=revision)
    )


def _provenance(
    evidence_class: EvidenceClass,
    signal_date: str,
) -> CompleteProvenance | None:
    if evidence_class is EvidenceClass.LIVE_FORWARD:
        generated_at = datetime.combine(
            date.fromisoformat(signal_date), datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=2)
        source = RunSource.FORMAL
        batch = None
    elif evidence_class is EvidenceClass.HISTORICAL_RECONSTRUCTION:
        generated_at = datetime(2026, 9, 1, 2, tzinfo=timezone.utc)
        source = RunSource.HISTORICAL_REPLAY
        batch = "batch-s3"
    else:
        return None
    identity = RecommendationEvidenceIdentity(
        evidence_class=evidence_class,
        signal_date=signal_date,
        actual_generated_at=generated_at,
        data_as_of=signal_date,
        source_snapshot_id="snapshot-s3",
        strategy_policy_fingerprint=_POLICY,
        execution_policy_fingerprint=_EXECUTION,
        code_revision=_REVISION,
        reconstruction_batch_id=batch,
    )
    return CompleteProvenance(identity, signal_date, source)


def _observation(
    sample_id: str,
    *,
    strategy_id: str = "strategy-0",
    day_offset: int = 0,
    evidence_class: EvidenceClass = EvidenceClass.RESEARCH_BACKTEST,
    board: str | None = "MAIN",
    regime: str | None = "NORMAL",
    score: float | None = 80.0,
    net_return_pct: float | None = 1.0,
    complete_provenance: bool = False,
    revision: str = _REVISION,
) -> EvidenceObservation:
    signal_date = (_SIGNAL + timedelta(days=day_offset)).isoformat()
    provenance = (
        _provenance(evidence_class, signal_date) if complete_provenance else None
    )
    return EvidenceObservation(
        sample_id=sample_id,
        record_type=LedgerRecordType.OUTCOME,
        evidence_class=evidence_class,
        strategy_id=strategy_id,
        strategy_version="v1",
        strategy_policy_fingerprint=_POLICY,
        source_snapshot_id="snapshot-s3",
        snapshot_schema_version="market-snapshot-v1",
        execution_policy_fingerprint=_EXECUTION,
        code_revision=revision,
        signal_date=signal_date,
        trade_date=signal_date,
        board=board,
        market_regime=regime,
        score=score,
        net_return_pct=net_return_pct,
        provenance=provenance,
    )


@settings(max_examples=100, deadline=None)
@given(
    strategy_ids=st.lists(
        st.integers(min_value=0, max_value=30),
        min_size=0,
        max_size=8,
        unique=True,
    ),
    counts=st.lists(st.integers(min_value=0, max_value=4), min_size=0, max_size=8),
)
def test_property_15_active_strategies_map_one_to_one_to_truthful_profiles(
    strategy_ids, counts
):
    """**Property 15: Active strategies map one-to-one to truthful profiles**"""
    active = tuple(_strategy(f"strategy-{value}") for value in strategy_ids)
    normalized_counts = {
        strategy.strategy_id: (counts[index] if index < len(counts) else 0)
        for index, strategy in enumerate(active)
    }
    population = tuple(
        _observation(
            f"sample-{strategy.strategy_id}-{index}",
            strategy_id=strategy.strategy_id,
            day_offset=index,
        )
        for strategy in active
        for index in range(normalized_counts[strategy.strategy_id])
    )

    profiles = build_canonical_profiles(active, population, _context())

    assert len(profiles) == len(active)
    assert len({profile.profile_id for profile in profiles}) == len(profiles)
    assert {profile.profile_identity.strategy_id for profile in profiles} == {
        item.strategy_id for item in active
    }
    for profile in profiles:
        expected = normalized_counts[profile.profile_identity.strategy_id]
        assert profile.n == expected
        assert profile.distinct_signal_days == expected
        if expected == 0:
            assert profile.status is ProfileStatus.INSUFFICIENT_SAMPLE
            assert profile.confidence_status is ConfidenceStatus.INSUFFICIENT_SAMPLE
            assert profile.performance is None
        else:
            assert profile.status is ProfileStatus.READY
            assert profile.performance is not None
            assert profile.performance.n == expected


@settings(max_examples=100, deadline=None)
@given(
    scenario=st.sampled_from(
        ("zero", "stale", "historical", "research", "single_positive")
    ),
    nonce=st.integers(min_value=0, max_value=10_000),
)
def test_property_16_champion_fails_closed_on_insufficient_or_stale_evidence(
    scenario, nonce
):
    """**Property 16: Champion state fails closed on insufficient or stale evidence**"""
    identity = _profile_identity()
    if scenario == "zero":
        population = ()
        scope = PopulationScope.FULL_POPULATION
    elif scenario == "stale":
        population = (
            _observation(
                f"live-stale-{nonce}",
                evidence_class=EvidenceClass.LIVE_FORWARD,
                complete_provenance=True,
            ),
        )
        scope = PopulationScope.FULL_POPULATION
    elif scenario == "historical":
        population = (
            _observation(
                f"historical-{nonce}",
                evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
                net_return_pct=5.0,
            ),
        )
        scope = PopulationScope.FULL_POPULATION
    elif scenario == "research":
        population = (
            _observation(
                f"research-{nonce}",
                evidence_class=EvidenceClass.RESEARCH_BACKTEST,
                net_return_pct=5.0,
            ),
        )
        scope = PopulationScope.FULL_POPULATION
    else:
        population = (
            _observation(
                f"single-{nonce}",
                evidence_class=EvidenceClass.LIVE_FORWARD,
                complete_provenance=True,
                net_return_pct=5.0,
            ),
        )
        scope = PopulationScope.SINGLE_POSITIVE_SLICE

    package = build_evidence_package(
        identity, population, population_scope=scope
    )
    current = _profile_identity(revision="d" * 40) if scenario == "stale" else identity
    decision = evaluate_champion(current, package)

    assert decision.state is not ChampionState.PRODUCTION_CHAMPION
    if scenario == "stale":
        assert decision.state is ChampionState.STALE_UNVERIFIED
        assert decision.reason_code is ChampionReason.STALE_IDENTITY
    elif scenario in {"zero", "historical", "research"}:
        assert decision.state is ChampionState.UNVERIFIED
        assert decision.reason_code is ChampionReason.UNVERIFIED_CURRENT_POLICY
        assert decision.clean_forward_n == 0
    else:
        assert decision.state is ChampionState.FORWARD_CANDIDATE
        assert (
            decision.reason_code
            is ChampionReason.SINGLE_POSITIVE_SLICE_FORBIDDEN
        )


_segment_rows = st.lists(
    st.tuples(
        st.sampled_from(("MAIN", "GROWTH", None)),
        st.sampled_from(("STRONG", "NORMAL", "WEAK", None)),
        st.integers(min_value=0, max_value=2),
        st.one_of(
            st.none(),
            st.floats(
                min_value=0,
                max_value=100,
                allow_nan=False,
                allow_infinity=False,
            ),
        ),
        st.integers(min_value=0, max_value=9),
        st.one_of(
            st.none(),
            st.floats(
                min_value=-20,
                max_value=20,
                allow_nan=False,
                allow_infinity=False,
            ),
        ),
    ),
    min_size=0,
    max_size=30,
)


@settings(max_examples=100, deadline=None)
@given(rows=_segment_rows)
def test_property_17_evidence_packages_preserve_population_accounting(rows):
    """**Property 17: Evidence packages preserve population accounting**"""
    population = tuple(
        _observation(
            f"population-{index}",
            strategy_id=f"strategy-{strategy_no}",
            day_offset=day_offset,
            board=board,
            regime=regime,
            score=score,
            net_return_pct=net_return,
        )
        for index, (board, regime, strategy_no, score, day_offset, net_return) in enumerate(rows)
    )

    package = build_evidence_package(_profile_identity(), population)

    assert package.n == len(population)
    assert package.metrics.n == package.n
    assert package.distinct_signal_days == len({item.signal_date for item in population})
    assert package.population_signal_dates == tuple(
        sorted({item.signal_date for item in population})
    )
    assert package.expected_signal_dates == _context().actual_signal_dates
    assert package.coverage == package.distinct_signal_days / len(
        package.expected_signal_dates
    )
    assert sum(count for _, count in package.evidence_class_counts) == package.n
    assert {axis.axis for axis in package.strata} == {
        "board",
        "market_regime",
        "strategy",
        "score_bucket",
        "month",
    }
    assert all(axis.total == package.n for axis in package.strata)
    assert all(package.segment_total(axis) == package.n for axis in (
        "board", "market_regime", "strategy", "score_bucket", "month"
    ))
    if package.n == 0:
        assert package.confidence_status is ConfidenceStatus.INSUFFICIENT_SAMPLE
        assert "INSUFFICIENT_SAMPLE" in package.failed_gates


def test_zero_trade_profile_exposes_no_fabricated_zero_percent_win_rate() -> None:
    profile = build_canonical_profiles((_strategy(),), (), _context())[0]

    assert profile.n == 0
    assert profile.status is ProfileStatus.INSUFFICIENT_SAMPLE
    assert profile.performance is None
    assert profile.coverage == 0.0


def test_true_forward_ledger_is_append_only_and_rejects_all_other_classes() -> None:
    live = _observation(
        "ledger-live",
        evidence_class=EvidenceClass.LIVE_FORWARD,
        complete_provenance=True,
    )
    ledger = TrueForwardLedger().append(live)

    assert TrueForwardLedger().entries == ()
    assert ledger.entries == (live,)
    assert ledger.append(live) is ledger

    for evidence_class in (
        EvidenceClass.HISTORICAL_RECONSTRUCTION,
        EvidenceClass.RESEARCH_BACKTEST,
        EvidenceClass.EXPERIMENT,
        EvidenceClass.UNVERIFIED,
    ):
        with pytest.raises(ReleaseGovernanceError) as rejected:
            ledger.append(
                _observation(
                    f"ledger-{evidence_class.value}",
                    evidence_class=evidence_class,
                )
            )
        assert rejected.value.code == "LIVE_FORWARD_PROVENANCE_REQUIRED"

    incomplete_live = _observation(
        "ledger-incomplete-live", evidence_class=EvidenceClass.LIVE_FORWARD
    )
    with pytest.raises(ReleaseGovernanceError) as rejected:
        ledger.append(incomplete_live)
    assert rejected.value.code == "LIVE_FORWARD_PROVENANCE_REQUIRED"


def test_identity_drift_marks_an_existing_champion_stale() -> None:
    old_identity = _profile_identity()
    live_population = tuple(
        _observation(
            f"stale-live-{index}",
            day_offset=index,
            evidence_class=EvidenceClass.LIVE_FORWARD,
            complete_provenance=True,
            net_return_pct=(-1.0 if index == 0 else 2.0),
        )
        for index in range(2)
    )
    package = build_evidence_package(old_identity, live_population)
    incumbent = ChampionRecord(
        old_identity, ChampionState.PRODUCTION_CHAMPION, package.package_id
    )

    decision = evaluate_champion(
        _profile_identity(revision="e" * 40), package, incumbent=incumbent
    )

    assert decision.state is ChampionState.STALE_UNVERIFIED
    assert decision.reason_code is ChampionReason.STALE_IDENTITY


def test_historical_oos_success_cannot_cover_current_forward_n_zero() -> None:
    identity = _profile_identity()
    historical = tuple(
        _observation(
            f"historical-oos-{index}",
            day_offset=index,
            evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
            net_return_pct=10.0,
        )
        for index in range(3)
    )
    package = build_evidence_package(identity, historical)

    decision = evaluate_champion(identity, package)

    assert package.n == 3
    assert package.clean_forward_n == 0
    assert decision.state is ChampionState.UNVERIFIED
    assert decision.reason_code is ChampionReason.UNVERIFIED_CURRENT_POLICY


def test_segment_degradation_uses_explicit_unknown_buckets_without_data_loss() -> None:
    package = build_evidence_package(
        _profile_identity(),
        (
            _observation(
                "known-segments", board="MAIN", regime="NORMAL", score=85
            ),
            _observation(
                "missing-segments", board=None, regime=None, score=None
            ),
        ),
    )

    for axis_name in ("board", "market_regime", "score_bucket"):
        axis = next(axis for axis in package.strata if axis.axis == axis_name)
        assert axis.total == 2
        assert any(segment.segment == "UNKNOWN" for segment in axis.segments)


def test_single_positive_forward_slice_cannot_become_production_champion() -> None:
    identity = _profile_identity()
    package = build_evidence_package(
        identity,
        (
            _observation(
                "only-winning-slice",
                evidence_class=EvidenceClass.LIVE_FORWARD,
                complete_provenance=True,
                net_return_pct=9.0,
            ),
        ),
        population_scope=PopulationScope.SINGLE_POSITIVE_SLICE,
    )

    decision = evaluate_champion(identity, package)

    assert decision.state is ChampionState.FORWARD_CANDIDATE
    assert decision.reason_code is ChampionReason.SINGLE_POSITIVE_SLICE_FORBIDDEN


def test_balanced_current_forward_population_can_retain_requested_champion_state() -> None:
    identity = _profile_identity()
    package = build_evidence_package(
        identity,
        tuple(
            _observation(
                f"balanced-live-{index}",
                day_offset=index,
                evidence_class=EvidenceClass.LIVE_FORWARD,
                complete_provenance=True,
                net_return_pct=value,
            )
            for index, value in enumerate((2.0, -1.0))
        ),
    )

    decision = evaluate_champion(identity, package)

    assert package.clean_forward_n == 2
    assert decision.state is ChampionState.PRODUCTION_CHAMPION
    assert decision.reason_code is ChampionReason.VERIFIED
