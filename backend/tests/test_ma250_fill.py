import pandas as pd

from app.services.indicator_computer import compute_daily_bar_indicators
from app.services.ma250_fill import compute_ma250


def test_ma250_null_below_250_bars_and_unadjusted_mean():
    closes = pd.Series([float(i) for i in range(1, 301)])
    out = compute_ma250(closes)
    assert out.iloc[:249].isna().all()
    assert out.iloc[249] == round(sum(range(1, 251)) / 250, 3)
    assert out.iloc[-1] == round(sum(range(51, 301)) / 250, 3)


def test_indicator_computer_ma250_none_when_short():
    bars = [{"trade_date": f"d{i:04d}", "close": 10.0, "volume": 1.0} for i in range(260)]
    res = compute_daily_bar_indicators("000001", bars)
    assert res["d0248"]["ma250"] is None
    assert res["d0249"]["ma250"] == 10.0
