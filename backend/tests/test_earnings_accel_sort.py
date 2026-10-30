"""财报业绩加速：入选条件不变，排序改为 §RV-C EA_combo（不连库、不调网关）。"""
import datetime as dt
import json
from pathlib import Path

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


def test_ea_combo_rank_matches_average_percentile():
    """平均秩 / n，涨幅越大越好、市值越小越好；空值该项为 0；平手按代码、不写进分数。"""
    rows = [
        {"code": "000003", "ann_pct_chg": 3.0, "total_mv": 20.0},
        {"code": "000001", "ann_pct_chg": None, "total_mv": 10.0},
        {"code": "000002", "ann_pct_chg": 1.0, "total_mv": None},
    ]
    ranked = ms.ea_combo_rank(rows)
    assert [r["code"] for r in ranked] == ["000003", "000001", "000002"]
    by = {r["code"]: r for r in ranked}
    assert by["000001"]["ann_pct"] == 0.0
    assert by["000002"]["mv_pct"] == 0.0
    assert by["000003"]["score"] == round(by["000003"]["ann_pct"] + by["000003"]["mv_pct"], 6)
    assert abs(by["000003"]["ann_pct"] - 2 / 3) < 1e-6
    assert abs(by["000003"]["mv_pct"] - 1 / 3) < 1e-6
    assert abs(by["000001"]["mv_pct"] - 2 / 3) < 1e-6
    assert abs(by["000002"]["ann_pct"] - 1 / 3) < 1e-6
    # 同涨幅同市值：分数相同，代码升序
    tied = ms.ea_combo_rank([
        {"code": "600002", "ann_pct_chg": 1.0, "total_mv": 5.0},
        {"code": "000001", "ann_pct_chg": 1.0, "total_mv": 5.0},
    ])
    assert [r["code"] for r in tied] == ["000001", "600002"]
    assert tied[0]["score"] == tied[1]["score"] == 1.5  # 两项都并列，平均秩 1.5/2 + 1.5/2
    assert ms.ea_combo_rank([{"code": "000001", "ann_pct_chg": -3.0, "total_mv": 9.0}])[0]["score"] == 2.0
    assert ms.ea_combo_rank([]) == []


def test_earnings_accel_sort_is_ea_combo_not_capped_yoy(monkeypatch):
    """入选条件仍是 G；排序不再按扣非同比封顶 300。"""
    cur, prev, older = "20260930", "20260630", "20260331"
    assert ms._quarter_ends("2026-10-16", 3) == [cur, prev, older]
    current = [
        _row("000688.SZ", "20261016", cur, 784.7, 51.7, 1e8),
        _row("002265.SZ", "20261016", cur, 1750.7, 35.7, 1e8),
        _row("300343.SZ", "20261016", cur, 532.6, 45.8, 1e8),
        _row("600001.SH", "20261016", cur, 80.0, 25.0, 1e8),
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
    liq = {
        "000688": {"total_mv": 300.0, "amt20": 1e8, "pct_chg": 1.0},
        "002265": {"total_mv": 100.0, "amt20": 1e8, "pct_chg": 5.0},
        "300343": {"total_mv": 400.0, "amt20": 1e8, "pct_chg": 5.0},
        "600001": {"total_mv": 50.0, "amt20": 1e8, "pct_chg": -2.0},
    }
    monkeypatch.setattr(ms, "_fetch_fina_period", lambda period: frames[period])
    monkeypatch.setattr(ms, "_liq_ok", lambda db, as_of, dates, codes: {c: liq[c] for c in codes if c in liq})
    res = ms.earnings_accel_candidates(_FakeDB(_dates()), "2026-10-16")
    # n=4。涨幅秩：-2→1，1→2，5/5→3.5；市值升序秩：50→4，100→3，300→2，400→1。百分位=秩/4。
    assert [r["code"] for r in res] == ["002265", "600001", "300343", "000688"]
    assert [r["score"] for r in res] == [1.625, 1.25, 1.125, 1.0]
    assert [r["dt_netprofit_yoy"] for r in res] == [1750.7, 80.0, 532.6, 784.7]
    assert res[0]["ann_pct"] == 0.875 and res[0]["mv_pct"] == 0.75
    assert res[0]["ann_pct_chg"] == 5.0 and res[0]["total_mv"] == 100.0
    shown = [ms.annotate_pick("mid-earnings-accel", dict(r), i) for i, r in enumerate(res, 1)]
    assert shown[0]["rule_score_label"] == "公告日涨幅百分位+小市值百分位"
    assert shown[0]["rule_score_better"] == "higher"
    assert shown[0]["rule_score"] == 1.625
    assert shown[0]["rule_score_note"] == "当日候选池内等权相加，平手按代码"
    assert "封顶" not in shown[0]["rule_score_label"]
    assert "1750.7%" in shown[0]["selection_reason"]
    assert "信号日涨跌幅 5.00%" in shown[0]["selection_reason"]
    assert [p["selection_rank"] for p in shown] == [1, 2, 3, 4]
    # 旧批次没有百分位字段：仍展示真实扣非同比，不把封顶分说成 EA_combo
    old = ms.annotate_pick("mid-earnings-accel", {"code": "1", "score": 300.0, "dt_netprofit_yoy": 592.1})
    assert old["rule_score"] == 592.1
    assert old["rule_score_label"] == "扣非净利同比(%)"
    assert old["rule_score_note"] == "排序时≥300%同等优先，平手按代码"


def test_earnings_accel_copy_keeps_hold_and_leaves_forecast():
    ea = ms.STRATEGIES["mid-earnings-accel"]
    fb = ms.STRATEGIES["mid-forecast-beat"]
    assert ea["hold_days"] == 20 and fb["hold_days"] == 20
    assert "EA_combo" in ea["summary"] and "百分位" in ea["summary"]
    assert "封顶300%" not in ea["summary"]
    assert "按预告增幅下限（封顶300%）" in fb["summary"]
    metrics = json.loads((Path(__file__).resolve().parents[1] / "app/services/midterm_backtest_metrics.json").read_text())
    cfg = metrics["mid-earnings-accel"]["config"]
    assert "EA_combo" in cfg and "平均秩百分位" in cfg and "持20日" in cfg
    assert "扣非同比封顶 300%" in metrics["mid-earnings-accel"]["note"]
    assert "+3.09%" in metrics["mid-earnings-accel"]["note"]
    assert "min(p_change_min,300)" in metrics["mid-forecast-beat"]["config"]
