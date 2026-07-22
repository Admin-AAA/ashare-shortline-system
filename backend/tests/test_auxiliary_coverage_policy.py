from datetime import date, datetime, timezone

from sqlalchemy import create_engine

from app.models.tables import CapitalFlow
from app.services.auxiliary.contracts import AuxiliaryDatasetRequest, AuxiliaryInstrument
from app.services.auxiliary.production import (
    AUXILIARY_DATASET_INVENTORY,
    StandardAuxiliaryDatasetAdapters,
    assemble_standard_auxiliary_descriptors,
)
from app.services.auxiliary.state_adapters import SQLAlchemyStateRepository


class _Ready:
    def assert_ready(self, _entry) -> None:
        return None


class _Adapter:
    def fetch(self, _request):
        return ()

    def normalize(self, raw, _request):
        return raw

    def inspect(self, _request):
        raise AssertionError("descriptor assembly must not inspect storage")

    def upsert(self, _records, _request):
        raise AssertionError("descriptor assembly must not persist")

    def load(self, _request):
        raise AssertionError("descriptor assembly must not load")


def test_capital_flow_descriptor_requires_ninety_percent_instrument_coverage() -> None:
    entry = next(item for item in AUXILIARY_DATASET_INVENTORY if item.key == "capital_flow")
    adapter = _Adapter()
    descriptor = assemble_standard_auxiliary_descriptors(
        {
            "capital_flow": StandardAuxiliaryDatasetAdapters(
                fetcher=adapter,
                normalizer=adapter,
                repository=adapter,
                loader=adapter,
            )
        },
        schema_readiness=_Ready(),
        inventory=(entry,),
    )[0]
    requested = (AuxiliaryInstrument("000001"), AuxiliaryInstrument("000002"))

    partial = descriptor.coverage_policy.evaluate_records(
        ({"code": "000001"},), requested, source="test"
    )
    complete = descriptor.coverage_policy.evaluate_records(
        ({"code": "000001"}, {"code": "000002"}), requested, source="test"
    )

    assert partial.sufficient is False
    assert complete.sufficient is True


def test_daily_state_repository_inspects_only_requested_trade_date() -> None:
    engine = create_engine("sqlite:///:memory:")
    CapitalFlow.__table__.create(engine)
    entry = next(item for item in AUXILIARY_DATASET_INVENTORY if item.key == "capital_flow")
    request = AuxiliaryDatasetRequest(
        dataset_key="capital_flow",
        instruments=(AuxiliaryInstrument("000001"), AuxiliaryInstrument("000002")),
        trade_date=date(2026, 7, 21),
        refresh_enabled=False,
        requested_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )
    with engine.begin() as connection:
        connection.execute(
            CapitalFlow.__table__.insert(),
            (
                {"code": "000001", "trade_date": "2026-07-20"},
                {"code": "000002", "trade_date": "2026-07-21"},
            ),
        )

        snapshot = SQLAlchemyStateRepository(connection=connection, entry=entry).inspect(request)

    assert snapshot.record_count == 1
    assert snapshot.covered_instruments == frozenset({"000002"})
    assert snapshot.freshness == date(2026, 7, 21)
