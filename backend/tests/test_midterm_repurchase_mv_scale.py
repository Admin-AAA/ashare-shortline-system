"""回购预案总市值：daily_basic 快照过期时按不复权收盘价折算到信号日。"""
import datetime as dt

import pandas as pd

from app.services import midterm_service as ms


def test_scaled_total_mv_price_ratio_no_corp_action():
    # 凯旺科技 10-09 快照 518683 万 × 46.27/54.13 → 信号日市值
    mv = ms._scaled_total_mv(518683.0, 54.13, 46.27, adj_bd=1.017, adj_t=1.017)
    assert abs(mv - 518683.0 * 46.27 / 54.13) < 1e-6


def test_scaled_total_mv_same_day_unchanged():
    assert ms._scaled_total_mv(1000.0, 10.0, 10.0, 1.0, 1.0) == 1000.0


def test_scaled_total_mv_missing_close_keeps_snapshot():
    assert ms._scaled_total_mv(1000.0, None, 10.0) == 1000.0
    assert ms._scaled_total_mv(1000.0, 10.0, None) == 1000.0
    assert ms._scaled_total_mv(1000.0, 0.0, 10.0) == 1000.0


def test_scaled_total_mv_corp_action_falls_back_to_total_share():
    # adj 变化：不用收盘比；有 total_share 则 万股×元=万元
    mv = ms._scaled_total_mv(5000.0, 20.0, 10.0, adj_bd=1.0, adj_t=2.0, total_share=800.0)
    assert mv == 8000.0


def test_scaled_total_mv_corp_action_without_share_keeps_snapshot():
    mv = ms._scaled_total_mv(5000.0, 20.0, 10.0, adj_bd=1.0, adj_t=2.0, total_share=None)
    assert mv == 5000.0


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return self._rows

    def mappings(self):
        return self

    def scalar(self):
        return self._rows[0][0] if self._rows else None


class _FakeDB:
    """驱动 repurchase_plan_candidates 市值折算路径（不连库、不调网关）。"""

    def __init__(self, dates, rows, st=None, names=None, bd="2026-10-09"):
        self.dates = dates
        self.rows = rows
        self.st = st or set()
        self.names = names or {}
        self.bd = bd

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "DISTINCT trade_date" in sql:
            return _Res([(d,) for d in self.dates])
        if "MAX(trade_date) FROM daily_basic" in sql:
            return _Res([(self.bd,)])
        if "FROM daily_basic b" in sql and "close_bd" in sql:
            return _Res(self.rows)
        if "stock_metadata_history" in sql:
            return _Res([(c,) for c in self.st])
        if "FROM stocks" in sql:
            codes = (params or {}).get("codes", [])
            return _Res([(c, self.names.get(c, f"N{c}")) for c in codes])
        raise AssertionError(sql)


def _dates(as_of="2026-10-16", n=251):
    d, out = dt.date.fromisoformat(as_of), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return out


def test_repurchase_plan_candidates_scales_stale_total_mv(monkeypatch):
    day = pd.DataFrame([{
        "ts_code": "301182.SZ", "ann_date": "20261016", "proc": "预案", "amount": 60_000_000.0,
    }])
    monkeypatch.setattr(ms, "_fetch_repurchase_window", lambda s, e: day)
    monkeypatch.setattr(ms, "_store_repurchase_events", lambda new: None)
    monkeypatch.setattr(ms, "EVENTS_DIR", type("P", (), {"glob": lambda self, p: []})())

    row = {
        "code": "301182",
        "total_mv": 518683.0,
        "amt20": 1e8,
        "close_bd": 54.13,
        "close_t": 46.27,
        "adj_bd": 1.017,
        "adj_t": 1.017,
    }
    db = _FakeDB(_dates(), [row], names={"301182": "凯旺科技"})
    res = ms.repurchase_plan_candidates(db, "2026-10-16")
    assert res is not None and len(res) == 1
    expected_mv = 518683.0 * 46.27 / 54.13
    assert abs(res[0]["total_mv"] - expected_mv) < 1e-6
    assert res[0]["ratio"] == round(60_000_000.0 / (expected_mv * 1e4), 5)
    assert res[0]["code"] == "301182"
