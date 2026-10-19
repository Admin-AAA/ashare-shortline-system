"""margin_detail 截断补缺：只用内存 sqlite，不连任何 MySQL 库。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.services.margin_detail_fill as mf
from app.models.tables import MarginDetail

SH = ZoneInfo("Asia/Shanghai")
AFTER_CLOSE = datetime(2026, 10, 19, 19, 0, tzinfo=SH)   # 周一盘后
INTRADAY = datetime(2026, 10, 19, 10, 30, tzinfo=SH)     # 周一盘中
WEEKEND_NOON = datetime(2026, 10, 18, 10, 30, tzinfo=SH)  # 周日


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ASHARE_MARGIN_FILL_RUNTIME_DIR", str(tmp_path))
    for k in ("ASHARE_MARGIN_PAGE_CAPS", "ASHARE_MARGIN_COVERAGE_DROP", "ASHARE_MARGIN_FILL_ENABLED", "ASHARE_MARGIN_FILL_ON_EMPTY"):
        monkeypatch.delenv(k, raising=False)
    eng = create_engine("sqlite://")
    MarginDetail.__table__.create(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _seed(db, day: str, codes, rzye: float = 1.0):
    db.bulk_insert_mappings(MarginDetail, [{"code": c, "trade_date": day, "rzye": rzye} for c in codes])
    db.commit()


def _codes(n: int, start: int = 600000):
    return [f"{start + i:06d}" for i in range(n)]


# ── 截断判定 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("rows,expected", [(2000, True), (2001, True), (3000, True), (5001, True), (1999, False), (2002, False), (4459, False)])
def test_page_cap_not_hardcoded_2001(rows, expected):
    assert mf.is_page_cap(rows) is expected


def test_page_caps_configurable(monkeypatch):
    monkeypatch.setenv("ASHARE_MARGIN_PAGE_CAPS", "2500, 7000")
    assert mf.is_page_cap(2501) and mf.is_page_cap(7000)
    assert not mf.is_page_cap(2001)
    assert mf.is_page_cap(1000, caps=(1000,), min_rows=500)


def test_coverage_drop_vs_median_of_5():
    base = [4450, 4460, 4455, 4440, 4470]
    ok = mf.detect_truncation(4300, base)          # 4300 >= 4455*0.95=4232.25
    assert ok["truncated"] is False and ok["baseline_median"] == 4455 and ok["coverage"] == round(4300 / 4455, 4)
    bad = mf.detect_truncation(4200, base)
    assert bad["truncated"] and bad["reasons"] == ["coverage_drop"]
    both = mf.detect_truncation(2001, base)
    assert both["reasons"] == ["page_cap", "coverage_drop"]
    assert mf.detect_truncation(2001, [])["reasons"] == ["page_cap"]       # 无基线时仍能按分页上限判
    assert mf.detect_truncation(3000, [], caps=())["truncated"] is False


def test_trading_session_window():
    assert mf.in_trading_session(INTRADAY)
    assert mf.in_trading_session(datetime(2026, 10, 19, 9, 15, tzinfo=SH))
    assert not mf.in_trading_session(datetime(2026, 10, 19, 15, 5, tzinfo=SH))
    assert not mf.in_trading_session(AFTER_CLOSE)
    assert not mf.in_trading_session(WEEKEND_NOON)


# ── 补缺：只补缺失、不覆盖 ────────────────────────────────────────────────
def _baseline(db):
    for d in ("2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"):
        _seed(db, d, _codes(2001) + _codes(2450, 0))


def test_fills_only_missing_and_never_overwrites(db):
    _baseline(db)
    sh = _codes(2001)
    _seed(db, "2026-10-16", sh, rzye=1.0)
    calls = []

    def fetch(td):
        calls.append(td)
        # 东财同时返回已有的沪市代码（值不同）和缺失的深市代码，外加一行别的日期和一行坏代码
        return ([{"code": c, "trade_date": "2026-10-16", "rzye": 99.0} for c in sh[:10]]
                + [{"code": c, "trade_date": "2026-10-16", "rzye": 2.0, "rqyl": 5.0} for c in _codes(2450, 0)]
                + [{"code": "000001", "trade_date": "2026-10-15", "rzye": 3.0}, {"code": "12", "trade_date": "2026-10-16"}])

    r = mf.fill_margin_day(db, "20261016", fetch=fetch, now=AFTER_CLOSE)
    assert calls == ["20261016"]
    assert r["status"] == "filled" and r["reasons"][0] == "page_cap"
    assert r["promax_rows"] == 2001 and r["eastmoney_inserted"] == 2450 and r["rows_after"] == 4451
    assert r["coverage_after"] == 1.0
    # 已有行一行没变
    vals = {c: v for c, v in db.execute(select(MarginDetail.code, MarginDetail.rzye).where(MarginDetail.trade_date == "2026-10-16"))}
    assert all(vals[c] == 1.0 for c in sh)
    assert vals["000000"] == 2.0
    # 别的日期没被写
    assert db.execute(select(MarginDetail.rzye).where(MarginDetail.trade_date == "2026-10-15", MarginDetail.code == "000001")).scalar() == 1.0


def test_insert_ignore_guards_against_race(db):
    _seed(db, "2026-10-16", ["000001"], rzye=1.0)
    n = mf._insert_missing(db, [{"code": "000001", "trade_date": "2026-10-16", "rzye": 9.0},
                                {"code": "000002", "trade_date": "2026-10-16", "rzye": 9.0}])
    assert n == 1
    assert db.execute(select(MarginDetail.rzye).where(MarginDetail.code == "000001")).scalar() == 1.0


def test_not_truncated_does_not_fetch(db):
    _baseline(db)
    _seed(db, "2026-10-16", _codes(2001) + _codes(2449, 0))
    r = mf.fill_margin_day(db, "2026-10-16", fetch=lambda td: pytest.fail("must not fetch"), now=AFTER_CLOSE)
    assert r["status"] == "not_truncated"


def test_empty_primary_skipped_by_default(db):
    _baseline(db)
    r = mf.fill_margin_day(db, "2026-10-16", fetch=lambda td: pytest.fail("must not fetch"), now=AFTER_CLOSE)
    assert r["status"] == "skipped_no_primary"


# ── 盘中跳过 ─────────────────────────────────────────────────────────────
def test_intraday_defers_and_after_close_picks_up_pending(db, tmp_path):
    _baseline(db)
    _seed(db, "2026-10-16", _codes(2001))
    r = mf.fill_margin_day(db, "2026-10-16", fetch=lambda td: pytest.fail("no fetch intraday"), now=INTRADAY)
    assert r["status"] == "deferred_trading_session" and r["eastmoney_inserted"] == 0
    assert mf.load_pending() == ["2026-10-16"]
    # 盘后入口：即使 lookback 不含该日，也会处理待补清单
    _seed(db, "2026-10-19", _codes(2001) + _codes(2450, 0))
    out = mf.run_margin_detail_fill(db, "2026-10-19", lookback_days=1,
                                    fetch=lambda td: [{"code": c, "trade_date": "2026-10-16", "rzye": 2.0} for c in _codes(2450, 0)],
                                    now=AFTER_CLOSE)
    by_day = {d["trade_date"]: d for d in out["days"]}
    assert by_day["2026-10-16"]["status"] == "filled" and by_day["2026-10-19"]["status"] == "not_truncated"
    assert out["inserted"] == 2450 and mf.load_pending() == []


def test_fetch_error_marks_pending_and_never_raises(db):
    _baseline(db)
    _seed(db, "2026-10-16", _codes(2001))

    def boom(td):
        raise RuntimeError("circuit open")

    r = mf.fill_margin_day(db, "2026-10-16", fetch=boom, now=AFTER_CLOSE)
    assert r["status"] == "error" and "circuit open" in r["error"]
    assert mf.load_pending() == ["2026-10-16"]


def test_disabled_switch(db, monkeypatch):
    monkeypatch.setenv("ASHARE_MARGIN_FILL_ENABLED", "0")
    assert mf.run_margin_detail_fill(db, "2026-10-16")["skipped"]


def test_independent_of_eastmoney_alt_flag(db, monkeypatch):
    import app.services.datasource.gateway as gw

    monkeypatch.setattr(gw, "is_cutover", lambda ds: False)  # eastmoney.alt 关
    _baseline(db)
    _seed(db, "2026-10-16", _codes(2001))
    r = mf.fill_margin_day(db, "2026-10-16", fetch=lambda td: [{"code": "000001", "trade_date": "2026-10-16"}], now=AFTER_CLOSE)
    assert r["status"] == "filled" and r["eastmoney_inserted"] == 1


# ── 日志 / 来源记录 ───────────────────────────────────────────────────────
def test_log_line_and_provenance(db, tmp_path, caplog):
    _baseline(db)
    _seed(db, "2026-10-16", _codes(2001))
    with caplog.at_level(logging.WARNING, logger=mf.__name__):
        mf.fill_margin_day(db, "2026-10-16", fetch=lambda td: [{"code": c, "trade_date": "2026-10-16"} for c in _codes(3, 0)], now=AFTER_CLOSE)
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("margin_detail_fill td=2026-10-16")]
    assert len(line) == 1
    msg = line[0]
    for part in ("status=filled", "promax_rows=2001", "eastmoney_rows=3", "eastmoney_inserted=3", "rows_after=2004",
                 "baseline_median=4451.0", "coverage_after=0.4502", "source=eastmoney.margin_detail_fill"):
        assert part in msg, part
    recs = [json.loads(x) for x in (tmp_path / "margin_fill_provenance.jsonl").read_text().splitlines()]
    assert len(recs) == 1
    assert recs[0]["source"] == mf.SOURCE and recs[0]["inserted_codes"] == ["000000", "000001", "000002"]
    assert recs[0]["promax_rows"] == 2001 and recs[0]["eastmoney_inserted"] == 3
