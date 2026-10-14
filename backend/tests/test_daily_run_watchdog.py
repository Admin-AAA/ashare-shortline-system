import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from scripts.daily_run_watchdog import CST, evaluate  # noqa: E402


def _t(y, m, d, h=18, mi=30):
    return dt.datetime(y, m, d, h, mi, tzinfo=CST)


def test_missing_run_on_weekday_alerts():
    kinds = [k for k, _ in evaluate(_t(2026, 10, 14), 0, True)]
    assert kinds == ["DAILY_RUN_MISSING"]


def test_prefect_down_alerts_even_if_ran():
    kinds = [k for k, _ in evaluate(_t(2026, 10, 14), 1, False)]
    assert kinds == ["PREFECT_DOWN"]


def test_ok_and_weekend_quiet():
    assert evaluate(_t(2026, 10, 14), 1, True) == []
    assert evaluate(_t(2026, 10, 17), 0, False) == []  # Saturday
