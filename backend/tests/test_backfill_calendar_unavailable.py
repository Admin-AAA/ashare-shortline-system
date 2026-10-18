"""交易日历不可用时，回填脚本应明确失败（返回码 2），不按工作日猜日期。"""
import importlib.util
import sys
from pathlib import Path

from app.services.trade_calendar import TradeCalendarUnavailable

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_t_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _raise(*_a, **_k):
    raise TradeCalendarUnavailable("test")


class _DB:
    def close(self):
        pass


def test_backfill_daily_bars_exits_when_calendar_unavailable(monkeypatch):
    mod = _load("backfill_daily_bars")
    monkeypatch.setattr(sys, "argv", ["backfill_daily_bars.py"])
    monkeypatch.setattr(mod, "fetch_latest_trade_date", _raise)
    monkeypatch.setattr(mod, "SessionLocal", lambda: (_ for _ in ()).throw(AssertionError("must not open db")))
    assert mod.main() == 2


def test_backfill_latest_days_exits_when_calendar_unavailable(monkeypatch):
    mod = _load("backfill_latest_days")
    monkeypatch.setattr(mod, "SessionLocal", _DB)
    monkeypatch.setattr(mod, "get_missing_trade_dates", _raise)
    monkeypatch.setattr(mod, "batch_backfill_date", lambda *a: (_ for _ in ()).throw(AssertionError))
    assert mod.main() == 2
