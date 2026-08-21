from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from app.services.data_gap_remediation_service import _backfill_minute_kbar_date


def test_minute_remediation_fetches_codes_with_bounded_parallelism(monkeypatch):
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_fetch(code, *, trade_date, freq):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.01)
            return [
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
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr("app.services.data_gap_remediation_service.fetch_minute_kbars", fake_fetch)

    result = _backfill_minute_kbar_date(MagicMock(), "2026-05-27", [f"00000{i}" for i in range(1, 17)])

    assert result["target_hit_count"] == 16
    assert result["failed_code_count"] == 0
    assert 2 <= max_active <= 8
