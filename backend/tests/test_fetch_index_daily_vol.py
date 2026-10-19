import pandas as pd

from app.services import tushare_client


def test_fetch_index_daily_reads_tushare_vol_column(monkeypatch):
    """Tushare index_daily 返回 vol（手），不能因为只读 volume 而写成 0。"""

    class _Pro:
        def index_daily(self, **_kwargs):
            return pd.DataFrame([{
                "ts_code": "000905.SH", "trade_date": "20261019", "close": 7000.0, "open": 6990.0,
                "high": 7010.0, "low": 6980.0, "pct_chg": 0.5, "vol": 123456789.0,
            }])

    monkeypatch.setattr(tushare_client, "get_pro", lambda: _Pro())

    rows = tushare_client.fetch_index_daily(ts_code="000905.SH", start_date="20261019", end_date="20261019")

    assert rows[0]["trade_date"] == "2026-10-19"
    assert rows[0]["volume"] == 123456789.0
