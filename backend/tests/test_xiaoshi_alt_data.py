from __future__ import annotations

import app.services.xiaoshi_alt_data as xa


def test_margin_mapping_units():
    rows = xa.margin_rows_from_xiaoshi([{
        "code": "000001", "event_date": "2026-10-13", "financing_buy": 85754028.0,
        "financing_balance": 4377991690.0, "financing_repayment": None,
        "securities_lending_balance": 182017354.0, "margin_total": 4560009044.0,
    }])
    assert rows == [{
        "code": "000001", "trade_date": "2026-10-13", "rzye": 43.779917, "rqye": 1.820174,
        "rzmre": 0.85754, "rzche": None, "rzrqye": 45.60009, "rqyl": None,
    }]


def test_float_ratio_uses_total_shares_not_upstream_ratio():
    """上游 unlock_ratio 的分母是流通股本，直接用会写出 1546% 这种值。

    实测 688781：解禁 643,975,000 股，流通 41,628,998 股，总股本 1,000,000,000 股。
    上游给 15.4697（=1546.97%），正确的占总股本口径是 64.3975%。
    """
    items = [{
        "code": "688781", "event_date": "2027-03-25", "unlock_type": "首发原股东限售股份",
        "unlock_shares": 643975000.0, "unlock_ratio": 15.4697,
    }]
    rows = xa.float_rows_from_xiaoshi(items, {"688781": 1000000000.0})
    assert rows[0]["float_date"] == "20270325"
    assert rows[0]["float_ratio"] == 64.3975
    assert rows[0]["holder_name"].startswith(xa.XIAOSHI_FLOAT_PREFIX)


def test_float_ratio_null_when_total_shares_unavailable():
    rows = xa.float_rows_from_xiaoshi([{
        "code": "301660", "event_date": "2027-04-13", "unlock_type": "首发机构配售股份",
        "unlock_shares": 2411759.0, "unlock_ratio": 0.0099,
    }], {})
    assert rows[0]["float_ratio"] is None
    assert rows[0]["float_share"] == 2411759.0


def test_upcoming_float_ratio_tolerates_null_ratio():
    """feature_builder 汇总时 NULL 比例必须跳过：dict.get 的默认值只在键缺失时生效。"""
    from app.services.feature_builder import _sum_float_ratio

    assert _sum_float_ratio([{"float_ratio": 12.5}, {"float_ratio": None}, {}]) == 12.5


def test_paged_stops_on_total_and_tolerates_growing_total():
    calls = []

    class C:
        def get_quant_data(self, ds, limit, offset):
            calls.append(offset)
            return {"items": [{"code": "000001"}], "has_more": True, "next_offset": offset + 1, "total_count": 2}

    rows = xa._paged(C(), "x")
    assert len(rows) == 2 and calls == [0, 1]


def test_sync_never_raises_without_client(monkeypatch):
    class Boom:
        def get_quant_data(self, *a, **k):
            raise RuntimeError("down")

    class DB:
        def rollback(self):
            pass

    r = xa.sync_xiaoshi_alt_data(DB(), client=Boom())
    assert r["margin_inserted"] == 0 and r["float_inserted"] == 0 and len(r["errors"]) == 2
