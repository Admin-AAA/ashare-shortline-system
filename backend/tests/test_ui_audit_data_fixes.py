"""UI 审计 + 指标列修复（2026-10-19）。DB 用例走 ashare_test。"""
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services import data_live_sources, data_source_live_stats
from app.services.indicator_computer import bias, max_drawdown
from app.services.local_indicator_fill import compute_one
from tests.conftest import _resolve_test_db_url

CODE = "999971"


def _bars(n=80):
    c = np.linspace(10, 12, n) + np.sin(np.arange(n))
    return pd.DataFrame({"open": c - 0.1, "high": c + 0.3, "low": c - 0.3, "close": c, "volume": np.arange(n) + 100.0})


def test_compute_one_matches_reference_and_nulls_short_windows():
    g = _bars()
    r = compute_one(g)
    c = g["close"].tolist()
    assert r["max_dd60"].iloc[-1] == pytest.approx(max_drawdown(c[-60:]), abs=1e-3)
    assert r["bias_10"].iloc[-1] == pytest.approx(bias(c[-1], float(np.mean(c[-10:]))), abs=1e-3)
    assert r["max_dd60"].iloc[:59].isna().all()  # 窗口不足 → NULL，不是 0
    assert r["ema_50"].iloc[:49].isna().all()
    assert not np.isinf(r.to_numpy(dtype=float)).any()


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
    g = _bars()
    for i, row in g.iterrows():
        d = (pd.Timestamp("2098-01-01") + pd.Timedelta(days=int(i))).strftime("%Y-%m-%d")
        s.execute(text("INSERT INTO daily_kbars (code, trade_date, open, high, low, close, volume, amount, pct_chg, "
                       "turnover_rate, breakout20, breakout60, upper_shadow_count20, max_dd20, source, updated_at) "
                       "VALUES (:c, :d, :o, :h, :l, :cl, :v, 1, 0, 0, 0, 0, 0, :dd, 'test', UTC_TIMESTAMP())"),
                  {"c": CODE, "d": d, "o": row.open, "h": row.high, "l": row.low, "cl": row.close, "v": row.volume,
                   "dd": -99.0})
    s.commit()
    yield s
    s.execute(text("DELETE FROM daily_kbars WHERE code=:c"), {"c": CODE})
    s.commit()
    s.close()


def test_fill_local_indicators_fills_null_only(db):
    from app.services.local_indicator_fill import fill_local_indicators

    out = fill_local_indicators(db, since="2098-01-01", end="2098-12-31")
    assert out["rows_changed"] > 0
    last = db.execute(text("SELECT max_dd60, bias_10, max_dd20, obv FROM daily_kbars WHERE code=:c "
                           "ORDER BY trade_date DESC LIMIT 1"), {"c": CODE}).one()
    assert last[0] is not None and last[1] is not None and last[3] is not None
    assert last[2] == pytest.approx(-99.0)  # 已有值不覆盖
    first = db.execute(text("SELECT max_dd60 FROM daily_kbars WHERE code=:c ORDER BY trade_date LIMIT 1"),
                       {"c": CODE}).scalar()
    assert first is None


def test_freshness_indicator_check_flags_empty_columns(db):
    from app.services.data_freshness import check_indicator_columns

    r = check_indicator_columns(db, "2098-01-01")
    assert "profit_efficiency" in r["empty_columns"]
    assert r["issues"] and "atr" in r["issues"][0]
    assert "profit_efficiency" not in r["issues"][0]


def test_repurchase_live_source_reads_parquet(tmp_path, monkeypatch):
    pd.DataFrame({"ann_date": ["20261017", "20260101"]}).to_parquet(tmp_path / "repurchase_2026.parquet")
    monkeypatch.setattr(data_live_sources, "EVENTS_DIR", tmp_path)
    assert data_live_sources.repurchase_events_latest() == (2, "2026-10-17")


def test_legacy_rows_get_live_stats(monkeypatch):
    monkeypatch.setattr(data_source_live_stats, "_live_stats",
                        lambda _db, ops: {"success_count": 9, "failure_count": 1, "empty_count": 0,
                                          "success_rate": 0.9, "last_success_at": "2026-10-18T16:58:41",
                                          "last_failure_at": None})
    rows = [{"source": "tushare", "source_type": "dragon_tiger", "success_rate": 1.0,
             "last_success_at": "2026-07-15T10:06:19", "success_count": 74, "failure_count": 0},
            {"source": "xiaoshi", "source_type": "kline", "success_rate": 0.538, "success_count": 7,
             "failure_count": 6, "last_success_at": "2026-10-19T01:44:16"},
            {"source": "promax", "source_type": "tushare_provider", "success_rate": 0.88}]
    data_source_live_stats.apply_live_stats(None, rows)
    assert rows[0]["last_success_at"].startswith("2026-10-18") and rows[0]["stats_basis"] == "gateway_traces_7d"
    assert rows[0]["probe_last_success_at"].startswith("2026-07-15")
    assert rows[1]["stats_basis"] == "manual_probe_cumulative"
    assert "stats_basis" not in rows[2]
