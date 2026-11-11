"""中线盘中确认（_intraday_overlay）：入场日当天日K未入库时用实时快照给出持有中 + 浮动收益。"""
from __future__ import annotations

import datetime as dt

import pytest

from app.services import midterm_service as ms
from app.services import trade_calendar


def _now(h: int = 10, m: int = 0) -> dt.datetime:
    return dt.datetime(2026, 11, 11, h, m, tzinfo=ms._CST)


@pytest.fixture
def calendar_says(monkeypatch):
    """让「信号日之后的第一个交易日」可配置。"""
    def _apply(next_day: str | None):
        def fake(start, end, **kwargs):
            if next_day is None:
                return []
            compact = next_day.replace("-", "")
            return [compact]
        monkeypatch.setattr(trade_calendar, "open_dates_between", fake)
    return _apply


def _doc(signal_date: str, code: str = "600000", status: str = "pending") -> dict:
    return {"signal_date": signal_date, "status": status, "avg_ret": None,
            "picks": [{"code": code, "status": status}]}


def test_overlay_marks_holding_with_live_return(monkeypatch, calendar_says):
    calendar_says("2026-11-11")
    monkeypatch.setattr(ms, "_intraday_quotes",
                        lambda codes: {"600000": {"open": 10.0, "price": 10.5, "pre_close": 9.8}})
    docs = [_doc("2026-11-10")]

    ms._intraday_overlay(object(), docs, now=_now())

    p = docs[0]["picks"][0]
    assert p["status"] == "holding"
    assert p["intraday"] is True
    assert p["entry_date"] == "2026-11-11"
    assert p["entry_price"] == 10.0
    assert p["mark_price"] == 10.5
    assert p["ret"] == 5.0          # 10.5 / 10.0 - 1，浮盈不扣成本（与日K结算同口径）
    assert docs[0]["status"] == "holding"
    assert docs[0]["avg_ret"] == 5.0


def test_overlay_skips_limit_up_open(monkeypatch, calendar_says):
    """一字涨停买不进 -> skipped，与 _realize_rows 的判据一致（主板 10%）。"""
    calendar_says("2026-11-11")
    monkeypatch.setattr(ms, "_intraday_quotes",
                        lambda codes: {"600000": {"open": 10.78, "price": 10.78, "pre_close": 9.8}})
    docs = [_doc("2026-11-10")]

    ms._intraday_overlay(object(), docs, now=_now())

    p = docs[0]["picks"][0]
    assert p["status"] == "skipped"
    assert p["cancel_code"] == "limit_up_open"
    assert p["intraday"] is True


def test_overlay_ignores_non_entry_day(monkeypatch, calendar_says):
    """下一个交易日不是今天（比如今天不是入场日）-> 保持待买入，不发实时请求。"""
    calendar_says("2026-11-12")
    called = []
    monkeypatch.setattr(ms, "_intraday_quotes", lambda codes: called.append(codes) or {})
    docs = [_doc("2026-11-10")]

    ms._intraday_overlay(object(), docs, now=_now())

    assert docs[0]["picks"][0]["status"] == "pending"
    assert called == []


def test_overlay_before_open_auction_is_noop(monkeypatch, calendar_says):
    """09:25 之前开盘价还没定，不能确认。"""
    calendar_says("2026-11-11")
    monkeypatch.setattr(ms, "_intraday_quotes", lambda codes: pytest.fail("不应请求实时行情"))
    docs = [_doc("2026-11-10")]

    ms._intraday_overlay(object(), docs, now=_now(9, 20))

    assert docs[0]["picks"][0]["status"] == "pending"


def test_overlay_leaves_suspended_stock_pending(monkeypatch, calendar_says):
    """停牌（无开盘价）顺延买入，保持待买入。"""
    calendar_says("2026-11-11")
    monkeypatch.setattr(ms, "_intraday_quotes",
                        lambda codes: {"600000": {"open": 0.0, "price": 0.0, "pre_close": 9.8}})
    docs = [_doc("2026-11-10")]

    ms._intraday_overlay(object(), docs, now=_now())

    assert docs[0]["picks"][0]["status"] == "pending"


def test_overlay_quotes_failure_keeps_pending(monkeypatch, calendar_says):
    """实时源不可用 -> 保持待买入，不阻塞列表。"""
    calendar_says("2026-11-11")
    monkeypatch.setattr(ms, "_intraday_quotes", lambda codes: {})
    docs = [_doc("2026-11-10")]

    ms._intraday_overlay(object(), docs, now=_now())

    assert docs[0]["picks"][0]["status"] == "pending"
