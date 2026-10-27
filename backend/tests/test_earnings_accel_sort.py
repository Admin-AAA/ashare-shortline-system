"""财报业绩加速：排序分仍封顶 300，列表展示真实扣非同比（不连库、不调网关）。"""
import datetime as dt

import pandas as pd

from app.services import midterm_service as ms


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, dates):
        self.dates = dates

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "DISTINCT trade_date" in sql:
            return _Res([(d,) for d in self.dates])
        if "FROM stocks" in sql:
            return _Res([(c, f"N{c}") for c in (params or {}).get("codes", [])])
        raise AssertionError(sql)


def _dates(as_of="2026-10-16", n=251):
    d, out = dt.date.fromisoformat(as_of), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return out


def _row(ts_code, ann, end, dtp, orv, pdd):
    return {"ts_code": ts_code, "ann_date": ann, "end_date": end,
            "dt_netprofit_yoy": dtp, "or_yoy": orv, "profit_dedt": pdd}


def test_earnings_accel_sort_caps_display_does_not(monkeypatch):
    """入选与排序仍用 min(扣非同比, 300)、平手按代码；annotate 后列展示真实同比。"""
    cur, prev, older = "20260930", "20260630", "20260331"
    assert ms._quarter_ends("2026-10-16", 3) == [cur, prev, older]
    current = [
        _row("000688.SZ", "20261016", cur, 784.7, 51.7, 1e8),   # 封顶 300，代码靠前
        _row("002265.SZ", "20261016", cur, 1750.7, 35.7, 1e8),  # 真实更高，排序分仍 300
        _row("300343.SZ", "20261016", cur, 532.6, 45.8, 1e8),
        _row("600001.SH", "20261016", cur, 80.0, 25.0, 1e8),    # 未到封顶，排在 300 之后
        _row("000004.SZ", "20261016", cur, 40.0, 30.0, 1e8),    # 扣非同比 < 50
        _row("000005.SZ", "20261016", cur, 400.0, 10.0, 1e8),   # 营收同比 < 20
        _row("000006.SZ", "20261016", cur, 400.0, 30.0, -1.0),  # 扣非净利 ≤ 0
        _row("000007.SZ", "20261016", cur, 100.0, 30.0, 1e8),   # 未加速（上期 200）
        _row("830001.BJ", "20261016", cur, 900.0, 50.0, 1e8),   # 北交所
    ]
    prev_rows = [
        _row("000688.SZ", "20260731", prev, 100.0, 10.0, 1.0),
        _row("002265.SZ", "20260731", prev, 40.0, 10.0, 1.0),
        _row("300343.SZ", "20260731", prev, 10.0, 10.0, 1.0),
        _row("600001.SH", "20260731", prev, 30.0, 10.0, 1.0),
        _row("000004.SZ", "20260731", prev, 10.0, 10.0, 1.0),
        _row("000005.SZ", "20260731", prev, 10.0, 10.0, 1.0),
        _row("000006.SZ", "20260731", prev, 10.0, 10.0, 1.0),
        _row("000007.SZ", "20260731", prev, 200.0, 10.0, 1.0),
        _row("830001.BJ", "20260731", prev, 10.0, 10.0, 1.0),
    ]
    frames = {
        cur: pd.DataFrame(current),
        prev: pd.DataFrame(prev_rows),
        older: pd.DataFrame(columns=["ts_code", "ann_date", "end_date", "dt_netprofit_yoy", "or_yoy", "profit_dedt"]),
    }
    monkeypatch.setattr(ms, "_fetch_fina_period", lambda period: frames[period])
    monkeypatch.setattr(ms, "_liq_ok", lambda db, as_of, dates, codes: {c: {"total_mv": 1.0, "amt20": 1e8} for c in codes})
    res = ms.earnings_accel_candidates(_FakeDB(_dates()), "2026-10-16")
    assert [r["code"] for r in res] == ["000688", "002265", "300343", "600001"]
    assert [r["score"] for r in res] == [300.0, 300.0, 300.0, 80.0]
    assert [r["dt_netprofit_yoy"] for r in res] == [784.7, 1750.7, 532.6, 80.0]
    # 若按真实同比降序，1750.7 会排到第一；封顶后与 784.7、532.6 同分，按代码
    assert [r["dt_netprofit_yoy"] for r in res] != sorted((r["dt_netprofit_yoy"] for r in res), reverse=True)
    shown = [ms.annotate_pick("mid-earnings-accel", dict(r), i) for i, r in enumerate(res, 1)]
    assert [p["rule_score"] for p in shown] == [784.7, 1750.7, 532.6, 80.0]
    assert shown[1]["rule_score"] != shown[1]["score"]
    assert shown[0]["rule_score_label"] == "扣非净利同比(%)"
    assert "封顶300" not in shown[0]["rule_score_label"]
    assert shown[0]["rule_score_note"] == "排序时≥300%同等优先，平手按代码"
    assert [p["selection_rank"] for p in shown] == [1, 2, 3, 4]
