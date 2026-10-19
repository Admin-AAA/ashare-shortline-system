import datetime as dt

from app.core.offset_json import add_utc_offset
from app.core.time import bj_now_iso


def test_naive_iso_gets_z():
    out = add_utc_offset({"a": "2026-10-19T01:02:03", "b": ["2026-10-19T01:02:03.123456"], "n": 1})
    assert out == {"a": "2026-10-19T01:02:03Z", "b": ["2026-10-19T01:02:03.123456Z"], "n": 1}


def test_other_strings_untouched():
    for v in ["2026-10-19", "2026-10-19T01:02:03Z", "2026-10-19T09:02:03+08:00", "2026-10-19 09:02:03", "abc", "20261019"]:
        assert add_utc_offset(v) == v


def test_bj_now_iso_has_offset():
    v = bj_now_iso()
    assert v.endswith("+08:00")
    assert dt.datetime.fromisoformat(v).utcoffset() == dt.timedelta(hours=8)


def test_standalone_bj_offset():
    from app.api.strategies import _bj_offset
    assert _bj_offset("2026-10-19T09:00:00") == "2026-10-19T09:00:00+08:00"
    assert _bj_offset("2026-10-19T09:00:00+08:00") == "2026-10-19T09:00:00+08:00"
    assert _bj_offset(None) is None
