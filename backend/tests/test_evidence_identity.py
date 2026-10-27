"""Task 1.1 evidence-class and canonical identity contract tests."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

from types import SimpleNamespace

import pytest

from app.services.evidence import (
    PROVENANCE_SCHEMA_VERSION,
    EvidenceClass,
    EvidenceIdentityBuilder,
    EvidenceIdentityError,
    EvidenceSnapshotIdentity,
    RunSource,
    build_recommendation_evidence_identity,
    has_canonical_persisted_identity,
)


STRATEGY_FINGERPRINT = "a" * 64
EXECUTION_FINGERPRINT = "b" * 64
CODE_REVISION = "c" * 40
SIGNAL_DATE = "2026-07-14"
GENERATED_AT = datetime(2026, 7, 14, 8, 30, 45, 123456, tzinfo=timezone.utc)


def _snapshot(*, data_as_of: str = SIGNAL_DATE, snapshot_id: str = "snapshot-20260714") -> EvidenceSnapshotIdentity:
    return EvidenceSnapshotIdentity(
        source_snapshot_id=snapshot_id,
        data_as_of=data_as_of,
    )


def _build(source: RunSource, **overrides: object):
    values: dict[str, object] = {
        "run_source": source,
        "actual_generated_at": GENERATED_AT,
        "signal_date": SIGNAL_DATE,
        "snapshot": _snapshot(),
        "strategy_policy": STRATEGY_FINGERPRINT,
        "execution_policy": EXECUTION_FINGERPRINT,
        "code_revision": CODE_REVISION,
    }
    if source is RunSource.HISTORICAL_REPLAY:
        values["reconstruction_batch_id"] = "replay-batch-20260715"
    values.update(overrides)
    return EvidenceIdentityBuilder().build(**values)  # type: ignore[arg-type]


def test_evidence_classes_and_trusted_run_sources_classify_explicitly():
    """**Validates: Requirements 1.2, 1.3, 1.5**"""
    assert {item.value for item in EvidenceClass} == {
        "live_forward",
        "historical_reconstruction",
        "research_backtest",
        "experiment",
        "unverified",
    }

    expected = {
        RunSource.FORMAL: EvidenceClass.LIVE_FORWARD,
        RunSource.HISTORICAL_REPLAY: EvidenceClass.HISTORICAL_RECONSTRUCTION,
        RunSource.BACKTEST: EvidenceClass.RESEARCH_BACKTEST,
        RunSource.EXPERIMENT: EvidenceClass.EXPERIMENT,
        RunSource.UNKNOWN: EvidenceClass.UNVERIFIED,
    }
    for source, evidence_class in expected.items():
        identity = _build(source)
        assert identity.evidence_class is evidence_class
        assert identity.is_true_forward is (evidence_class is EvidenceClass.LIVE_FORWARD)


def test_only_provenance_complete_same_business_day_formal_can_be_live():
    """**Validates: Requirements 1.3–1.5**（2026-10-27：实盘窗口 = 信号日当天到入场日 09:15 前）"""
    # 信号日 07-14（周二）：次日 00:01 北京时间生成仍在入场日开盘前 → 实盘
    overnight = datetime(2026, 7, 14, 16, 1, tzinfo=timezone.utc)
    assert _build(RunSource.FORMAL, actual_generated_at=overnight).evidence_class is EvidenceClass.LIVE_FORWARD
    # 入场日 09:20 北京时间才生成：已开盘 → 不算实盘
    late_generation = datetime(2026, 7, 15, 1, 20, tzinfo=timezone.utc)
    assert _build(RunSource.FORMAL, actual_generated_at=late_generation).evidence_class is EvidenceClass.UNVERIFIED
    # 信号日之前生成的不可能是该信号日的实盘
    early = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)
    assert _build(RunSource.FORMAL, actual_generated_at=early).evidence_class is EvidenceClass.UNVERIFIED
    assert _build(
        RunSource.FORMAL,
        snapshot=_snapshot(data_as_of="2026-07-15"),
    ).evidence_class is EvidenceClass.UNVERIFIED
    assert _build(
        RunSource.FORMAL,
        snapshot=_snapshot(snapshot_id="unknown"),
    ).evidence_class is EvidenceClass.UNVERIFIED
    assert _build(RunSource.FORMAL, code_revision="unknown").evidence_class is EvidenceClass.UNVERIFIED

    # A replay remains reconstruction even when generated on the signal date.  The
    # display name/scope cannot be supplied to this canonical API to promote it.
    assert _build(RunSource.HISTORICAL_REPLAY).evidence_class is EvidenceClass.HISTORICAL_RECONSTRUCTION
    with pytest.raises(EvidenceIdentityError, match="on or before signal_date"):
        _build(
            RunSource.HISTORICAL_REPLAY,
            snapshot=_snapshot(data_as_of="2026-07-15"),
        )
    with pytest.raises(TypeError, match="run_name"):
        _build(RunSource.UNKNOWN, run_name="LIVE_FORWARD")
    with pytest.raises(TypeError, match="evaluation_scope"):
        _build(RunSource.UNKNOWN, evaluation_scope="live_forward")
    with pytest.raises(EvidenceIdentityError, match="trusted adapter"):
        _build("formal")  # type: ignore[arg-type]


def test_identity_is_immutable_canonical_and_uses_every_required_fact():
    """**Validates: Requirements 1.2, 1.3**"""
    beijing_time = GENERATED_AT.astimezone(timezone(timedelta(hours=8)))
    utc_identity = _build(RunSource.FORMAL)
    beijing_identity = _build(RunSource.FORMAL, actual_generated_at=beijing_time)

    assert utc_identity == beijing_identity
    assert utc_identity.provenance_schema_version == PROVENANCE_SCHEMA_VERSION
    assert utc_identity.canonical_payload()["actual_generated_at"] == "2026-07-14T08:30:45.123456Z"
    assert len(utc_identity.identity_hash) == 64
    assert utc_identity.identity_hash == utc_identity.fingerprint()
    assert utc_identity.identity_hash == beijing_identity.identity_hash
    with pytest.raises(dataclasses.FrozenInstanceError):
        utc_identity.signal_date = "2026-07-15"  # type: ignore[misc]

    baseline = _build(RunSource.BACKTEST)
    changed_identities = (
        _build(RunSource.EXPERIMENT),
        _build(RunSource.BACKTEST, actual_generated_at=GENERATED_AT + timedelta(microseconds=1)),
        _build(
            RunSource.BACKTEST,
            signal_date="2026-07-15",
            snapshot=_snapshot(data_as_of="2026-07-15"),
        ),
        _build(RunSource.BACKTEST, snapshot=_snapshot(snapshot_id="snapshot-other")),
        _build(RunSource.BACKTEST, snapshot=_snapshot(data_as_of="2026-07-13")),
        _build(RunSource.BACKTEST, strategy_policy="d" * 64),
        _build(RunSource.BACKTEST, execution_policy="e" * 64),
        _build(RunSource.BACKTEST, code_revision="f" * 40),
    )
    assert all(item.identity_hash != baseline.identity_hash for item in changed_identities)


def test_persisted_identity_hash_validation_fails_closed_for_non_hex_text():
    identity = _build(RunSource.FORMAL)
    row = SimpleNamespace(
        **identity.canonical_payload(),
        trade_date=identity.signal_date,
        evidence_identity_hash="é" * 64,
    )

    assert has_canonical_persisted_identity(row) is False


def test_historical_reconstruction_rejects_placeholder_provenance_identifiers():
    for snapshot_id in ("unknown", "unavailable", "none"):
        with pytest.raises(EvidenceIdentityError, match="verified snapshot"):
            _build(
                RunSource.HISTORICAL_REPLAY,
                snapshot=_snapshot(snapshot_id=snapshot_id),
            )
    with pytest.raises(EvidenceIdentityError, match="code revision"):
        _build(RunSource.HISTORICAL_REPLAY, code_revision="unknown")


def test_builder_accepts_canonical_policy_objects_and_rejects_invalid_provenance():
    """**Validates: Requirements 1.3, 1.5**"""

    class MethodPolicy:
        def fingerprint(self) -> str:
            return STRATEGY_FINGERPRINT.upper()

    class PropertyPolicy:
        fingerprint = EXECUTION_FINGERPRINT.upper()

    identity = build_recommendation_evidence_identity(
        run_source=RunSource.FORMAL,
        actual_generated_at=GENERATED_AT,
        signal_date=SIGNAL_DATE,
        snapshot=_snapshot(),
        strategy_policy=MethodPolicy(),
        execution_policy=PropertyPolicy(),
        code_revision=CODE_REVISION,
    )
    assert identity.strategy_policy_fingerprint == STRATEGY_FINGERPRINT
    assert identity.execution_policy_fingerprint == EXECUTION_FINGERPRINT

    with pytest.raises(EvidenceIdentityError, match="timezone-aware"):
        _build(RunSource.FORMAL, actual_generated_at=GENERATED_AT.replace(tzinfo=None))
    with pytest.raises(EvidenceIdentityError, match="SHA-256"):
        _build(RunSource.FORMAL, strategy_policy="not-a-hash")
    with pytest.raises(EvidenceIdentityError, match="reconstruction_batch_id"):
        _build(RunSource.HISTORICAL_REPLAY, reconstruction_batch_id=None)
    with pytest.raises(EvidenceIdentityError, match="only valid"):
        _build(RunSource.FORMAL, reconstruction_batch_id="not-a-replay")
