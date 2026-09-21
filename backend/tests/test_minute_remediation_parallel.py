from __future__ import annotations

from unittest.mock import MagicMock

from app.services.data_gap_remediation_service import _backfill_minute_kbar_date


def test_minute_remediation_uses_single_batch_call_for_all_codes(monkeypatch):
    """分钟补数按分桶批量取数：一批股票只调用一次批量入口，不再逐只并发抓取。"""
    calls: list[list[str]] = []

    def fake_batch(codes, *, trade_date, freq, universe=None, audit=None):
        calls.append(list(codes))
        if audit is not None:
            audit["unresolved_code_count"] = 0
            audit["fallback_skipped"] = False
        return {
            code: [
                {
                    "trade_date": trade_date,
                    "trade_time": f"{trade_date} 09:31:00",
                    "open": 1,
                    "high": 2,
                    "low": 1,
                    "close": 2,
                    "volume": 100,
                    "amount": 200,
                }
            ]
            for code in codes
        }

    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.fetch_minute_kbars_batch",
        fake_batch,
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._minute_probe_universe",
        lambda _db, _trade_date: ["600519"],
    )

    codes = [f"00000{i}" for i in range(1, 17)]
    result = _backfill_minute_kbar_date(MagicMock(), "2026-05-27", codes)

    assert len(calls) == 1
    assert calls[0] == codes
    assert result["target_hit_count"] == 16
    assert result["retried_code_count"] == 0
