"""`app.services.indicator_computer` 深模块单测（纯 in-process，无 DB/网络/Tushare）。

覆盖收口后的唯一指标实现：ma/ema/rsi/macd/macd_status/bias/efficiency/
upper_shadow_count/max_drawdown/safe_div，以及 compute_daily_bar_indicators /
compute_daily_indicators 两个聚合入口的典型值与边界（空/单元素、window 超长度、
除零保护、MACD 就绪窗口）。

关联需求：2.2（指标数学只在 indicator_computer）、2.5（调用方经 import 单一深模块）、
1.1（行为不变基线：相同输入相同输出）。
"""
from __future__ import annotations

import math

import pytest

from app.services import indicator_computer as ic


# ── ma ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "values, window, expected",
    [
        ([1, 2, 3, 4, 5], 3, 4.0),      # 取末 3 个 [3,4,5] 均值
        ([1, 2, 3], 5, 2.0),            # window 超长度 → 全量均值
        ([7], 3, 7.0),                  # 单元素
        ([], 5, 0.0),                   # 空序列兜底
        ([2, 4], 2, 3.0),
    ],
)
def test_ma(values, window, expected):
    assert ic.ma(values, window) == pytest.approx(expected)


def test_ma_default_short_is_mean():
    # 默认 short="mean"：样本不足 window 取全量均值（既有调用点零行为变化基线）
    assert ic.ma([1, 2, 3], 5) == pytest.approx(2.0)
    assert ic.ma([1, 2, 3], 5, short="mean") == pytest.approx(2.0)


def test_ma_short_last_below_window_takes_last_value():
    # short="last"：样本不足 window 取最后一个值（agents/tools 历史口径）
    assert ic.ma([1, 2, 3], 5, short="last") == 3.0


def test_ma_short_last_empty_is_zero():
    assert ic.ma([], 5, short="last") == 0.0


def test_ma_short_last_full_window_equals_mean():
    # 样本充足（len>=window）：short 不影响，仍取末 window 个均值
    assert ic.ma([1, 2, 3, 4, 5], 3, short="last") == pytest.approx(4.0)


# ── ema ───────────────────────────────────────────────────────────────

def test_ema_empty_is_zero():
    assert ic.ema([], 12) == 0.0


def test_ema_single_is_itself():
    assert ic.ema([5.0], 12) == 5.0


def test_ema_constant_series_is_constant():
    # 常数序列的 EMA 恒等于该常数
    assert ic.ema([4.0, 4.0, 4.0, 4.0, 4.0], 5) == pytest.approx(4.0)


def test_ema_known_value():
    # span=2 → alpha=2/3；[1,2,3]：1 → 5/3 → 2+5/9
    assert ic.ema([1.0, 2.0, 3.0], 2) == pytest.approx(2 + 5 / 9)


# ── rsi ───────────────────────────────────────────────────────────────

def test_rsi_short_series_returns_neutral():
    assert ic.rsi([1, 2], 6) == 50.0


def test_rsi_all_gains_is_100():
    assert ic.rsi([1, 2, 3, 4, 5], 3) == 100.0


def test_rsi_all_losses_is_0():
    assert ic.rsi([5, 4, 3, 2, 1], 3) == 0.0


def test_rsi_flat_series_is_neutral():
    assert ic.rsi([3, 3, 3, 3, 3], 3) == 50.0


def test_rsi_balanced_is_50():
    # window=2，[10,11,10]：一涨一跌，均幅相等 → RSI 50
    assert ic.rsi([10, 11, 10], 2) == 50.0


# ── macd ──────────────────────────────────────────────────────────────

def test_macd_short_series_dea_is_zero():
    dif, dea = ic.macd([1, 2, 3])
    assert dea == 0.0
    assert isinstance(dif, float)


def test_macd_constant_series_is_zero():
    dif, dea = ic.macd([5.0] * 30)
    assert dif == pytest.approx(0.0)
    assert dea == pytest.approx(0.0)


def test_macd_uptrend_dif_positive():
    dif, dea = ic.macd(list(range(1, 40)))
    assert dif > 0


# ── macd_status ───────────────────────────────────────────────────────

def test_macd_status_short_series():
    assert ic.macd_status([1, 2, 3]) == "上穿零轴"


def test_macd_status_constant_series():
    # 常数长序列：dif=dea=0 → 落到 "下穿零轴" 分支
    assert ic.macd_status([5.0] * 40) == "下穿零轴"


def test_macd_status_strong_uptrend():
    assert ic.macd_status(list(range(1, 50))) == "零轴上金叉"


def test_macd_status_downtrend():
    assert ic.macd_status(list(range(50, 1, -1))) == "空头"


# ── bias ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "price, ma_value, expected",
    [
        (110, 100, 10.0),
        (90, 100, -10.0),
        (100, 100, 0.0),
        (100, 0, 0.0),   # 除零保护
    ],
)
def test_bias(price, ma_value, expected):
    assert ic.bias(price, ma_value) == pytest.approx(expected)


# ── efficiency ────────────────────────────────────────────────────────

def test_efficiency_short_series_is_zero():
    assert ic.efficiency([1, 2, 3], 3) == 0.0


def test_efficiency_straight_line_is_one():
    # 单调等差 → 方向位移 == 路径噪声 → 效率 1.0
    assert ic.efficiency([1, 2, 3, 4], 3) == pytest.approx(1.0)


def test_efficiency_zigzag_is_zero():
    # 往返 → 方向位移 0，噪声>0 → 效率 0
    assert ic.efficiency([1, 2, 1], 2) == 0.0


def test_efficiency_flat_no_noise_is_zero():
    # 无噪声（分母 0）→ 兜底 0
    assert ic.efficiency([5, 5, 5], 2) == 0.0


# ── upper_shadow_count ────────────────────────────────────────────────

def test_upper_shadow_count_empty():
    assert ic.upper_shadow_count([]) == 0


def test_upper_shadow_count_long_upper_shadow():
    bars = [{"high": 10, "low": 0, "open": 1, "close": 2}]  # (10-2)/10=0.8 ≥ 0.45
    assert ic.upper_shadow_count(bars) == 1


def test_upper_shadow_count_short_upper_shadow():
    bars = [{"high": 10, "low": 0, "open": 9, "close": 8}]  # (10-9)/10=0.1 < 0.45
    assert ic.upper_shadow_count(bars) == 0


def test_upper_shadow_count_zero_spread_not_counted():
    bars = [{"high": 5, "low": 5, "open": 5, "close": 5}]
    assert ic.upper_shadow_count(bars) == 0


def test_upper_shadow_count_missing_keys_no_crash():
    assert ic.upper_shadow_count([{}]) == 0


def test_upper_shadow_count_real_finite_values_match_old_semantics():
    # 对真实有限数值/缺键 None，与旧 float(x or 0) 口径逐位一致
    bars = [
        {"high": 10.0, "low": 0.0, "open": 1.0, "close": 2.0},   # 上影线0.8≥0.45 → 计入
        {"high": 10.0, "low": 0.0, "open": 9.0, "close": 8.0},   # 上影线0.1<0.45 → 不计入
        {},                                                        # 缺键→全 0 → spread 0 → 不计入
    ]
    assert ic.upper_shadow_count(bars) == 1


def test_upper_shadow_count_numeric_string_still_parsed():
    # 数字串仍正常解析（等价 float("10")）
    bars = [{"high": "10", "low": "0", "open": "1", "close": "2"}]  # 0.8≥0.45
    assert ic.upper_shadow_count(bars) == 1


@pytest.mark.parametrize("bad", ["-", "abc", float("nan"), float("inf"), float("-inf")])
def test_upper_shadow_count_robust_abnormal_values_归零_不抛错(bad):
    # 健壮 _num 兜底：异常字符串/NaN/inf 归零且不抛错（旧 float(x or 0) 会抛错或透传）
    bars = [{"high": bad, "low": 0, "open": 1, "close": 2}]
    assert ic.upper_shadow_count(bars) == 0


# ── strict_upper_shadow_count（严格口径，独立指标）────────────────────────

class _BarObj:
    """轻量 ORM 行替身：仅提供属性访问（模拟 DailyKbar，不引入 DB / ORM）。"""

    def __init__(self, high, low, open_, close):
        self.high = high
        self.low = low
        self.open = open_
        self.close = close


def test_strict_upper_shadow_count_empty():
    assert ic.strict_upper_shadow_count([]) == 0


def test_strict_upper_shadow_count_dict_input_hit():
    # 上影线/全幅=(10-2)/10=0.8≥0.55；实体/全幅=|2-1|/10=0.1≤0.4；实体>0 → 计入
    bars = [{"high": 10, "low": 0, "open": 1, "close": 2}]
    assert ic.strict_upper_shadow_count(bars) == 1


def test_strict_upper_shadow_count_orm_like_object_hit():
    # ORM 行对象（属性访问）与 dict 结果一致
    bars = [_BarObj(high=10, low=0, open_=1, close=2)]
    assert ic.strict_upper_shadow_count(bars) == 1


def test_strict_upper_shadow_count_body_too_large_not_counted():
    # 实体/全幅=|6-1|/10=0.5>0.4 → 不计入
    bars = [{"high": 10, "low": 0, "open": 1, "close": 6}]
    assert ic.strict_upper_shadow_count(bars) == 0


def test_strict_upper_shadow_count_zero_body_not_counted():
    # 实体=0（open==close）违反 body>0 → 不计入
    bars = [{"high": 10, "low": 0, "open": 5, "close": 5}]
    assert ic.strict_upper_shadow_count(bars) == 0


def test_strict_and_loose_are_different_indicators():
    # 同一 bar：上影线/全幅=0.5，宽松阈值 0.45 计入、严格阈值 0.55 不计入 → 证明是两个指标
    bar = [{"high": 10, "low": 0, "open": 1, "close": 5}]
    assert ic.upper_shadow_count(bar) == 1
    assert ic.strict_upper_shadow_count(bar) == 0


# ── max_drawdown ──────────────────────────────────────────────────────

def test_max_drawdown_empty():
    assert ic.max_drawdown([]) == 0.0


def test_max_drawdown_monotonic_up_is_zero():
    assert ic.max_drawdown([1, 2, 3, 4]) == pytest.approx(0.0)


def test_max_drawdown_peak_then_drop():
    assert ic.max_drawdown([10, 5]) == pytest.approx(-50.0)


def test_max_drawdown_peak_in_middle():
    assert ic.max_drawdown([10, 20, 10]) == pytest.approx(-50.0)


# ── safe_div ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "left, right, default, expected",
    [
        (10, 2, 0.0, 5.0),
        (10, 0, 0.0, 0.0),
        (10, 0, 1.0, 1.0),
        (0, 5, 0.0, 0.0),
    ],
)
def test_safe_div(left, right, default, expected):
    assert ic.safe_div(left, right, default) == pytest.approx(expected)


# ── compute_daily_bar_indicators ──────────────────────────────────────

def test_compute_daily_bar_indicators_empty():
    assert ic.compute_daily_bar_indicators("000001", []) == {}


def test_compute_daily_bar_indicators_single_bar():
    bars = [{"trade_date": "20240101", "close": 10, "volume": 100}]
    out = ic.compute_daily_bar_indicators("000001", bars)
    row = out["20240101"]
    assert row["ma5"] is None                          # 2026-10-19: 窗口不足 → None（不兜底、不写 0）
    assert row["ma60"] is None
    assert row["volume_ratio_5d"] == pytest.approx(1.0)  # ma5/ma20 = 100/100


def test_compute_daily_bar_indicators_keys_and_count():
    bars = [
        {"trade_date": f"2024010{i}", "close": 10 + i, "volume": 100 + i}
        for i in range(1, 6)
    ]
    out = ic.compute_daily_bar_indicators("000001", bars)
    assert len(out) == 5
    for row in out.values():
        assert set(row) == {"ma5", "ma10", "ma20", "ma60", "ma250", "volume_ratio_5d"}


# ── compute_daily_indicators ──────────────────────────────────────────

_EXPECTED_INDICATOR_KEYS = {
    "rsi_6", "rsi_12", "rsi_24",
    "macd_dif", "macd_dea", "macd_bar",
    "bias_ma5", "bias_ma10", "bias_ma20",
    "breakout20", "breakout60",
    "upper_shadow_count20", "max_dd20", "max_dd60",
}


def _bars(n: int) -> list[dict]:
    out = []
    for i in range(n):
        price = 10 + i
        out.append({
            "trade_date": f"2024{i + 1:04d}",
            "open": price,
            "high": price + 1,
            "low": price - 1,
            "close": price,
            "volume": 100 + i,
        })
    return out


def test_compute_daily_indicators_empty():
    assert ic.compute_daily_indicators("000001", []) == {}


def test_compute_daily_indicators_keys():
    out = ic.compute_daily_indicators("000001", _bars(40))
    assert len(out) == 40
    for row in out.values():
        assert set(row) == _EXPECTED_INDICATOR_KEYS


def test_compute_daily_indicators_macd_none_before_ready_window():
    # 少于 35 根 → MACD 尚未就绪，返回 None
    out = ic.compute_daily_indicators("000001", _bars(10))
    last = out["20240010"]
    assert last["macd_dif"] is None
    assert last["macd_dea"] is None
    assert last["macd_bar"] is None


def test_compute_daily_indicators_macd_ready_after_window():
    out = ic.compute_daily_indicators("000001", _bars(40))
    last = out["20240040"]
    assert last["macd_dif"] is not None
    assert isinstance(last["macd_dif"], float)


def test_compute_daily_indicators_bias_zero_when_on_ma():
    # 常数价格序列：price==ma → bias 恒为 0
    flat = [
        {"trade_date": f"2024{i + 1:04d}", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100}
        for i in range(30)
    ]
    out = ic.compute_daily_indicators("000001", flat)
    last = out["20240030"]
    assert last["bias_ma5"] == pytest.approx(0.0)
    assert last["bias_ma20"] == pytest.approx(0.0)


# ── 属性/不变量：对多种长度成立 ───────────────────────────────────────────

def test_compute_daily_indicators_entry_count_matches_bars():
    for n in (1, 5, 21, 35, 61):
        out = ic.compute_daily_indicators("000001", _bars(n))
        assert len(out) == n


def test_ma_never_nan_for_finite_inputs():
    for n in range(0, 50):
        values = [float(i) for i in range(n)]
        for window in (1, 5, 20, 60):
            assert not math.isnan(ic.ma(values, window))


# ── fill_missing_local_indicators ─────────────────────────────────────

def test_fill_missing_local_indicators_empty():
    assert ic.fill_missing_local_indicators([]) == []


def test_fill_missing_fills_null_and_zero_ma():
    """None/0 视为缺失，从 close 现算；已有非 0 值不覆盖。"""
    bars = [
        {"trade_date": f"2024-01-{i:02d}", "close": float(10 + i), "volume": 1000.0,
         "ma5": None, "ma10": 0, "ma20": None, "ma60": None}
        for i in range(1, 26)
    ]
    # 第 25 根预先写入真实 ma5，应保留
    bars[-1]["ma5"] = 99.0
    out = ic.fill_missing_local_indicators(bars)
    assert out is bars  # 就地
    # 前 24 根 ma5 被填
    assert out[4]["ma5"] == pytest.approx(
        sum(b["close"] for b in bars[:5]) / 5, abs=1e-3
    )
    # 最后一根 ma5 保留 99
    assert out[-1]["ma5"] == 99.0
    # ma20 在第 20 根起应非 0
    assert out[19]["ma20"] and float(out[19]["ma20"]) > 0
    assert out[-1]["ma20"] == pytest.approx(
        sum(b["close"] for b in bars[-20:]) / 20, abs=1e-3
    )


def test_fill_missing_volume_ratio_alias():
    bars = [
        {"trade_date": f"2024-01-{i:02d}", "close": 10.0, "volume": float(100 * i),
         "ma5": 10.0, "ma10": 10.0, "ma20": 10.0, "ma60": 10.0,
         "volume_ratio_5d": None, "vol_ratio_5": None}
        for i in range(1, 26)
    ]
    ic.fill_missing_local_indicators(bars)
    assert bars[-1]["volume_ratio_5d"] is not None
    assert bars[-1]["vol_ratio_5"] == bars[-1]["volume_ratio_5d"]


def test_fill_missing_does_not_overwrite_real_values():
    bars = [
        {"trade_date": "2024-01-01", "close": 10.0, "volume": 100.0,
         "ma5": 12.5, "ma10": 11.0, "ma20": 10.5, "ma60": 9.0}
    ]
    ic.fill_missing_local_indicators(bars)
    assert bars[0]["ma5"] == 12.5
    assert bars[0]["ma20"] == 10.5
