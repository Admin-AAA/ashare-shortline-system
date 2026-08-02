"""Strict immutable snapshot/replay regression tests for the P0 runner."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.engine.backtest import (
    ArtifactScope,
    BacktestRequest,
    DataRequirement,
    ExecutionPolicy,
    FutureDataAccessDeniedError,
    SignalInput,
    SignalInputRequirement,
    SingleDate,
    SnapshotArtifactUnavailableError,
    SnapshotIntegrityError,
)
from app.models.tables import BacktestDataSnapshot, BacktestInputArtifact, DailyKbar, Stock
from app.services.backtest.research_repository import ResearchBacktestRepository
from app.services.backtest.research_runner import (
    ArtifactSignalDateDataReader,
    LocalResearchSnapshotBuilder,
    ResearchBacktestRunner,
)


@pytest.fixture
def captured_snapshot_bundle():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            DailyKbar.__table__,
            Stock.__table__,
            BacktestDataSnapshot.__table__,
            BacktestInputArtifact.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        signal_day = date(2026, 1, 2)
        session.add_all(
            [
                DailyKbar(
                    code="000001",
                    trade_date=(date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                    open=10,
                    high=11,
                    low=9,
                    close=10 + index,
                    volume=100,
                )
                for index in range(6)
            ]
        )
        session.commit()
        request = BacktestRequest(
            strategy_id="snapshot-replay-fixture",
            date_selector=SingleDate(signal_day.isoformat()),
            execution_policy=ExecutionPolicy(entry_lookahead_days=1, horizon_days=3),
            target_codes=("000001",),
        )
        bundle = LocalResearchSnapshotBuilder(session).capture(
            request,
            data_requirement=DataRequirement(
                lookback_bars=2,
                future_label_horizon_days=0,
                requires_daily_bars=True,
            ),
        )
        assert bundle.snapshot.coverage_by_signal_date[signal_day.isoformat()].is_ready
        repository = ResearchBacktestRepository(session)
        runner = ResearchBacktestRunner(repository)
        runner._persist_bundle(bundle)
        yield session, engine, repository, runner, bundle, signal_day.isoformat()
    finally:
        session.close()
        engine.dispose()


def test_replay_reads_original_artifact_after_source_rows_change(captured_snapshot_bundle) -> None:
    session, _engine, repository, runner, bundle, signal_date = captured_snapshot_bundle
    signal_ref = bundle.snapshot.artifacts_for(signal_date, ArtifactScope.SIGNAL_INPUT)[0]
    original = ArtifactSignalDateDataReader(repository, signal_ref).read(
        signal_date,
        SignalInputRequirement(lookback_bars=0),
    )
    original_price = original.payload["stocks"][0]["price"]
    original_manifest_hash = bundle.snapshot.input_manifest_hash

    source_row = session.query(DailyKbar).filter_by(code="000001", trade_date=signal_date).one()
    source_row.close = 999
    source_row.high = 1000
    session.commit()

    replay_bundle = runner._load_snapshot_bundle(bundle.snapshot.snapshot_id)
    assert replay_bundle.captured_locally is False
    assert replay_bundle.payloads_by_artifact_id == {}
    replay_ref = replay_bundle.snapshot.artifacts_for(signal_date, ArtifactScope.SIGNAL_INPUT)[0]
    replay = ArtifactSignalDateDataReader(repository, replay_ref).read(
        signal_date,
        SignalInputRequirement(lookback_bars=0),
    )

    assert replay.payload["stocks"][0]["price"] == original_price
    assert replay.payload["stocks"][0]["price"] != 999
    assert replay_bundle.snapshot.input_manifest_hash == original_manifest_hash


def test_deleted_or_corrupted_manifest_artifact_safe_fails(captured_snapshot_bundle) -> None:
    session, _engine, _repository, runner, bundle, signal_date = captured_snapshot_bundle
    signal_ref = bundle.snapshot.artifacts_for(signal_date, ArtifactScope.SIGNAL_INPUT)[0]

    session.delete(session.get(BacktestInputArtifact, signal_ref.artifact_id))
    session.commit()
    with pytest.raises(SnapshotArtifactUnavailableError) as missing:
        runner._load_snapshot_bundle(bundle.snapshot.snapshot_id)
    assert missing.value.code == "snapshot_artifact_unavailable"


@pytest.mark.parametrize("payload", [b"{}", b'{"schema_version":999}'])
def test_corrupted_manifest_artifact_is_a_snapshot_integrity_error(
    captured_snapshot_bundle,
    payload: bytes,
) -> None:
    session, _engine, _repository, runner, bundle, signal_date = captured_snapshot_bundle
    signal_ref = bundle.snapshot.artifacts_for(signal_date, ArtifactScope.SIGNAL_INPUT)[0]
    artifact = session.get(BacktestInputArtifact, signal_ref.artifact_id)
    artifact.payload = payload
    session.commit()

    with pytest.raises(SnapshotIntegrityError) as corrupt:
        runner._load_snapshot_bundle(bundle.snapshot.snapshot_id)
    assert corrupt.value.code == "snapshot_integrity_error"


def test_list_only_legacy_snapshot_manifest_never_infers_current_artifacts(captured_snapshot_bundle) -> None:
    _session, _engine, repository, runner, bundle, _signal_date = captured_snapshot_bundle
    repository.persist_snapshot(
        snapshot_id="legacy_list_manifest",
        snapshot_version=1,
        partition_manifest=[partition.canonical_payload() for partition in bundle.snapshot.partitions],
        coverage={},
        missing_requirements=[],
    )
    repository.session.commit()

    with pytest.raises(SnapshotArtifactUnavailableError) as legacy:
        runner._load_snapshot_bundle("legacy_list_manifest")
    assert legacy.value.code == "snapshot_artifact_unavailable"


def test_signal_input_denies_future_visible_at() -> None:
    with pytest.raises(FutureDataAccessDeniedError) as forward:
        SignalInput(
            signal_date="2026-01-02",
            visible_at="2026-01-03",
            payload={"stocks": []},
        )
    assert forward.value.code == "future_data_access_denied"
