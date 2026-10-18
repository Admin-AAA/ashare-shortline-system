from app.services.market_trend import ratios_from_returns, FIELDS


def test_ratios_zero_until_60_days_then_trend_sign():
    rets = {f"2026-01-{i:02d}" if i <= 31 else f"2026-02-{i-31:02d}" if i <= 59 else f"2026-03-{i-59:02d}": 0.5 for i in range(1, 70)}
    out = ratios_from_returns(rets)
    days = sorted(out)
    assert all(out[d][k] == 0.0 for d in days[:59] for k in FIELDS)
    assert out[days[-1]]["mkt_eqw_ma20_ratio"] > 1 and out[days[-1]]["mkt_eqw_ma60_ratio"] > 1
    down = {d: (-1.0 if d >= days[-5] else 0.5) for d in days}
    o2 = ratios_from_returns(down)
    assert 0 < o2[days[-1]]["mkt_eqw_ma20_ratio"] < 1
