"""ivol_fill on the ashare_test DB: write path updates only changed rows and bumps updated_at."""
import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tests.conftest import _resolve_test_db_url

CODE, D = "999991", "2099-01-02"


@pytest.fixture()
def db():
    try:
        eng = create_engine(_resolve_test_db_url())
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"ashare_test unavailable: {exc}")
    s = Session(eng)
    s.execute(text("DELETE FROM daily_kbars WHERE code=:c"), {"c": CODE})
    s.execute(text("DELETE FROM index_daily WHERE trade_date=:d"), {"d": D})
    s.execute(text("INSERT INTO daily_kbars (code, trade_date, open, high, low, close, volume, amount, pct_chg, "
                   "turnover_rate, breakout20, breakout60, upper_shadow_count20, source, updated_at) "
                   "VALUES (:c, :d, 1, 1, 1, 1, 1, 1, 0.5, 0, 0, 0, 0, 'test', UTC_TIMESTAMP())"), {"c": CODE, "d": D})
    s.execute(text("INSERT INTO index_daily (ts_code, trade_date, close, pct_chg, open, high, low, volume, created_at) "
              "VALUES ('000300.SH', :d, 1, 0.1, 1, 1, 1, 1, UTC_TIMESTAMP())"),
              {"d": D})
    s.commit()
    yield s
    s.execute(text("DELETE FROM daily_kbars WHERE code=:c"), {"c": CODE})
    s.execute(text("DELETE FROM index_daily WHERE trade_date=:d"), {"d": D})
    s.commit()
    s.close()


def test_fill_ivol_range_writes_only_changes(db, monkeypatch):
    import app.services.ivol_fill as f

    fake = lambda kb, idx: pd.DataFrame([{"code": CODE, "trade_date": D, "ivol20": 0.0123456}])  # noqa: E731
    monkeypatch.setattr(f, "_builders", lambda: {"ivol20": fake})
    monkeypatch.setattr("app.services.risk_features.BENCHMARK_TS_CODE", "000300.SH")
    assert f.fill_ivol_range(db, D, D, "ivol20") == 1
    assert abs(db.execute(text("SELECT ivol20 FROM daily_kbars WHERE code=:c"), {"c": CODE}).scalar() - 0.012346) < 1e-9
    assert f.fill_ivol_range(db, D, D, "ivol20") == 0  # unchanged -> no write, no updated_at bump
    assert f.ivol_coverage(db, D) > 0
