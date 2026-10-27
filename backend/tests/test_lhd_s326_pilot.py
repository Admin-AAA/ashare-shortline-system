"""§326 追龙方法试点的纯函数：一字/开板/保守成交/跌停顺延出场。"""
import sys
from pathlib import Path
import numpy as np

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/shortline_research")); sys.path.insert(0, str(B / "scripts/midterm"))
import lhd_s326_pilot as P  # noqa: E402

U = 11.0


def bars(rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]  # o,h,l,c,v


def test_yizi():
    o, h, l, c, v = bars([[11, 11, 11, 11, 100]] * 5)
    assert P.classify_next_day(o, h, l, c, v, U)[0] == "yizi"


def test_other_when_open_below():
    o, h, l, c, v = bars([[10.5, 10.6, 10.4, 10.5, 100]] * 3)
    assert P.classify_next_day(o, h, l, c, v, U)[0] == "other"
    assert P.ctrl_entry(o, U) == 10.5 * 1.001


def test_open_then_filled_next_bar_open_plus_slip():
    rows = [[11, 11, 11, 11, 100], [11, 11, 11, 11, 100], [11, 11, 10.8, 10.85, 100], [10.86, 10.9, 10.8, 10.88, 100], [10.9, 10.9, 10.9, 10.9, 100]]
    o, h, l, c, v = bars(rows)
    cls, i, px = P.classify_next_day(o, h, l, c, v, U)
    assert (cls, i) == ("open_sealed_filled", 3)
    assert abs(px - 10.86 * 1.001) < 1e-9  # 用下一根开盘价，不用开板那根的最低价


def test_no_fill_when_resealed_next_bar():
    rows = [[11, 11, 11, 11, 100], [11, 11, 10.8, 10.9, 100], [11, 11, 11, 11, 100], [11, 11, 11, 11, 100]]
    o, h, l, c, v = bars(rows)
    assert P.classify_next_day(o, h, l, c, v, U)[0] == "open_sealed_nofill"


def test_open_on_last_bar_nofill_and_noopen():
    o, h, l, c, v = bars([[11, 11, 11, 11, 100], [11, 11, 10.8, 10.9, 100]])
    assert P.classify_next_day(o, h, l, c, v, U)[0] == "open_sealed_nofill"
    o, h, l, c, v = bars([[11, 11, 11, 11, 100], [11, 11, 10.998, 11, 100]])
    assert P.classify_next_day(o, h, l, c, v, U)[0] == "yizi"


def test_exit_defers_on_limit_down():
    closes = [11.0, 10.5, 9.45, 9.9]  # k=2 收盘 9.45 = 10.5*0.9 跌停价 -> 顺延到 k=3
    xp, k = P.exit_price(closes, 10.0, 0.10, 2)
    assert (xp, k) == (9.9, 3)
    xp, k = P.exit_price([11.0, 10.5, 9.45], 10.0, 0.10, 2)
    assert xp is None


def test_net_and_ratio():
    assert abs(P.net_pct(10.0, 10.5) - (5.0 - 0.3)) < 1e-9
    assert P.snap_ratio(11.0, 10.0) == 0.10 and P.snap_ratio(12.0, 10.0) == 0.20 and P.snap_ratio(10.5, 10.0) == 0.05
