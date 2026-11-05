"""§993 mid-repurchase-incentive-dip：事件池合并、深跌门槛、涨跌停剔除、排序、注册与指标卡。不连库（假 db）。"""
import json

from app.services import midterm_service as ms

AS_OF = "2026-10-15"
DATES = [f"2026-09-{d:02d}" for d in range(1, 21)] + [AS_OF]  # 21 个交易日，升序


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return list(self._rows)

    def mappings(self):
        return _Res([dict(r) for r in self._rows])


class FakeDB:
    """daily: code -> (21 天 pct 列表, 信号日收盘价)。"""

    def __init__(self, daily):
        self.daily = daily

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "DISTINCT trade_date" in sql:
            return _Res([(d,) for d in sorted(DATES, reverse=True)])
        if "trade_date>:d20" in sql:  # _r20：最近 20 行
            rows = []
            for code, (pcts, _) in sorted(self.daily.items()):
                if code in params["codes"]:
                    rows += [(code, p) for p in pcts[-20:]]
            return _Res(rows)
        if "pct_chg, close" in sql:
            return _Res([{"code": c, "pct_chg": p[-1], "close": cl} for c, (p, cl) in self.daily.items() if c in params["codes"]])
        raise AssertionError(sql)


def _drop(total_pct):
    """20 天均匀跌到 total_pct（如 -0.2），返回每日 pct 列表（含 21 天，最后一天为信号日 0.5%）。"""
    per = ((1 + total_pct) / 1.005) ** (1 / 19) - 1
    return [0.0] + [per * 100] * 19 + [0.5]


def test_pool_merges_and_dedupes():
    rp = [{"code": "600001", "ratio": 0.01}, {"code": "600002", "ratio": 0.001}, {"code": "600003", "ratio": 0.02}]
    ic = [{"code": "600001", "title": "x"}, {"code": "300004", "title": "y"}]
    pool = ms._event_dip_pool(rp, ic, 0.005, held={"600003"})
    by = {p["code"]: p["event"] for p in pool}
    assert by == {"600001": "回购预案", "300004": "期权激励草案"}


def test_filter_threshold_limits_and_order():
    daily = {
        "600001": (_drop(-0.20), 10.0),   # 深跌 → 入选
        "600002": (_drop(-0.10), 10.0),   # 跌得不够 → 剔除
        "300003": (_drop(-0.30), 10.0),   # 更深 → 排第一
        "600004": (_drop(-0.25)[:-1] + [10.0], 11.0),  # 信号日封涨停 → 剔除
    }
    pool = [{"code": c, "event": "回购预案"} for c in daily]
    out = ms._filter_event_dip(FakeDB(daily), AS_OF, pool, -0.15)
    assert [p["code"] for p in out] == ["300003", "600001"]
    assert out[0]["r20"] <= -0.29 and out[0]["score"] == -out[0]["r20"]


def test_registered_with_quality_card():
    meta = ms.STRATEGIES["mid-repurchase-incentive-dip"]
    assert meta["kind"] == "event_dip" and meta["hold_days"] == 10 and meta["max_r20"] == -0.15
    q = json.loads(ms.METRICS_FILE.read_text())["mid-repurchase-incentive-dip"]["quality"]
    assert q["pass"] and q["R12"]["n"] >= 30 and q["N3"]["n"] >= 30
    p = ms.annotate_pick("mid-repurchase-incentive-dip", {"code": "600001", "r20": -0.2, "event": "回购预案",
                                                          "ratio": 0.01, "repurchase_amount": 1e8, "signal_pct": 0.5})
    assert p["rule_score"] == -20.0 and p["rule_score_better"] == "lower"
    assert "回购预案" in p["selection_reason"] and "≤−15%" in p["selection_reason"]


def test_dip_cards_registered_with_quality():
    data = json.loads(ms.METRICS_FILE.read_text())
    for sid, kind in (("mid-divplan-dip", "divplan_dip"), ("mid-earnings-accel-dip", "ea_dip")):
        meta = ms.STRATEGIES[sid]
        assert meta["kind"] == kind and meta["hold_days"] == 5 and meta["max_r20"] == -0.15
        q = data[sid]["quality"]
        assert q["pass"] and q["R12"]["n"] >= 30 and q["N3"]["n"] >= 30
    p = ms.annotate_pick("mid-divplan-dip", {"code": "600001", "r20": -0.18, "cash_div_tax": 0.5, "div_yield": 0.02,
                                             "end_date": "20251231", "signal_pct": -1.0})
    assert p["rule_score"] == -18.0 and "分红预案" in p["selection_reason"]
    p = ms.annotate_pick("mid-earnings-accel-dip", {"code": "600001", "r20": -0.2, "dt_netprofit_yoy": 80, "or_yoy": 30,
                                                    "prev_dt_netprofit_yoy": 40, "end_date": "20260630", "signal_pct": 1.0})
    assert p["rule_score"] == -20.0 and "加速" in p["selection_reason"]


class _DivDB:
    def __init__(self, closes):
        self.closes = closes

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "SELECT code, close FROM daily_kbars" in sql:
            return _Res([(c, v) for c, v in self.closes.items() if c in params["codes"]])
        if "SELECT code,name FROM stocks" in sql:
            return _Res([(c, "x") for c in params["codes"]])
        raise AssertionError(sql)


def test_divplan_candidates_first_plan_and_yield(monkeypatch):
    import pandas as pd
    from app.services import midterm_fina_events as fe
    local = pd.DataFrame([
        # 600001：本期第一次预案，派现 0.3 / 收盘 10 = 3% → 入选
        {"ts_code": "600001.SH", "end_date": "20251231", "ann_date": "20261015", "div_proc": "预案", "cash_div_tax": 0.3},
        # 600002：同一报告期之前已有预案 → 不是第一次
        {"ts_code": "600002.SH", "end_date": "20251231", "ann_date": "20260901", "div_proc": "预案", "cash_div_tax": 0.2},
        {"ts_code": "600002.SH", "end_date": "20251231", "ann_date": "20261015", "div_proc": "预案", "cash_div_tax": 0.3},
        # 600003：股息 0.05/10 = 0.5% < 1% → 剔除
        {"ts_code": "600003.SH", "end_date": "20251231", "ann_date": "20261015", "div_proc": "预案", "cash_div_tax": 0.05},
        # 600004：实施不是预案 → 剔除
        {"ts_code": "600004.SH", "end_date": "20251231", "ann_date": "20261015", "div_proc": "实施", "cash_div_tax": 0.5},
    ])
    monkeypatch.setattr(fe, "_window", lambda db, d: (["2026-10-15"] * 251, "20261015", "20261015"))
    monkeypatch.setattr(fe, "_local_div", lambda: (local, "20261015"))
    liq = {c: {"total_mv": 1e6, "amt20": 1e8} for c in ("600001", "600002", "600003", "600004")}
    monkeypatch.setattr(ms, "_liq_ok", lambda db, d, dates, codes: {c: liq[c] for c in codes if c in liq})
    out = fe.divplan_candidates(_DivDB({c: 10.0 for c in liq}), "2026-10-15")
    assert [p["code"] for p in out] == ["600001"]
    assert abs(out[0]["div_yield"] - 0.03) < 1e-9


def test_earnings_accel_keeps_filled_row_when_upstream_duplicates(monkeypatch):
    """同一 (股票, 公告日) 上游给两行、第一行扣非同比为空时，不能把事件丢掉（2026-08-11 301308 实例）。"""
    import pandas as pd
    import datetime as dt
    days = [(dt.date(2026, 8, 11) - dt.timedelta(days=i)).isoformat() for i in range(251)][::-1]

    class DB:
        def execute(self, stmt, params=None):
            sql = str(stmt)
            if "DISTINCT trade_date" in sql:
                return _Res([(d,) for d in sorted(days, reverse=True)])
            if "SELECT code,name FROM stocks" in sql:
                return _Res([(c, "x") for c in params["codes"]])
            raise AssertionError(sql)

    cur = pd.DataFrame([
        {"ts_code": "301308.SZ", "ann_date": "20260811", "dt_netprofit_yoy": None, "or_yoy": 136.0, "profit_dedt": None},
        {"ts_code": "301308.SZ", "ann_date": "20260811", "dt_netprofit_yoy": 31096.0, "or_yoy": 136.0, "profit_dedt": 1e10},
    ])
    prev = pd.DataFrame([{"ts_code": "301308.SZ", "ann_date": "20260428", "dt_netprofit_yoy": 2051.0, "or_yoy": 132.0, "profit_dedt": 3e9}])
    old = pd.DataFrame([{"ts_code": "301308.SZ", "ann_date": "20260331", "dt_netprofit_yoy": 600.0, "or_yoy": 30.0, "profit_dedt": 1e9}])
    frames = {"20260630": cur, "20260331": prev, "20251231": old}
    monkeypatch.setattr(ms, "_fetch_fina_period", lambda per: frames[per])
    monkeypatch.setattr(ms, "_liq_ok", lambda db, d, dates, codes: {c: {"total_mv": 1e6, "amt20": 1e8, "pct_chg": -1.0} for c in codes})
    out = ms.earnings_accel_candidates(DB(), days[-1])
    assert [p["code"] for p in out] == ["301308"]
