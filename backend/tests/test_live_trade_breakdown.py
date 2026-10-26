"""实盘分口径：组合时代 / 单策略分开；确认买入 / 开盘模拟（未确认）分两栏。"""
from types import SimpleNamespace

from app.services.live_trade_breakdown import classify_era, classify_fill, summarize


def test_classify_era():
    assert classify_era("preset_formal_opening", '[{"skill_id":"a"},{"skill_id":"b"}]') == "combo"
    assert classify_era(None, '[{"skill_id":"oversold-capital-reversal","weight":1}]') == "single"
    assert classify_era(None, '[{"skill_id":"a"},{"skill_id":"b"}]') == "other"


def test_classify_fill_requires_buy_confirmation():
    filled = SimpleNamespace(entry_triggered=True)
    assert classify_fill(filled, SimpleNamespace(ever_buy_confirmed=True)) == "confirmed"
    assert classify_fill(filled, SimpleNamespace(ever_buy_confirmed=False)) == "open_simulated"
    assert classify_fill(filled, None) == "open_simulated"  # 9-29 组合单：从没有确认记录
    assert classify_fill(SimpleNamespace(entry_triggered=False), None) == "not_filled"


def test_summarize_keeps_eras_and_fill_columns_apart():
    rows = [
        {"era": "combo", "strategy_id": "chao-gu-yang-jia-skill", "fill": "open_simulated", "net_return_pct": -5.7},
        {"era": "combo", "strategy_id": "chao-gu-yang-jia-skill", "fill": "open_simulated", "net_return_pct": 2.5},
        {"era": "single", "strategy_id": "oversold-capital-reversal", "fill": "confirmed", "net_return_pct": 3.0},
        {"era": "single", "strategy_id": "oversold-capital-reversal", "fill": "not_filled", "net_return_pct": None},
    ]
    out = summarize(rows)
    assert out["combo"]["open_simulated"]["n"] == 2 and out["combo"]["confirmed"]["n"] == 0
    assert out["combo"]["open_simulated"]["avg_net_return"] == -1.6
    assert out["single"]["confirmed"]["n"] == 1 and out["single"]["not_filled_count"] == 1
    assert out["single"]["by_strategy"]["oversold-capital-reversal"]["confirmed"]["avg_net_return"] == 3.0
