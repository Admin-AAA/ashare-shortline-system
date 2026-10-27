"""§109 mid-incentive-option 实盘候选：草案标题过滤 / 180 天首次 / 含期权 / r20 升序（不连库、不联网）。"""
import datetime as dt

from app.services import midterm_incentive as mi
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


def _row(code, day, title, i):
    return {"code": code, "name": "", "ann_ms": 0, "ann_date": day, "title": title, "ann_id": str(i)}


def test_is_draft():
    assert mi.is_draft("2026年股票期权激励计划（草案）摘要")
    assert mi.is_draft("关于2026年限制性股票激励计划（草案）的法律意见书")
    assert not mi.is_draft("2026年限制性股票激励计划（草案修订稿）")
    assert not mi.is_draft("关于终止实施2025年股票期权激励计划（草案）的公告")
    assert not mi.is_draft("2026年员工持股计划（草案）")


def test_first_events_180_days_and_opt():
    rows = [
        _row("000001", "20261016", "2026年股票期权激励计划（草案）", 1),
        _row("000001", "20261016", "2026年股票期权激励计划（草案）摘要", 2),
        _row("000002", "20261016", "2026年限制性股票激励计划（草案）", 3),  # 180 天内已有 -> 不算
        _row("000003", "20261016", "2026年股票期权与限制性股票激励计划（草案）", 4),  # 上次 200 天前 -> 算
        _row("000004", "20261015", "2026年股票期权激励计划（草案）", 5),  # 区间外（上一交易日）
    ]
    prior = {"000002": "20260701", "000003": "20260320"}
    ev = mi.first_events(rows, "20261016", "20261016", prior)
    assert sorted(ev) == ["000001", "000003"]
    assert ev["000001"]["opt"] and ev["000003"]["opt"]


def test_candidates_opt_only_sorted_by_r20(monkeypatch):
    rows = [
        _row("000001", "20261016", "2026年股票期权激励计划（草案）", 1),
        _row("300001", "20261016", "2026年股票期权激励计划（草案）", 2),
        _row("600001", "20261016", "2026年限制性股票激励计划（草案）", 3),  # 无期权 -> 不入选
        _row("830001", "20261016", "2026年股票期权激励计划（草案）", 4),  # 北交所
    ]
    monkeypatch.setattr(mi, "fetch_range", lambda s, e: rows)
    monkeypatch.setattr(mi, "_history_dates", lambda codes, before: {})
    monkeypatch.setattr(ms, "_liq_ok", lambda db, as_of, dates, codes: {c: {"total_mv": 1.0, "amt20": 1e8} for c in codes})
    monkeypatch.setattr(mi, "_r20", lambda db, as_of, dates, codes: {"000001": 0.12, "300001": -0.05})
    res = mi.incentive_candidates(_FakeDB(_dates()), "2026-10-16", store=False)
    assert [r["code"] for r in res] == ["300001", "000001"]
    assert res[0]["r20"] == -0.05 and res[0]["opt"]


def test_fetch_failure_returns_none(monkeypatch):
    monkeypatch.setattr(mi, "fetch_range", lambda s, e: None)
    assert mi.incentive_candidates(_FakeDB(_dates()), "2026-10-16", store=False) is None


def test_strategy_registered():
    meta = ms.STRATEGIES["mid-incentive-option"]
    assert meta["horizon"] == "mid" and meta["kind"] == "incentive_option" and meta["hold_days"] == 20
    assert "k_slots" not in meta and "max_new" not in meta and meta["schedule"] == "daily"
