import pytest

from app.services.datasource import eastmoney as em
from app.services.datasource import gateway as gw
from app.services.datasource import wrap


@pytest.fixture(autouse=True)
def _iso(monkeypatch):
    wrap._breakers.clear()
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: set())


def test_parsers(monkeypatch):
    def fake(url, params, referer=""):
        if "getTopicZTPool" in url:
            return {"data": {"pool": [{"c": "000573", "n": "粤宏远Ａ", "zdp": 10.0985, "amount": 35396531, "hs": 1.25,
                                       "lbc": 1, "fbt": 92500, "lbt": 93001, "fund": 158637171, "zbc": 0, "hybk": "房地产开",
                                       "zttj": {"days": 3, "ct": 2}}]}}
        if "getTopic" in url:
            return {"data": {"pool": []}}
        if "fflow" in url:
            return {"data": {"klines": ["2026-10-15,1e8,0,0,0,0,1.5", "2026-10-16,-2.6e7,8.9e7,-6.3e7,-4.4e7,1.8e7,-2.06"]}}
        return {"data": {"diff": [{"f12": "BK0480", "f14": "航天航空", "f104": 54, "f105": 4, "f106": 2}]}}

    monkeypatch.setattr(em, "_get", fake)
    lu = em.fetch_limit_up_board("20261016")
    assert lu == [{"code": "000573", "name": "粤宏远Ａ", "trade_date": "2026-10-16", "pct_chg": 10.0985, "limit_times": 1,
                   "limit_type": "U", "first_time": "09:25:00", "last_time": "09:30:01", "open_times": 0, "up_stat": "3/2",
                   "limit_amount_yi": 0.35396531, "turnover_ratio": 1.25, "fd_amount": 1.58637171, "industry": "房地产开"}]
    assert em.fetch_concept_sectors()[0]["member_count"] == 60
    assert em.fetch_capital_flow("000001", "20261015")["main_net_inflow_yi"] == 1.0
    assert em.fetch_capital_flow("000001")["small_net_inflow_yi"] == 0.89


def test_dispatch_gated_by_flag(monkeypatch):
    from app.services import market_auxiliary as ma

    monkeypatch.setattr(em, "fetch_dragon_tiger", lambda td: [{"code": "1"}])
    monkeypatch.setattr(gw, "is_cutover", lambda d: False)
    assert ma._fetch_dragon_tiger("eastmoney", None, "2026-10-16") == []
    monkeypatch.setattr(gw, "is_cutover", lambda d: d in ("aux.eastmoney",))
    assert ma._fetch_dragon_tiger("eastmoney", None, "2026-10-16") == [{"code": "1"}]


def test_block_opens_breaker(monkeypatch):
    monkeypatch.setattr(gw, "is_cutover", lambda d: True)

    def blocked(td):
        raise em.EastmoneyBlockedError("567")

    monkeypatch.setattr(em, "fetch_dragon_tiger", blocked)
    with pytest.raises(em.EastmoneyBlockedError):
        em.dragon_tiger("20261016")
    with pytest.raises(gw.DataSourceError):
        em.dragon_tiger("20261016")


def test_shadow_compares_keysets(monkeypatch):
    from app.services import market_auxiliary as ma

    seen = []
    monkeypatch.setattr(gw, "is_shadow", lambda d: d == "aux.eastmoney")
    monkeypatch.setattr(gw, "shadow_compare", lambda d, a, f: seen.append((d, a, f())))
    monkeypatch.setattr(ma, "_fetch_tushare_dragon_tiger", lambda s, trade_date=None: [{"code": "000001"}])
    monkeypatch.setattr(em, "fetch_dragon_tiger", lambda td: [{"code": "000001"}, {"code": "000002"}])
    assert ma._fetch_dragon_tiger("tushare", None, "2026-10-16") == [{"code": "000001"}]
    assert seen == [("aux.eastmoney.dragon_tiger", [("000001", "")], [("000001", ""), ("000002", "")])]
