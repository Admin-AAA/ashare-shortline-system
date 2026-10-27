"""promax 冷却按实测窗口/上游等待时间 + research 窗口令牌桶 + 筹码补数不因 quantdata 日限停（2026-10-19）。"""
from __future__ import annotations

import importlib.util
import pathlib

from app.core import promax_window as pw
from app.services.tushare_transport import FailureCategory, ProviderFailure, TushareProviderError


class _Resp:
    def __init__(self, headers=None, body=None):
        self.headers = headers or {}
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def test_upstream_wait_header_then_body():
    assert pw.upstream_wait_seconds(_Resp({"Retry-After": "42"})) == 42.0
    assert pw.upstream_wait_seconds(_Resp(body={"ok": False, "error": "upstream_pool_exhausted", "retry_after": 7})) == 7.0
    assert pw.upstream_wait_seconds(_Resp(body={"ok": False, "error": "upstream_pool_exhausted"})) is None


def test_cooldown_prefers_upstream_then_measured_with_backoff():
    pw.reset_streak("cyq_perf")
    assert pw.cooldown_seconds("cyq_perf", 33, now=1000.0) == 33
    pw.reset_streak("cyq_perf")
    assert pw.cooldown_seconds("cyq_perf", None, now=1000.0) == 60
    assert pw.cooldown_seconds("cyq_perf", None, now=1100.0) == 120
    assert pw.cooldown_seconds("cyq_perf", None, now=1200.0) == 300
    assert pw.cooldown_seconds("cyq_perf", None, now=5000.0) == 60  # streak reset after quiet period
    pw.reset_streak("rt_k")
    assert pw.cooldown_seconds("rt_k", None, now=1.0) == 15


def test_research_acquire_yields_to_live_rt_k(monkeypatch):
    import time as _t

    slept = []
    monkeypatch.setattr(pw, "_rt_k_live_open_until", lambda: _t.time() + 30)
    monkeypatch.setattr(pw, "_limiters", {})
    pw.research_acquire("cyq_perf", intraday=True, sleep=slept.append)
    assert slept and 25 < slept[0] <= 30
    slept.clear()
    pw.research_acquire("cyq_perf", intraday=False, sleep=slept.append)
    assert not slept


def test_window_bucket_limits(monkeypatch):
    monkeypatch.setattr(pw, "_limiters", {})
    lim = pw._limiter(300.0, 3, True)
    assert lim.headroom() == 1.0
    for _ in range(3):
        lim.acquire()
    assert lim.headroom() == 0.0


def _script():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "backfill_chip_dates.py"
    spec = importlib.util.spec_from_file_location("backfill_chip_dates", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _f(provider, cat, wait=None):
    return ProviderFailure(provider=provider, api_name="cyq_perf", category=cat, retryable=False,
                           fallback_allowed=True, retry_after_seconds=wait)


def test_chip_backfill_waits_on_promax_limit_instead_of_stopping():
    mod = _script()
    exc = TushareProviderError("cyq_perf", [_f("quantdata", FailureCategory.BUSINESS_RATE_LIMIT),
                                            _f("promax", FailureCategory.HTTP_RATE_LIMIT, 45)])
    assert mod.transient_wait(exc) == 45
    exc2 = TushareProviderError("cyq_perf", [_f("quantdata", FailureCategory.BUSINESS_RATE_LIMIT),
                                             _f("promax", FailureCategory.CIRCUIT_OPEN)])
    assert mod.transient_wait(exc2) == pw.DEFAULT_COOLDOWN_SECONDS
    hard = TushareProviderError("cyq_perf", [_f("promax", FailureCategory.PERMISSION)])
    assert mod.transient_wait(hard) is None


def test_chip_backfill_continues_when_only_quantdata_exhausted(monkeypatch):
    mod = _script()
    monkeypatch.setattr(mod, "backfill_budget_check",
                        lambda ps, f: (False, "quantdata 今日额度已耗尽") if ps == ("quantdata",) else (True, "ok"))
    ok, blocked = mod.usable_providers(0.6)
    assert "promax" in ok and "quantdata" not in ok and blocked
