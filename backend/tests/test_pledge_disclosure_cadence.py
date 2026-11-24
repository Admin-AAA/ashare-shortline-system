"""质押披露节奏：每周五，不是季度。

2026-11-24 体检：pledge_stats 停在 2026-09-30 整 55 天。候选日只列季末，
而上游 end_date=20261120 有 2265 行、20261113 有 2262 行、20261106 有 2247 行。
000001 的 pledge_ratio 在 09-30 是 0.13%、11-06 已是 0.23%，取到的是过期值。
"""
from datetime import date

from app.services.auxiliary.state_adapters import (
    _pledge_disclosure_candidates,
    _quarter_end_candidates,
)


def test_pledge_candidates_are_recent_fridays():
    got = _pledge_disclosure_candidates(date(2026, 11, 24), lookback_weeks=4)
    assert got[:4] == ["20261120", "20261113", "20261106", "20261030"]
    assert all(date(int(d[:4]), int(d[4:6]), int(d[6:])).weekday() == 4 for d in got[:4])


def test_pledge_candidates_include_the_day_itself_when_friday():
    assert _pledge_disclosure_candidates(date(2026, 11, 20), lookback_weeks=2)[:2] == ["20261120", "20261113"]


def test_pledge_candidates_never_reach_into_the_future():
    for d in _pledge_disclosure_candidates(date(2026, 11, 24)):
        assert d <= "20261124"


def test_pledge_candidates_fall_back_to_quarter_ends():
    got = _pledge_disclosure_candidates(date(2026, 11, 24), lookback_weeks=2)
    assert got[:2] == ["20261120", "20261113"]
    assert "20260930" in got and "20260630" in got
    assert len(got) == len(set(got))


def test_quarter_end_candidates_still_used_for_quarterly_datasets():
    """财报与股东人数确实按季度披露，候选口径不能被质押的周度节奏改掉。"""
    assert _quarter_end_candidates(date(2026, 11, 24))[0] == "20260930"
    assert _quarter_end_candidates(date(2026, 3, 31))[0] == "20260331"