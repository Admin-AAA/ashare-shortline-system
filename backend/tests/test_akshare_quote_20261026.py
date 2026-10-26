"""AKShare 全市场实时快照（quote.realtime 第二备份）：新鲜度/合法性筛选、缓存与限速、冷却、单飞、链路位置、开关。"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.services.datasource import akshare_quote as aq
from app.services.datasource.gateway import DataSourceError, ErrorKind

TZ = ZoneInfo("Asia/Shanghai")
MON = lambda h, m, s=0: datetime(2026, 10, 26, h, m, s, tzinfo=TZ)  # noqa: E731  周一（交易日）
SAT = datetime(2026, 10, 24, 11, 0, 0, tzinfo=TZ)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    aq.reset_for_tests()
    monkeypatch.setattr(aq, "_is_trading_day", lambda d: d.weekday() < 5)
    monkeypatch.setattr(aq, "_setting", lambda name, default: {"quote_akshare_enabled": True, "quote_akshare_func": "sina"}.get(name, default))
    yield
    aq.reset_for_tests()


def raw_rows(n=3200, ts="10:30:00", **over):
    rows = []
    for i in range(n):
        code = f"{600000 + i:06d}"
        r = {"prefix": "sh", "code": code, "name": f"N{i}", "price": 10.0, "open": 9.9, "pre_close": 9.8, "high": 10.2, "low": 9.7,
             "volume": 100000.0, "amount": 1000000.0, "time": ts}
        r.update(over)
        rows.append(r)
    return rows


def test_live_fresh_rows_pass_and_are_tagged():
    snap = aq.build_snapshot(raw_rows(ts="10:29:50"), now=MON(10, 30), func="sina")
    row = snap["rows"]["600001"]
    assert row["source"] == "akshare_sina_snapshot" and row["trade_date"] == "2026-10-26" and row["quote_time"] == "10:29:50"
    assert (row["price"], row["pre_close"], row["volume"], row["amount"]) == (10.0, 9.8, 100000.0, 1000000.0)
    assert row["ts_code"] == "600001.SH"


def test_live_frozen_snapshot_is_rejected_whole():
    with pytest.raises(DataSourceError) as ei:
        aq.build_snapshot(raw_rows(ts="09:31:00"), now=MON(10, 30), func="sina")  # 全部落后 59 分钟
    assert ei.value.kind is ErrorKind.BAD_DATA


def test_live_single_stale_rows_are_dropped_not_whole_snapshot():
    rows = raw_rows(ts="10:29:58")
    rows[0]["time"] = "09:25:00"  # 一只没有新 tick
    snap = aq.build_snapshot(rows, now=MON(10, 30), func="sina")
    assert "600000" not in snap["rows"] and len(snap["rows"]) == 3199 and snap["dropped"]["stale"] == 1


def test_suspended_and_bad_price_rows_never_enter():
    rows = raw_rows(ts="10:29:58")
    rows[0].update(price=0.0)                                  # 价格 0
    rows[1].update(volume=0.0, open=0.0)                       # 停牌：无成交无开盘
    rows[2].update(price=11.5)                                 # 价格超出 [low, high]
    rows[3].update(pre_close=0.0)                              # 无昨收
    snap = aq.build_snapshot(rows, now=MON(10, 30), func="sina")
    assert not ({"600000", "600001", "600002", "600003"} & set(snap["rows"]))
    assert snap["dropped"]["suspended"] == 3 and snap["dropped"]["bad_price"] == 1


def test_after_close_requires_close_timestamp_and_lunch_requires_1130():
    assert len(aq.build_snapshot(raw_rows(ts="15:30:00"), now=MON(20, 50), func="sina")["rows"]) == 3200
    with pytest.raises(DataSourceError):
        aq.build_snapshot(raw_rows(ts="14:30:00"), now=MON(20, 50), func="sina")  # 收盘后但时间戳停在盘中 -> 全丢
    assert len(aq.build_snapshot(raw_rows(ts="11:30:03"), now=MON(12, 0), func="sina")["rows"]) == 3200
    with pytest.raises(DataSourceError):
        aq.build_snapshot(raw_rows(ts="10:00:00"), now=MON(12, 0), func="sina")


def test_weekend_and_preopen_label_previous_trading_day():
    assert aq.build_snapshot(raw_rows(ts="15:30:00"), now=SAT, func="sina")["rows"]["600000"]["trade_date"] == "2026-10-23"
    pre = aq.build_snapshot(raw_rows(ts="15:00:00"), now=MON(9, 10), func="sina")
    assert pre["rows"]["600000"]["trade_date"] == "2026-10-23"


def test_too_few_rows_is_bad_data():
    with pytest.raises(DataSourceError) as ei:
        aq.build_snapshot(raw_rows(n=100), now=MON(10, 30), func="sina")
    assert ei.value.kind is ErrorKind.BAD_DATA


def test_sina_and_em_unit_mapping():
    sina = pd.DataFrame([{"代码": "bj920000", "名称": "安徽凤凰", "最新价": 13.26, "昨收": 13.38, "今开": 13.36, "最高": 13.68, "最低": 13.2,
                          "成交量": 357854.0, "成交额": 4818655.0, "时间戳": "15:30:00"}])
    r = aq._raw_sina(sina)[0]
    assert (r["prefix"], r["code"], r["volume"], r["amount"]) == ("bj", "920000", 357854.0, 4818655.0)  # 股 / 元
    em = pd.DataFrame([{"代码": "600000", "名称": "浦发", "最新价": 8.0, "昨收": 7.9, "今开": 7.95, "最高": 8.1, "最低": 7.9, "成交量": 1234.0, "成交额": 9.0e6}])
    r2 = aq._raw_em(em)[0]
    assert r2["volume"] == 123400.0 and r2["prefix"] == "sh"  # 手 ×100 = 股
    assert aq._ts_code("920000", "bj") == "920000.BJ"          # 北交所 92 开头不能落到 .SH


# ── provider ────────────────────────────────────────────────────────────
class Puller:
    def __init__(self, delay=0.0, fail=None, ts=None):
        self.calls, self.delay, self.fail, self.ts = 0, delay, fail, ts

    def __call__(self, func):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise self.fail
        now = aq._now()
        ts = self.ts or now.strftime("%H:%M:%S")
        rows = raw_rows(ts=ts)
        return pd.DataFrame([{"代码": f"sh{r['code']}", "名称": r["name"], "最新价": r["price"], "昨收": r["pre_close"], "今开": r["open"],
                              "最高": r["high"], "最低": r["low"], "成交量": r["volume"], "成交额": r["amount"], "时间戳": r["time"]} for r in rows])


@pytest.fixture
def after_close(monkeypatch):
    monkeypatch.setattr(aq, "_now", lambda: MON(20, 50))
    return Puller(ts="15:30:00")


def test_provider_serves_requested_codes_with_age_and_caches(after_close, monkeypatch):
    monkeypatch.setattr(aq, "WAIT_SECONDS", 5.0)
    p = aq.AkshareSnapshotProvider(after_close)
    out = p.fetch("quote.realtime", {"keys": ["600001", "600002", "999999"]})
    assert set(out) == {"600001", "600002"} and out["600001"]["source"] == "akshare_sina_snapshot" and "snapshot_age_s" in out["600001"]
    p.fetch("quote.realtime", {"keys": ["600003"]})
    assert after_close.calls == 1  # TTL 内复用整份快照


def test_min_interval_and_failure_cooldown_protect_the_ip(after_close, monkeypatch):
    monkeypatch.setattr(aq, "WAIT_SECONDS", 5.0)
    monkeypatch.setattr(aq, "CACHE_TTL", 0.0)
    p = aq.AkshareSnapshotProvider(after_close)
    p.fetch("quote.realtime", {"keys": ["600001"]})
    p.fetch("quote.realtime", {"keys": ["600001"]})  # 过了 TTL 但没到 MIN_INTERVAL：用 MAX_AGE 内的旧快照，不再翻页
    assert after_close.calls == 1
    # 失败 → 冷却：没有可用旧快照时 DATA_PENDING，且不会再翻页
    aq.reset_for_tests()
    bad = Puller(fail=ConnectionError("RemoteDisconnected"))
    p2 = aq.AkshareSnapshotProvider(bad)
    with pytest.raises(DataSourceError):
        p2.fetch("quote.realtime", {"keys": ["600001"]})
    with pytest.raises(DataSourceError) as ei:
        p2.fetch("quote.realtime", {"keys": ["600001"]})
    assert ei.value.kind is ErrorKind.DATA_PENDING and bad.calls == 1
    assert aq.status()["fail_streak"] == 1 and aq.status()["cooling_s"] > 0


def test_slow_paging_returns_pending_then_next_call_uses_result(after_close, monkeypatch):
    monkeypatch.setattr(aq, "WAIT_SECONDS", 0.05)
    slow = Puller(delay=0.4, ts="15:30:00")
    p = aq.AkshareSnapshotProvider(slow)
    with pytest.raises(DataSourceError) as ei:
        p.fetch("quote.realtime", {"keys": ["600001"]})
    assert ei.value.kind is ErrorKind.DATA_PENDING  # 不计熔断，由下一级顶上
    time.sleep(0.6)
    assert "600001" in p.fetch("quote.realtime", {"keys": ["600001"]}) and slow.calls == 1


def test_single_flight_under_concurrency(after_close, monkeypatch):
    monkeypatch.setattr(aq, "WAIT_SECONDS", 5.0)
    slow = Puller(delay=0.3, ts="15:30:00")
    p = aq.AkshareSnapshotProvider(slow)
    res = []
    ts = [threading.Thread(target=lambda: res.append(p.fetch("quote.realtime", {"keys": ["600001"]}))) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert slow.calls == 1 and all("600001" in r for r in res)


def test_old_snapshot_beyond_max_age_is_discarded(after_close, monkeypatch):
    monkeypatch.setattr(aq, "WAIT_SECONDS", 0.05)
    p = aq.AkshareSnapshotProvider(after_close)
    monkeypatch.setattr(aq, "WAIT_SECONDS", 5.0)
    p.fetch("quote.realtime", {"keys": ["600001"]})
    aq._STATE["snap"]["built_at"] -= aq.MAX_AGE + 1
    aq._STATE["last_start"] = time.monotonic()  # 刚拉过：不会立刻再翻页
    with pytest.raises(DataSourceError) as ei:
        p.fetch("quote.realtime", {"keys": ["600001"]})
    assert ei.value.kind is ErrorKind.DATA_PENDING  # 过期快照不给用


def test_disable_switch(monkeypatch):
    p = aq.AkshareSnapshotProvider(Puller())
    assert p.supports("quote.realtime", {}) and not p.supports("bar.minute", {})
    monkeypatch.setattr(aq, "_setting", lambda name, default: False if name == "quote_akshare_enabled" else default)
    assert not p.supports("quote.realtime", {})


def test_chain_position_is_after_tencent_before_rt_min():
    from app.services.datasource import quote_realtime as qr

    qr._GATEWAY = None
    names = [p.name for p in qr.gateway()._providers]
    assert names == ["tushare_rt_k", "sina_realtime", "tencent_qt", "akshare_snapshot", "tushare_rt_min", "xiaoshi_snapshot"]
    qr._GATEWAY = None
