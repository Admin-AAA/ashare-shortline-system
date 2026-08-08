"""No-network S4 application-boundary regression tests.

**Validates: Requirements 4.4**
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.services.auxiliary.application import (
    InstitutionalSurveyAuditRequest,
    capture_institutional_survey_audit,
    run_auxiliary_application,
    resolve_auxiliary_trade_date,
)
from app.services.auxiliary.composition import (
    EVENT_SNAPSHOT_CONTRACTS,
    INSTITUTIONAL_SURVEY_AUDIT_CONTRACT,
)
from app.services.auxiliary.contracts import (
    AuxiliaryDatasetRequest,
    AuxiliaryInstrument,
    AuxiliaryLoadedView,
    AuxiliaryPersistResult,
    AuxiliaryRepositorySnapshot,
)
from app.services.auxiliary.event_snapshot import TushareRawProviderPort
from app.services.auxiliary.production import (
    AUXILIARY_DATASET_INVENTORY,
    AuxiliaryInventoryError,
    StandardAuxiliaryDatasetAdapters,
)
from app.services.auxiliary.snapshot_repository import (
    active_partition_snapshots,
    snapshot_batches,
    snapshot_metadata,
)
from app.services.tushare_client import TushareRawEnvelope
from app.models.tables import HolderNumber, PledgeStat


NOW = datetime(2026, 7, 15, 9, 30, tzinfo=timezone.utc)
TRADE_DATE = date(2026, 7, 15)


class _RawProvider:
    def __init__(self, responses: Mapping[str, object]) -> None:
        self.responses = dict(responses)
        self.calls: list[str] = []

    def fetch_raw(self, api_name: str, *, params=None, fields: str = "") -> TushareRawEnvelope:
        del params, fields
        self.calls.append(api_name)
        response = self.responses[api_name]
        if isinstance(response, BaseException):
            raise response
        assert isinstance(response, TushareRawEnvelope)
        return response


class _StateFetcher:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, request: AuxiliaryDatasetRequest) -> tuple[dict[str, object], ...]:
        self.calls += 1
        return ({"code": request.requested_codes[0] if request.requested_codes else "000001"},)


class _StateNormalizer:
    def normalize(self, raw: Mapping[str, object], request: AuxiliaryDatasetRequest) -> dict[str, object]:
        return {**raw, "trade_date": request.trade_date}


class _StateRepository:
    def inspect(self, request: AuxiliaryDatasetRequest) -> AuxiliaryRepositorySnapshot:
        del request
        return AuxiliaryRepositorySnapshot(0)

    def upsert(
        self,
        records: tuple[Mapping[str, object], ...],
        request: AuxiliaryDatasetRequest,
    ) -> AuxiliaryPersistResult:
        del request
        return AuxiliaryPersistResult(
            attempted_rows=len(records),
            affected_rows=None,
            failed_rows=0,
            execute_count=1,
            duration_ms=0.0,
            batch_size=1,
            batch_statement_bound=1,
        )


class _StateLoader:
    def __init__(self, key: str) -> None:
        self.key = key

    def load(self, request: AuxiliaryDatasetRequest) -> AuxiliaryLoadedView[dict[str, object]]:
        return AuxiliaryLoadedView(self.key, {request.dataset_key: {"loaded": True}})


class _ReadySchema:
    def __init__(self) -> None:
        self.checked: list[str] = []

    def assert_ready(self, entry: Any) -> None:
        self.checked.append(entry.key)


def _state_adapters(fetcher: _StateFetcher) -> dict[str, StandardAuxiliaryDatasetAdapters]:
    return {
        entry.key: StandardAuxiliaryDatasetAdapters(
            fetcher=fetcher,
            normalizer=_StateNormalizer(),
            repository=_StateRepository(),
            loader=_StateLoader(entry.view_key),
        )
        for entry in AUXILIARY_DATASET_INVENTORY
        if entry.is_state_dataset
    }


def _envelope(key: str, rows: tuple[Mapping[str, object], ...] = ()) -> TushareRawEnvelope:
    contract = (
        INSTITUTIONAL_SURVEY_AUDIT_CONTRACT
        if key == "institutional_survey"
        else EVENT_SNAPSHOT_CONTRACTS[key]
    )
    values: dict[str, object] = {
        "ts_code": "000001.SZ", "trade_date": "20260715", "ann_date": "20260715",
        "float_date": "20260720", "surv_date": "20260715", "name": "fixture",
        "title": "fixture", "url": "https://example.invalid/fixture", "rec_time": "2026-07-15 09:30:00",
        "price": 1.0, "vol": 1.0, "amount": 1.0, "buyer": "fixture", "seller": "fixture",
        "float_share": 1.0, "float_ratio": 1.0, "holder_name": "fixture", "share_type": "fixture",
        "holder_type": "fixture", "in_de": "IN", "change_vol": 1.0, "change_ratio": 1.0,
        "after_share": 1.0, "after_ratio": 1.0, "avg_price": 1.0, "total_share": 1.0,
        "begin_date": "20260701", "close_date": "20260715", "fund_visitors": 1,
        "rece_place": "fixture", "rece_mode": "fixture", "rece_org": "fixture",
        "org_type": "fixture", "comp_rece": "fixture",
    }
    records = rows or (({field: values[field] for field in contract.documented_fields},) if key == "institutional_survey" else ())
    return TushareRawEnvelope.from_envelope(
        api_name=contract.endpoint,
        requested_fields="",
        envelope={
            "code": 0,
            "data": {
                "fields": list(contract.documented_fields),
                "items": [[record[field] for field in contract.documented_fields] for record in records],
            },
        },
    )


def _session():
    engine = create_engine("sqlite:///:memory:")
    snapshot_metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _state_session():
    engine = create_engine("sqlite:///:memory:")
    PledgeStat.__table__.create(engine)
    HolderNumber.__table__.create(engine)
    return sessionmaker(bind=engine)()


def test_replay_as_of_date_excludes_future_pledge_and_holder_rows() -> None:
    db = _state_session()
    try:
        db.add_all(
            [
                PledgeStat(
                    code="000001",
                    end_date="2026-03-31",
                    pledge_count=3,
                    unrest_pledge=100.0,
                    rest_pledge=20.0,
                    pledge_ratio=8.0,
                ),
                PledgeStat(
                    code="000001",
                    end_date="2026-06-30",
                    pledge_count=9,
                    unrest_pledge=300.0,
                    rest_pledge=40.0,
                    pledge_ratio=30.0,
                ),
                HolderNumber(code="000001", end_date="2025-12-31", holder_num=1200),
                HolderNumber(code="000001", end_date="2026-03-31", holder_num=1000),
                HolderNumber(code="000001", end_date="2026-06-30", holder_num=600),
            ]
        )
        db.flush()

        live = run_auxiliary_application(
            db,
            instruments=("000001",),
            trade_date="2026-07-15",
            refresh=False,
            datasets=("pledge_stat", "holder_number"),
        ).feature_views
        replay = run_auxiliary_application(
            db,
            instruments=("000001",),
            trade_date="2026-03-31",
            as_of_date="2026-03-31",
            refresh=False,
            datasets=("pledge_stat", "holder_number"),
        ).feature_views

        assert live["pledges"]["000001"]["end_date"] == "2026-06-30"
        assert live["holder_numbers"]["000001"]["holder_num"] == 600
        assert replay["pledges"]["000001"]["end_date"] == "2026-03-31"
        assert replay["pledges"]["000001"]["pledge_ratio"] == 8.0
        assert replay["holder_numbers"]["000001"] == {
            "holder_num": 1000,
            "holder_num_end_date": "2026-03-31",
            "holder_num_prev": 1200,
            "holder_num_change_pct": -16.67,
            "holder_num_decreasing": True,
        }
    finally:
        db.close()


def test_auxiliary_trade_date_never_exceeds_latest_closed_trade_date() -> None:
    assert resolve_auxiliary_trade_date("2026-07-22", latest_closed_trade_date="2026-07-21") == date(2026, 7, 21)
    assert resolve_auxiliary_trade_date("2026-07-20", latest_closed_trade_date="2026-07-21") == date(2026, 7, 20)


def test_application_routes_the_complete_frozen_inventory_without_event_table_fallback() -> None:
    db = _session()
    fetcher = _StateFetcher()
    readiness = _ReadySchema()
    raw = _RawProvider({contract.endpoint: _envelope(key) for key, contract in EVENT_SNAPSHOT_CONTRACTS.items()})
    try:
        result = run_auxiliary_application(
            db,
            instruments=("000001",),
            trade_date=TRADE_DATE,
            refresh=True,
            raw_provider=raw,
            state_adapters=_state_adapters(fetcher),
            schema_readiness=readiness,
        )

        assert result.composition.registry.is_frozen
        assert tuple(item.dataset_key for item in result.orchestrated.results) == tuple(
            entry.key for entry in AUXILIARY_DATASET_INVENTORY
        )
        assert len(result.orchestrated.results) == 17
        assert readiness.checked == [entry.key for entry in AUXILIARY_DATASET_INVENTORY if entry.is_state_dataset]
        assert fetcher.calls == 13
        assert set(raw.calls) == {contract.endpoint for contract in EVENT_SNAPSHOT_CONTRACTS.values()}
        assert "stk_surv" not in raw.calls
        assert "institutional_survey" not in result.orchestrated.by_dataset
        assert "surveys" not in result.feature_views
    finally:
        db.close()


def test_skip_refresh_reads_current_state_and_active_pointers_without_provider_io(monkeypatch) -> None:
    import app.services.auxiliary.application as application

    db = _session()
    fetcher = _StateFetcher()
    readiness = _ReadySchema()
    raw = _RawProvider({contract.endpoint: _envelope(key) for key, contract in EVENT_SNAPSHOT_CONTRACTS.items()})
    captured: dict[str, object] = {}
    original_builder = application.build_auxiliary_production_composition

    def capture_composition(**kwargs):
        captured["raw_provider"] = kwargs["raw_provider"]
        return original_builder(**kwargs)

    try:
        monkeypatch.setattr(application, "build_auxiliary_production_composition", capture_composition)
        result = run_auxiliary_application(
            db,
            instruments=("000001",),
            trade_date=TRADE_DATE,
            refresh=False,
            raw_provider=raw,
            state_adapters=_state_adapters(fetcher),
            schema_readiness=readiness,
        )
        assert tuple(item.dataset_key for item in result.orchestrated.results) == tuple(
            entry.key for entry in AUXILIARY_DATASET_INVENTORY
        )
        assert fetcher.calls == 0
        assert raw.calls == []
        assert captured["raw_provider"] is not raw
        with pytest.raises(RuntimeError, match="provider I/O is forbidden while refresh is disabled"):
            captured["raw_provider"].fetch_raw("must-not-fetch")  # type: ignore[union-attr]
        assert "institutional_survey" not in result.orchestrated.by_dataset
        assert "surveys" not in result.feature_views
    finally:
        db.close()


def test_default_application_rejects_the_audit_only_dataset_without_provider_io() -> None:
    db = _session()
    raw = _RawProvider({"stk_surv": _envelope("institutional_survey")})
    try:
        with pytest.raises(AuxiliaryInventoryError, match="audit-only"):
            run_auxiliary_application(
                db,
                instruments=("000001",),
                trade_date=TRADE_DATE,
                refresh=True,
                datasets=("institutional_survey",),
                raw_provider=raw,
                state_adapters={},
                schema_readiness=_ReadySchema(),
            )
        assert raw.calls == []
    finally:
        db.close()


def test_explicit_survey_audit_capture_persists_only_within_caller_transaction_and_rolls_back_without_residue() -> None:
    db = _session()
    raw = _RawProvider(
        {
            "stk_surv": _envelope("institutional_survey"),
        }
    )
    try:
        request = InstitutionalSurveyAuditRequest(
            instruments=(AuxiliaryInstrument("000001", "SZ"),),
            requested_at=NOW,
        )
        transaction = db.begin()
        try:
            success = capture_institutional_survey_audit(db, request=request, raw_provider=raw)
            assert len(success.captures) == 1
            assert success.captures[0].persisted.created is True
            assert success.captures[0].active_pointer_published is False
            assert not hasattr(success, "feature_views")
            assert raw.calls == ["stk_surv"]

            raw.responses["stk_surv"] = RuntimeError("fixture failure")
            failure = capture_institutional_survey_audit(db, request=request, raw_provider=raw)
            assert failure.captures[0].provider_result.failure is not None
            assert failure.captures[0].persisted.created is True
            assert failure.captures[0].active_pointer_published is False
            assert db.execute(select(func.count()).select_from(snapshot_batches)).scalar_one() == 2
            assert db.execute(select(func.count()).select_from(active_partition_snapshots)).scalar_one() == 0
        finally:
            transaction.rollback()

        assert db.execute(select(func.count()).select_from(snapshot_batches)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(active_partition_snapshots)).scalar_one() == 0
    finally:
        db.close()


def test_survey_audit_rejects_a_fresh_session_before_provider_or_database_use(monkeypatch) -> None:
    db = _session()
    raw = _RawProvider({"stk_surv": _envelope("institutional_survey")})
    connection_calls = 0

    def fail_if_connection_is_requested():
        nonlocal connection_calls
        connection_calls += 1
        raise AssertionError("fresh audit capture must not acquire a database connection")

    try:
        monkeypatch.setattr(db, "connection", fail_if_connection_is_requested)
        request = InstitutionalSurveyAuditRequest(
            instruments=(AuxiliaryInstrument("000001", "SZ"),),
            requested_at=NOW,
        )

        with pytest.raises(AuxiliaryInventoryError, match="caller-owned transaction"):
            capture_institutional_survey_audit(db, request=request, raw_provider=raw)

        assert raw.calls == []
        assert connection_calls == 0
    finally:
        db.close()


def test_production_sources_do_not_reference_the_removed_manual_auxiliary_dispatcher() -> None:
    root = Path(__file__).resolve().parents[1]
    production_paths = (
        root / "app/services/workbench_service.py",
        root / "app/services/feature_builder.py",
        root / "app/services/data_gap_remediation_service.py",
        root / "app/services/scheduler_service.py",
        root / "app/services/backtest_service.py",
        root / "app/services/single_stock_analysis.py",
        root / "app/api/data_sources.py",
        root / "app/services/market_pool/__init__.py",
        root / "scripts/backfill_auxiliary.py",
        root / "scripts/backfill_auxiliary_data.py",
        root / "scripts/rebuild_data_pool.py",
        root / "scripts/fix_index_daily_and_limit_up.py",
        root / "scripts/backfill_fina_indicator.py",
        root / "scripts/diagnose_skill_prefilter.py",
    )
    banned = (
        "market_pool.auxiliary",
        "load_auxiliary_for_stocks",
        "project_legacy_auxiliary_audit",
        "refresh_limit_up_board",
        "refresh_concept_sectors",
        "refresh_dragon_tiger",
        "refresh_capital_flows_for_codes",
        "refresh_news_for_codes",
        "refresh_block_trade",
    )
    for path in production_paths:
        text = path.read_text(encoding="utf-8")
        assert all(symbol not in text for symbol in banned), path
    assert not (root / "app/services/market_pool/auxiliary.py").exists()


def test_only_the_application_boundary_acquires_the_tushare_client() -> None:
    root = Path(__file__).resolve().parents[1]
    application = (root / "app/services/auxiliary/application.py").read_text(encoding="utf-8")
    assert "from app.services.tushare_client import get_pro" in application
    for relative in (
        "app/services/auxiliary/state_adapters.py",
        "app/services/auxiliary/composition.py",
        "app/services/auxiliary/event_snapshot.py",
    ):
        assert "from app.services.tushare_client import get_pro" not in (root / relative).read_text(encoding="utf-8")
