"""参考交易日必须落在交易所日历的真实交易日上。

背景数据墙：2026-09-28（周一）09:37 健康度掉到 78 分，报「日K线/分钟K线/指数日线/资金流向
最近 10 个交易日缺失 1 天: 2026-09-25」外加 10 个数据域滞后——共 22 分。而 Tushare SSE 日历
明确写着 2026-09-25 `is_open=0`（当周 09-25/26/27 全休市）。根因：参考交易日取
`min(Tushare 最新交易日, latest_allowed_bar_date())`，后者是纯工作日算术，17:30 前回退到
「上一个工作日」= 09-25，正好是休市的周五。而 `_recent_daily_trade_days` 会把参考日强行塞进
交易日窗口，于是所有表都被判成缺这一天，全是假扣分。

本测试锁定三件事：
  1. 休市的工作日不得成为参考交易日
  2. 盘后数据就绪窗口仍然生效（当日未到 17:30 不取当日）
  3. 日历不可用时回退到本地推断，不再凭工作日算术编造日期
"""
from __future__ import annotations

from app.services import data_health_service as service

TRADE_DATES = [
    "2026-09-18",
    "2026-09-21",
    "2026-09-22",
    "2026-09-23",
    "2026-09-24",
    "2026-09-28",
]


def _resolve(monkeypatch, *, trade_dates: list[str], data_ready_cutoff: str) -> dict:
    monkeypatch.setattr(service, "_get_tushare_recent_trade_dates", lambda: list(trade_dates))
    monkeypatch.setattr(service, "_latest_allowed_bar_date", lambda: data_ready_cutoff)
    monkeypatch.setattr(service, "_infer_local_reference_trade_date", lambda _db: "2026-09-11")
    return service._resolve_reference_trade_date(None)


def test_holiday_weekday_is_never_the_reference(monkeypatch):
    """09-25 是休市的周五：就绪判据落在它上面时，参考日必须退到 09-24。"""
    result = _resolve(monkeypatch, trade_dates=TRADE_DATES, data_ready_cutoff="2026-09-25")
    assert result["reference_trade_date"] == "2026-09-24"
    assert result["source"] == "tushare"
    assert result["external_trade_date"] == "2026-09-28"


def test_data_ready_window_still_excludes_today_before_cutoff(monkeypatch):
    """当日 17:30 前不取当日：就绪判据给上一工作日，参考日取不晚于它的交易日。"""
    result = _resolve(monkeypatch, trade_dates=TRADE_DATES, data_ready_cutoff="2026-09-23")
    assert result["reference_trade_date"] == "2026-09-23"


def test_after_cutoff_today_becomes_the_reference(monkeypatch):
    """当日已过 17:30 且当日是交易日：参考日就是当日。"""
    result = _resolve(monkeypatch, trade_dates=TRADE_DATES, data_ready_cutoff="2026-09-28")
    assert result["reference_trade_date"] == "2026-09-28"


def test_calendar_unavailable_falls_back_to_local_inference(monkeypatch):
    """日历拿不到时回退本地推断，而不是用工作日算术编一个日期。"""
    result = _resolve(monkeypatch, trade_dates=[], data_ready_cutoff="2026-09-25")
    assert result["reference_trade_date"] == "2026-09-11"
    assert result["source"] == "local"


def test_no_trade_date_at_or_before_cutoff_falls_back(monkeypatch):
    """就绪判据早于日历窗口内所有交易日时不得硬取最早那天，走本地回退。"""
    result = _resolve(monkeypatch, trade_dates=TRADE_DATES, data_ready_cutoff="2026-09-01")
    assert result["source"] == "local"
