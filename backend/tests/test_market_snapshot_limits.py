from app.services.market_source.snapshot import _build_quote_snapshot


def test_market_snapshot_counts_price_limits_by_board_and_st_status() -> None:
    quotes = [
        {"code": "600000", "name": "浦发银行", "pct": 9.9, "trade_date": "2026-07-21"},
        {"code": "000001", "name": "平安银行", "pct": -9.9, "trade_date": "2026-07-21"},
        {"code": "300001", "name": "特锐德", "pct": 10.0, "trade_date": "2026-07-21"},
        {"code": "300002", "name": "神州泰岳", "pct": 19.9, "trade_date": "2026-07-21"},
        {"code": "688001", "name": "华兴源创", "pct": -19.9, "trade_date": "2026-07-21"},
        {"code": "830001", "exchange": "BJ", "name": "北交样本", "pct": 20.0, "trade_date": "2026-07-21"},
        {"code": "830002", "exchange": "BJ", "name": "北交跌停", "pct": -29.9, "trade_date": "2026-07-21"},
        {"code": "600001", "name": "ST样本", "is_st": True, "pct": 4.9, "trade_date": "2026-07-21"},
        {"code": "600002", "name": "*ST名称样本", "pct": -4.9, "trade_date": "2026-07-21"},
    ]

    snapshot = _build_quote_snapshot(quotes, "local", "test", [])

    assert snapshot["limit_up_count"] == 3
    assert snapshot["limit_down_count"] == 4
