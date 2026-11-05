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
