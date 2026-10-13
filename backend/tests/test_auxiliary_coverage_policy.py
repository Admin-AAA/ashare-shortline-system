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


def test_margin_detail_partial_write_is_insufficient_but_empty_day_is_not() -> None:
    """全市场刷新不传 instruments，只有行数下限能判出「有数据但数据不全」。

    margin_detail 曾经在库内只有 2002 行（上游 4452 行）的情况下被判为 fresh，
    补数入口报 errors=[] 自称成功却一行未写。空集是另一回事：T+1 披露日当天
    上游本就返回 0 行，不能判成缺口。
    """
    entry = next(item for item in AUXILIARY_DATASET_INVENTORY if item.key == "margin_detail")
    assert entry.minimum_record_count > 0, "全市场数据集必须声明行数下限，否则部分写入无法被发现"

    adapter = _Adapter()
    descriptor = assemble_standard_auxiliary_descriptors(
        {
            "margin_detail": StandardAuxiliaryDatasetAdapters(
                fetcher=adapter,
                normalizer=adapter,
                repository=adapter,
                loader=adapter,
            )
        },
        schema_readiness=_Ready(),
        inventory=(entry,),
    )[0]
    policy = descriptor.coverage_policy
    floor = entry.minimum_record_count

    def evaluate(record_count: int):
        return policy.evaluate_snapshot(
            record_count=record_count,
            covered_instruments=frozenset(),
            requested_instruments=(),
            source="test",
        )

    assert evaluate(floor - 1).sufficient is False
    assert evaluate(floor).sufficient is True
    assert evaluate(0).sufficient is True


def test_capital_flow_top_n_partial_day_is_insufficient_for_full_market_only() -> None:
    """L-166：09-10 起资金流向每天只补前 300 只，全市场批量同步却按「当天有行」判 fresh。

    行数下限只描述全市场规模；按股票请求时行数已被过滤到请求范围，由覆盖率判定，
    否则正式推荐的 153 只候选会因为「153 < 4500」被误判不足。
    """
    entry = next(item for item in AUXILIARY_DATASET_INVENTORY if item.key == "capital_flow")
    assert entry.minimum_record_count > 300

    adapter = _Adapter()
    descriptor = assemble_standard_auxiliary_descriptors(
        {
            "capital_flow": StandardAuxiliaryDatasetAdapters(
                fetcher=adapter, normalizer=adapter, repository=adapter, loader=adapter,
            )
        },
        schema_readiness=_Ready(),
        inventory=(entry,),
    )[0]
    policy = descriptor.coverage_policy

    full_market = policy.evaluate_snapshot(
        record_count=300, covered_instruments=frozenset(), requested_instruments=(), source="test",
    )
    assert full_market.sufficient is False

    requested = tuple(AuxiliaryInstrument(code=f"{i:06d}") for i in range(153))
    all_covered = policy.evaluate_snapshot(
        record_count=153,
        covered_instruments=frozenset(item.code for item in requested),
        requested_instruments=requested,
        source="test",
    )
    assert all_covered.sufficient is True
    mostly_missing = policy.evaluate_snapshot(
        record_count=30,
        covered_instruments=frozenset(item.code for item in requested[:30]),
        requested_instruments=requested,
        source="test",
    )
    assert mostly_missing.sufficient is False
