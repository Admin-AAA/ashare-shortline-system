from app.services.market_source.snapshot import _build_quote_snapshot


def _quotes(trade_date: str, st_pct: float = 4.9) -> list[dict]:
    return [
        {"code": "600000", "name": "浦发银行", "pct": 9.9, "trade_date": trade_date},
        {"code": "000001", "name": "平安银行", "pct": -9.9, "trade_date": trade_date},
        {"code": "300001", "name": "特锐德", "pct": 10.0, "trade_date": trade_date},
        {"code": "300002", "name": "神州泰岳", "pct": 19.9, "trade_date": trade_date},
        {"code": "688001", "name": "华兴源创", "pct": -19.9, "trade_date": trade_date},
        {"code": "830001", "exchange": "BJ", "name": "北交样本", "pct": 20.0, "trade_date": trade_date},
        {"code": "830002", "exchange": "BJ", "name": "北交跌停", "pct": -29.9, "trade_date": trade_date},
        {"code": "600001", "name": "ST样本", "is_st": True, "pct": st_pct, "trade_date": trade_date},
        {"code": "600002", "name": "*ST名称样本", "pct": -st_pct, "trade_date": trade_date},
    ]


def test_market_snapshot_counts_price_limits_by_board_and_st_status_before_st_rule_change() -> None:
    # 2026-07-06 之前主板 ST 涨跌停 5%：±4.9 算触及涨/跌停
    snapshot = _build_quote_snapshot(_quotes("2026-07-03"), "local", "test", [])

    assert snapshot["limit_up_count"] == 3
    assert snapshot["limit_down_count"] == 4


def test_market_snapshot_main_board_st_limit_is_10pct_since_2026_07_06() -> None:
    # 2026-07-06 起主板 ST 改为 10%：±4.9 不再是涨跌停，±9.9 才是
    snapshot = _build_quote_snapshot(_quotes("2026-07-21"), "local", "test", [])
    assert snapshot["limit_up_count"] == 2
    assert snapshot["limit_down_count"] == 3

    snapshot = _build_quote_snapshot(_quotes("2026-07-21", st_pct=9.9), "local", "test", [])
    assert snapshot["limit_up_count"] == 3
    assert snapshot["limit_down_count"] == 4
