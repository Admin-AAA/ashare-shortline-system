"""`app.engine.trading_calendar` 深模块单测（纯 in-process，无 DB/网络/Tushare）。

覆盖：周末回退、跨周边界、quote 与 bar 日期差异（盘后 17:30 就绪窗口）。
关联需求：2.1（三助手只在 trading_calendar）、2.5（调用方经 import 单一深模块）、
1.1（行为不变基线：相同输入相同输出）。
"""
from datetime import date, datetime

import pytest

from app.engine import trading_calendar as tc


class _FrozenDatetime(datetime):
    """把 `datetime.now()` 冻结到指定时刻，用于确定性测试 today=None 分支。"""

    _frozen: datetime

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, D102
        return cls._frozen


def _freeze(monkeypatch, moment: datetime) -> None:
    frozen = type("_Frozen", (_FrozenDatetime,), {"_frozen": moment})
    monkeypatch.setattr(tc, "datetime", frozen)


# ── 周末回退：显式传入日期 ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "given, expected",
    [
        (date(2026, 6, 13), "2026-06-12"),  # 周六 -> 周五
        (date(2026, 6, 14), "2026-06-12"),  # 周日 -> 周五
        (date(2026, 6, 10), "2026-06-10"),  # 周三 -> 当日
        (date(2026, 6, 12), "2026-06-12"),  # 周五 -> 当日
        (date(2026, 6, 8), "2026-06-08"),   # 周一 -> 当日
    ],
)
def test_bar_date_weekend_rollback(given, expected):
    assert tc.latest_allowed_bar_date(given) == expected


@pytest.mark.parametrize(
    "given, expected",
    [
        (date(2026, 6, 13), "2026-06-12"),  # 周六 -> 周五
        (date(2026, 6, 14), "2026-06-12"),  # 周日 -> 周五
        (date(2026, 6, 10), "2026-06-10"),  # 周三 -> 当日
        (date(2026, 6, 12), "2026-06-12"),  # 周五 -> 当日
    ],
)
def test_quote_date_weekend_rollback(given, expected):
    assert tc.latest_allowed_quote_date(given) == expected


# ── previous_weekday：跨周边界 ────────────────────────────────────────────

@pytest.mark.parametrize(
    "given, expected",
    [
        (date(2026, 6, 10), date(2026, 6, 9)),   # 周三 -> 周二
        (date(2026, 6, 8), date(2026, 6, 5)),    # 周一 -> 上周五（跨周）
        (date(2026, 6, 14), date(2026, 6, 12)),  # 周日 -> 周五
        (date(2026, 6, 13), date(2026, 6, 12)),  # 周六 -> 周五
        (date(2026, 6, 12), date(2026, 6, 11)),  # 周五 -> 周四
    ],
)
def test_previous_weekday(given, expected):
    assert tc.previous_weekday(given) == expected


# ── quote 与 bar 的差异：today=None 时的盘后 17:30 就绪窗口 ───────────────

def test_bar_before_1730_rolls_back_but_quote_does_not(monkeypatch):
    # 周三 09:00：daily 未就绪 -> bar 回退到周二；quote 不受此约束仍取当日
    _freeze(monkeypatch, datetime(2026, 6, 10, 9, 0))
    assert tc.latest_allowed_bar_date() == "2026-06-09"
    assert tc.latest_allowed_quote_date() == "2026-06-10"


def test_bar_after_1730_stays_today(monkeypatch):
    # 周三 18:00：daily 已就绪 -> bar 与 quote 均为当日
    _freeze(monkeypatch, datetime(2026, 6, 10, 18, 0))
    assert tc.latest_allowed_bar_date() == "2026-06-10"
    assert tc.latest_allowed_quote_date() == "2026-06-10"


def test_bar_at_exactly_1730_is_ready(monkeypatch):
    # 边界：恰好 17:30 视为已就绪（minute < 30 为假）-> 当日
    _freeze(monkeypatch, datetime(2026, 6, 10, 17, 30))
    assert tc.latest_allowed_bar_date() == "2026-06-10"


def test_bar_at_1729_not_ready(monkeypatch):
    # 边界：17:29 仍未就绪 -> 回退到上一交易日
    _freeze(monkeypatch, datetime(2026, 6, 10, 17, 29))
    assert tc.latest_allowed_bar_date() == "2026-06-09"


def test_bar_monday_before_close_crosses_week(monkeypatch):
    # 周一 09:00：回退跨周 -> 上周五
    _freeze(monkeypatch, datetime(2026, 6, 8, 9, 0))
    assert tc.latest_allowed_bar_date() == "2026-06-05"


def test_weekend_now_ignores_close_window(monkeypatch):
    # 周六任意时刻：pre-close 分支被 weekday<5 守卫跳过，直接周末回退
    _freeze(monkeypatch, datetime(2026, 6, 13, 9, 0))
    assert tc.latest_allowed_bar_date() == "2026-06-12"
    assert tc.latest_allowed_quote_date() == "2026-06-12"


# ── 属性/不变量：对大量日期成立 ───────────────────────────────────────────

def _date_range(start: date, days: int):
    from datetime import timedelta
    return [start + timedelta(days=i) for i in range(days)]


def test_previous_weekday_always_returns_weekday_strictly_before():
    """previous_weekday 结果恒为工作日且严格早于入参。"""
    for d in _date_range(date(2025, 1, 1), 400):
        prev = tc.previous_weekday(d)
        assert prev < d
        assert prev.weekday() < 5  # 0..4 = 周一..周五


def test_bar_and_quote_never_return_weekend():
    """显式传入任意日期，bar/quote 返回的日期都不是周末。"""
    for d in _date_range(date(2025, 1, 1), 400):
        bar = date.fromisoformat(tc.latest_allowed_bar_date(d))
        quote = date.fromisoformat(tc.latest_allowed_quote_date(d))
        assert bar.weekday() < 5
        assert quote.weekday() < 5


def test_explicit_today_bar_equals_quote():
    """行为契约：显式给定 today 时，bar 与 quote 唯一差异（盘后窗口）不生效，二者恒等。"""
    for d in _date_range(date(2025, 1, 1), 400):
        assert tc.latest_allowed_bar_date(d) == tc.latest_allowed_quote_date(d)


def test_quote_on_weekday_is_same_day():
    """工作日的 quote 日期恒为当日。"""
    for d in _date_range(date(2025, 1, 1), 400):
        if d.weekday() < 5:
            assert tc.latest_allowed_quote_date(d) == d.isoformat()


# ── normalize_trade_date：交易日字符串归一（补 1.1 收口缺口） ──────────────

@pytest.mark.parametrize(
    "given, expected",
    [
        ("2026-05-28", "2026-05-28"),            # 已是 ISO
        ("2026-05-28 09:30:00", "2026-05-28"),   # ISO 带时间 -> 截前 10 位
        ("2026-05-28T09:30:00", "2026-05-28"),   # ISO 带 T -> 截前 10 位
        ("20260528", "2026-05-28"),              # 纯数字紧凑 -> 补连字符
        ("20260528.0", "2026-05-28"),            # 数字带小数点 -> 抽数字补连字符
        (20260528, "2026-05-28"),                # int -> str 后紧凑归一
        (date(2026, 5, 28), "2026-05-28"),       # date 对象 -> str 即 ISO
        (None, ""),                              # 空值 -> 空串
        ("", ""),                                # 空串 -> 空串
        ("   ", ""),                             # 纯空白 strip 后 -> 空串
        ("abc", "abc"),                          # 无数字杂串 -> 截前 10 位兜底
        ("2026/05/28", "2026-05-28"),            # 斜杠分隔 -> 抽数字补连字符
    ],
)
def test_normalize_trade_date(given, expected):
    assert tc.normalize_trade_date(given) == expected


def test_normalize_trade_date_is_idempotent():
    """归一是幂等的：normalize(normalize(x)) == normalize(x)。"""
    for raw in ["2026-05-28", "20260528", "2026/05/28", "", "abc", "2026-05-28 09:30:00", None]:
        once = tc.normalize_trade_date(raw)
        assert tc.normalize_trade_date(once) == once


def test_normalize_trade_date_output_shape():
    """非空归一结果要么是 10 位 ISO，要么是长度<=10 的兜底截断，永不超过 10 位。"""
    for raw in ["2026-05-28", "20260528", "2026-05-28 09:30:00", "20260528.0", "abcdefghijklmn"]:
        out = tc.normalize_trade_date(raw)
        assert len(out) <= 10
