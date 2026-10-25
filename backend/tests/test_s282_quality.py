"""§282 收益来源指标与新达标标准（纯函数 + 展示字段结构）。"""
import json
from pathlib import Path

from scripts.midterm.s282_quality import LABEL_LOTTERY, LABEL_ROBUST, tail_metrics, verdict


def test_tail_metrics_robust_and_lottery():
    robust = tail_metrics([1.0] * 20)
    assert robust["label"] == LABEL_ROBUST and robust["trim5"] == 1.0 and robust["median"] == 1.0
    lot = tail_metrics([-1.0] * 18 + [30.0, 40.0])  # 靠两笔暴涨
    assert lot["label"] == LABEL_LOTTERY and lot["median"] < 0
    assert lot["top10_share"] is not None and lot["top10_red"]
    assert tail_metrics([-1.0, -2.0])["top10_share"] is None  # 总利润≤0 不适用
    assert tail_metrics([-1, -1, 2, -1, -1, -1])["streak"] == 3


def test_verdict_clauses():
    ok = {"n": 60, "avg": 1.0, "pf": 1.5, "mdd": -10.0}
    ph = {"P19": 1, "P20": 1, "P21": 1, "W0": -1.0, "W00": -2.0, "W1": 1, "W2": 1, "R12": 1}
    assert verdict("long", ok, ok, ph)["pass"]
    assert verdict("long", ok, {**ok, "mdd": -21.0}, ph)["fail"] == ["2b_mdd"]
    assert verdict("mid", ok, {**ok, "mdd": -21.0}, ph)["pass"]  # 中线上限 25%
    assert "3b_bear" in verdict("mid", ok, ok, {**ph, "W00": -5.1})["fail"]
    assert "3a_phases" in verdict("mid", ok, ok, {**ph, "P19": -1, "P20": -1, "W1": -1})["fail"]


def test_live_strategies_have_quality_block():
    from app.services import midterm_service as ms
    data = json.loads(ms.METRICS_FILE.read_text())
    for sid in ms.STRATEGIES:
        q = data[sid].get("quality")
        assert q and q["standard"] == "§282" and {"R12", "N3", "label", "pass"} <= set(q), sid
        assert {"trim5", "top10_share", "median", "streak"} <= set(q["R12"]), sid
    short = json.loads((Path(ms.__file__).resolve().parents[1] / "policies" / "short_quality_metrics.json").read_text())
    assert "oversold-capital-reversal" in short["skills"]
