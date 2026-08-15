from __future__ import annotations

from app.services.runs.stages.adapters import ProductionMarketUniverse


def test_production_market_universe_uses_historical_pool_for_as_of_date(monkeypatch):
    captured: dict[str, str] = {}

    def fake_load(_db, as_of_date):
        captured["as_of_date"] = as_of_date
        return (
            [{"code": "000001", "name": "历史候选"}],
            {"snapshot_id": f"snapshot_pool_historical_{as_of_date}", "trade_date": as_of_date},
        )

    monkeypatch.setattr(
        "app.services.backtest.historical_universe.load_historical_universe",
        fake_load,
    )

    stocks, snapshot, audit = ProductionMarketUniverse(object()).load_universe(
        data_source="local_pool",
        as_of_date="2026-05-14",
    )

    assert captured == {"as_of_date": "2026-05-14"}
    assert stocks == ({"code": "000001", "name": "历史候选"},)
    assert snapshot["snapshot_id"] == "snapshot_pool_historical_2026-05-14"
    assert audit["source"] == "historical_pool"
    assert audit["status"] == "success"
