"""self_check D 项 phase 信息量摘要接入回归。"""
from __future__ import annotations

import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import diagnose_phase_information as dpi  # type: ignore
import self_check  # type: ignore


def test_build_phase_report_regime_significant_when_skewed():
    """构造明显分桶差异 → regime p 应显著。"""
    recs = (
        [{"regime": "弱势", "sentiment": "活跃", "win": True, "ret": 3.0} for _ in range(40)]
        + [{"regime": "弱势", "sentiment": "活跃", "win": False, "ret": -3.0} for _ in range(10)]
        + [{"regime": "震荡", "sentiment": "活跃", "win": True, "ret": 2.0} for _ in range(15)]
        + [{"regime": "震荡", "sentiment": "活跃", "win": False, "ret": -4.0} for _ in range(35)]
    )
    rep = dpi.build_phase_report(recs)
    assert rep["overall"]["n"] == 100
    assert rep["regime_p"] < 0.05
    assert rep["by_regime"]["弱势"]["winrate"] > rep["by_regime"]["震荡"]["winrate"]


def test_format_phase_information_lines_contains_summary():
    """format 输出含 n / χ² / p。"""
    recs = (
        [{"regime": "弱势", "sentiment": "活跃", "win": True, "ret": 3.0} for _ in range(40)]
        + [{"regime": "弱势", "sentiment": "活跃", "win": False, "ret": -3.0} for _ in range(10)]
        + [{"regime": "震荡", "sentiment": "平淡", "win": True, "ret": 1.0} for _ in range(12)]
        + [{"regime": "震荡", "sentiment": "平淡", "win": False, "ret": -2.0} for _ in range(28)]
    )
    rep = dpi.build_phase_report(recs)
    lines = self_check._format_phase_information_lines(rep)
    text = "\n".join(lines)
    assert "phase 信息量" in text
    assert "LIVE formal n=" in text
    assert "χ²=" in text
    assert "p=" in text
    assert "regime" in text and "sentiment" in text


def test_format_phase_information_lines_regime_not_significant():
    """各桶同质 → regime 无显著信息量，提示勿加 phase veto。"""
    recs = []
    for reg in ("弱势", "震荡", "结构性强势"):
        recs += [{"regime": reg, "sentiment": "活跃", "win": True, "ret": 2.0} for _ in range(20)]
        recs += [{"regime": reg, "sentiment": "活跃", "win": False, "ret": -2.0} for _ in range(20)]
    rep = dpi.build_phase_report(recs)
    lines = self_check._format_phase_information_lines(rep)
    text = "\n".join(lines)
    assert "无显著信息量" in text
    assert "勿在 phase 上加精细" in text or "过拟合" in text
