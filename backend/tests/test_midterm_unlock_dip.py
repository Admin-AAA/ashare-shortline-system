"""L-241 mid-unlock-dip：解禁窗口、去重合计、比例与深跌门槛、北交所/已持有剔除、排序、注册、数据依赖。不连库（假 db）。"""
import json

import pytest

from app.services import midterm_incentive as mi
from app.services import midterm_service as ms
from app.services import strategy_data_readiness as sdr

AS_OF = "2026-11-16"
PREV = "2026-11-13"
DATES = [AS_OF, PREV] + [f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}" for i in range(249)]  # 251 个，降序在前两位


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._rows[0][0] if self._rows else None


class FakeDB:
    def __init__(self, floats):
        self.floats = floats
        self.seen_params = None

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "DISTINCT trade_date" in sql:
            return _Res([(d,) for d in DATES])
        if "FROM share_floats" in sql:
            self.seen_params = params
            return _Res([r for r in self.floats if params["lo"] < r[1] <= params["hi"]])
        if "FROM stocks" in sql:
            return _Res([(c, f"名{c}") for c in params["codes"]])
        raise AssertionError(sql)


@pytest.fixture
def patched(monkeypatch):
    r20 = {"600001": -0.20, "600002": -0.15, "300003": -0.05, "600004": -0.30, "600005": -0.25, "600006": -0.40}
    monkeypatch.setattr(ms, "_liq_ok", lambda db, as_of, dates, codes: {c: {} for c in codes if c != "600005"})
    monkeypatch.setattr(mi, "_r20", lambda db, as_of, dates, codes: {c: r20[c] for c in codes if c in r20})


def test_candidates_window_dedupe_thresholds_and_order(patched):
    floats = [
        ("600001", "20261116", "股东甲", 1e7, 4.0),
        ("600001", "20261116", "股东甲", 1e7, 4.0),        # 同一笔解禁因公告日不同重复入库：只算一次
        ("600001", "20261116", "股东乙", 2e7, 2.0),        # 合计 6% → 入选
        ("600002", "20261114", "股东丙", 5e7, 12.0),       # 周六解禁、周一出信号 → 入选，比例最大排第一
        ("300003", "20261116", "股东丁", 5e7, 20.0),       # 解禁前没跌够（−5%）→ 剔除
        ("600004", "20261116", "股东戊", 1e6, 3.0),        # 比例不足 5% → 剔除
        ("600005", "20261116", "股东己", 5e7, 9.0),        # 流动性不达标 → 剔除
        ("920007", "20261116", "股东庚", 5e7, 30.0),       # 北交所 → 剔除
        ("600006", "20261113", "股东辛", 5e7, 30.0),       # 上一交易日的解禁不重复出 → 剔除
        ("600008", "20261116", "股东壬", 5e7, 30.0),       # 已持有 → 剔除
    ]
    db = FakeDB(floats)
    out = ms.unlock_dip_candidates(db, AS_OF, 5.0, -0.10, held={"600008"})
    assert db.seen_params == {"lo": "20261113", "hi": "20261116"}
    assert [p["code"] for p in out] == ["600002", "600001"]
    assert out[0]["float_date"] == "2026-11-14" and out[0]["score"] == out[0]["float_ratio"] == 12.0
    assert out[1]["float_ratio"] == 6.0 and out[1]["r20"] == -0.20 and out[1]["name"] == "名600001"


def test_candidates_no_data_when_calendar_short(patched):
    class Short(FakeDB):
        def execute(self, stmt, params=None):
            if "DISTINCT trade_date" in str(stmt):
                return _Res([(AS_OF,)])
            return super().execute(stmt, params)

    assert ms.unlock_dip_candidates(Short([]), AS_OF, 5.0, -0.10, set()) is None


def test_registered_live_with_card_and_reason():
    assert "mid-unlock-dip" in ms.STRATEGIES and "mid-unlock-dip" in ms.NON_SUE_CULL_PROTECTED
    meta = ms.STRATEGIES["mid-unlock-dip"]
    assert (meta["kind"], meta["min_float_ratio"], meta["max_r20"], meta["hold_days"]) == ("unlock_dip", 5.0, -0.10, 20)
    q = json.loads(ms.METRICS_FILE.read_text())["mid-unlock-dip"]["quality"]
    assert q["standard"] == "§282" and q["R12"]["n"] >= 30 and q["N3"]["n"] >= 30
    p = ms.annotate_pick("mid-unlock-dip", {"code": "600001", "float_date": "2026-11-16", "float_ratio": 6.0, "r20": -0.2})
    assert p["rule_score"] == 6.0 and p["rule_score_better"] == "higher"
    assert "解禁" in p["selection_reason"] and "-20.00%" in p["selection_reason"]


class _CountDB:
    def __init__(self, n):
        self.n, self.params = n, None

    def execute(self, stmt, params=None):
        assert "FROM share_floats" in str(stmt)
        self.params = params
        return _Res([(self.n,)])


def test_share_float_calendar_dependency():
    assert ("share_float_calendar", 0) in sdr.deps_for("mid-unlock-dip")
    db = _CountDB(275)
    ok = sdr.check_share_float_calendar(db, "2026-11-27")
    assert ok["ok"] and db.params == {"a": "20261127", "b": "20261227"}
    bad = sdr.check_share_float_calendar(_CountDB(3), "2026-11-27")
    assert not bad["ok"] and "只排了 3 条" in bad["reason"]
