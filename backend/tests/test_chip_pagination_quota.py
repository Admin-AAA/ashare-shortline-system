"""cyq_perf 5000 行截断续页 + 补数额度闸（2026-10-19）。"""
from __future__ import annotations

import pandas as pd

from app.core import provider_quota
from app.services.tushare_client import fetch_trade_date_paged


def _frame(start: int, n: int) -> pd.DataFrame:
    return pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(start, start + n)], "v": range(n)})


class _Pro:
    def __init__(self, total: int, honour_offset: bool = True, cap: int = 5000):
        self.total, self.honour, self.cap, self.calls = total, honour_offset, cap, []

    def cyq_perf(self, **kw):
        self.calls.append(kw)
        off = kw.get("offset", 0) if self.honour else 0
        return _frame(off, max(0, min(self.cap, self.total - off)))


def test_under_cap_single_call():
    pro = _Pro(4800)
    df = fetch_trade_date_paged(pro, "cyq_perf", trade_date="20260430", fields="f")
    assert len(df) == 4800 and len(pro.calls) == 1 and "offset" not in pro.calls[0]


def test_provider_without_cap_single_call():
    pro = _Pro(5460, cap=10_000)
    assert len(fetch_trade_date_paged(pro, "cyq_perf", trade_date="20260430")) == 5460
    assert len(pro.calls) == 1


def test_exact_cap_pages_to_full_market():
    pro = _Pro(5460)
    df = fetch_trade_date_paged(pro, "cyq_perf", trade_date="20260430", fields="f")
    assert len(df) == 5460 and df["ts_code"].is_unique
    assert pro.calls[1] == {"trade_date": "20260430", "fields": "f", "offset": 5000, "limit": 5000}


def test_offset_ignored_stops_without_duplicates():
    pro = _Pro(5460, honour_offset=False)
    df = fetch_trade_date_paged(pro, "cyq_perf", trade_date="20260430")
    assert len(df) == 5000 and len(pro.calls) == 2


def test_continuation_error_keeps_first_page():
    class _Strict(_Pro):
        def cyq_perf(self, **kw):
            if "offset" in kw:
                raise RuntimeError("2003: parameter not allowed: limit")
            return super().cyq_perf(**kw)

    pro = _Strict(5460)
    assert len(fetch_trade_date_paged(pro, "cyq_perf", trade_date="20260430")) == 5000


def test_budget_gate(monkeypatch):
    monkeypatch.setattr(provider_quota, "_conn", lambda: None)
    monkeypatch.setattr(provider_quota, "_pending", {})
    monkeypatch.setattr(provider_quota, "_local_exhausted", set())
    monkeypatch.setenv("ASHARE_TUSHARE_QUANTDATA_DAILY_QUOTA", "10")
    monkeypatch.delenv("ASHARE_TUSHARE_PROMAX_DAILY_QUOTA", raising=False)
    monkeypatch.setattr(provider_quota, "FLUSH_EVERY", 10_000)
    monkeypatch.setattr(provider_quota, "FLUSH_SECONDS", 1e9)
    for _ in range(5):
        provider_quota.record_request("quantdata")
    assert provider_quota.backfill_budget_check(["promax", "quantdata"])[0]
    provider_quota.record_request("quantdata")
    ok, reason = provider_quota.backfill_budget_check(["promax", "quantdata"])
    assert not ok and "quantdata" in reason
    provider_quota.mark_exhausted("promax")
    ok, reason = provider_quota.backfill_budget_check(["promax"])
    assert not ok and "耗尽" in reason


def test_daily_limit_failure_detected():
    from app.services.tushare_transport import FailureCategory, ProviderFailure, _is_daily_limit

    f = ProviderFailure(provider="quantdata", api_name="cyq_perf", category=FailureCategory.BUSINESS_RATE_LIMIT,
                        retryable=False, fallback_allowed=True, upstream_detail="42901: token daily limit exceeded")
    assert _is_daily_limit(f)
