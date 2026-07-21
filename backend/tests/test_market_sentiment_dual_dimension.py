"""市场情绪双维度：涨停率 + 涨停/跌停比（T28，2026-07-21）。

背景: _market_sentiment / market_sentiment_phase 仅用 limit_up_rate，
会把「普跌日零星涨停」标活跃——07-07 up_ratio=0.124、lu=44、ld=54 仍被标活跃，
madj=+5 放宽 score_line，弱势·活跃 残留毒区 11 笔核心日。
修复：活跃要求 lu_rate>0.5% 且 lu/ld ≥ 1.2（或 ld=0）。
"""
from __future__ import annotations

import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.engine.aggregator import (  # noqa: E402
    _market_sentiment,
    resolve_score_line_context,
)
from app.services.factors.shortline import market_sentiment_phase  # noqa: E402


def test_0707_pseudo_active_becomes_flat():
    """07-07：涨停 0.9% 但跌停更多、普跌 → 平淡，非活跃。"""
    snap = {
        "sample_count": 4948,
        "limit_up_count": 44,
        "limit_down_count": 54,
        "up_count": 615,
    }
    assert _market_sentiment(snap) == "平淡"
    assert market_sentiment_phase(snap) == "平淡"
    ctx = resolve_score_line_context(72.0, snap)
    assert ctx["market_sentiment"] == "平淡"
    assert ctx["market_base"] == "弱势"
    # 弱势 +2，平淡 +0 → madj=2，有效线 74（非活跃 +5→77）
    assert ctx["market_adjustment"] == 2.0
    assert ctx["effective_score_line"] == 74.0


def test_healthy_active_still_active():
    snap = {
        "sample_count": 5000,
        "limit_up_count": 40,  # 0.8%
        "limit_down_count": 10,
        "up_count": 2000,
    }
    assert _market_sentiment(snap) == "活跃"
    assert market_sentiment_phase(snap) == "活跃"


def test_active_with_zero_limit_down():
    snap = {
        "sample_count": 5000,
        "limit_up_count": 40,
        "limit_down_count": 0,
        "up_count": 2500,
    }
    assert _market_sentiment(snap) == "活跃"


def test_climax_unchanged():
    snap = {
        "sample_count": 1000,
        "limit_up_count": 20,  # 2%
        "limit_down_count": 0,
        "up_count": 600,
    }
    assert _market_sentiment(snap) == "高潮"
    assert market_sentiment_phase(snap) == "高潮"


def test_0529_ld_heavy_not_active():
    """05-29：lu=61 ld=134 → 不应活跃。"""
    snap = {
        "sample_count": 4947,
        "limit_up_count": 61,
        "limit_down_count": 134,
        "up_count": 1354,
    }
    assert _market_sentiment(snap) != "活跃"
    assert market_sentiment_phase(snap) != "活跃"


def test_aggregator_and_factor_in_sync():
    """两入口必须同标签，防 score_line 与 feature 漂移。"""
    cases = [
        {"sample_count": 4948, "limit_up_count": 44, "limit_down_count": 54},
        {"sample_count": 5000, "limit_up_count": 40, "limit_down_count": 10},
        {"sample_count": 1000, "limit_up_count": 20, "limit_down_count": 0},
        {"sample_count": 1000, "limit_up_count": 0, "limit_down_count": 5},
    ]
    for s in cases:
        assert _market_sentiment(s) == market_sentiment_phase(s), s
