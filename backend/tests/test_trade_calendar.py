from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.market import TradeCalendar
from app.services import trade_calendar as tc


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    TradeCalendar.__table__.create(engine)
    s = Session(engine)
    monkeypatch.setattr(tc, "_last_sync_attempt", 0.0)
    yield s
    s.close()


def _pro(open_fn=lambda d: d.weekday() < 5, fail=False):
    class Pro:
        calls = 0

        def trade_cal(self, exchange, start_date, end_date, **_):
            Pro.calls += 1
            if fail:
                raise RuntimeError("all providers failed")
            s = pd.Timestamp(start_date).date(); e = pd.Timestamp(end_date).date()
            days = [s + timedelta(days=i) for i in range((e - s).days + 1)]
            return pd.DataFrame({"cal_date": [d.strftime("%Y%m%d") for d in days],
                                 "is_open": [1 if open_fn(d) else 0 for d in days]})
    p = Pro()
    return lambda: p


def test_latest_trade_date_skips_weekend_and_holiday(db):
    holiday = date(2026, 10, 16)
    pro = _pro(lambda d: d.weekday() < 5 and d != holiday)
    assert tc.latest_trade_date(date(2026, 10, 18), db=db, pro_factory=pro) == "20261015"
    assert tc.is_open(date(2026, 10, 18), db=db, pro_factory=pro) is False


def test_reads_local_without_remote_after_sync(db):
    pro = _pro()
    tc.sync_trade_calendar(db, pro_factory=pro)
    calls = pro().calls
    tc.open_dates_between(date.today() - timedelta(days=10), date.today(), db=db, pro_factory=pro)
    assert pro().calls == calls


def test_failure_raises_instead_of_guessing_today(db):
    with pytest.raises(tc.TradeCalendarUnavailable):
        tc.latest_trade_date(date(2026, 10, 18), db=db, pro_factory=_pro(fail=True))
    with pytest.raises(tc.TradeCalendarUnavailable):
        tc.open_dates_between(date(2026, 10, 1), date(2026, 10, 18), db=db, pro_factory=_pro(fail=True))


def test_stale_refresh_failure_keeps_local(db, monkeypatch):
    tc.sync_trade_calendar(db, pro_factory=_pro())
    monkeypatch.setattr(tc, "REFRESH_AFTER_SECONDS", -1.0)
    assert tc.open_dates_between(date.today() - timedelta(days=7), date.today(), db=db, pro_factory=_pro(fail=True))


def test_trade_cal_route_includes_quantdata():
    from app.services.tushare_transport import TushareProviderRouter
    assert "quantdata" in TushareProviderRouter.DEFAULT_API_ROUTES["trade_cal"]
