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
