"""L-185：批次内共享 universe——scope 内同日只构建一次、读写深拷贝；scope 外不缓存。"""
from app.services.backtest import historical_universe as hu
import app.services.market_pool.core as core


def _patch(monkeypatch):
    calls = []
    monkeypatch.setattr(hu, "build_historical_quotes", lambda db, d, stock_metadata_provider=None: [])

    def fake(db, refresh=False, as_of_date=None, quotes_override=None):
        calls.append(as_of_date)
        return [{"code": "000001", "x": 1}], {"trade_date": as_of_date}

    monkeypatch.setattr(core, "get_market_universe_from_pool", fake)
    return calls


def test_no_scope_no_cache(monkeypatch):
    calls = _patch(monkeypatch)
    hu.load_historical_universe(None, "2026-10-12")
    hu.load_historical_universe(None, "2026-10-12")
    assert calls == ["2026-10-12", "2026-10-12"]


def test_scope_caches_with_copy_on_read(monkeypatch):
    calls = _patch(monkeypatch)
    with hu.shared_universe_scope():
        a, _ = hu.load_historical_universe(None, "2026-10-12")
        a[0]["x"] = 99  # 下游就地改写不得污染缓存
        b, _ = hu.load_historical_universe(None, "2026-10-12")
        hu.load_historical_universe(None, "2026-10-13")
    assert b[0]["x"] == 1
    assert calls == ["2026-10-12", "2026-10-13"]
    hu.load_historical_universe(None, "2026-10-12")  # scope 退出即清空
    assert calls[-1] == "2026-10-12" and len(calls) == 3
