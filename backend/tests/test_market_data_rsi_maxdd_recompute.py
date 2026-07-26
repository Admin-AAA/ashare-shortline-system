"""回归测试：_build_stock_from_bars 必须始终从收盘价重算 rsi / max_dd，
不信任 daily_kbars 回填的 _indicator 缓存列。

已知数据墙：daily_kbars.rsi_12 / max_dd20 回填列失真（如 301047 下跌日
rsi_12 从 22.3 跳到 55.4，max_dd20 缓存 -19.4 而按收盘价实为 -7.3）。
失真列会污染策略 when 条件（rsi_12<55 / max_dd20>=-18 等）与 LLM 提示，
导致推荐过滤与 LLM 推理基于错误超买/超卖信号。本测试锁定"始终重算"。
"""

from datetime import date, timedelta

from app.services.market_data import _build_stock_from_bars, _max_drawdown, _rsi


def _make_bars() -> list[dict]:
    """构造 25 根 K 线：先缓慢上行至 110，末 5 根暴跌至 85（深度回撤+超卖）。"""
    bars = []
    base = date(2026, 6, 20)
    prices = [100 + i for i in range(20)]          # 100..119 上行
    prices += [110, 100, 92, 88, 85]                # 末 5 根暴跌
    for i, close in enumerate(prices):
        d = (base + timedelta(days=i)).isoformat()
        bars.append({
            "trade_date": d,
            "open": float(close),
            "high": float(close + 1),
            "low": float(close - 1),
            "close": float(close),
            "volume": 1000.0,
            "amount": 100000.0,
            "pct_chg": 0.0,
            "turnover_rate": 1.0,
            "ma5": None, "ma10": None, "ma20": None, "ma60": None,
            "volume_ratio_5d": 1.0,
            # 故意写入失真的缓存列：rsi_12 写成 999（明显不可能），
            # max_dd20 写成 -1.0（浅回撤，而实际深度回撤）。
            "_indicator": {
                "rsi_6": 999.0, "rsi_12": 999.0, "rsi_24": 999.0,
                "max_dd20": -1.0, "max_dd60": -1.0,
                "macd_dif": 0.0, "macd_dea": 0.0, "macd_bar": 0.0,
                "bias_ma5": 0.0, "bias_ma10": 0.0, "bias_ma20": 0.0,
                "breakout20": 0, "breakout60": 0, "upper_shadow_count20": 0,
            },
        })
    return bars


def _quote() -> dict:
    return {
        "code": "000001", "name": "测试股", "industry": "",
        "trade_date": "2026-07-21", "price": 85.0, "pct": -5.0,
        "exchange": "SZ", "is_st": False, "main_net_inflow_yi": 0.0,
    }


def test_rsi_recomputed_from_closes_not_cached():
    bars = _make_bars()
    closes = [b["close"] for b in bars]
    expected_rsi12 = round(_rsi(closes, 12), 3)
    expected_rsi6 = round(_rsi(closes, 6), 3)
    expected_rsi24 = round(_rsi(closes, 24), 3)

    stock = _build_stock_from_bars(_quote(), bars, "test", "test")

    # 暴跌末段应为超卖（rsi 远低于 50），绝不可能是缓存的 999。
    # 顶层只暴露 rsi_12（策略 when 条件与 LLM 提示消费此字段）。
    assert stock["rsi_12"] == expected_rsi12
    assert stock["rsi_12"] < 50, f"暴跌末段 rsi_12 应超卖，实际 {stock['rsi_12']}"
    assert stock["rsi_12"] != 999.0, "绝不能信任失真的 _indicator.rsi_12 缓存列"


def test_max_dd_recomputed_from_closes_not_cached():
    bars = _make_bars()
    closes = [b["close"] for b in bars]
    expected_dd20 = round(_max_drawdown(closes[-20:]), 3)
    expected_dd60 = round(_max_drawdown(closes[-60:]), 3)

    stock = _build_stock_from_bars(_quote(), bars, "test", "test")

    # 末 5 根从 110 暴跌到 85 = -22.7%，深度回撤，绝不可能是缓存的 -1.0
    assert stock["max_dd20"] == expected_dd20
    assert stock["max_dd60"] == expected_dd60
    assert stock["max_dd20"] <= -15.0, f"深度回撤 max_dd20 应 <= -15，实际 {stock['max_dd20']}"
    assert stock["max_dd20"] != -1.0, "绝不能信任失真的 _indicator.max_dd20 缓存列"


def test_platform_highs_exclude_the_signal_bar_breakout():
    bars = _make_bars()
    prior_high = max(bar["high"] for bar in bars[:-1])
    bars[-1].update({"open": 125.0, "high": 130.0, "low": 124.0, "close": 130.0})
    quote = {**_quote(), "price": 130.0, "pct": 9.9}

    stock = _build_stock_from_bars(quote, bars, "test", "test")

    assert stock["platform_high_20d"] == prior_high
    assert stock["platform_high_60d"] == prior_high
    assert stock["high_20d"] == 130.0
