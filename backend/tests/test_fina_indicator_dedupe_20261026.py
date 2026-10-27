"""fina_indicator 适配器：上游同一 (ts_code, end_date) 多行时按自然键去重（2026-10-26 persist 失败回归）。"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from app.services.auxiliary import state_adapters as sa
from app.services.auxiliary.contracts import AuxiliaryDatasetRequest


class _Client:
    def fina_indicator_vip(self, period=None, **kw):
        return pd.DataFrame([
            {"ts_code": "000001.SZ", "ann_date": "20261024", "end_date": "20260930", "roe": 7.0, "eps": 1.94, "bps": 24.5646},
            {"ts_code": "000001.SZ", "ann_date": "20261024", "end_date": "20260930", "roe": 7.0, "eps": 1.94, "bps": 24.56},
            {"ts_code": "000002.SZ", "ann_date": "20261020", "end_date": "20260930", "roe": 1.0, "eps": 0.1, "bps": 5.0},
            {"ts_code": "000002.SZ", "ann_date": "20261022", "end_date": "20260930", "roe": 2.0, "eps": 0.2, "bps": 5.1},
        ])


def test_fina_indicator_rows_unique_by_natural_key():
    req = AuxiliaryDatasetRequest("fina_indicator", (), date(2026, 10, 26), True, datetime.now(timezone.utc))
    rows = sa.TushareStateProvider(_Client(), None).fina_indicator(req)
    keys = [(r["code"], r["end_date"]) for r in rows]
    assert len(keys) == len(set(keys)) == 2
    by = {r["code"]: r for r in rows}
    assert by["000002"]["ann_date"] == "20261022" and by["000002"]["roe"] == 2.0
