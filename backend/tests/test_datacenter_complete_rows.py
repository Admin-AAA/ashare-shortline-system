"""数据中心 provider 优先级链取完整单日数据 + 两融补数接入回归测试。

背景：margin_detail_fill 此前硬编码只调东财，绕开 data_source_config 启停/优先级、
routing_table 链序与熔断；且「只回半个市场」这类截断被网关当成成功，不会换源。
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.tables import MarginDetail
from app.services import margin_detail_fill as mf
from app.services.datasource import complete_rows as cr


class _FakeProvider:
    def __init__(self, name: str):
        self.name = name


class _FakeRouter:
    """按数据中心 _ordered_providers 语义返回已排序 provider，并记录实际请求链。"""

    def __init__(self, sources: dict[str, list[str] | Exception]):
        self._providers = {n: _FakeProvider(n) for n in sources}
        self._sources = sources
        self.calls: list[str] = []

    def _ordered_providers(self, api_name):
        return [self._providers[n] for n in self._sources]

    def request_pinned(self, provider_name, api_name, *, params=None, fields=""):
        self.calls.append(provider_name)
        rows = self._sources[provider_name]
        if isinstance(rows, Exception):
            raise rows
        return {"data": {"fields": ["ts_code", "rzye"], "items": [[c, 1e8] for c in rows]}}


def _env(fields, items):
    return {"data": {"fields": fields, "items": items}}


# ---------------------------------------------------------------- complete_rows
def test_merges_all_sources_and_deduplicates():
    router = _FakeRouter({"first": ["000001.SZ", "000002.SZ"],
                          "second": ["000002.SZ", "000003.SZ"], "third": ["000004.SZ"]})
    out = cr.fetch_complete_trade_date("margin_detail", "20261113", router=router)
    assert sorted(r["ts_code"] for r in out["rows"]) == ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"]
    assert out["by_source"] == {"first": 2, "second": 1, "third": 1}
    assert out["attempted"] == ["first", "second", "third"]
    assert out["complete"] is False  # expected_rows=0 表示无法判断


def test_stops_as_soon_as_expected_rows_reached():
    router = _FakeRouter({"first": ["000001.SZ", "000002.SZ"], "second": ["000003.SZ"],
                           "third": ["000004.SZ"]})
    out = cr.fetch_complete_trade_date("margin_detail", "20261113", expected_rows=2,
                                       complete_ratio=1.0, router=router)
    assert router.calls == ["first"]
    assert out["complete"] is True and len(out["rows"]) == 2


def test_excluded_and_failing_sources_do_not_abort_chain():
    router = _FakeRouter({"promax": ["600001.SH"], "second": RuntimeError("quota"),
                          "third": ["000001.SZ"]})
    out = cr.fetch_complete_trade_date("margin_detail", "20261113", router=router,
                                        exclude=("promax",))
    assert router.calls == ["second", "third"]
    assert len(out["rows"]) == 1 and out["by_source"] == {"second": 0, "third": 1}
    assert any("quota" in e for e in out["errors"])


def test_extra_fetchers_run_last_and_are_recorded():
    router = _FakeRouter({"first": ["000001.SZ"]})
    out = cr.fetch_complete_trade_date("margin_detail", "20261113", router=router,
                                        extra_fetchers=(("eastmoney", lambda d: [{"code": "000002", "rzye": 1.0}]),))
    assert out["attempted"] == ["first", "eastmoney"]
    assert out["by_source"]["eastmoney"] == 1 and len(out["rows"]) == 2


def test_envelope_rows_handles_empty_and_malformed():
    assert cr.envelope_rows(None) == []
    assert cr.envelope_rows(_env(["ts_code", "rzye"], [["000001.SZ", 1]])) == [{"ts_code": "000001.SZ", "rzye": 1}]
    assert cr.envelope_rows(_env(["ts_code", "rzye"], [["000001.SZ"]])) == []


# ---------------------------------------------------------------- 单位与接入
def test_from_provider_row_converts_yuan_to_yi_and_normalizes_code():
    row = mf._from_provider_row({"ts_code": "600001.SH", "rzye": 1.5e8, "rqye": 2e8,
                                 "rzmre": 3e8, "rzche": 4e8, "rqyl": 12345}, "20261113")
    assert row["code"] == "600001" and row["trade_date"] == "2026-11-13"
    assert row["rzye"] == 1.5 and row["rqye"] == 2.0 and row["rqyl"] == 12345.0
    assert row["rzrqye"] is None  # provider 不返回该字段，交给东财链尾补
    assert mf._from_provider_row({"ts_code": "BAD"}, "20261113") is None


def test_default_fetch_goes_through_datacenter_and_eastmoney_tail(monkeypatch):
    calls = {}

    def fake_complete(api, td, **kw):
        calls["api"], calls["td"], calls["kw"] = api, td, kw
        return {"rows": [{"ts_code": "600001.SH", "rzye": 1e8}],
                "by_source": {"teajoin": 1}, "attempted": ["teajoin"],
                "errors": [], "complete": False, "expected_rows": 4489}

    monkeypatch.setattr(cr, "fetch_complete_trade_date", fake_complete)
    import app.services.datasource.eastmoney as em
    monkeypatch.setattr(em, "_call", lambda key, fn, d: [{"code": "000001", "trade_date": d, "rzye": 2.0}])
    out = mf._default_fetch("20261113", expected_rows=4489)
    assert calls["api"] == "margin_detail" and calls["kw"]["exclude"] == ("promax",)
    assert calls["kw"]["expected_rows"] == 4489
    assert {r["code"] for r in out["rows"]} == {"600001", "000001"}
    assert out["probe"]["by_source"]["eastmoney"] == 1
    assert "eastmoney" in out["probe"]["attempted"]


# ---------------------------------------------------------------- 退避感知
@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ASHARE_MARGIN_FILL_RUNTIME_DIR", str(tmp_path))
    eng = create_engine("sqlite://")
    MarginDetail.__table__.create(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _seed(db, day, codes):
    db.bulk_insert_mappings(MarginDetail, [{"code": c, "trade_date": day, "rzye": 1.0} for c in codes])
    db.commit()


def _write_backoff_history(day: str, rows_after: int = 2019):
    for _ in range(4):
        mf._append_provenance({"ts": datetime.now().isoformat(timespec="seconds"),
                               "trade_date": day, "source": mf.SOURCE, "status": "incomplete",
                               "rows_after": rows_after})


def test_upstream_recovered_only_when_row_count_grows(monkeypatch):
    monkeypatch.setattr(cr, "fetch_complete_trade_date",
                        lambda *a, **k: {"rows": [{"ts_code": f"{i:06d}.SZ"} for i in range(3000)]})
    assert mf._upstream_recovered("2026-11-13", 2019) is True
    assert mf._upstream_recovered("2026-11-13", 3000) is False


def test_upstream_probe_failure_keeps_backoff(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("all providers down")
    monkeypatch.setattr(cr, "fetch_complete_trade_date", boom)
    assert mf._upstream_recovered("2026-11-13", 2019) is False


def test_backoff_is_cleared_when_upstream_recovers(db, monkeypatch):
    day = "2026-11-13"
    _seed(db, day, [f"{600000 + i:06d}" for i in range(2019)])
    _write_backoff_history(day)
    assert mf._backoff_active(day) is True
    monkeypatch.setattr(mf, "_upstream_recovered", lambda d, n: True)
    monkeypatch.setattr(mf, "in_trading_session", lambda now=None: False)
    fetched = [{"code": f"{i:06d}", "trade_date": day, "rzye": 1.0} for i in range(4489)]
    out = mf.run_margin_detail_fill(db, trade_date=day, lookback_days=1, fetch=lambda d: fetched)
    assert out["backoffed"] == []
    assert any(x.get("trade_date") == day and x.get("status") != "skipped_no_primary" for x in out["days"])
    lines = [json.loads(x) for x in (mf.runtime_dir() / "margin_fill_provenance.jsonl").read_text().splitlines()]
    assert any(r.get("status") == "recovered_probe" for r in lines)
    assert mf._backoff_active(day) is False


def test_fill_margin_day_accepts_probe_dict(db):
    day = "2026-11-13"
    # 截断判定靠「对比前 5 交易日行数中位数」，先造出正常规模 4489 行的基线日期。
    for prev in ("2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"):
        _seed(db, prev, [f"{i:06d}" for i in range(4489)])
    _seed(db, day, [f"{600000 + i:06d}" for i in range(2019)])
    monkeypatch_probe = {"attempted": ["teajoin"], "by_source": {"teajoin": 1},
                         "errors": [], "complete": False, "expected_rows": 4489}
    more = [{"code": f"{i:06d}", "trade_date": day, "rzye": 1.0} for i in range(4400, 4489)]
    out = mf.fill_margin_day(db, day,
                             fetch=lambda d: {"rows": more, "probe": monkeypatch_probe},
                             now=datetime(2026, 11, 14, 19, 0))
    # 链上所有源合计只给 2108/4489，必须诚实标 incomplete 并留在待补清单，不能标 filled。
    assert out["status"] == "incomplete" and out["provider_probe"] == monkeypatch_probe
    assert out["eastmoney_inserted"] == 89
    assert day in mf.load_pending()
