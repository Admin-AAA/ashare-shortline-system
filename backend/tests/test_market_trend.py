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


def test_qvix_chg5_by_dates_warmup_calc_and_cleaning():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.market import IndexDaily
    from app.services.market_trend import qvix_chg5_by_dates

    assert "mkt_qvix50_chg5" in FIELDS
    eng = create_engine("sqlite://")
    IndexDaily.__table__.create(eng)
    session = sessionmaker(bind=eng)()
    ds = _dates(10)
    # 前 5 日 QVIX=20，后 5 日跳到 30 → 第 6 日起五日涨幅 = 30/20-1 = 0.5
    for i, d in enumerate(ds):
        session.add(IndexDaily(ts_code="QVIX50.SH", trade_date=d, close=20.0 if i < 5 else 30.0))
    # 其它指数不纳入；close<=0 丢弃
    session.add(IndexDaily(ts_code="QVIX300.SH", trade_date=ds[-1], close=99.0))
    session.add(IndexDaily(ts_code="QVIX50.SH", trade_date="2026-01-11", close=0.0))
    session.commit()

    out = qvix_chg5_by_dates(session, ds[0], "2026-01-11")
    for d in ds[:5]:
        assert d not in out  # 预热不足
    for d in ds[5:]:
        assert out[d] == 0.5
    assert "2026-01-11" not in out  # close<=0 丢弃
    session.close()
