"""daily_basic 日更写入 + 停更告警（ashare_test）。"""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core import alerts
from app.services import daily_basic_ingest as dbi
from tests.conftest import _resolve_test_db_url

D = "2099-01-02"
CODES = ["999981", "999982"]


@pytest.fixture()
def db():
    try:
        eng = create_engine(_resolve_test_db_url())
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"ashare_test unavailable: {exc}")
    s = Session(eng)

    def clean():
        s.execute(text("DELETE FROM daily_basic WHERE trade_date=:d"), {"d": D})
        s.execute(text("DELETE FROM daily_kbars WHERE trade_date=:d"), {"d": D})
        s.commit()

    clean()
    for c in CODES:
        s.execute(text("INSERT INTO daily_kbars (code, trade_date, open, high, low, close, volume, amount, pct_chg, "
                       "turnover_rate, breakout20, breakout60, upper_shadow_count20, source, updated_at) "
                       "VALUES (:c, :d, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 'test', UTC_TIMESTAMP())"), {"c": c, "d": D})
    s.commit()
    alerts.reset_alerts()
    yield s
    clean()
    s.close()


class FakePro:
    def __init__(self, df):
        self.df = df
        self.calls = []

    def daily_basic(self, **kw):
        self.calls.append(kw)
        return self.df


def _df(n=2):
    return pd.DataFrame({
        "ts_code": [f"{c}.SZ" for c in CODES][:n], "trade_date": ["20990102"] * n,
        "turnover_rate": [1.5, float("nan")][:n], "pe_ttm": [10.0, None][:n],
        "total_mv": [123456.0, 2.0][:n], "circ_mv": [100000.0, 1.0][:n],
    })


def test_staleness_alert_then_ingest_clears(db, caplog):
    before = dbi.check_daily_basic_staleness(db, latest=D)
    assert before["ok"] is False
    assert "daily_basic_stale" in caplog.text
    out = dbi.ingest_daily_basic(db, D, pro=FakePro(_df()))
    assert out["status"] == "ok" and out["fetched"] == 2 and out["coverage"] == 1.0
    rows = db.execute(text("SELECT code, turnover_rate, pe_ttm, total_mv FROM daily_basic WHERE trade_date=:d "
                           "ORDER BY code"), {"d": D}).all()
    assert rows[0][3] == pytest.approx(123456.0)
    assert rows[1][1] is None and rows[1][2] is None  # 缺值写 NULL 不写 0
    # 幂等
    dbi.ingest_daily_basic(db, D, pro=FakePro(_df()))
    assert db.execute(text("SELECT COUNT(*) FROM daily_basic WHERE trade_date=:d"), {"d": D}).scalar() == 2
    assert dbi.check_daily_basic_staleness(db, latest=D)["ok"] is True


def test_incomplete_response_not_written(db):
    out = dbi.ingest_daily_basic(db, D, pro=FakePro(_df(1)))  # 1/2 < 90%
    assert out["status"] == "incomplete"
    assert db.execute(text("SELECT COUNT(*) FROM daily_basic WHERE trade_date=:d"), {"d": D}).scalar() == 0


def test_backfill_script_blocked_windows():
    p = Path(__file__).resolve().parent.parent / "scripts" / "backfill_daily_basic.py"
    spec = importlib.util.spec_from_file_location("bdb", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tz = timezone(timedelta(hours=8))
    mon = datetime(2026, 10, 19, tzinfo=tz)
    assert mod.blocked_window(mon.replace(hour=10)) == "盘中"
    assert mod.blocked_window(mon.replace(hour=17)) == "日更窗口"
    assert mod.blocked_window(mon.replace(hour=15, minute=10)) is None
    assert mod.blocked_window(datetime(2026, 10, 18, 10, tzinfo=tz)) is None


def test_freshness_check_flags_lag_and_dead_columns(db):
    from app.services import data_freshness as df_mod

    db.execute(text("INSERT INTO daily_basic (code, trade_date, turnover_rate, pe_ttm, total_mv, circ_mv) "
                    "VALUES ('999981', :d, 0, NULL, 0, NULL), ('999982', :d, NULL, NULL, 0, 0)"), {"d": D})
    db.commit()
    r = df_mod.check_daily_table(db, "daily_basic", "trade_date", 0, ("total_mv",), True, dates=[D, "2099-01-03"])
    assert r["lag_trade_days"] == 1
    assert any("滞后" in i for i in r["issues"])
    assert any("关键列全 0/NULL: total_mv" in i for i in r["issues"])


def test_freshness_task_registered_and_dispatched():
    from app.services import scheduler_pure, scheduler_service

    assert scheduler_pure.DATA_FRESHNESS_CRON == "0 18 * * mon-fri"
    assert hasattr(scheduler_service, "_ensure_data_freshness_task")
    assert hasattr(scheduler_service, "_run_data_freshness_check")
