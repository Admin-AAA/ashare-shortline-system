import app.services.single_stock_analysis as m


class _Delegate:
    def load_universe(self, *, data_source, as_of_date):
        return ({"code": "000001"}, {"code": "600519"}), {"trade_date": "2026-10-16", "sample_count": 4971}, {}


def _universe(monkeypatch, resolved):
    u = m._SingleStockMarketUniverse.__new__(m._SingleStockMarketUniverse)
    u._delegate, u._db, u._code, u.snapshot_source = _Delegate(), None, "000001", "pipeline_full_universe"
    monkeypatch.setattr(m, "_resolve_analysis_market_snapshot", lambda db, d, q: resolved)
    return u


def test_prefers_same_day_formal_snapshot(monkeypatch):
    u = _universe(monkeypatch, ({"trade_date": "2026-10-16", "sample_count": 4950}, "formal_run_snapshot"))
    stocks, snap, audit = u.load_universe(data_source="x", as_of_date=None)
    assert [s["code"] for s in stocks] == ["000001"]
    assert snap["sample_count"] == 4950
    assert u.snapshot_source == audit["market_snapshot_source"] == "formal_run_snapshot"


def test_falls_back_to_full_universe_not_minimal(monkeypatch):
    u = _universe(monkeypatch, ({"sample_count": 1}, "minimal_fallback"))
    _, snap, _ = u.load_universe(data_source="x", as_of_date=None)
    assert snap["sample_count"] == 4971
    assert u.snapshot_source == "pipeline_full_universe"
