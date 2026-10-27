"""fina_indicator_vip 备用源 + ths_index 降级 + broker_recommend 告警（2026-10-19）。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from app.services.datasource import fina_fallback as ff


class _Pro:
    def __init__(self):
        self.calls = []

    def fina_indicator(self, **kw):
        self.calls.append(kw)
        return pd.DataFrame([{"ts_code": kw["ts_code"], "ann_date": "20261019", "end_date": kw["period"],
                              "dt_netprofit_yoy": 80.0, "or_yoy": 30.0, "profit_dedt": 1.0}])


def test_fallback_merges_baseline_and_new_announcers(monkeypatch):
    ff._cache.clear()
    base = pd.DataFrame([{"ts_code": "000001.SZ", "ann_date": "20261010", "end_date": "20260930",
                          "dt_netprofit_yoy": 1.0, "or_yoy": 2.0, "profit_dedt": 3.0, "roe": 9.9}])
    pro = _Pro()
    out = ff.fina_period_fallback(
        pro, "20260930", "ts_code,ann_date,end_date,dt_netprofit_yoy,or_yoy,profit_dedt",
        today=date(2026, 10, 19), baseline_loader=lambda p: base,
        announcer_loader=lambda d: ["000001", "600000", "300750"],
    )
    # 只为基线缺失的新公告股票逐股请求
    assert sorted(c["ts_code"] for c in pro.calls) == ["300750.SZ", "600000.SH"]
    assert set(out["ts_code"]) == {"000001.SZ", "600000.SH", "300750.SZ"}
    assert list(out.columns) == ["ts_code", "ann_date", "end_date", "dt_netprofit_yoy", "or_yoy", "profit_dedt"]
    # 同日同期第二次调用走缓存，不再打上游
    ff.fina_period_fallback(pro, "20260930", "", today=date(2026, 10, 19),
                            baseline_loader=lambda p: base, announcer_loader=lambda d: ["999999"])
    assert len(pro.calls) == 2


def test_delta_capped(monkeypatch):
    ff._cache.clear()
    monkeypatch.setattr(ff, "MAX_DELTA_CODES", 3)
    pro = _Pro()
    ff.fina_period_fallback(pro, "20260630", "", today=date(2026, 10, 19),
                            baseline_loader=lambda p: pd.DataFrame(),
                            announcer_loader=lambda d: [f"00000{i}" for i in range(1, 9)])
    assert len(pro.calls) == 3


def test_session_routes_vip_failure_to_fallback(monkeypatch):
    from app.services import tushare_client as tc
    from app.services.tushare_transport import TushareProviderError

    class _Router:
        def request(self, api_name, params=None, fields=""):
            raise TushareProviderError(api_name, [])

    seen = {}

    def fake(pro, period, fields):
        seen["args"] = (period, fields)
        return pd.DataFrame({"ts_code": ["000001.SZ"]})

    monkeypatch.setattr(ff, "fina_period_fallback", fake)
    sess = tc._TushareSession(_Router(), http_url="")
    out = sess.fina_indicator_vip(period="20260930", fields="ts_code")
    assert seen["args"] == ("20260930", "ts_code") and len(out) == 1


def test_quota_reads_dotenv(monkeypatch):
    from app.core import provider_quota as pq

    monkeypatch.delenv("ASHARE_TUSHARE_QUANTDATA_DAILY_QUOTA", raising=False)
    monkeypatch.delenv("ASHARE_TUSHARE_PROMAX_DAILY_QUOTA", raising=False)  # 本机 .env 已为 promax 设 1000，别让它串进来
    monkeypatch.setattr(pq, "_dotenv_value", lambda k: "920" if k.endswith("QUANTDATA_DAILY_QUOTA") else "")
    assert pq.daily_quota("quantdata") == 920 and pq.daily_quota("promax") is None


def _concept_provider(client):
    from types import SimpleNamespace

    return SimpleNamespace(_client=client)


def _req():
    from types import SimpleNamespace

    return SimpleNamespace(trade_date=date(2026, 10, 19))


def test_ths_index_retry_after_1505(monkeypatch):
    from app.services.auxiliary import state_adapters as sa

    monkeypatch.setattr(sa, "_now_hm", lambda: "17:35")
    monkeypatch.setattr(sa.time, "sleep", lambda s: None)
    calls = []

    class _C:
        def ths_index(self):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("upstream_pool_exhausted")
            return pd.DataFrame([{"ts_code": "885001.TI", "name": "x", "count": 3}])

    rows = sa.TushareStateProvider.concept_sector(_concept_provider(_C()), _req())
    assert len(calls) == 2 and rows[0]["sector_code"] == "885001.TI"


def test_ths_index_alerts_and_raises(monkeypatch):
    import pytest

    from app.services.auxiliary import state_adapters as sa

    alerts = []
    monkeypatch.setattr(sa, "emit_alert", lambda *a, **k: alerts.append(a))
    monkeypatch.setattr(sa.time, "sleep", lambda s: None)

    class _C:
        def ths_index(self):
            raise RuntimeError("down")

    for hm, n in (("10:00", 1), ("15:06", 2)):
        monkeypatch.setattr(sa, "_now_hm", lambda hm=hm: hm)
        with pytest.raises(RuntimeError):
            sa.TushareStateProvider.concept_sector(_concept_provider(_C()), _req())
    assert [a[0] for a in alerts] == ["concept_sector_failed", "concept_sector_failed"]


def test_snapshot_announcers_filter_periodic_titles(monkeypatch):
    """公告快照是新定期报告的主源（MySQL news_announcement 10-16 后停更，财报季只识别出 1 只）。"""
    from types import SimpleNamespace

    from app.services.auxiliary import event_snapshot, snapshot_codec

    days = {}

    class _Reader:
        def __init__(self, contract, repository):
            pass

        def load(self, request):
            day = request.parameter_map["ann_date"]
            days.setdefault(day, 0)
            days[day] += 1
            rows = {
                "20261020": [{"ts_code": "000408.SZ", "title": "2026年第三季度报告"},
                             {"ts_code": "600000.SH", "title": "2026年第三季度报告摘要"},
                             {"ts_code": "300750.SZ", "title": "关于召开股东大会的通知"}],
                "20261018": [{"ts_code": "688001.SH", "title": "2026年半年度报告"}],
            }.get(day)
            return None if rows is None else SimpleNamespace(rows=[SimpleNamespace(lossless_payload=r) for r in rows])

    monkeypatch.setattr(event_snapshot, "ActiveEventSnapshotReader", _Reader)
    monkeypatch.setattr(snapshot_codec, "decode_canonical_payload", lambda payload: payload)

    codes = ff._snapshot_periodic_announcers(object(), date(2026, 10, 20), 4)

    assert codes == {"000408", "688001"}
    assert sorted(days) == ["20261016", "20261017", "20261018", "20261019", "20261020"]
