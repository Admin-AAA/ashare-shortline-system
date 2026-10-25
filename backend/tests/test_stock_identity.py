"""stock_identity：ST 区间重建 / 区间 diff / 日期归一 / namechange 缓存（纯函数，无 DB / 网络）。"""
from __future__ import annotations

import pandas as pd
import pytest

from app.services import stock_identity as si
from app.services.backtest.stock_metadata import StockMetadataRecord, resolve_stock_metadata_as_of


def _cur(name="测试", is_st=False, list_date="2010-01-01", **kw):
    return {"name": name, "industry": "银行", "market": "主板", "list_date": list_date, "is_st": is_st, **kw}


def _nc(rows):
    return pd.DataFrame(rows, columns=["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"])


def _intervals(current, rows, today="2026-10-25"):
    return si.build_intervals(current, si.derive_events(_nc(rows)), today=today)


def test_iso_date_normalizes_compact_and_rejects_garbage():
    assert si.iso_date("20260522") == "2026-05-22"
    assert si.iso_date("2026-05-22") == "2026-05-22"
    assert si.iso_date(None) is None and si.iso_date("None") is None and si.iso_date("") is None
    assert si.iso_date(float("nan")) is None


@pytest.mark.parametrize("name, expected", [
    ("*ST康佳A", True), ("ST景峰", True), ("S*ST前锋", True), ("萃华退", True), ("退市新亿", True), ("退新亿(退)", True),
    ("平安银行", False), ("", False), (None, False),
])
def test_is_st_name_includes_delisting_period_names(name, expected):
    assert si.is_st_name(name) is expected


def test_st_name_state_comes_from_every_namechange_row_not_only_st_reasons():
    # 600530：「其他」原因的 *ST 名称也必须算 ST（旧逻辑只看 reason 含 ST 的行，漏掉 2020-04-29~05-05）
    rows = [
        ("600530.SH", "*ST昂立", "20200429", "20200505", "20200429", "其他"),
        ("600530.SH", "*ST交昂", "20200506", "20210516", "20200430", "*ST"),
        ("600530.SH", "交大昂立", "20210517", "20230704", "20210514", "撤销*ST"),
    ]
    out, _ = _intervals({"600530": _cur("交大昂立")}, rows)
    by_from = {i["effective_from"]: i for i in out}
    assert by_from["2020-04-29"]["is_st"] is True and by_from["2020-04-29"]["effective_to"] == "2021-05-16"
    assert by_from["2021-05-17"]["is_st"] is False and by_from["2021-05-17"]["effective_to"] is None
    assert by_from["2015-01-01"]["is_st"] is False and by_from["2015-01-01"]["effective_to"] == "2020-04-28"


def test_intervals_are_contiguous_non_overlapping_and_resolve_point_in_time():
    rows = [
        ("000001.SZ", "ST甲", "20200101", None, "x", "ST"),
        ("000001.SZ", "甲", "20210301", None, "x", "撤销ST"),
        ("000001.SZ", "*ST甲", "20230601", None, "x", "*ST"),
    ]
    out, _ = _intervals({"000001": _cur("*ST甲", is_st=True)}, rows)
    assert si.count_overlaps(out) == 0
    recs = tuple(StockMetadataRecord(**{k: i[k] for k in ("code", "name", "industry", "market", "list_date", "is_st", "effective_from", "effective_to")},
                                      source_version="t") for i in out)
    st_on = lambda d: resolve_stock_metadata_as_of(recs, ("000001",), d).metadata_by_code["000001"]["is_st"]  # noqa: E731
    assert [st_on(d) for d in ("2019-12-31", "2020-01-01", "2021-02-28", "2021-03-01", "2023-06-01", "2026-10-25")] == [
        False, True, True, False, True, True]


def test_new_listing_gets_single_current_interval_with_iso_dates():
    out, stats = _intervals({"920285": _cur("远大健科", list_date="2026-10-23")}, [])
    assert out == [{"code": "920285", "name": "远大健科", "industry": "银行", "market": "主板", "list_date": "2026-10-23",
                    "is_st": False, "effective_from": "2026-10-23", "effective_to": None}]
    assert stats["codes"] == 1


def test_current_name_is_authority_when_namechange_lags():
    # stock_basic 现名已是 ST，但 namechange 还没有这条 → 在今天补校正点，而不是整段非 ST
    rows = [("000002.SZ", "乙", "20150101", None, "x", "其他")]
    out, stats = _intervals({"000002": _cur("ST乙", is_st=True)}, rows, today="2026-10-25")
    assert out[-1]["is_st"] is True and out[-1]["effective_from"] == "2026-10-25"
    assert stats["sync_points"] == 1
    assert si.count_overlaps(out) == 0


def test_delisted_stock_intervals_stop_at_delist_date_and_pre_window_is_skipped():
    rows = [("600145.SH", "*ST新亿", "20150701", None, "x", "其他"), ("600145.SH", "退市新亿", "20220330", None, "x", "退市整理期")]
    cur = {"600145": _cur("退市新亿", is_st=True, delist_date="2022-04-28"),
           "600001": _cur("邯郸钢铁", list_date="1998-01-01", delist_date="2009-12-30")}
    out, stats = _intervals(cur, rows)
    assert all(i["code"] == "600145" for i in out) and stats["skipped_before_window"] == 1
    assert out[-1]["effective_to"] == "2022-04-28" and out[-1]["is_st"] is True


def test_first_event_exit_reason_means_previously_st():
    rows = [("000003.SZ", "丙", "20160301", None, "x", "撤销ST")]
    out, _ = _intervals({"000003": _cur("丙")}, rows)
    assert (out[0]["is_st"], out[0]["effective_from"], out[0]["effective_to"]) == (True, "2015-01-01", "2016-02-29")
    assert out[1]["is_st"] is False


def test_invalid_codes_are_dropped():
    out, _ = _intervals({"T60001": _cur("坏代码"), "000004": _cur("好")}, [])
    assert [i["code"] for i in out] == ["000004"]


def test_diff_intervals_insert_update_delete_and_effective_from_normalization():
    new = [
        {"code": "001237", "name": "惠康科技", "industry": None, "market": "主板", "list_date": "2026-05-22", "is_st": False,
         "effective_from": "2026-05-22", "effective_to": None},
        {"code": "000001", "name": "乙", "industry": "银行", "market": "主板", "list_date": "2010-01-01", "is_st": True,
         "effective_from": "2026-09-01", "effective_to": None},
        {"code": "000001", "name": "乙", "industry": "银行", "market": "主板", "list_date": "2010-01-01", "is_st": False,
         "effective_from": "2015-01-01", "effective_to": "2026-08-31"},
    ]
    sv = si.SOURCE_VERSION
    existing = [
        {"id": 1, "code": "001237", "name": "惠康科技", "industry": None, "market": "主板", "list_date": "2026-05-22", "is_st": 0,
         "effective_from": "20260522", "effective_to": None, "source_version": sv},          # 格式修正
        {"id": 2, "code": "000001", "name": "乙", "industry": "银行", "market": "主板", "list_date": "2010-01-01", "is_st": 0,
         "effective_from": "2015-01-01", "effective_to": None, "source_version": sv},        # effective_to 收口
        {"id": 3, "code": "000009", "name": "丁", "industry": "银行", "market": "主板", "list_date": "2010-01-01", "is_st": 0,
         "effective_from": "2015-01-01", "effective_to": None, "source_version": sv},        # 不在新区间
    ]
    d = si.diff_intervals(existing, new)
    assert d["normalized"] == 1
    assert [(r["code"], r["effective_from"]) for r in d["insert"]] == [("000001", "2026-09-01")]
    upd = {rid: fields for rid, _, fields in d["update"]}
    assert upd[1] == ["effective_from"] and upd[2] == ["effective_to"]
    assert d["delete"] == [3]
    # 范围限定：取数失败的代码不动
    scoped = si.diff_intervals(existing, new, scope_codes={"000001"})
    assert scoped["delete"] == [] and [rid for rid, *_ in scoped["update"]] == [2]


def test_diff_is_idempotent_after_applying():
    new = [{"code": "000001", "name": "乙", "industry": "银行", "market": "主板", "list_date": "2010-01-01", "is_st": True,
            "effective_from": "2026-09-01", "effective_to": None}]
    existing = [{"id": 5, **new[0], "is_st": 1, "source_version": si.SOURCE_VERSION}]
    d = si.diff_intervals(existing, new)
    assert d == {"insert": [], "update": [], "delete": [], "normalized": 0}


def test_namechange_completed_years_are_cached_current_year_always_fetched(tmp_path):
    calls: list[str] = []

    class FakePro:
        def namechange(self, start_date, end_date):
            calls.append(start_date[:4])
            return pd.DataFrame([{"ts_code": "000001.SZ", "name": "x", "start_date": f"{start_date[:4]}0105", "end_date": None,
                                  "ann_date": None, "change_reason": "其他"}])

    df, per_year = si.fetch_namechange(FakePro(), 2024, 2026, tmp_path)
    assert calls == ["2024", "2025", "2026"] and len(df) == 3 and per_year == {2024: 1, 2025: 1, 2026: 1}
    calls.clear()
    df2, _ = si.fetch_namechange(FakePro(), 2024, 2026, tmp_path)
    assert calls == ["2026"] and len(df2) == 3  # 2024/2025 命中缓存


def test_refresh_rejects_truncated_stock_basic():
    class Row:
        def __init__(self, v):
            self.v = v

        def scalar(self):
            return self.v

    class FakeDb:
        def execute(self, *_a, **_k):
            return Row(5525)

    class FakePro:
        def stock_basic(self, **_k):
            return pd.DataFrame([{"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "list_date": "19910403"}])

    with pytest.raises(si.StockIdentityError):
        si.refresh_stock_identity(FakeDb(), FakePro(), dry_run=True, cache_dir=None)
