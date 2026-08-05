"""Feature: production-evidence-risk-hardening, Property 32 Layer-1 proof."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hypothesis import given, settings, strategies as st

from app.services.dashboard_hardening import (
    DashboardEvidenceSection,
    DashboardRunEvidence,
    projection_is_stale,
    select_current_run,
)

UTC = timezone.utc
BASE = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


def _fact(index: int, section: DashboardEvidenceSection, day: int, minute: int):
    generated = BASE + timedelta(days=day, minutes=minute)
    business_date = (BASE.date() + timedelta(days=day)).isoformat()
    return DashboardRunEvidence(
        run_id=f"run-{index:03d}",
        evidence_section=section,
        evidence_class=section.value.lower(),
        actual_generated_at=generated,
        business_date=business_date,
        signal_date=business_date,
        trade_date=business_date,
        reconstruction_label=("历史重建" if section is DashboardEvidenceSection.HISTORICAL_RECONSTRUCTION else None),
        data_quality_decision="ready",
        data_quality_reasons=(),
        risk_gate="allow",
        risk_reasons=(),
        last_updated_at=generated,
        status="completed",
    )


@settings(max_examples=100, deadline=None)
@given(
    rows=st.lists(
        st.tuples(
            st.sampled_from(tuple(DashboardEvidenceSection)),
            st.integers(min_value=-30, max_value=30),
            st.integers(min_value=0, max_value=1439),
        ),
        max_size=30,
    ),
    age_seconds=st.integers(min_value=-60, max_value=20_000),
    threshold_seconds=st.integers(min_value=0, max_value=10_000),
)
def test_property_32_dashboard_selection_and_staleness_are_truthful(
    rows,
    age_seconds,
    threshold_seconds,
):
    """Property 32: Dashboard selection and staleness are truthful."""
    facts = tuple(
        _fact(index, section, day, minute)
        for index, (section, day, minute) in enumerate(rows)
    )
    selected = select_current_run(facts)

    live = [item for item in facts if item.evidence_section is DashboardEvidenceSection.LIVE_FORWARD]
    reconstruction = [
        item
        for item in facts
        if item.evidence_section is DashboardEvidenceSection.HISTORICAL_RECONSTRUCTION
    ]
    eligible = live or reconstruction
    if not eligible:
        assert selected is None
    else:
        expected = max(
            eligible,
            key=lambda item: (item.business_date, item.actual_generated_at, item.run_id),
        )
        assert selected == expected
        assert selected.evidence_section is (
            DashboardEvidenceSection.LIVE_FORWARD
            if live
            else DashboardEvidenceSection.HISTORICAL_RECONSTRUCTION
        )

    last_updated = BASE
    now = BASE + timedelta(seconds=age_seconds)
    threshold = timedelta(seconds=threshold_seconds)
    assert projection_is_stale(last_updated, now=now, stale_threshold=threshold) is (
        age_seconds > threshold_seconds
    )
