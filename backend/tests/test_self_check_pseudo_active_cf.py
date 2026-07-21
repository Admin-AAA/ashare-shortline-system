"""伪活跃反事实：历史弱势·活跃毒区用双维度情绪重判（T30）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "self_check.py"
_spec = importlib.util.spec_from_file_location("self_check_pseudo", _SCRIPTS)
sc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sc)


def _u(td, ret, base="弱势", sent="活跃", score=70.0, rid="rec_20260707_001_x"):
    return {
        "rid": rid,
        "trade_date": td,
        "executed_return": ret,
        "final_score": score,
        "fs": {
            "market_regime": base,
            "market_sentiment_phase": sent,
            "final_score": score,
        },
    }


def test_pseudo_active_majority_from_0707():
    # 07-07 伪活跃（lu<ld），06-10 真活跃（lu>>ld）
    snaps = {
        "2026-07-07": {
            "sample_count": 4948,
            "up_count": 615,
            "limit_up_count": 44,
            "limit_down_count": 54,
        },
        "2026-06-10": {
            "sample_count": 4950,
            "up_count": 1478,
            "limit_up_count": 90,
            "limit_down_count": 51,
        },
    }
    unc = [
        _u("2026-07-07", -5.7, score=79.0, rid="rec_20260707_a"),
        _u("2026-07-07", -5.7, score=78.0, rid="rec_20260707_b"),
        _u("2026-06-10", -5.7, score=70.0, rid="rec_20260610_a"),
    ]
    cf = sc._pseudo_active_sentiment_counterfactual(unc, snaps)
    assert cf is not None
    assert cf["weak_active_n"] == 3
    assert cf["pseudo_n"] == 2
    assert cf["still_active_n"] == 1
    assert cf["day_breakdown"]["2026-07-07"]["pseudo"] == 2
    assert cf["day_breakdown"]["2026-06-10"]["pseudo"] == 0


def test_no_weak_active_returns_none():
    unc = [_u("2026-07-08", -1.0, base="强牛", sent="高潮")]
    assert sc._pseudo_active_sentiment_counterfactual(unc, {}) is None


def test_composite_regime_label_parsed():
    """market_regime 存「弱势·活跃」合体时也能识别。"""
    snaps = {
        "2026-07-07": {
            "sample_count": 4948,
            "up_count": 615,
            "limit_up_count": 44,
            "limit_down_count": 54,
        },
    }
    u = {
        "rid": "rec_20260707_x",
        "trade_date": "2026-07-07",
        "executed_return": -5.0,
        "final_score": 80.0,
        "fs": {"market_regime": "弱势·活跃"},
    }
    cf = sc._pseudo_active_sentiment_counterfactual([u], snaps)
    assert cf is not None
    assert cf["weak_active_n"] == 1
    assert cf["pseudo_n"] == 1


def test_residual_breakdown_reads_meta_fallback():
    unc = [
        {
            "fs": {
                "score_line_meta": {
                    "market_base": "弱势",
                    "market_sentiment": "活跃",
                },
            },
            "executed_return": -5.0,
        }
    ]
    rows = sc._residual_regime_sentiment_breakdown(unc)
    assert rows
    assert rows[0][0] == ("弱势", "活跃")
    assert rows[0][1]["count"] == 1
