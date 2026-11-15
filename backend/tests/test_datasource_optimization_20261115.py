"""数据源架构三项优化（2026-11-15）：适配模板化 / 链路自适应 / 分钟线按需缓存。

全部用打桩，不联网、不写生产缓存目录。
"""
from __future__ import annotations

import time

import pytest

from app.services.datasource import adapter, minute_cache
from app.services.datasource import gateway as gw


# ── ① 适配模板：归一化逻辑收敛到 adapter ──────────────────────────────────

def test_to_float_matches_legacy_semantics():
    assert adapter.to_float(None) == 0.0
    assert adapter.to_float(float("nan")) == 0.0
    assert adapter.to_float("--") == 0.0
    assert adapter.to_float("12.5") == 12.5
    assert adapter.to_float(0) == 0.0           # 0 不能被当成"缺失"
    assert adapter.to_float("", default=1.0) == 1.0


def test_legacy_f_helpers_now_delegate_to_adapter():
    # 8 份拷贝改成转调后，行为必须与原来逐字节一致
    from app.services.datasource import eastmoney, qvix, sina_realtime, tdx_quote, web_backups

    for mod in (eastmoney, qvix, sina_realtime, tdx_quote, web_backups):
        assert mod._f(None) == 0.0
        assert mod._f(float("nan")) == 0.0
        assert mod._f("3.25") == 3.25


def test_code6_normalizes_suffix_and_venue():
    assert adapter.code6("600000.SH") == "600000"
    assert adapter.code6("sh600000") == "600000"
    assert adapter.code6("000001") == "000001"
    assert adapter.code6("") == ""


def test_quote_row_fills_defaults_and_completes_ts_code():
    row = adapter.quote_row(code="600000.SH", source="demo", price="9.9", volume=None)
    assert row["code"] == "600000"
    assert row["ts_code"] == "600000.SH"      # 未传时自动补
    assert row["price"] == 9.9 and row["volume"] == 0.0
    assert row["name"] == "" and row["source"] == "demo"
    assert set(adapter.QUOTE_FIELDS) == set(row)  # 字段不多不少，正好对齐 SCHEMAS


def test_quote_row_from_raw_maps_chinese_aliases():
    row = adapter.quote_row_from_raw(
        {"代码": "USHA600519", "名称": "贵州茅台", "价格": 1435.26, "昨收价": 1410.27, "总金额": 5_954_834_100},
        source="ths_realtime",
    )
    assert row["code"] == "600519"
    assert row["name"] == "贵州茅台"
    assert row["price"] == 1435.26 and row["pre_close"] == 1410.27
    assert row["amount"] == 5_954_834_100


def test_make_provider_needs_only_fetch_raw():
    seen: list[dict] = []

    def fetch_raw(query):
        seen.append(dict(query))
        return {"600000": {"code": "600000"}}

    cls = adapter.make_provider(name="demo_quote", dataset="quote.realtime", fetch_raw=fetch_raw, limiter_rpm=None)
    provider = cls()
    assert provider.supports("quote.realtime", {"keys": ["600000"]})
    assert not provider.supports("bar.minute", {})
    assert provider.fetch("quote.realtime", {"keys": ["600000"]}) == {"600000": {"code": "600000"}}
    assert seen == [{"keys": ["600000"]}]


def test_provider_base_respects_accept_and_skips_limiter():
    class _IndexOnly(adapter.ProviderBase):
        name = "index_only"
        dataset = "bar.minute"

        def accept(self, query):
            return bool(query.get("is_index"))

        def _fetch(self, dataset, query):  # noqa: ARG002
            return []

    provider = _IndexOnly()
    assert provider.limiter() is None                    # 未配 limiter_rpm 就不建桶
    assert provider.supports("bar.minute", {"is_index": True})
    assert not provider.supports("bar.minute", {})
    assert not provider.supports("quote.realtime", {"is_index": True})


def test_build_chain_drops_none_and_retired():
    class _P:
        def __init__(self, name):
            self.name = name

    chain = adapter.build_chain([_P("tushare_rt_k"), None, _P("xiaoshi_snapshot"), _P("sina_realtime")])
    assert [p.name for p in chain] == ["tushare_rt_k", "sina_realtime"]


# ── ② 链路自适应：失败后置 + 单跳预算 ────────────────────────────────────

class _P:
    def __init__(self, name, rows=None, exc=None, delay=0.0):
        self.name, self._rows, self._exc, self._delay = name, rows, exc, delay
        self.config_keys: tuple[tuple[str, str], ...] = ()
        self.calls = 0

    def supports(self, dataset, query):
        return True

    def fetch(self, dataset, query):
        self.calls += 1
        if self._delay:
            time.sleep(self._delay)
        if self._exc:
            raise self._exc
        return self._rows


def _all_on(p):
    return True


@pytest.fixture(autouse=True)
def _clean_health():
    gw._HEALTH.reset()
    yield
    gw._HEALTH.reset()


def test_failed_provider_is_moved_to_the_back():
    a, b, c = _P("a"), _P("b"), _P("c")
    gate = gw.DatasetGateway([a, b, c], enabled=_all_on)
    assert [p.name for p in gate._candidates("quote.realtime", {})] == ["a", "b", "c"]

    gw._HEALTH.record_failure("a", "quote.realtime")
    assert [p.name for p in gate._candidates("quote.realtime", {})] == ["b", "c", "a"]

    gw._HEALTH.record_success("a", "quote.realtime", 0.2)   # 恢复后立刻回到原位
    assert [p.name for p in gate._candidates("quote.realtime", {})] == ["a", "b", "c"]


def test_health_snapshot_expires_so_one_outage_does_not_stick(monkeypatch):
    gw._HEALTH.record_failure("a", "quote.realtime")
    assert gw._HEALTH.snapshot("a", "quote.realtime")[0] == 1
    monkeypatch.setattr(gw._HEALTH, "ttl", lambda: 0.0)
    assert gw._HEALTH.snapshot("a", "quote.realtime") == (0, 0.0)


def test_adaptive_routing_can_be_switched_off(monkeypatch):
    original = gw._setting
    monkeypatch.setattr(
        gw, "_setting",
        lambda attr, default=0: 0 if attr == "datasource_adaptive_routing" else original(attr, default),
    )
    a, b = _P("a"), _P("b")
    gate = gw.DatasetGateway([a, b], enabled=_all_on)
    gw._HEALTH.record_failure("a", "quote.realtime")
    assert [p.name for p in gate._candidates("quote.realtime", {})] == ["a", "b"]


def test_step_timeout_caps_first_and_backup_hops():
    budget = gw._Budget("quote.realtime", 3.0)
    first = budget.step_timeout(0, 8)
    assert 1.5 <= first <= 2.0            # 60% of 3s，够 rt_k 的 1.5s 硬截止
    caps = [budget.step_timeout(i, 8) for i in range(1, 8)]
    assert min(caps) >= 0.8               # 后备不低于下限
    assert min(caps) < 3.0                # 后备确实被限住

    single = gw._Budget("quote.realtime", 3.0)
    assert single.step_timeout(0, 1) == pytest.approx(3.0, abs=0.2)   # 只有一个候选就没有意义限它


def _quote(code, price):
    return adapter.quote_row(code=code, source="stub", trade_date="2026-11-14", price=price)


def test_slow_chain_head_still_leaves_budget_for_backup():
    """原先的痛点：链首慢源吃掉整个 3s，快源一个都轮不上。"""
    slow = _P("slow", rows={"600000": _quote("600000", 1.0)}, delay=5.0)
    fast = _P("fast", rows={"600000": _quote("600000", 2.0)})
    gate = gw.DatasetGateway([slow, fast], enabled=_all_on)

    result = gate.fetch_fill("quote.realtime", {}, ["600000"], deadline_s=3.0)

    assert result.rows["600000"]["price"] == 2.0      # 快源补上了
    assert result.provider == "fast"
    outcomes = {a["provider"]: a["outcome"] for a in result.attempts}
    assert outcomes["slow"] == "timeout"


# ── ③ 分钟线：按需补缺口，不囤全市场 ─────────────────────────────────────

@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """真实缓存行为：临时目录 + 显式打开（conftest 默认把它关掉以免污染）。"""
    monkeypatch.setattr(minute_cache, "cache_root", lambda: tmp_path / "minute_cache")
    monkeypatch.setattr(minute_cache, "enabled", lambda: True)
    return tmp_path / "minute_cache"


def _rows(code, day, prices):
    return [
        {"code": code, "trade_time": f"{day} 09:{31 + i:02d}:00", "close": p, "source": "stub"}
        for i, p in enumerate(prices)
    ]


def test_month_windows_splits_and_clips():
    windows = minute_cache.month_windows("2024-01-15", "2024-03-02")
    assert [w[0] for w in windows] == ["2024-01", "2024-02", "2024-03"]
    assert windows[0][1:] == ("2024-01-15", "2024-01-31")     # 首月裁剪到起点
    assert windows[2][1:] == ("2024-03-01", "2024-03-02")     # 尾月裁剪到终点
    assert minute_cache.month_windows("2024-03-02", "2024-01-15") == []


def test_ensure_writes_cache_then_reads_from_disk(cache_dir, monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_fetch(code, start, end, freq, trace=None):  # noqa: ARG001
        calls.append((start, end))
        return _rows(code, start, [1.0, 1.1]), None

    monkeypatch.setattr(minute_cache, "_fetch_window", fake_fetch)

    first = minute_cache.ensure("600000", "2024-01-01", "2024-02-29")
    assert len(first["rows"]) == 4
    assert first["months_fetched"] == ["2024-01", "2024-02"] and first["months_cached"] == []
    assert len(calls) == 2

    calls.clear()
    second = minute_cache.ensure("600000", "2024-01-01", "2024-02-29")   # 第二次全命中
    assert calls == []
    assert second["months_cached"] == ["2024-01", "2024-02"]
    assert [r["close"] for r in second["rows"]] == [1.0, 1.1, 1.0, 1.1]


def test_ensure_refresh_forces_refetch(cache_dir, monkeypatch):
    calls = []
    def fake_fetch(code, start, end, freq, trace=None):  # noqa: ARG001
        calls.append(start)
        return _rows(code, start, [1.0]), None

    monkeypatch.setattr(minute_cache, "_fetch_window", fake_fetch)
    minute_cache.ensure("600000", "2024-01-01", "2024-01-31")
    minute_cache.ensure("600000", "2024-01-01", "2024-01-31", refresh=True)
    assert len(calls) == 2


def test_empty_window_is_bisected_instead_of_given_up(cache_dir, monkeypatch):
    """上游对长窗口返回空（满 8000 行整单放弃 / 偶发限流）时，二分重拉要能救回数据。"""
    asked: list[tuple[str, str]] = []

    def fake_fetch(code, start, end, freq, trace=None):  # noqa: ARG001
        asked.append((start, end))
        span = (minute_cache._parse(end) - minute_cache._parse(start)).days
        if span <= 10:                        # 上游只对短窗口给得回来
            return _rows(code, start, [9.9]), None
        return [], None

    monkeypatch.setattr(minute_cache, "_fetch_window", fake_fetch)
    result = minute_cache.ensure("600000", "2024-01-01", "2024-01-31")

    assert result["extra_calls"] > 0
    assert result["rows"], "二分后应拿到数据，而不是像原来那样整窗放弃"
    assert min((minute_cache._parse(e) - minute_cache._parse(s)).days for s, e in asked) <= 10


def test_bisect_stops_at_floor_and_reports_empty(cache_dir, monkeypatch):
    calls = []
    def fake_fetch(code, start, end, freq, trace=None):  # noqa: ARG001
        calls.append(start)
        return [], None

    monkeypatch.setattr(minute_cache, "_fetch_window", fake_fetch)
    result = minute_cache.ensure("600000", "2024-01-01", "2024-01-31")
    assert result["rows"] == []
    assert result["empty_months"] == ["2024-01"]
    assert len(calls) <= 1 + minute_cache._BISECT_MAX_CALLS   # 有重试上限，不会打爆上游


def test_stats_reports_local_coverage(cache_dir, monkeypatch):
    monkeypatch.setattr(
        minute_cache, "_fetch_window",
        lambda code, start, end, freq, trace=None: (_rows(code, start, [1.0]), None),
    )
    minute_cache.ensure("600000", "2024-01-01", "2024-02-29")
    info = minute_cache.stats("600000")
    assert info["months"] == ["2024-01", "2024-02"] and info["bytes"] > 0
    assert minute_cache.stats("999999")["months"] == []


# ── ③ 接线：bar.minute 默认先走本地缓存 ────────────────────────────────────

def test_bar_minute_fetch_serves_from_cache(cache_dir, monkeypatch):
    from app.services.datasource import bar_minute

    monkeypatch.setattr(
        minute_cache, "_fetch_window",
        lambda code, start, end, freq, trace=None: (_rows(code, start, [1.0, 1.1]), None),
    )
    monkeypatch.setattr(
        bar_minute, "gateway",
        lambda: pytest.fail("缓存命中时不应触碰网关链"),
    )
    res = bar_minute.fetch("600000", "2024-01-01", "2024-02-29", "1min")
    assert res.provider == "minute_cache" and len(res.rows) == 4
    assert res.attempts[0]["months_fetched"] == 2


def test_bar_minute_fetch_bypasses_cache_when_disabled(monkeypatch):
    from app.services.datasource import bar_minute

    class _GW:
        def fetch_first(self, dataset, query):
            return gw.GatewayResult([{"code": "600000"}], "tushare_stk_mins", [{"provider": "tushare_stk_mins", "outcome": "success"}])

    monkeypatch.setattr(minute_cache, "enabled", lambda: False)
    monkeypatch.setattr(bar_minute, "gateway", lambda: _GW())
    res = bar_minute.fetch("600000", "2024-01-01", "2024-01-31", "1min")
    assert res.provider == "tushare_stk_mins"


def test_current_month_is_not_persisted(cache_dir, monkeypatch):
    """当月还会继续增长，写盘后再也不会刷新；必须每次重拉。"""
    from datetime import datetime

    month = datetime.now().strftime("%Y-%m")
    calls = []

    def fake_fetch(code, start, end, freq, trace=None):  # noqa: ARG001
        calls.append(start)
        return _rows(code, start, [1.0]), None

    monkeypatch.setattr(minute_cache, "_fetch_window", fake_fetch)
    meta = minute_cache.ensure("600000", f"{month}-01", f"{month}-02")
    assert meta["months_volatile"] == [month] and meta["months_fetched"] == []
    assert minute_cache.stats("600000")["months"] == []

    minute_cache.ensure("600000", f"{month}-01", f"{month}-02")
    assert len(calls) == 2, "当月不应命中磁盘缓存"


def test_serve_never_raises_on_cache_failure(cache_dir, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(minute_cache, "ensure", boom)
    rows, meta, upstream = minute_cache.serve("600000", "2024-01-01", "2024-01-31")
    assert rows == [] and upstream is None and "disk full" in meta["error"]


def test_current_month_skips_bisect(cache_dir, monkeypatch):
    """当月不落盘，二分救回来也没地方存；盘中每次轮询多打十几次上游只会加速耗尽额度。"""
    from datetime import datetime

    month = datetime.now().strftime("%Y-%m")
    calls = []

    def fake_fetch(code, start, end, freq, trace=None):  # noqa: ARG001
        calls.append(start)
        return [], None

    monkeypatch.setattr(minute_cache, "_fetch_window", fake_fetch)
    meta = minute_cache.ensure("600000", f"{month}-01", f"{month}-28")
    assert calls == [f"{month}-01"] and meta["rows"] == []
    assert meta["extra_calls"] == 0
