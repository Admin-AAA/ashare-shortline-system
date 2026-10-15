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


def test_float_mapping_percent_and_compact_date():
    rows = xa.float_rows_from_xiaoshi([{
        "code": "301660", "event_date": "2027-04-13", "unlock_type": "首发机构配售股份",
        "unlock_shares": 2411759.0, "unlock_ratio": 0.0099,
    }])
    assert rows[0]["float_date"] == "20270413"
    assert rows[0]["float_ratio"] == 0.99
    assert rows[0]["holder_name"].startswith(xa.XIAOSHI_FLOAT_PREFIX)


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
