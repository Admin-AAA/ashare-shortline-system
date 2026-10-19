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


def test_strict_failure_raises_instead_of_guessing_today(db):
    with pytest.raises(tc.TradeCalendarUnavailable):
        tc.latest_trade_date(date(2026, 10, 18), db=db, pro_factory=_pro(fail=True), strict=True)
    with pytest.raises(tc.TradeCalendarUnavailable):
        tc.open_dates_between(date(2026, 10, 1), date(2026, 10, 18), db=db, pro_factory=_pro(fail=True),
                              strict=True)


def test_failure_falls_back_to_local_plus_weekday_and_alerts(db, monkeypatch):
    alerts = []
    monkeypatch.setattr(tc, "_alert", lambda key, msg: alerts.append(key))
    # local table knows 10-01..10-08 is the National Day holiday
    for i in range(1, 9):
        db.add(tc.TradeCalendar(exchange="SSE", cal_date=f"202610{i:02d}", is_open=False,
                                synced_at=tc.utcnow_naive()))
    db.commit()
    got = tc.open_dates_between(date(2026, 10, 1), date(2026, 10, 18), db=db, pro_factory=_pro(fail=True))
    assert got == ["20261009", "20261012", "20261013", "20261014", "20261015", "20261016"]
    assert tc.latest_trade_date(date(2026, 10, 18), db=db, pro_factory=_pro(fail=True)) == "20261016"
    assert tc.is_open(date(2026, 10, 17), db=db, pro_factory=_pro(fail=True)) is False
    assert tc.is_open(date(2026, 10, 19), db=db, pro_factory=_pro(fail=True)) is True
    assert alerts


def test_intraday_reader_never_calls_upstream(db, monkeypatch):
    monkeypatch.setattr(tc, "in_trading_session", lambda now=None: True)
    monkeypatch.setattr(tc, "_alert", lambda key, msg: None)
    monkeypatch.setattr(tc, "REFRESH_AFTER_SECONDS", -1.0)

    def boom():
        raise AssertionError("intraday must not hit trade_cal upstream")
    import app.services.tushare_client as tcl
    monkeypatch.setattr(tcl, "get_pro", boom)
    db.add(tc.TradeCalendar(exchange="SSE", cal_date="20261019", is_open=True, synced_at=tc.utcnow_naive()))
    db.commit()
    assert tc.is_open(date(2026, 10, 19), db=db) is True          # cached
    assert tc.is_open(date(2026, 10, 24), db=db) is False         # uncovered -> weekday rule, no network
    assert tc.open_dates_between(date(2026, 10, 19), date(2026, 10, 21), db=db) == [
        "20261019", "20261020", "20261021"]


def test_in_trading_session_window():
    from datetime import datetime
    assert tc.in_trading_session(datetime(2026, 10, 19, 10, 5, tzinfo=tc.CST))
    assert not tc.in_trading_session(datetime(2026, 10, 19, 15, 30, tzinfo=tc.CST))
    assert not tc.in_trading_session(datetime(2026, 10, 18, 10, 5, tzinfo=tc.CST))


def test_refresh_trade_calendar_failure_alerts_and_keeps_local(db, monkeypatch):
    alerts = []
    monkeypatch.setattr(tc, "_alert", lambda key, msg: alerts.append(key))
    assert tc.refresh_trade_calendar(db=db, pro_factory=_pro(fail=True))["status"] == "failed"
    assert alerts == ["refresh"]
    assert tc.refresh_trade_calendar(db=db, pro_factory=_pro())["status"] == "ok"


def test_akshare_and_baostock_trade_cal_backups(monkeypatch):
    from types import SimpleNamespace
    from app.services.datasource import akshare_backup as ab, baostock_backup as bb

    ab.clear_cache()
    monkeypatch.setattr(ab, "_limit", lambda: None)
    monkeypatch.setattr(ab, "_ak", lambda: SimpleNamespace(tool_trade_date_hist_sina=lambda: pd.DataFrame(
        {"trade_date": [date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20)]})))
    rows = ab.AkshareProvider().fetch("tushare.trade_cal", {"params": {
        "exchange": "SSE", "start_date": "20261016", "end_date": "20261231"}})
    assert [(r["cal_date"], r["is_open"]) for r in rows] == [
        ("20261016", 1), ("20261017", 0), ("20261018", 0), ("20261019", 1), ("20261020", 1)]
    only_open = ab.AkshareProvider().fetch("tushare.trade_cal", {"params": {
        "start_date": "20261016", "end_date": "20261020", "is_open": "1"}})
    assert [r["cal_date"] for r in only_open] == ["20261016", "20261019", "20261020"]

    class RS:
        error_code, error_msg, fields = "0", "", ["calendar_date", "is_trading_day"]
        data = [["2026-10-16", "1"], ["2026-10-17", "0"], ["2026-10-18", "0"], ["2026-10-19", "1"]]

        def next(self):
            return bool(self.data)

        def get_row_data(self):
            return self.data.pop(0)
    fake = SimpleNamespace(login=lambda: SimpleNamespace(error_code="0", error_msg=""),
                           logout=lambda: None, query_trade_dates=lambda **kw: RS())
    monkeypatch.setattr(bb, "_bs", lambda: fake)
    rows = bb.BaostockTradeCalProvider().fetch("tushare.trade_cal", {"params": {
        "start_date": "20261016", "end_date": "20261019"}})
    assert [(r["cal_date"], r["is_open"]) for r in rows] == [
        ("20261016", 1), ("20261017", 0), ("20261018", 0), ("20261019", 1)]
    assert "trade_cal" in ab.AKSHARE_BACKUP_APIS
    names = [p.name for p in ab.DatasetGateway([ab.AkshareProvider(), bb.BaostockTradeCalProvider()])._providers]
    assert names == ["akshare", "baostock"]


def test_stale_refresh_failure_keeps_local(db, monkeypatch):
    tc.sync_trade_calendar(db, pro_factory=_pro())
    monkeypatch.setattr(tc, "REFRESH_AFTER_SECONDS", -1.0)
    assert tc.open_dates_between(date.today() - timedelta(days=7), date.today(), db=db, pro_factory=_pro(fail=True))


def test_trade_cal_route_includes_quantdata():
    from app.services.tushare_transport import TushareProviderRouter
    assert "quantdata" in TushareProviderRouter.DEFAULT_API_ROUTES["trade_cal"]
