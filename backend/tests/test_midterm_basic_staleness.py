from unittest import mock

from app.services import midterm_service as m


class _R:
    def __init__(self, v):
        self.v = v

    def scalar(self):
        return self.v


class _DB:
    def __init__(self, bd, lag):
        self.bd, self.lag = bd, lag

    def execute(self, q, params=None):
        return _R(self.bd if "MAX(trade_date) FROM daily_basic" in str(q) else self.lag)


def test_stale_alerts():
    with mock.patch("app.core.alerts.emit_alert") as ea:
        info = m.check_daily_basic_staleness(_DB("2026-10-09", 5), "2026-10-16")
    assert info["stale"] and info["lag_trading_days"] == 5
    ea.assert_called_once()


def test_fresh_no_alert():
    with mock.patch("app.core.alerts.emit_alert") as ea:
        info = m.check_daily_basic_staleness(_DB("2026-10-16", 0), "2026-10-16")
    assert not info["stale"]
    ea.assert_not_called()


def test_missing_snapshot_alerts():
    with mock.patch("app.core.alerts.emit_alert") as ea:
        info = m.check_daily_basic_staleness(_DB(None, 0), "2026-10-16")
    assert info["stale"] and info["lag_trading_days"] is None
    ea.assert_called_once()
