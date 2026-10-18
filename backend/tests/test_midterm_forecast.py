"""§103 mid-forecast-beat 实盘候选：首次预告判断 / 条件 / 排序（不连库、不调网关）。"""
import datetime as dt

import pandas as pd

from app.services import midterm_forecast as mf
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


def test_first_forecast_conditions_and_order(monkeypatch, tmp_path):
    day = pd.DataFrame([
        # 首次预告、预增 120% -> 入选
        {"ts_code": "000001.SZ", "ann_date": "20261016", "end_date": "20260930", "type": "预增", "p_change_min": 120.0, "net_profit_min": 1e4},
        # 同报告期之前已预告过（修正）-> 不算
        {"ts_code": "000002.SZ", "ann_date": "20261016", "end_date": "20260930", "type": "预增", "p_change_min": 500.0, "net_profit_min": 1e4},
        # 略增、封顶前 20% -> 入选
        {"ts_code": "600000.SH", "ann_date": "20261016", "end_date": "20260930", "type": "略增", "p_change_min": 20.0, "net_profit_min": 5e3},
        # 扭亏增幅 900 -> 封顶 300 入选；与 300001 同分按代码
        {"ts_code": "300002.SZ", "ann_date": "20261016", "end_date": "20260930", "type": "扭亏", "p_change_min": 900.0, "net_profit_min": 10.0},
        {"ts_code": "300001.SZ", "ann_date": "20261016", "end_date": "20260930", "type": "预增", "p_change_min": 300.0, "net_profit_min": 10.0},
        # 预减 / 净利下限<=0 / 北交所 -> 不入选
        {"ts_code": "600001.SH", "ann_date": "20261016", "end_date": "20260930", "type": "预减", "p_change_min": 50.0, "net_profit_min": 10.0},
        {"ts_code": "600002.SH", "ann_date": "20261016", "end_date": "20260930", "type": "预增", "p_change_min": 50.0, "net_profit_min": -1.0},
        {"ts_code": "830001.BJ", "ann_date": "20261016", "end_date": "20260930", "type": "预增", "p_change_min": 80.0, "net_profit_min": 10.0},
    ])
    monkeypatch.setattr(mf, "_fetch_forecast_day", lambda d: day if d == "20261016" else day.iloc[0:0])
    hist = pd.DataFrame([{"ts_code": "000002.SZ", "ann_date": "20260710", "end_date": "20260930"}])
    monkeypatch.setattr(mf, "_history", lambda codes: hist)
    monkeypatch.setattr(ms, "_liq_ok", lambda db, as_of, dates, codes: {c: {"total_mv": 1.0, "amt20": 1e8} for c in codes})
    res = mf.forecast_beat_candidates(_FakeDB(_dates()), "2026-10-16", store=False)
    assert [r["code"] for r in res] == ["300001", "300002", "000001", "600000"]
    assert res[1]["score"] == 300.0 and res[1]["forecast_type"] == "扭亏"


def test_fetch_failure_returns_none(monkeypatch):
    monkeypatch.setattr(mf, "_fetch_forecast_day", lambda d: None)
    assert mf.forecast_beat_candidates(_FakeDB(_dates()), "2026-10-16", store=False) is None


def test_strategy_registered():
    meta = ms.STRATEGIES["mid-forecast-beat"]
    assert meta["horizon"] == "mid" and meta["kind"] == "forecast_beat" and meta["hold_days"] == 20
    assert (meta["k_slots"], meta["max_new"], meta["schedule"]) == (20, 5, "daily")
