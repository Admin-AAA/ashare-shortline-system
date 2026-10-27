"""§309：「比沪深300多赚」指标——计算函数与落盘字段（只展示，不参与达标判定）。"""
import importlib.util
import json
from pathlib import Path

import pandas as pd

B = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("s309", B / "scripts/midterm/s309_excess_hs300.py")
s309 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s309)


def test_idx_returns_and_summary_alignment():
    ix = pd.DataFrame({"open": [100.0, 102.0, 110.0], "close": [101.0, 105.0, 108.0]},
                      index=["2026-01-05", "2026-01-06", "2026-01-07"])
    # 买入日开盘 → 卖出日开盘 / 收盘；日期 YYYYMMDD 与 YYYY-MM-DD 都认
    assert abs(s309.idx_returns(ix, ["2026-01-05"], ["2026-01-07"], "open")[0] - 10.0) < 1e-9
    assert abs(s309.idx_returns(ix, ["20260105"], ["20260107"], "close")[0] - 8.0) < 1e-9
    r = s309.summarize([12.0, -1.0, 3.0], [10.0, 2.0, 1.0])
    assert r == {"excess_hs300": 0.33, "hs300_avg": 4.33, "beat_hs300": 66.7}


def test_missing_index_day_raises():
    ix = pd.DataFrame({"open": [1.0], "close": [1.0]}, index=["2026-01-05"])
    try:
        s309.idx_returns(ix, ["2026-01-05"], ["2026-01-09"], "open")
    except KeyError:
        return
    raise AssertionError("缺沪深300日线应中止，而不是静默按 0 算")


def test_metrics_files_have_hs300_fields():
    m = json.loads((B / "app/services/midterm_backtest_metrics.json").read_text())
    for sid, e in m.items():
        for w in ("R12", "N3"):
            q = e["quality"][w]
            assert {"excess_hs300", "hs300_avg", "beat_hs300"} <= set(q), (sid, w)
            assert 0 <= q["beat_hs300"] <= 100
            # 每笔平均 = 策略每笔均值 − 指数同期均值（四舍五入误差内）
            assert abs((q["avg"] - q["hs300_avg"]) - q["excess_hs300"]) < 0.03, (sid, w)
    s = json.loads((B / "app/policies/short_quality_metrics.json").read_text())
    for g, e in s["skills"].items():
        for w in ("R12", "N3"):
            assert "excess_hs300" in e[w] and 0 <= e[w]["beat_hs300"] <= 100, (g, w)
