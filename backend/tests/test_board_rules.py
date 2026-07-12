"""board_rules 深模块单测：涨跌停价 / 板位 / 一字板判定 / 20cm 识别。

纯 in-process（无数据库 / 网络）。bar 用 SimpleNamespace 构造即可，因为 board_rules
以 getattr / 属性访问读取 bar 字段。

覆盖：
- 两个并存的一字涨停接口（代码感知四点齐平 vs 主板给定价 open/low 两点）真 / 假与边界；
- 卡片口径一字涨停；一字跌停镜像；
- 主板涨停价精确值；
- limit_pct_for_code 与 is_twenty_cm_stock 在主板 / 创业 / 科创 / 北交及缺板块信息下的判定。
"""
from types import SimpleNamespace

import pytest

from app.engine import board_rules as br


def _bar(*, open, high, low, close, pct_chg):
    return SimpleNamespace(open=open, high=high, low=low, close=close, pct_chg=pct_chg)


# ── limit_pct_for_code：板位 → 涨跌停幅度 ──
@pytest.mark.parametrize(
    "code, expected",
    [
        ("600000", 10.0),
        ("000001", 10.0),
        ("002415", 10.0),
        ("300123", 20.0),
        ("301001", 20.0),
        ("688001", 20.0),
        ("830799", 30.0),  # 北交 8 开头
        ("430047", 30.0),  # 北交 4 开头
        ("", 10.0),
    ],
)
def test_limit_pct_for_code(code, expected):
    assert br.limit_pct_for_code(code) == expected


# ── main_board_up_limit：主板 10% 涨停价（精确值，与原口径逐字一致）──
@pytest.mark.parametrize(
    "prev_close, expected",
    [
        (10.0, 11.0),
        (20.0, 22.0),
        (5.0, 5.5),
        (7.0, 7.7),
    ],
)
def test_main_board_up_limit(prev_close, expected):
    assert br.main_board_up_limit(prev_close) == expected


def test_main_board_up_limit_equivalent_to_limit_price_10pct():
    # 数值上等价于 limit_price(., 10, up=True)，两条路径一致
    for pc in (3.21, 10.0, 88.88, 100.0):
        assert br.main_board_up_limit(pc) == br.limit_price(pc, 10.0, up=True)


# ── is_one_word_limit_up_bar：代码感知、四点齐平 ──
def test_one_word_up_bar_main_board_true():
    # 主板前收 10 → 涨停 11，四点齐平
    bar = _bar(open=11.0, high=11.0, low=11.0, close=11.0, pct_chg=10.0)
    assert br.is_one_word_limit_up_bar("600000", bar) is True


def test_one_word_up_bar_not_limit_false():
    bar = _bar(open=10.5, high=10.8, low=10.3, close=10.6, pct_chg=6.0)
    assert br.is_one_word_limit_up_bar("600000", bar) is False


def test_one_word_up_bar_open_at_limit_but_low_below_false():
    # 盘中砸开：open 到涨停但 low 掉下来 → 非一字（四点缺一）
    bar = _bar(open=11.0, high=11.0, low=10.2, close=10.9, pct_chg=9.0)
    assert br.is_one_word_limit_up_bar("600000", bar) is False


def test_one_word_up_bar_is_code_aware_20cm():
    # 20cm(300) 前收 10 → 20% 涨停 12，四点齐平 → True
    bar_20 = _bar(open=12.0, high=12.0, low=12.0, close=12.0, pct_chg=20.0)
    assert br.is_one_word_limit_up_bar("300123", bar_20) is True
    # 同代码若只涨到 +10%（close=11）：对 20cm 而言未达 20% 涨停价 12 → False
    # 验证板位幅度按代码前缀（20%）而非固定 10%
    bar_10 = _bar(open=11.0, high=11.0, low=11.0, close=11.0, pct_chg=10.0)
    assert br.is_one_word_limit_up_bar("300123", bar_10) is False


def test_one_word_up_bar_invalid_prev_close_false():
    # close<=0 → 前收无效 → False（不抛异常）
    bar = _bar(open=0.0, high=0.0, low=0.0, close=0.0, pct_chg=10.0)
    assert br.is_one_word_limit_up_bar("600000", bar) is False


# ── is_one_word_limit_up_open_low：调用方给定涨停价、仅看 open/low ──
def test_one_word_open_low_true():
    assert br.is_one_word_limit_up_open_low(SimpleNamespace(open=11.0, low=11.0), 11.0) is True


def test_one_word_open_low_low_below_false():
    assert br.is_one_word_limit_up_open_low(SimpleNamespace(open=11.0, low=10.5), 11.0) is False


def test_one_word_open_low_open_below_false():
    assert br.is_one_word_limit_up_open_low(SimpleNamespace(open=10.9, low=11.0), 11.0) is False


def test_one_word_open_low_ignores_high_close():
    # 该口径只读 open/low：即使不提供 high/close 也应工作，且封板判定为真
    bar = SimpleNamespace(open=11.0, low=10.98)  # 10.98 >= 11*0.995=10.945 → True
    assert br.is_one_word_limit_up_open_low(bar, 11.0) is True


def test_one_word_open_low_tolerance_boundary():
    # 阈值 up_limit*0.995：10.0 涨停价，边界 9.95
    assert br.is_one_word_limit_up_open_low(SimpleNamespace(open=9.95, low=9.95), 10.0) is True
    assert br.is_one_word_limit_up_open_low(SimpleNamespace(open=9.94, low=9.95), 10.0) is False


# ── is_one_word_limit_up_card：卡片口径 ──
def test_one_word_up_card_true():
    card = {"code": "600000", "is_limit_up": True, "open": 11.0, "high": 11.0,
            "low": 11.0, "close": 11.0, "pct": 10.0}
    assert br.is_one_word_limit_up_card(card) is True


def test_one_word_up_card_not_limit_up_flag_false():
    card = {"code": "600000", "is_limit_up": False, "open": 11.0, "high": 11.0,
            "low": 11.0, "close": 11.0, "pct": 10.0}
    assert br.is_one_word_limit_up_card(card) is False


def test_one_word_up_card_missing_field_false():
    # 缺 pct → 数据不全 → False
    card = {"code": "600000", "is_limit_up": True, "open": 11.0, "high": 11.0,
            "low": 11.0, "close": 11.0}
    assert br.is_one_word_limit_up_card(card) is False


# ── is_one_word_limit_down_bar：一字跌停镜像 ──
def test_one_word_down_bar_true():
    # 主板前收 10 → 跌停 9，四点齐平
    bar = _bar(open=9.0, high=9.0, low=9.0, close=9.0, pct_chg=-10.0)
    assert br.is_one_word_limit_down_bar("600000", bar) is True


def test_one_word_down_bar_false_when_not_locked():
    bar = _bar(open=9.5, high=9.8, low=9.3, close=9.6, pct_chg=-4.0)
    assert br.is_one_word_limit_down_bar("600000", bar) is False


# ── is_twenty_cm_stock：各板识别 + 边界 ──
@pytest.mark.parametrize(
    "stock, expected_flag, expected_basis",
    [
        ({"code": "300123"}, True, "创业板 20cm 标的"),
        ({"code": "301001"}, True, "创业板 20cm 标的"),
        ({"code": "688001"}, True, "科创板 20cm 标的"),
        ({"code": "689009"}, True, "科创板 20cm 标的"),
        ({"code": "830799"}, True, "北交所高波动标的"),
        ({"code": "430047"}, True, "北交所高波动标的"),
        ({"code": "600000", "exchange": "BJ"}, True, "北交所高波动标的"),
        # market/board 文字优先于代码前缀
        ({"code": "600000", "market": "创业板"}, True, "创业板 20cm 标的"),
        ({"code": "000001", "board": "科创板"}, True, "科创板 20cm 标的"),
        ({"code": "000001", "market": "北交所"}, True, "北交所高波动标的"),
    ],
)
def test_is_twenty_cm_stock_positive(stock, expected_flag, expected_basis):
    flag, basis = br.is_twenty_cm_stock(stock)
    assert flag is expected_flag
    assert basis == expected_basis


def test_is_twenty_cm_stock_main_board_with_market_false():
    flag, basis = br.is_twenty_cm_stock({"code": "600000", "market": "主板", "exchange": "SH"})
    assert flag is False
    assert basis == "非 20cm 标的"


def test_is_twenty_cm_stock_missing_board_info_false():
    # 主板代码但既无 exchange 也无 market → 未识别 + 缺板块信息说明
    flag, basis = br.is_twenty_cm_stock({"code": "600000"})
    assert flag is False
    assert basis == "缺少板块信息，按代码前缀未识别为 20cm"
