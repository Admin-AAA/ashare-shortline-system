"""L-165：盘后 17:35 决策时刻不可见的辅助数据（龙虎榜/两融）只能读严格早于信号日的数据。"""
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import DragonTiger
from app.services.auxiliary.state_adapters import _StateViews


class _Req:
    def __init__(self, d, codes=()):
        self.trade_date = d
        self.requested_codes = list(codes)


def _session():
    engine = create_engine("sqlite://")
    DragonTiger.__table__.create(engine)
    return Session(engine)


def test_dragon_tiger_same_day_not_visible():
    s = _session()
    s.add(DragonTiger(code="000001", trade_date=date(2026, 9, 1)))
    s.add(DragonTiger(code="000002", trade_date=date(2026, 9, 2)))
    s.commit()
    views = _StateViews(s, as_of_date=date(2026, 9, 2))
    out = views.dragon_tiger(_Req(date(2026, 9, 2)))
    assert set(out["dragon_tigers"]) == {"000001"}
    assert out["_coverage"]["covered_trade_date"] == "2026-09-01"


def test_dragon_tiger_no_history_returns_empty():
    s = _session()
    views = _StateViews(s, as_of_date=date(2026, 9, 2))
    out = views.dragon_tiger(_Req(date(2026, 9, 2)))
    assert out["dragon_tigers"] == {}


def _session_with_kbars(*days):
    from app.models.tables import DailyKbar

    engine = create_engine("sqlite://")
    DragonTiger.__table__.create(engine)
    DailyKbar.__table__.create(engine)
    s = Session(engine)
    for d in days:
        s.add(DailyKbar(code="000001", trade_date=d))
    return s


def test_dragon_tiger_previous_day_counts_as_covered_for_signal_day():
    """L-170：读前一交易日榜单是设计行为，覆盖校验不能因此把全市场判成龙虎榜缺失。"""
    from app.services.feature_builder import _dragon_tiger_trade_date_covered

    s = _session_with_kbars("2026-09-01", "2026-09-02")
    s.add(DragonTiger(code="000001", trade_date=date(2026, 9, 1)))
    s.commit()
    out = _StateViews(s, as_of_date=date(2026, 9, 2)).dragon_tiger(_Req(date(2026, 9, 2)))
    assert out["_coverage"]["decision_visible_for"] == "2026-09-02"
    assert _dragon_tiger_trade_date_covered({"dragon_tiger": out["_coverage"]}, None, "2026-09-02")


def test_dragon_tiger_stale_list_is_not_covered():
    """前一交易日榜单缺失（入库断档）时，更早的榜单不能冒充当期，仍判缺失。"""
    from app.services.feature_builder import _dragon_tiger_trade_date_covered

    s = _session_with_kbars("2026-08-31", "2026-09-01", "2026-09-02")
    s.add(DragonTiger(code="000001", trade_date=date(2026, 8, 31)))
    s.commit()
    out = _StateViews(s, as_of_date=date(2026, 9, 2)).dragon_tiger(_Req(date(2026, 9, 2)))
    assert out["_coverage"]["decision_visible_for"] is None
    assert not _dragon_tiger_trade_date_covered({"dragon_tiger": out["_coverage"]}, None, "2026-09-02")
