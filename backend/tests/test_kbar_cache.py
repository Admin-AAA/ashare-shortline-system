"""日K Parquet 缓存：增量刷新 + 与 SQL 路径逐值一致（临时 sqlite）。"""
from __future__ import annotations

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.tables import Base
    from app.services.market_pool import kbar_cache

    engine = create_engine(f"sqlite:///{tmp_path/'k.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(kbar_cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(kbar_cache, "PARQUET", tmp_path / "cache" / "daily_kbar.parquet")
    monkeypatch.setattr(kbar_cache, "FP_FILE", tmp_path / "cache" / "fp.json")
    kbar_cache._mem["mtime"] = None
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    kbar_cache._mem["mtime"] = None


def _add(db, code, day, close, chip=None):
    from app.models.tables import DailyKbar
    db.add(DailyKbar(code=code, trade_date=day, open=close, high=close, low=close, close=close,
                     volume=1.0, amount=2.0, pct_chg=0.5, turnover_rate=1.2, ma5=close,
                     chip_cost_15=chip, chip_cost_85=(chip * 1.1 if chip else None), upper_shadow_count20=2))


def test_cache_matches_sql_and_refreshes_changes(db, monkeypatch):
    from app.services.market_pool import kbar_cache
    from app.services.market_pool.core import _load_cached_bars_batch
    from app.models.tables import DailyKbar

    days = [f"2026-09-{d:02d}" for d in range(1, 11)]
    for i, d in enumerate(days):
        _add(db, "000001", d, 10 + i, chip=9.0)
        if i >= 6:
            _add(db, "000002", d, 20 + i)  # 短历史股票
    db.commit()
    st = kbar_cache.refresh_kbar_cache(db)
    assert st["rows"] == 14

    def both(as_of, limit=5):
        monkeypatch.setenv("ASHARE_KBAR_CACHE", "1")
        a = _load_cached_bars_batch(db, ["000001", "000002", "000009"], limit, as_of_date=as_of)
        monkeypatch.setenv("ASHARE_KBAR_CACHE", "0")
        b = _load_cached_bars_batch(db, ["000001", "000002", "000009"], limit, as_of_date=as_of)
        return a, b

    for as_of in ("2026-09-05", "2026-09-10"):
        a, b = both(as_of)
        assert a == b and [list(x) for x in a.values()] == [list(x) for x in b.values()]

    # 改历史值 → 增量刷新只重拉该日，结果仍一致
    row = db.query(DailyKbar).filter_by(code="000001", trade_date="2026-09-03").one()
    row.close = 99.0
    db.commit()
    st = kbar_cache.refresh_kbar_cache(db)
    assert st["changed_dates"] == 1
    a, b = both("2026-09-10", limit=10)
    assert a == b

    # 缓存不覆盖的日期 → 回退 SQL
    monkeypatch.setenv("ASHARE_KBAR_CACHE", "1")
    assert kbar_cache.load_rows(["000001"], 5, "2026-09-30") is None


def test_disabled_on_sqlite_by_default(monkeypatch):
    from app.services.market_pool import kbar_cache
    monkeypatch.delenv("ASHARE_KBAR_CACHE", raising=False)
    monkeypatch.setattr("app.core.config.get_settings", lambda: type("S", (), {"database_url": "sqlite://"})())
    assert kbar_cache.enabled() is False


def test_polars_rows_match_to_pylist_with_nulls_and_ints():
    import gc
    import math

    import polars as pl
    import pyarrow as pa

    from app.services.market_pool import kbar_cache

    table = pa.table({
        "f": pa.chunked_array([[1.5, None, -0.0], [3.25, None]], type=pa.float64()),
        "i": pa.chunked_array([[1, 2, 3, 4, 5]], type=pa.int64()),
        "ni": pa.chunked_array([[None, 7, None, 8, None]], type=pa.int64()),
        "n": pa.chunked_array([[None] * 5], type=pa.null()),
        "s": pa.chunked_array([["000001", "600000", "a", "b", "c"]], type=pa.string()),
    })
    got = pl.from_arrow(table).rows(named=True)
    want = table.to_pylist()
    assert got == want
    for g, w in zip(got, want):
        assert [type(v) for v in g.values()] == [type(v) for v in w.values()]
        assert list(g) == list(w)
    assert math.copysign(1, got[2]["f"]) == -1
    was = gc.isenabled()
    with kbar_cache.gc_paused():
        assert not gc.isenabled()
    assert gc.isenabled() == was
