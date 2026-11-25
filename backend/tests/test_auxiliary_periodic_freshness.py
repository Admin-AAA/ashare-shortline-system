"""期末/公告日类数据集的新鲜度必须卡请求日上界。

2026-11-24 体检：pledge_stat 历史补数报 errors=0、stored=47777040 却一行未写。
inspect 只在 freshness_field == "trade_date" 时约束日期，其余数据集取整表 max(end_date)。
库里已有 2026-11-20，请求 2026-10-30 时 target - observed 为负，判 FRESH 直接返回库内数据。
"""
from datetime import date, datetime, timezone

from sqlalchemy import create_engine

from app.models.tables import BrokerRecommend, FinaIndicator, HolderNumber, PledgeStat
from app.services.auxiliary.contracts import (
    AuxiliaryDatasetRequest,
    CoveragePolicy,
    RefreshMode,
    RefreshPolicy,
    RefreshReason,
)
from app.services.auxiliary.production import AUXILIARY_DATASET_BY_KEY
from app.services.auxiliary.state_adapters import SQLAlchemyStateRepository


def _request(key: str, trade_date: date) -> AuxiliaryDatasetRequest:
    return AuxiliaryDatasetRequest(
        dataset_key=key,
        instruments=(),
        trade_date=trade_date,
        refresh_enabled=True,
        requested_at=datetime.combine(trade_date, datetime.min.time(), tzinfo=timezone.utc),
    )


def _pledge_partition(end_date: str, size: int) -> list[dict]:
    return [{"code": f"{i:06d}", "end_date": end_date} for i in range(size)]


def _decide(entry, request, snapshot):
    policy = CoveragePolicy(
        minimum_record_count=entry.minimum_record_count,
        minimum_instrument_coverage=entry.minimum_instrument_coverage,
    )
    coverage = policy.evaluate_snapshot(
        record_count=snapshot.record_count,
        covered_instruments=snapshot.covered_instruments,
        requested_instruments=request.instruments,
        source="repository",
    )
    return RefreshPolicy(RefreshMode.IF_STALE).decide(request, snapshot, coverage)


def test_pledge_stat_history_is_not_satisfied_by_a_later_disclosure():
    engine = create_engine("sqlite:///:memory:")
    PledgeStat.__table__.create(engine)
    entry = AUXILIARY_DATASET_BY_KEY["pledge_stat"]
    request = _request("pledge_stat", date(2026, 10, 30))
    with engine.begin() as connection:
        connection.execute(PledgeStat.__table__.insert(), _pledge_partition("2026-09-30", entry.minimum_record_count))
        connection.execute(PledgeStat.__table__.insert(), _pledge_partition("2026-11-20", entry.minimum_record_count))
        snapshot = SQLAlchemyStateRepository(connection=connection, entry=entry).inspect(request)

    assert snapshot.record_count == entry.minimum_record_count
    assert snapshot.freshness == date(2026, 9, 30)
    decision = _decide(entry, request, snapshot)
    assert decision.required is True
    assert decision.reason is RefreshReason.STALE


def test_pledge_stat_requested_friday_already_stored_stays_fresh():
    engine = create_engine("sqlite:///:memory:")
    PledgeStat.__table__.create(engine)
    entry = AUXILIARY_DATASET_BY_KEY["pledge_stat"]
    request = _request("pledge_stat", date(2026, 11, 20))
    with engine.begin() as connection:
        connection.execute(PledgeStat.__table__.insert(), _pledge_partition("2026-11-20", entry.minimum_record_count))
        snapshot = SQLAlchemyStateRepository(connection=connection, entry=entry).inspect(request)

    assert snapshot.freshness == date(2026, 11, 20)
    assert _decide(entry, request, snapshot).required is False


def test_holder_number_history_uses_the_same_upper_bound():
    engine = create_engine("sqlite:///:memory:")
    HolderNumber.__table__.create(engine)
    entry = AUXILIARY_DATASET_BY_KEY["holder_number"]
    request = _request("holder_number", date(2026, 6, 30))
    with engine.begin() as connection:
        connection.execute(
            HolderNumber.__table__.insert(),
            (
                {"code": "000001", "end_date": "2026-03-31", "holder_num": 100},
                {"code": "000001", "end_date": "2026-09-30", "holder_num": 90},
            ),
        )
        snapshot = SQLAlchemyStateRepository(connection=connection, entry=entry).inspect(request)

    assert snapshot.record_count == 1
    assert snapshot.freshness == date(2026, 3, 31)
    assert _decide(entry, request, snapshot).required is True


def test_fina_indicator_compact_ann_date_is_bounded_by_request_day():
    """stock_fina_indicator.ann_date 存 YYYYMMDD，右值必须渲染成同样格式。"""
    engine = create_engine("sqlite:///:memory:")
    FinaIndicator.__table__.create(engine)
    entry = AUXILIARY_DATASET_BY_KEY["fina_indicator"]
    request = _request("fina_indicator", date(2026, 8, 20))
    with engine.begin() as connection:
        connection.execute(
            FinaIndicator.__table__.insert(),
            (
                {"code": "000001", "end_date": "20260331", "ann_date": "20260428"},
                {"code": "000001", "end_date": "20260630", "ann_date": "20260829"},
            ),
        )
        snapshot = SQLAlchemyStateRepository(connection=connection, entry=entry).inspect(request)

    assert snapshot.record_count == 1
    assert snapshot.freshness == date(2026, 4, 28)
    assert _decide(entry, request, snapshot).required is True


def test_broker_recommend_month_column_is_bounded_by_request_month():
    """broker_recommends.month 存 YYYYMM，按月份而不是按日比较。"""
    engine = create_engine("sqlite:///:memory:")
    BrokerRecommend.__table__.create(engine)
    entry = AUXILIARY_DATASET_BY_KEY["broker_recommend"]
    request = _request("broker_recommend", date(2026, 10, 15))
    with engine.begin() as connection:
        connection.execute(
            BrokerRecommend.__table__.insert(),
            (
                {"code": "000001", "month": "202610", "broker": "A"},
                {"code": "000001", "month": "202611", "broker": "A"},
            ),
        )
        snapshot = SQLAlchemyStateRepository(connection=connection, entry=entry).inspect(request)

    assert snapshot.record_count == 1


def test_pledge_stat_partial_latest_partition_is_detected():
    """规模按最新一期披露算：历史累计行数会把 116 行的残缺期掩盖掉。"""
    engine = create_engine("sqlite:///:memory:")
    PledgeStat.__table__.create(engine)
    entry = AUXILIARY_DATASET_BY_KEY["pledge_stat"]
    assert entry.minimum_record_count > 0, "周度全市场数据集必须声明行数下限"
    request = _request("pledge_stat", date(2026, 5, 22))
    complete = _pledge_partition("2026-05-15", entry.minimum_record_count)
    with engine.begin() as connection:
        connection.execute(PledgeStat.__table__.insert(), complete)
        connection.execute(PledgeStat.__table__.insert(), _pledge_partition("2026-05-22", 116))
        snapshot = SQLAlchemyStateRepository(connection=connection, entry=entry).inspect(request)

    assert snapshot.record_count == 116
    assert snapshot.freshness == date(2026, 5, 22)
    decision = _decide(entry, request, snapshot)
    assert decision.required is True
    assert decision.reason is RefreshReason.INSUFFICIENT_COVERAGE
