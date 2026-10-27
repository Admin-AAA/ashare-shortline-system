"""第 3 条：推荐天数按交易所日历数；第 1 日日 K 缺失（全市场/单只）判「数据未就绪」，停牌与缺数据分开。"""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.market import StockQuote, TradeCalendar
from app.models.tables import DailyKbar
from app.services import entry_window_state as ews
from app.services.execution_confirmation import _derive_decisions


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    for table in (DailyKbar, TradeCalendar, StockQuote):
        table.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    # 10-16 周五、10-17/18 周末、10-19 周一、10-20 周二
    for day in range(14, 22):
        session.add(TradeCalendar(exchange="SSE", cal_date=f"202610{day:02d}", is_open=day not in (17, 18)))
    session.commit()
    yield session
    session.close()


def _kbar(db, code, day, *, open_=10.1, close=10.2, high=10.3, low=9.9, volume=100, amount=1e6, pct=1.0):
    db.add(DailyKbar(code=code, trade_date=day, open=open_, high=high, low=low, close=close,
                     volume=volume, amount=amount, pct_chg=pct))


def test_signal_age_counts_exchange_calendar_not_kbars(db):
    # 全市场 10-19 日 K 还没入库：天数照样按日历数，周末不算
    assert ews.open_days_after(db, "2026-10-16", "2026-10-20") == ["2026-10-19", "2026-10-20"]
    assert ews.signal_age_trade_days(db, "2026-10-16", "2026-10-19") == 1
    assert ews.signal_age_trade_days(db, "2026-10-16", "2026-10-20") == 2


def test_signal_age_falls_back_to_market_kbars_when_calendar_missing(db):
    _kbar(db, "600000", "2026-11-02")
    _kbar(db, "600000", "2026-11-03")
    db.commit()
    assert ews.open_days_after(db, "2026-10-31", "2026-11-03") == ["2026-11-02", "2026-11-03"]


def test_first_day_status_buyable_and_blocked(db, monkeypatch):
    monkeypatch.setattr(ews, "MARKET_MIN_ROWS", 1)
    _kbar(db, "600000", "2026-10-19", open_=10.1)
    _kbar(db, "600001", "2026-10-19", open_=10.8, close=10.9, high=11.0, low=10.6, pct=9.0)  # 跳空 8%
    db.commit()
    assert ews.first_day_status(db, "600000", "2026-10-16", "2026-10-20", 10.0) == ("buyable", "2026-10-19")
    assert ews.first_day_status(db, "600001", "2026-10-16", "2026-10-20", 10.0) == ("blocked", "2026-10-19")


def test_first_day_status_whole_market_missing(db):
    assert ews.first_day_status(db, "600000", "2026-10-16", "2026-10-20", 10.0) == ("missing_market", "2026-10-19")


def test_first_day_status_single_code_missing_vs_suspended(db, monkeypatch):
    monkeypatch.setattr(ews, "MARKET_MIN_ROWS", 1)
    _kbar(db, "600000", "2026-10-19")
    # 600002 当日行情快照成交为 0 → 停牌；600003 没有任何停牌证据 → 缺数据
    db.add(StockQuote(code="600002", trade_date="2026-10-19", name="停牌股", price=10, volume=0, amount_yi=0,
                      source="test"))
    db.commit()
    assert ews.first_day_status(db, "600002", "2026-10-16", "2026-10-20", 10.0) == ("suspended", "2026-10-19")
    assert ews.first_day_status(db, "600003", "2026-10-16", "2026-10-20", 10.0) == ("missing_code", "2026-10-19")
    assert "suspended" in ews.SKIPPED_STATUSES and "missing_code" in ews.MISSING_STATUSES


def _decide(age, *, missing=False, blocked=False, window=2):
    entry, _ = _derive_decisions(
        fresh=True, signal_age_trade_days=age, entry_window_closed=False, prefilter_ok=True,
        signal={}, review=None, current_price=10.0, stop_loss=0.0,
        entry_check=SimpleNamespace(blocked=False, inconclusive=False, reason="ok", rule_id="ok", observed={}),
        prior_day_blocked=blocked, prior_day_missing=missing, window_days=window,
    )
    return entry


def test_day1_missing_is_not_ready_inside_window_not_buy_not_expired():
    entry = _decide(2, missing=True)
    assert entry["code"] == "not_ready" and entry["label"] == "数据未就绪"


def test_day1_missing_past_window_expires_by_calendar():
    assert _decide(3, missing=True)["code"] == "expired"


def test_suspended_day1_lets_day2_confirm():
    assert _decide(2, blocked=True)["code"] == "buy"


def test_first_day_blocked_requires_bar():
    from app.services.entry_day_checks import first_day_blocked

    with pytest.raises(ValueError):
        first_day_blocked("600000", None, 10.0)


def test_day1_status_in_window_scope(db, monkeypatch):
    from app.services import execution_confirmation as ec

    rec = SimpleNamespace(code="600000", trade_date="2026-10-16", recommend_price=10.0,
                          recommendation_id="r1", name="x", entry_rule="", invalid_rule="",
                          feature_snapshot_json="{}", recommended_at=None, bucket="sector-relative-strength-leader")
    monkeypatch.setattr(ec, "_is_intraday_recommendation", lambda _r: False)
    assert ec.day1_status_in_window(db, rec, signal_age_trade_days=1, window_days=2, data_as_of="2026-10-19") == ("none", None)
    assert ec.day1_status_in_window(db, rec, signal_age_trade_days=3, window_days=2, data_as_of="2026-10-21")[0] == "past_window"
    assert ec.day1_status_in_window(db, rec, signal_age_trade_days=2, window_days=1, data_as_of="2026-10-20") == ("none", None)
    assert ec.day1_status_in_window(db, rec, signal_age_trade_days=2, window_days=2, data_as_of="2026-10-20")[0] == "missing_market"


def test_day1_missing_alert_emits_then_resolves(monkeypatch):
    from app.core import alerts
    from app.services import execution_confirmation as ec

    alerts.reset_alerts()
    rec = SimpleNamespace(code="600000", name="x", trade_date="2026-10-16", recommendation_id="r_alert")
    ec.sync_day1_missing_alert(rec, "missing_market", "2026-10-19")
    assert [a["key"] for a in alerts.active_alert_records(ec.DAY1_MISSING_ALERT_KIND)] == ["r_alert"]
    ec.sync_day1_missing_alert(rec, "buyable", "2026-10-19")
    assert alerts.active_alert_records(ec.DAY1_MISSING_ALERT_KIND) == []


def test_calendar_ending_before_end_date_falls_back_to_kbars_and_alerts(db, monkeypatch):
    """日历只到 10-21，要查到 10-23：不拿半截日历数，退回全市场日 K 日期并告警；覆盖后解除。"""
    from app.core import alerts

    alerts.reset_alerts()
    # monotonic 从开机算起。写成 0 时，开机不足冷却窗口的 CI 会把解除告警直接丢掉。
    monkeypatch.setattr(
        ews, "_last_resolve_check", ews.time.monotonic() - ews._RESOLVE_CHECK_INTERVAL - 1
    )
    for d in ("2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"):
        _kbar(db, "600000", d)
    db.commit()
    # 日历 10-14..10-21（10-17/18 休市）只覆盖一半
    assert ews.open_days_after(db, "2026-10-16", "2026-10-23") == [
        "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]
    assert [a["key"] for a in alerts.active_alert_records(ews.CALENDAR_GAP_ALERT_KIND)] == ["SSE"]
    # 日历补齐后按日历数，并解除告警
    for day in (22, 23):
        db.add(TradeCalendar(exchange="SSE", cal_date=f"202610{day}", is_open=True))
    db.commit()
    assert ews.open_days_after(db, "2026-10-16", "2026-10-23")[-1] == "2026-10-23"
    assert alerts.active_alert_records(ews.CALENDAR_GAP_ALERT_KIND) == []
