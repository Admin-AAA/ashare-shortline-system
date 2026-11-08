from datetime import date, timedelta

from app.services.market_trend import ratios_from_returns, FIELDS


def _dates(n: int) -> list[str]:
    start = date(2026, 1, 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def test_ratios_zero_until_60_days_then_trend_sign():
    rets = {d: 0.5 for d in _dates(69)}
    out = ratios_from_returns(rets)
    days = sorted(out)
    assert all(out[d][k] == 0.0 for d in days[:59] for k in ("mkt_eqw_ma20_ratio", "mkt_eqw_ma60_ratio"))
    assert out[days[-1]]["mkt_eqw_ma20_ratio"] > 1 and out[days[-1]]["mkt_eqw_ma60_ratio"] > 1
    # 连续上涨 → RSI6=100（第 6 天后即可计算）
    assert out[days[-1]]["mkt_rsi6"] == 100.0
    down = {d: (-1.0 if d >= days[-5] else 0.5) for d in days}
    o2 = ratios_from_returns(down)
    assert 0 < o2[days[-1]]["mkt_eqw_ma20_ratio"] < 1
    assert o2[days[-1]]["mkt_rsi6"] < 50


def test_rsi6_warmup_and_falling_series():
    rets = {d: -1.0 for d in _dates(20)}
    out = ratios_from_returns(rets)
    days = sorted(out)
    # 前 6 天预热为 0；之后连续下跌为 0
    assert out[days[5]]["mkt_rsi6"] == 0.0
    assert out[days[-1]]["mkt_rsi6"] == 0.0
    assert "mkt_rsi6" in FIELDS
