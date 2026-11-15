"""同花顺 thsdk 三条 P0 接入点（2026-11-15）：实时行情冗余 / 板块广度 / 市场级快讯。

全部用 monkeypatch 打桩 ``ths_client.call``，不联网、不依赖游客账户。
"""
from __future__ import annotations

import pytest

from app.services.datasource import sector_breadth, ths_client
from app.services.datasource.gateway import DataSourceError, ErrorKind


# ── 数值清洗：哨兵必须精确匹配 ─────────────────────────────────────────

def test_clean_number_keeps_real_magnitudes():
    # 实测半导体板块总金额 2.3e11、流通市值 6.4e12，都远大于哨兵 4294967295，不能被误杀
    assert ths_client.clean_number(231_757_200_000) == 231_757_200_000
    assert ths_client.clean_number(6_419_361_300_000) == 6_419_361_300_000
    assert ths_client.clean_number(ths_client.SENTINEL) is None
    assert ths_client.clean_number("--") is None
    assert ths_client.clean_number(None) is None


def test_venue_for():
    assert ths_client.venue_for("600519") == "USHA"
    assert ths_client.venue_for("000001") == "USZA"
    assert ths_client.venue_for("300033") == "USZA"
    assert ths_client.venue_for("830799") == "USTM"
    assert ths_client.venue_for("ST600519") == "USHA"   # 数字仍在
    assert ths_client.venue_for("abcdef") == ""


# ── 实时行情冗余 ──────────────────────────────────────────────────────

def _cn_rows(*codes):
    base = {"成交方向": 21, "成交量": 4_177_984, "交易笔数": 30_351, "总金额": 5_954_834_100,
            "涨速": 0.052, "当前量": 100, "昨收价": 1410.27, "开盘价": 1404.0,
            "最高价": 1437.69, "最低价": 1394.0, "名称": "测试股"}
    venue = {"6": "USHA", "0": "USZA", "3": "USZA", "8": "USTM"}
    out = []
    for code in codes:
        row = dict(base)
        row.update({"代码": f"{venue[code[0]]}{code}", "价格": 1435.26})
        out.append(row)
    return out


def test_ths_realtime_groups_by_venue_and_maps_fields(monkeypatch):
    from app.services.datasource import quote_realtime as qr

    calls = []

    def fake_call(api, *args, **kw):
        calls.append((api, args))
        return _cn_rows(*[c[-6:] for c in args[0]])

    monkeypatch.setattr(ths_client, "call", fake_call)
    monkeypatch.setattr(ths_client, "is_enabled", lambda: True)
    rows = qr.ThsRealtimeProvider().fetch("quote.realtime", {"keys": ["600519", "000001", "300033"]})

    assert len(calls) == 2  # 沪 / 深各一次，绝不把两个市场塞进一次调用
    assert {a[1][0][0] for a in calls} == {"USHA600519", "USZA000001"}
    assert set(rows) == {"600519", "000001", "300033"}
    row = rows["600519"]
    assert row["ts_code"] == "600519.SH" and row["price"] == 1435.26
    assert row["pre_close"] == 1410.27 and row["amount"] == 5_954_834_100
    assert row["source"] == "ths_realtime" and row["trade_date"]

    from app.services.datasource.gateway import validate_rows

    validate_rows("quote.realtime", rows.values(), "ths_realtime")


def test_ths_realtime_raises_on_empty(monkeypatch):
    from app.services.datasource import quote_realtime as qr

    monkeypatch.setattr(ths_client, "call", lambda *a, **kw: [])
    with pytest.raises(DataSourceError) as exc:
        qr.ThsRealtimeProvider().fetch("quote.realtime", {"keys": ["600519"]})
    assert exc.value.kind == ErrorKind.BAD_DATA


def test_chain_position_and_disable_switch(monkeypatch):
    from app.services.datasource import quote_realtime as qr

    monkeypatch.setattr(ths_client, "is_enabled", lambda: True)
    qr._GATEWAY = None
    names = [p.name for p in qr.gateway()._providers]
    assert names[:3] == ["tushare_rt_k", "ths_realtime", "sina_realtime"]

    monkeypatch.setattr(ths_client, "is_enabled", lambda: False)  # ASHARE_THS_ENABLED=false
    qr._GATEWAY = None
    assert "ths_realtime" not in [p.name for p in qr.gateway()._providers]
    qr._GATEWAY = None


# ── 板块广度 ─────────────────────────────────────────────────────────

_BLOCK_RAW = [{"成交量": 3_146_678_300, "总金额": 231_757_200_000, "领涨股": "688720",
               "涨停家数": 0, "跌停家数": 0, "上涨家数": 14, "下跌家数": 175,
               "代码": "URFI881121", "名称": "半导体",
               "板块流通市值": 6_419_361_300_000, "板块总市值": 11_542_849_500_000}]


def test_block_breadth_maps_breadth_fields(monkeypatch):
    monkeypatch.setattr(ths_client, "call", lambda api, *a, **kw: _BLOCK_RAW if api == "market_data_block" else [])
    rows = sector_breadth.ThsBlockBreadthProvider().fetch(
        sector_breadth.DATASET, {"block_codes": ["URFI881121"]}
    )
    row = rows["URFI881121"]
    assert (row["up_count"], row["down_count"]) == (14, 175)
    assert (row["limit_up_count"], row["limit_down_count"]) == (0, 0)
    assert row["leading_code"] == "688720"
    assert row["amount"] == 231_757_200_000 and row["float_mv"] == 6_419_361_300_000
    assert row["source"] == "ths_block" and row["trade_date"]


def test_block_breadth_index_fallback_and_sentinel(monkeypatch):
    index = [{"代码": f"URFI88{i:04d}", "名称": f"行业{i}"} for i in range(10)]
    bad = [{"代码": "URFI880000", "名称": "行业0", "上涨家数": ths_client.SENTINEL, "涨停家数": 3}]

    def fake_call(api, *a, **kw):
        if api == "ths_industry":
            return index
        return bad

    sector_breadth._INDEX_CACHE.clear()
    monkeypatch.setattr(ths_client, "call", fake_call)
    rows = sector_breadth.ThsBlockBreadthProvider().fetch(
        sector_breadth.DATASET, {"kind": "industry", "max_blocks": 2}
    )
    assert len(rows) == 2
    first = rows["URFI880000"]
    assert first["up_count"] is None  # 哨兵归一为 None
    assert first["limit_up_count"] == 3
    assert first["block_name"] == "行业0"  # 名称从目录补全


def test_block_breadth_registered_in_topology():
    from app.services.datasource.topology import LABELS, _gateway_datasets, gateway_config_keys

    assert sector_breadth.DATASET in [d["key"] for d in _gateway_datasets()]
    assert LABELS["ths_block"] == "同花顺板块广度"
    assert ("ths", "concept_sector") in gateway_config_keys()


# ── 市场级快讯 ────────────────────────────────────────────────────────

def test_fetch_ths_news_shape(monkeypatch):
    from app.core.config import get_settings
    from app.services.market_auxiliary import _fetch_ths_news

    raw = [{"Op": 1, "Time": 1_794_712_096, "Title": "某公司收到处罚决定", "ID": 681828943,
            "Code": "17:600519", "Stock": "600519",
            "Properties": "ctime=1794711886\nsumm=摘要内容\nsource=同花顺7x24快讯"}]
    monkeypatch.setattr(ths_client, "call", lambda *a, **kw: raw)
    monkeypatch.setattr(ths_client, "is_enabled", lambda: True)
    items = _fetch_ths_news("600519", get_settings())
    assert len(items) == 1
    it = items[0]
    assert it["source"] == "ths" and it["article_id"] == "ths-681828943"
    assert it["title"] == "某公司收到处罚决定" and it["summary"] == "摘要内容"
    assert it["announce_date"] == "2026-11-15"
    assert it["sentiment"] == "negative" and it["is_risk_warning"] is True


def test_ths_news_not_in_default_chain():
    from app.core.config import get_settings

    # 实测 news(code=) 不过滤个股，默认并入会污染 news_risk_count，故只保留可显式开启的能力
    assert "ths" not in [s.strip() for s in get_settings().news_sources.split(",")]
