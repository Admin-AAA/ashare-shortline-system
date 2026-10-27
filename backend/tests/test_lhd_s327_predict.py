"""§327：分钟派生特征、AUC/分档命中率的纯函数。"""
import sys
from pathlib import Path
import numpy as np

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/shortline_research")); sys.path.insert(0, str(B / "scripts/midterm"))
import lhd_s327_predict as P  # noqa: E402

U = 11.0


def day(rows, start=570):
    a = np.array(rows, dtype=float)
    tm = start + np.arange(len(a))
    tm = np.where(tm > 690, tm + 90, tm)  # 午休
    return tm, a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]


def test_trade_minute_index():
    assert list(P.trade_minute_index([570, 600, 690, 780, 900])) == [0, 30, 120, 120, 240]


def test_day_stats_open_board_once():
    rows = []
    for i in range(241):
        if i < 10:
            rows.append([10.5, 10.6, 10.4, 10.55, 100])           # 触板前
        elif i == 10:
            rows.append([10.8, 11.0, 10.8, 11.0, 300])            # 触板并封住
        elif i == 50:
            rows.append([11.0, 11.0, 10.9, 10.95, 200])           # 开板一次
        else:
            rows.append([11.0, 11.0, 11.0, 11.0, 50])
    tm, o, h, l, c, v = day(rows)
    f = P.day_stats(tm, o, h, l, c, v, U, 10.0)
    assert f["m_touch"] == 10 and f["m_open_n"] == 1
    assert 0.0 < f["m_vol_pre"] < f["m_vol_post"] < 1.0
    assert abs(f["m_gap"] - 0.05) < 1e-9 and f["m_dd_pre"] < 0


def test_prev_day_stats():
    rows = [[10, 10.5, 9.5, 10.0, 100]] * 240 + [[10, 11, 9, 10.5, 100]]
    tm, o, h, l, c, v = day(rows)
    g = P.prev_day_stats(tm, o, h, l, c, v)
    assert abs(g["m1_pos"] - 0.75) < 1e-9 and g["m1_amp"] > 0 and 0 <= g["m1_vol30"] <= 1


def test_auc_hit_table():
    y = np.array([0] * 90 + [1] * 10); s = np.concatenate([np.linspace(0, 0.8, 90), np.linspace(0.7, 1, 10)])
    assert P.auc(y, s) > 0.9
    h = P.hit_table(y, s)
    assert h["top10"]["hit"] >= 0.6 and h["top10"]["lift"] >= 6 and h["base"] == 0.1


def test_uni_auc_direction():
    import pandas as pd
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 2000).astype(float)
    df = pd.DataFrame({"Y": y, "year": rng.choice([2022, 2023], 2000), "x": y * 1.5 + rng.normal(size=2000), "z": rng.normal(size=2000)})
    r = P.uni_auc(df, ["x", "z"], [2022, 2023])
    assert r["x"]["has_power"] and not r["z"]["has_power"]
